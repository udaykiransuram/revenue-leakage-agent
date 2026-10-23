"""Actual supplied source records, explicit assumptions, no model/network calls."""
import copy
import pytest
from leakage.actions import Actions, ActionError, records
from leakage.source_actions import SourceActions
from leakage.source_billing import reconcile_source
from leakage.store import Store


def test_source_factory_initializes_adapter_after_actions_module_reload(root):
    # Isolate reloads so other tests retain their normal class identities.
    import subprocess
    import sys
    from pathlib import Path
    script = '''
import importlib
import os
import sys
import leakage.actions as module
import leakage.source_actions
from leakage.store import Store
os.environ['DATASET_MODE'] = 'challenge'
os.environ['MODEL_PROVIDER'] = 'groq'
store = Store(sys.argv[1])
thread = store.new_conversation()
importlib.reload(module)
actions = module.Actions(store, thread)
assert actions.store is store
assert actions.thread == thread
assert actions.pending() == []
assert store.read()['proposals'] == {}
from leakage.agent import Agent
from langchain_core.messages import AIMessage
class Model:
    def bind_tools(self, *args, **kwargs): return self
    def invoke(self, messages):
        if messages[-1].type == 'human':
            return AIMessage(content='', tool_calls=[{'name': 'reconcile_plan', 'args': {'plan_id': 'C-1001'}, 'id': 'reload-check'}])
        return AIMessage(content='Investigation completed from source evidence.')
agent = Agent(sys.argv[1], mode='live', model=Model())
result = agent.send(thread, 'Check C-1001')
assert result['selected_plan'] == 'C-1001'
assert any(f['kind'] == 'candidate_missing_issue_month' for f in result['findings'])
assert agent.store.read()['proposals'] == {}
assert agent.store.read()['receipts'] == {}
'''
    result = subprocess.run([sys.executable, '-c', script, str(root)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def approve(actions, proposal):
    actions.store.displayed(actions.thread, [proposal["key"]])
    actions.approve([proposal["key"]])


def acme_context(actions):
    invoices = [i for i in actions.store.read()["invoices"] if i.get("plan_id") == "C-1001"]
    from datetime import date
    from leakage.domain import add_months
    return {"period_amount": "8000.00", "term_end": "2025-11-01", "proration": "none",
            "fx_date_policy": "invoice_issue_date", "entitlement_scope": "all_plan_entitlements",
            "invoice_periods": {i["invoice_id"]: [i["issue_date"][:7]+"-01", str(add_months(date.fromisoformat(i["issue_date"][:7]+"-01"), 1))] for i in invoices}}


def confirmed_acme(actions):
    p = actions.propose_billing_context("C-1001", acme_context(actions), "User confirms per-month charge, January through October term, and every service mapping")
    approve(actions, p)
    return actions.apply(p["key"])


def test_combined_proposal_can_be_rejected_without_saving_assumptions(actions):
    before = copy.deepcopy(actions.store.read())
    p = actions.draft('make_good', 'C-1001', '8000', 'Proposed missing September invoice',
                      billing_context=acme_context(actions))
    assert p['period'] == ['2025-09-01', '2025-10-01']
    assert p['evidence'][0]['validation'] == 'conditional'
    assert p['preview']['billing_assumptions']['period_amount'] == '8000.00'
    assert records(actions.store.read()) == records(before)
    assert actions.store.read().get('billing_contexts', {}) == before.get('billing_contexts', {})
    with pytest.raises(ActionError, match='Explicit approval'):
        actions.apply(p['key'])
    actions.reject(p['key'])
    assert records(actions.store.read()) == records(before)
    assert actions.store.read().get('billing_contexts', {}) == before.get('billing_contexts', {})
    assert not actions.store.read()['receipts']


def test_combined_approval_saves_assumptions_and_applies_exact_invoice_once(actions):
    before = copy.deepcopy(actions.store.read())
    p = actions.draft('make_good', 'C-1001', '8000', 'Proposed missing September invoice',
                      billing_context=acme_context(actions))
    approve(actions, p)
    receipt = actions.apply(p['key'])
    state = actions.store.read()
    assert len(state['invoices']) == len(before['invoices']) + 1
    assert state['billing_contexts']['C-1001']['status'] == 'approved'
    assert all(f['kind'] == 'matched' for f in receipt['verification'])
    assert actions.apply(p['key']) == receipt
    assert len(actions.store.read()['invoices']) == len(state['invoices'])


def test_proposed_context_does_not_allow_duplicate_globex_credit(actions):
    context = {'period_amount': '25000', 'term_end': '2025-10-01',
               'invoice_periods': {'I-9123': ['2025-07-01', '2025-10-01']},
               'proration': 'none', 'fx_date_policy': 'invoice_issue_date',
               'entitlement_scope': 'all_plan_entitlements'}
    with pytest.raises(ActionError):
        actions.draft('credit', 'C-1007-A1', '2000', 'Already credited FX difference',
                      invoice_id='I-9123', billing_context=context)
    assert not actions.store.read()['proposals']
    assert not actions.store.read().get('billing_contexts')


def test_context_draft_is_not_authority_and_originals_remain_immutable(actions, root):
    assert isinstance(actions, SourceActions)
    original = (root / "data" / "billing_plans.json").read_bytes()
    before = copy.deepcopy(records(actions.store.read()))
    p = actions.propose_billing_context("C-1001", acme_context(actions), "Confirmed assumptions")
    assert records(actions.store.read()) == before and not actions.store.read().get("billing_contexts")
    with pytest.raises(ActionError): actions.apply({"key": p["key"], "approved": True})
    with pytest.raises(ActionError): actions.draft("make_good", "C-1001", "8000", "Missing September")
    approve(actions, p)
    result = actions.apply(p["key"])
    assert result["source_context_status"] == "confirmed"
    assert any(f["kind"] == "missing_period" and f["period"] == ["2025-09-01", "2025-10-01"] for f in result["verification"])
    assert (root / "data" / "billing_plans.json").read_bytes() == original


def test_actual_makegood_verifies_restarts_and_rolls_back_context_mapping(actions, root):
    confirmed_acme(actions)
    before = actions.store.read()
    p = actions.draft("make_good", "C-1001", "8000.00", "Recover confirmed missing September", period=["2025-09-01", "2025-10-01"])
    approve(actions, p)
    result = actions.apply({"key": p["key"], "amount": "999999", "approved": True})
    state = actions.store.read()
    new = state["invoices"][-1]
    assert new["amount_invoiced"] == "8000.00" and new["currency"] == "USD"
    assert new["due_date"] is None and new["status"] == "unpaid"
    assert all(f["kind"] == "matched" for f in result["verification"])
    assert state["billing_contexts"]["C-1001"]["invoice_periods"][new["invoice_id"]] == p["period"]
    restarted = Actions(Store(root), actions.thread)
    assert restarted.apply(p["key"])["action_id"] == result["action_id"]
    assert restarted.store.read()["revision"] == 2
    rb = restarted.request_rollback(result["action_id"])
    approve(restarted, rb); restarted.rollback(result["action_id"])
    assert records(restarted.store.read()) == records(before)
    assert restarted.store.read()["billing_contexts"] == before["billing_contexts"]


def test_existing_cross_currency_credit_prevents_extra_credit(actions):
    context = {"period_amount": "25000.00", "term_end": "2025-10-01", "proration": "none",
               "fx_date_policy": "invoice_issue_date", "entitlement_scope": "all_plan_entitlements",
               "invoice_periods": {"I-9123": ["2025-07-01", "2025-10-01"]}}
    p = actions.propose_billing_context("C-1007-A1", context, "Confirm one quarterly term and existing credit allocation")
    approve(actions, p); receipt = actions.apply(p["key"])
    assert receipt["verification"][0]["actual"] == "25000.00"
    assert receipt["verification"][0]["kind"] == "matched"
    with pytest.raises(ActionError): actions.draft("credit", "C-1007-A1", "2000", "Already resolved", invoice_id="I-9123")


def test_credit_can_apply_only_supported_remaining_overcharge(actions):
    context = {"period_amount": "24000.00", "term_end": "2025-10-01", "proration": "none",
               "fx_date_policy": "invoice_issue_date", "entitlement_scope": "all_plan_entitlements",
               "invoice_periods": {"I-9123": ["2025-07-01", "2025-10-01"]}}
    p = actions.propose_billing_context("C-1007-A1", context, "Explicitly confirmed negotiated quarterly target")
    approve(actions, p); actions.apply(p["key"])
    with pytest.raises(ActionError): actions.draft("credit", "C-1007-A1", "1000.01", "Too large", invoice_id="I-9123")
    p = actions.draft("credit", "C-1007-A1", "1000", "Remaining supported overcharge", invoice_id="I-9123")
    approve(actions, p); r = actions.apply(p["key"])
    assert r["verification"][0]["kind"] == "matched"
    credit = actions.store.read()["credit_memos"][-1]
    assert credit["currency"] == "USD" and credit["amount"] == "1000.00" and credit["invoice_id"] == "I-9123"


def test_source_amendment_preserves_parent_invalidates_context_and_rolls_back(actions):
    confirmed_acme(actions)
    before = actions.store.read()
    p = actions.draft("amendment", "C-1001", change_set={"total_value": "100000", "effective_date": "2025-10-01", "entitlements": ["Analytics", "Support", "Premium"], "reason": "Explicit upgrade"})
    assert p["preview"]["after"]["amends"] == "C-1001"
    approve(actions, p); r = actions.apply(p["key"])
    state = actions.store.read()
    assert state["billing_plans"][:-1] == before["billing_plans"]
    assert state["billing_plans"][-1]["total_value"] == "100000.00"
    assert "C-1001" not in state["billing_contexts"]
    assert r["source_context_status"] == "needs confirmation"
    rb = actions.request_rollback(r["action_id"]); approve(actions, rb); actions.rollback(r["action_id"])
    assert records(actions.store.read()) == records(before)
    assert actions.store.read()["billing_contexts"] == before["billing_contexts"]


def test_stale_edits_and_revision_approval_are_guarded(actions):
    confirmed_acme(actions)
    p = actions.draft("make_good", "C-1001", "8000", "Recover missing September")
    approve(actions, p)
    revised = actions.draft("make_good", "C-1001", "4000", "Partial recovery", supersedes=p["key"])
    with pytest.raises(ActionError): actions.apply(p["key"])
    with pytest.raises(ActionError): actions.apply(revised["key"])
    approve(actions, revised)
    actions.store.transaction(lambda s: s["invoices"][0].update(description="concurrent change"))
    assert actions.apply(revised["key"])["status"] == "stale"


def test_later_action_blocks_rollback_and_rejection_leaves_ledgers(actions):
    first = confirmed_acme(actions)
    before = copy.deepcopy(records(actions.store.read()))
    rejected = actions.draft("make_good", "C-1001", "4000", "Partial recovery")
    actions.reject(rejected["key"])
    assert records(actions.store.read()) == before
    p = actions.draft("make_good", "C-1001", "8000", "Recover missing September")
    approve(actions, p); actions.apply(p["key"])
    with pytest.raises(ActionError): actions.request_rollback(first["action_id"])


def test_source_action_failed_write_never_claims_success(actions, monkeypatch):
    confirmed_acme(actions)
    p = actions.draft("make_good", "C-1001", "8000", "Recover September")
    approve(actions, p); before = actions.store.read()
    def fail(*args): raise OSError("simulated write failure")
    monkeypatch.setattr(actions.store, "atomic", fail)
    with pytest.raises(OSError): actions.apply(p["key"])
    assert actions.store.read() == before


def test_unresolved_proposal_is_visible_but_cannot_be_approved_or_applied(actions):
    from leakage.tools import Toolbox
    before = records(actions.store.read())
    box = Toolbox(actions.store, actions.thread)
    draft = box.propose_make_good_invoice('C-1001', '8000', 'Review the missing September invoice')
    assert draft['status'] == 'Draft: needs details'
    assert draft['validation_issues']
    assert records(actions.store.read()) == before
    assert not actions.pending()
    actions.store.displayed(actions.thread, [draft['key']])
    with pytest.raises(ActionError, match='not awaiting approval'):
        actions.approve([draft['key']])
    with pytest.raises(ActionError, match='Explicit approval'):
        actions.apply(draft['key'])
    assert not actions.store.read()['receipts']


def test_unresolved_drafts_preserve_proration_issue_and_supersede_older_ideas(actions):
    from leakage.tools import Toolbox
    box = Toolbox(actions.store, actions.thread)
    first = box.propose_make_good_invoice('C-1001', '8000', 'Review September')
    second = box.propose_make_good_invoice('C-1001', '8000', 'Updated September idea')
    assert actions.get(first['key'])['status'] == 'Superseded'
    assert actions.get(second['key'])['status'] == 'Draft: needs details'
    with pytest.raises(ValueError):
        box.propose_make_good_invoice('C-1001', '-1', 'Invalid negative amount')


def test_monthly_proposal_derives_end_and_keeps_approval_boundary(actions):
    before = snapshot = copy.deepcopy(actions.store.read())
    context = acme_context(actions)
    context['term_end'] = '2025-12-31'
    draft = actions.draft('make_good', 'C-1001', '8000', 'Recover September for review',
                          period=['2025-09-01','2025-10-01'], billing_context=context)
    assert draft['status'] == 'Awaiting approval'
    assert draft['billing_context']['term_end'] == '2026-01-01'
    assert draft['preview']['derived_assumptions']
    assert draft['period'] == ['2025-09-01','2025-10-01']
    assert actions.store.read()['invoices'] == before['invoices']
    assert not actions.store.read().get('billing_contexts')
    with pytest.raises(ActionError, match='Explicit approval'):
        actions.apply(draft['key'])
