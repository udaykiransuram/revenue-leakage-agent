"""Contract creation uses the same reviewed boundary as monetary corrections."""
import copy
import pytest
from leakage.actions import ActionError, Actions
from leakage.agent import Agent
from leakage.tools import Toolbox


def contract():
    return {"id":"P-NEW","customer_id":"C-NEW","product_id":"service","currency":"USD","amount":"1000.00","cadence":"monthly","start_date":"2026-01-01","end_date":"2026-04-01","discount_percent":"10","proration":"none","notes":"Approved sample agreement"}


def approve(actions, p):
    actions.store.displayed(actions.thread,[p["key"]])
    actions.approve([p["key"]])
    return actions.apply({"key":p["key"],"amount":"1","approved":True})


def test_contract_creation_amendment_and_rollbacks(root):
    agent=Agent(root,mode="offline")
    thread=agent.store.new_conversation()
    actions=Actions(agent.store,thread)
    initial=copy.deepcopy(agent.store.read()["billing_plans"])
    p=actions.propose_contract(contract(),"Approved new customer agreement")
    assert agent.store.read()["billing_plans"] == initial
    assert p["preview"]["billing_after"][0]["expected"] == "900.00"
    with pytest.raises(ActionError,match="Explicit approval"):
        actions.apply(p["key"])
    agent.store.displayed(thread,[p["key"]])
    agent.confirm(thread,[p["key"]])
    state=agent.store.read()
    assert state["billing_plans"][-1]["id"] == "P-NEW"
    assert state["billing_plans"][-1]["amount"] == "1000.00"
    assert not any(i["plan_id"]=="P-NEW" for i in state["invoices"])
    assert len(agent.state(thread)["findings"])==3
    assert actions.apply(p["key"])["action_id"] == state["receipts"][p["key"]]["action_id"]
    amendment=actions.draft("amendment","P-NEW",change_set={"effective_date":"2026-02-01","amount":"1200.00","reason":"Approved expansion"})
    assert amendment["preview"]["billing_after"][1]["expected"] == "1080.00"
    amended=approve(actions,amendment)
    assert agent.store.read()["billing_plans"][-1]["amount"]=="1000.00"
    assert len(agent.store.read()["billing_plans"][-1]["amendments"])==1
    approve(actions,actions.request_rollback(amended["action_id"]))
    assert agent.store.read()["billing_plans"][-1]["amendments"]==[]
    undo=actions.request_rollback(state["receipts"][p["key"]]["action_id"])
    agent.store.displayed(thread,[undo["key"]])
    result=agent.confirm(thread,[undo["key"]])[0]
    assert result["contract_exists"] is False
    assert result["verification"]==[]
    assert agent.store.read()["billing_plans"]==initial
    assert "no longer in the active records" in agent.state(thread)["messages"][-1].content


@pytest.mark.parametrize("changes",[
    {"id":"P-12345"},{"amount":"100.001"},{"currency":"ZZZ"},
    {"end_date":"2025-01-01"},{"start_date":"2026-01-15"},
    {"amendments":[{"effective_date":"2026-02-01","amount":"1100","reason":"Unreviewed"}]},
])
def test_invalid_contract_cannot_create_records(actions, changes):
    before=copy.deepcopy(actions.store.read()["billing_plans"])
    with pytest.raises(ValueError): actions.propose_contract({**contract(),**changes},"New agreement")
    assert actions.store.read()["billing_plans"]==before
    assert not actions.store.read()["proposals"]


def test_duplicate_creation_drafts_become_stale_and_rejection_is_safe(actions):
    first=actions.propose_contract(contract(),"New agreement")
    second=actions.propose_contract(contract(),"Same proposed agreement")
    approve(actions,first)
    actions.store.displayed(actions.thread,[second["key"]])
    with pytest.raises(ActionError,match="Source records changed"):
        actions.approve([second["key"]])
    other=actions.propose_contract({**contract(),"id":"P-OTHER"},"Another agreement")
    actions.reject(other["key"])
    assert not any(p["id"]=="P-OTHER" for p in actions.store.read()["billing_plans"])


def test_contract_tool_stages_only_and_is_registered(actions):
    box=Toolbox(actions.store,actions.thread)
    tool=next(t for t in box.available() if t.name=="propose_contract")
    result=tool.invoke({"contract":contract(),"reason":"Approved initial terms"})
    assert result["status"]=="Awaiting approval"
    assert not any(p["id"]=="P-NEW" for p in actions.store.read()["billing_plans"])
