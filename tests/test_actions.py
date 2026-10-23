import copy
import pytest
from leakage.actions import Actions, ActionError, records
from leakage.store import Store
from leakage.reconcile import reconcile

def draft(a):return a.draft('make_good','P-12345','10000','Missing October')
def approve(a,p):
    a.store.displayed(a.thread,[p['key']]);a.approve([p['key']])

def test_drafts_rejection_and_simulation_leave_records_unchanged(actions):
    from leakage.tools import Toolbox
    initial=records(actions.store.read());p=draft(actions)
    assert records(actions.store.read())==initial
    Toolbox(actions.store,actions.thread).simulate_amendment('P-12345',{'effective_date':'2024-11-01','amount':'12000','reason':'upgrade'})
    actions.reject(p['key']);assert records(actions.store.read())==initial
    assert actions.get(p['key'])['status']=='Rejected'

def test_explicit_approval_and_no_replacement_authority(actions):
    p=draft(actions)
    with pytest.raises(ActionError):actions.apply({'key':p['key'],'approved':True})
    approve(actions,p)
    result=actions.apply({'key':p['key'],'amount':'999999','approved':True})
    state=actions.store.read();inv=state['invoices'][-1]
    assert inv['amount']=='10000.00' and result['verification']
    assert all(f['kind']=='matched' for f in reconcile(state,'P-12345'))

def test_repeat_execution_and_restart(actions,root):
    p=draft(actions);approve(actions,p);first=actions.apply(p['key'])
    resumed=Actions(Store(root),actions.thread)
    again=resumed.apply(p['key'])
    assert first['action_id']==again['action_id'] and resumed.store.read()['revision']==1
    resumed.approve([p['key']]);resumed.apply(p['key']);assert resumed.store.read()['revision']==1

def test_stale_approval_blocked(actions):
    p=draft(actions)
    def mutate(s):s['invoices'][0]['amount']='1'
    actions.store.transaction(mutate)
    with pytest.raises(ActionError):approve(actions,p)

def test_stale_execution_returns_review(actions):
    p=draft(actions);approve(actions,p)
    actions.store.transaction(lambda s:s['invoices'][0].update(amount='1'))
    assert actions.apply(p['key'])['status']=='stale'
    assert actions.store.read()['revision']==0

def test_revision_invalidates_old_approval(actions):
    p=draft(actions);approve(actions,p)
    revised=actions.draft('make_good','P-12345','5000','Partial recovery',supersedes=p['key'])
    assert revised['revision']==2 and actions.get(p['key'])['status']=='Superseded'
    with pytest.raises(ActionError):actions.apply(p['key'])
    with pytest.raises(ActionError):actions.apply(revised['key'])
    approve(actions,revised);actions.apply(revised['key'])
    assert reconcile(actions.store.read(),'P-12345')[1]['discrepancy']=='5000.00'

def test_credit_and_amendment_persist(actions,root):
    p=actions.draft('credit','P-OVER','2000','Correct overcharge',invoice_id='INV-OVER');approve(actions,p);actions.apply(p['key'])
    p=actions.draft('amendment','P-12345',change_set={'effective_date':'2024-11-01','amount':'12000','reason':'Requested upgrade'});approve(actions,p);actions.apply(p['key'])
    state=Store(root).read()
    assert reconcile(state,'P-OVER')[0]['kind']=='matched'
    assert reconcile(state,'P-12345')[2]['expected']=='12000.00'

def test_rollback_confirmation_and_before_images(actions):
    initial=records(actions.store.read());p=draft(actions);approve(actions,p);receipt=actions.apply(p['key'])
    with pytest.raises(ActionError):actions.rollback(receipt['action_id'])
    rb=actions.request_rollback(receipt['action_id']);assert records(actions.store.read())!=initial
    approve(actions,rb);actions.rollback(receipt['action_id'])
    assert records(actions.store.read())==initial
    assert actions.get(p['key'])['status']=='Rolled back'
    assert len(actions.store.read()['audit'])>=6

def test_later_change_blocks_rollback(actions):
    p=draft(actions);approve(actions,p);first=actions.apply(p['key'])
    p=actions.draft('credit','P-OVER','2000','Correct overcharge',invoice_id='INV-OVER');approve(actions,p);actions.apply(p['key'])
    with pytest.raises(ActionError):actions.request_rollback(first['action_id'])

def test_failed_canonical_write_never_claims_success(actions,monkeypatch):
    p=draft(actions);approve(actions,p);initial=actions.store.read()
    original=actions.store.atomic
    def fail(path,value):
        if path==actions.store.path:raise OSError('simulated disk failure')
        return original(path,value)
    monkeypatch.setattr(actions.store,'atomic',fail)
    with pytest.raises(OSError):actions.apply(p['key'])
    assert actions.store.read()==initial

def test_projection_failure_is_committed_and_repaired(actions,root,monkeypatch):
    p=draft(actions);approve(actions,p);original=actions.store.atomic
    def fail(path,value):
        if path.name=='invoices.json':raise OSError('projection failure')
        return original(path,value)
    monkeypatch.setattr(actions.store,'atomic',fail)
    r=actions.apply(p['key']);assert r['status']=='Applied' and r['projection_warning']
    import json
    restarted=Store(root)
    assert json.loads((restarted.sandbox/'invoices.json').read_text())==restarted.read()['invoices']

def test_multiple_approved_distinct_plans_sequential(actions):
    a=draft(actions);b=actions.draft('credit','P-OVER','2000','Correct overcharge',invoice_id='INV-OVER')
    actions.store.displayed(actions.thread,[a['key'],b['key']]);actions.approve([a['key'],b['key']])
    assert actions.apply(a['key'])['status']=='Applied'
    assert actions.apply(b['key'])['status']=='Applied'

def test_wrong_conversation_cannot_approve_or_apply(actions):
    p=draft(actions);other=Actions(actions.store,actions.store.new_conversation())
    with pytest.raises(ActionError):other.get(p['key'])
    with pytest.raises(ActionError):other.apply(p['key'])
