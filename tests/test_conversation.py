from leakage.agent import Agent
from leakage.actions import Actions
from langchain_core.messages import AIMessage

def test_end_to_end_restart_rollback(root):
    agent=Agent(root,mode='offline');thread=agent.store.new_conversation()
    result=agent.send(thread,'Check P-12345');assert result['selected_plan']=='P-12345'
    assert any(f['kind']=='missing_period' for f in result['findings'])
    result=agent.send(thread,'What currency is that plan in?');assert 'USD' in result['messages'][-1].content
    agent.send(thread,'Propose a make-good invoice for October')
    assert len(Actions(agent.store,thread).pending())==1
    agent.send(thread,'yes');assert agent.store.read()['revision']==1
    # Streamlit render / graph state reads cannot replay mutations.
    agent.state(thread);agent.state(thread);assert agent.store.read()['revision']==1
    restarted=Agent(root,mode='offline');assert restarted.state(thread)['selected_plan']=='P-12345'
    restarted.send(thread,'Check the other months');assert all(f['kind']=='matched' for f in restarted.state(thread)['findings'])
    restarted.send(thread,'roll back');restarted.send(thread,'yes')
    assert restarted.store.read()['revision']==2
    restarted.send(thread,'check again');assert any(f['kind']=='missing_period' for f in restarted.state(thread)['findings'])

def test_questions_while_pending_and_ambiguous_yes(root):
    a=Agent(root,mode='offline');t=a.store.new_conversation()
    a.send(t,'Check P-12345');a.send(t,'Propose make-good for October')
    a.send(t,'What currency is it?');a.send(t,'yes')
    assert a.store.read()['revision']==0 and 'ambiguous' in a.state(t)['messages'][-1].content
    p=Actions(a.store,t).pending()[0];a.send(t,'approve '+p['key']);assert a.store.read()['revision']==1

def test_scope_and_conversation_isolation(root):
    a=Agent(root,mode='offline');first=a.store.new_conversation();second=a.store.new_conversation()
    a.send(first,'Check P-12345');a.send(first,'What currency is P-FX in?')
    assert a.state(first)['selected_plan']=='P-FX' and not a.state(first)['findings']
    assert not a.state(second).get('selected_plan')
    a.send(second,'What currency is that plan in?');assert 'Which plan' in a.state(second)['messages'][-1].content

def test_eight_iteration_bound(root):
    class LoopModel:
        def bind_tools(self,*args,**kw):return self
        def invoke(self,messages):
            import uuid
            return AIMessage(content='',tool_calls=[{'name':'list_plans','args':{},'id':uuid.uuid4().hex}])
    a=Agent(root,mode='live',model=LoopModel());t=a.store.new_conversation();s=a.send(t,'list plans')
    assert s['iterations']==8 and 'limit' in s['messages'][-1].content
    assert len(s['activity'])==8

def test_untrusted_source_has_no_authority(root):
    a=Agent(root,mode='offline');t=a.store.new_conversation();a.send(t,'Check P-UNTRUSTED')
    assert a.store.read()['revision']==0 and not Actions(a.store,t).pending()

def test_bad_filters_and_unknown_ids(store):
    import pytest
    from leakage.tools import Toolbox
    b=Toolbox(store,store.new_conversation())
    with pytest.raises(ValueError):b.query_invoices({'invented':'x'})
    with pytest.raises(ValueError):b.load_plan('../secret')

def test_interrupted_write_checkpoint_reconciles_receipt_without_replay(root):
    a=Agent(root,mode='offline');t=a.store.new_conversation();actions=Actions(a.store,t)
    p=actions.draft('make_good','P-12345','10000','Missing October')
    a.store.displayed(t,[p['key']]);actions.approve([p['key']])
    # Crash window: model tool call checkpointed, canonical write committed,
    # but the tool response never made it into the graph checkpoint.
    call=AIMessage(content='',tool_calls=[{'name':'apply','args':{'action_draft':{'key':p['key']}},'id':'interrupted-write'}])
    a.graph.update_state(a.config(t),{'messages':[call]},as_node='model')
    receipt=actions.apply(p['key'])
    resumed=Agent(root,mode='offline')
    import json
    recovered=json.loads(resumed.state(t)['messages'][-1].content)
    assert recovered['action_id']==receipt['action_id']
    assert resumed.store.read()['revision']==1
    resumed.send(t,'Check P-12345')
    assert all(f['kind']=='matched' for f in resumed.state(t)['findings'])

def test_multiple_pending_yes_and_natural_rejection(root):
    a=Agent(root,mode='offline');t=a.store.new_conversation()
    a.send(t,'Check P-12345');a.send(t,'Propose make-good for October')
    a.send(t,'Check P-OVER');a.send(t,'Propose a credit memo');a.send(t,'yes')
    assert a.store.read()['revision']==0 and len(Actions(a.store,t).pending())==2
    keys=[p['key'] for p in Actions(a.store,t).pending()]
    a.send(t,'reject '+keys[0]);assert len(Actions(a.store,t).pending())==1
    a.send(t,'reject it');assert not Actions(a.store,t).pending()

def test_proposal_model_context_keeps_preview_in_store_not_prompt(root):
    from langchain_core.messages import ToolMessage
    import json
    a=Agent(root,mode='offline');t=a.store.new_conversation()
    a.send(t,'Check P-12345');a.send(t,'Propose make-good for October')
    tool=next(m for m in reversed(a.state(t)['messages']) if isinstance(m,ToolMessage) and m.name=='propose_make_good_invoice')
    result=json.loads(tool.content)
    assert result['amount']=='10000.00' and 'preview' not in result and 'before' not in result
    proposal=Actions(a.store,t).get(result['key'])
    assert proposal['preview']['after']['new_invoice']=='10000.00' and proposal['evidence']

def test_mixed_sequential_outcomes_keep_verified_plan_scope(root):
    a=Agent(root,mode='offline');t=a.store.new_conversation();actions=Actions(a.store,t)
    first=actions.draft('make_good','P-12345','10000','Missing October')
    second=actions.propose_record_edit('invoices','INV-PARTIAL',{'amount':'8500.00'},'Manual correction')
    a.store.displayed(t,[first['key'],second['key']])
    results=a.confirm(t,[first['key'],second['key']])
    assert [r['status'] for r in results]==['Applied','stale']
    s=a.state(t);assert s['selected_plan']=='P-12345'
    assert {f['plan_id'] for f in s['findings']}=={'P-12345'}
