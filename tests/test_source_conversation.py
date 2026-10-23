"""Actual-source integration through the conversational/tool boundary, isolated sandbox."""
import copy
from pathlib import Path
import shutil
from langchain_core.messages import AIMessage
from leakage.agent import Agent
from leakage.actions import Actions

class Model:
    def __init__(self,responses):self.responses=list(responses)
    def bind_tools(self,*args,**kwargs):return self
    def invoke(self,messages):return self.responses.pop(0)

def call(name,args):return AIMessage(content='',tool_calls=[{'name':name,'args':args,'id':'call-'+name}])

def case(tmp_path,monkeypatch):
    monkeypatch.setenv('DATASET_MODE','challenge');monkeypatch.setenv('MODEL_PROVIDER','groq')
    shutil.copytree(Path(__file__).resolve().parents[1]/'data',tmp_path/'data')
    return tmp_path

def context(state):
    from leakage.domain import add_months
    from datetime import date
    return {'period_amount':'8000.00','term_end':'2026-01-01','reconcile_through':'2025-11-01','proration':'none','fx_date_policy':'invoice_issue_date','entitlement_scope':'all_plan_entitlements','invoice_periods':{i['invoice_id']:[i['issue_date'][:7]+'-01',str(add_months(date.fromisoformat(i['issue_date'][:7]+'-01'),1))] for i in state['invoices'] if i['plan_id']=='C-1001'}}

def test_clarification_context_approval_correction_followup_and_rollback(tmp_path,monkeypatch):
    root=case(tmp_path,monkeypatch)
    model=Model([call('reconcile_plan',{'plan_id':'C-1001'}),AIMessage(content='Can we treat each issue month as the service month at 8000 USD, including both entitlements, checking through October?')])
    a=Agent(root,mode='live',model=model);thread=a.store.new_conversation();original=copy.deepcopy(a.store.read()['invoices'])
    a.send(thread,'Check C-1001')
    assert not Actions(a.store,thread).pending()
    ctx=context(a.store.read())
    model.responses.extend([call('propose_billing_context',{'plan_id':'C-1001','context':ctx,'reason':'User confirmed monthly service mapping, price, term, entitlements and audit horizon'}),AIMessage(content='Review the interpreted billing context.')])
    a.send(thread,'Yes')  # Clarification, not a financial approval.
    pending=Actions(a.store,thread).pending()
    assert len(pending)==1 and pending[0]['kind']=='billing_context'
    assert a.store.read()['invoices']==original
    a.send(thread,'yes')
    assert a.store.read()['billing_contexts']['C-1001']['status']=='approved'
    assert a.store.read()['invoices']==original
    assert a.state(thread)['selected_plan']=='C-1001'
    assert any(f['kind']=='missing_period' and f['discrepancy']=='8000.00' for f in a.state(thread)['findings'])
    assert a.state(thread)['trace'][-1]['name']=='apply'
    model.responses.extend([call('load_plan',{'plan_id':'C-1001'}),AIMessage(content='The same plan is in USD.')])
    a.send(thread,'What currency is that plan in?')
    assert a.state(thread)['selected_plan']=='C-1001'
    model.responses.extend([call('propose_make_good_invoice',{'plan_id':'C-1001','amount':'8000.00','reason':'Missing confirmed September service billing'}),AIMessage(content='Review the September correction.')])
    a.send(thread,'Create a correction for the missing September month')
    p=Actions(a.store,thread).pending()[0]
    assert p['kind']=='make_good' and p['period']==['2025-09-01','2025-10-01']
    assert a.store.read()['invoices']==original
    a.send(thread,'yes, apply it')
    assert len(a.store.read()['invoices'])==len(original)+1
    assert all(f['kind']=='matched' for f in a.state(thread)['findings'])
    model.responses.extend([call('reconcile_plan',{'plan_id':'C-1001'}),AIMessage(content='The other months match the confirmed scope.')])
    a.send(thread,'What about the other months?')
    assert all(f['kind']=='matched' for f in a.state(thread)['findings'])
    actions=Actions(a.store,thread);receipt=actions.latest(a.store.read())
    model.responses.extend([call('request_rollback',{'action_id':receipt['action_id']}),AIMessage(content='Review rollback.')])
    a.send(thread,'Undo that invoice')
    a.send(thread,'yes')
    assert a.store.read()['invoices']==original
    assert any(f['kind']=='missing_period' for f in a.state(thread)['findings'])
    restarted=Agent(root,mode='live',model=Model([]))
    assert restarted.state(thread)['selected_plan']=='C-1001'
    assert restarted.state(thread)['trace']
    assert len(restarted.store.read()['receipts'])==3


def test_source_credit_tool_uses_invoice_id_and_cannot_duplicate_existing_credit(tmp_path,monkeypatch):
    from leakage.tools import Toolbox
    import pytest
    root=case(tmp_path,monkeypatch);a=Agent(root,mode='live',model=Model([]));thread=a.store.new_conversation()
    toolbox=Toolbox(a.store,thread)
    before = copy.deepcopy(a.store.read()['credit_memos'])
    draft = toolbox.propose_credit_memo('I-9123','2000.00','Already credited')
    assert draft['status'] == 'Draft: needs details'
    assert not Actions(a.store, thread).pending()
    with pytest.raises(ValueError, match='Explicit approval'):
        Actions(a.store, thread).apply(draft['key'])
    assert a.store.read()['credit_memos'] == before
    assert not a.store.read()['receipts']


def test_propose_before_confirmation_then_reject_or_approve_once(tmp_path, monkeypatch):
    root = case(tmp_path, monkeypatch)
    model = Model([])
    agent = Agent(root, mode='live', model=model)
    thread = agent.store.new_conversation()
    original = copy.deepcopy(agent.store.read()['invoices'])
    args = {'plan_id': 'C-1001', 'amount': '8000', 'reason': 'Proposed recovery for September; review service-month assumptions',
            'billing_context': context(agent.store.read())}
    for decision in ('reject', 'yes'):
        model.responses.extend([call('propose_make_good_invoice', args), AIMessage(content='Review the proposed $8,000 invoice and billing assumptions. You may approve or reject it.')])
        agent.send(thread, 'Propose the September fix now so I can review it')
        pending = Actions(agent.store, thread).pending()
        assert len(pending) == 1 and pending[0]['kind'] == 'make_good'
        assert pending[0]['preview']['billing_assumptions']
        assert agent.store.read()['invoices'] == original
        assert not agent.store.read().get('billing_contexts')
        agent.send(thread, decision)
        if decision == 'reject':
            assert agent.store.read()['invoices'] == original
            assert not agent.store.read().get('billing_contexts')
    assert len(agent.store.read()['invoices']) == len(original) + 1
    assert len(agent.store.read()['receipts']) == 1
    assert all(f['kind'] == 'matched' for f in agent.state(thread)['findings'])


def test_context_tool_exposes_exact_fields_and_rejects_invented_aliases(tmp_path,monkeypatch):
    from leakage.tools import Toolbox
    from pydantic import ValidationError
    import pytest
    root=case(tmp_path,monkeypatch);a=Agent(root,mode='live',model=Model([]));t=a.store.new_conversation()
    tool=next(x for x in Toolbox(a.store,t).available() if x.name=='propose_billing_context')
    schema=tool.args_schema.model_json_schema()
    fields=schema['$defs']['BillingContextInput']['properties']
    assert {'period_amount','term_end','invoice_periods','reconcile_through'} <= set(fields)
    bad=context(a.store.read());bad['per_period_price']=bad.pop('period_amount')
    with pytest.raises(ValidationError):tool.invoke({'plan_id':'C-1001','context':bad,'reason':'Confirmed terms'})
    assert not a.store.read()['proposals']
    result=tool.invoke({'plan_id':'C-1001','context':context(a.store.read()),'reason':'Confirmed terms'})
    assert result['kind']=='billing_context'
    assert len(a.store.read()['invoices'])==13
