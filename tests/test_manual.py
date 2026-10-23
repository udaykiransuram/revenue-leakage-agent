import pytest
from leakage.actions import Actions, ActionError, records
from leakage.store import Store

def stage(actions, **kwargs):
    return actions.propose_record_edit("invoices", "INV-OVER", {"amount": "10000.00"}, "Correct source overcharge", **kwargs)

def approve(actions, p):
    actions.store.displayed(actions.thread, [p["key"]])
    actions.approve([p["key"]])

def test_manual_edit_requires_review_and_exact_stored_revision(actions):
    before = records(actions.store.read())
    p = stage(actions)
    assert records(actions.store.read()) == before
    with pytest.raises(ActionError): actions.apply({"key": p["key"], "approved": True})
    with pytest.raises(ActionError): actions.approve([p["key"]])
    approve(actions, p)
    r = actions.apply({"key": p["key"], "change_set": {"amount": "999999.00"}})
    inv = next(i for i in actions.store.read()["invoices"] if i["id"] == "INV-OVER")
    assert inv["amount"] == "10000.00"
    assert r["status"] == "Applied" and r["verification"]
    assert all(f["kind"] == "matched" for f in r["verification"])

def test_manual_restart_idempotency_and_rollback(actions, root):
    before = records(actions.store.read())
    p = stage(actions); approve(actions, p); receipt = actions.apply(p["key"])
    resumed = Actions(Store(root), actions.thread)
    assert resumed.apply(p["key"])["action_id"] == receipt["action_id"]
    assert resumed.store.read()["revision"] == 1
    rb = resumed.request_rollback(receipt["action_id"])
    approve(resumed, rb); resumed.rollback(receipt["action_id"])
    assert records(resumed.store.read()) == before
    assert resumed.get(p["key"])["status"] == "Rolled back"

def test_manual_reject_and_revision(actions):
    p = stage(actions); approve(actions, p)
    q = actions.propose_record_edit("invoices", "INV-OVER", {"amount": "11000.00"}, "Partial adjustment", supersedes=p["key"])
    assert q["id"] == p["id"] and q["revision"] == 2
    with pytest.raises(ActionError): actions.apply(p["key"])
    with pytest.raises(ActionError): actions.apply(q["key"])
    actions.reject(q["key"])
    assert actions.store.read()["revision"] == 0

@pytest.mark.parametrize("changes", [{"id": "OTHER"}, {"plan_id": "P-12345"}, {"currency": "EUR"}, {"amount": "1.001"}, {"amount": True}, {"amount": "NaN"}, {"amount": "-1"}, {"period_end": "2020-01-01"}, {"status": "paid"}, {"unknown": "x"}, {}, {"duplicate_of": "INV-OVER"}])
def test_manual_invalid_edits_do_not_write(actions, changes):
    before = actions.store.read()
    with pytest.raises(ActionError): actions.propose_record_edit("invoices", "INV-OVER", changes, "Correction")
    assert actions.store.read() == before

def test_manual_duplicate_ids_blocked(actions):
    actions.store.transaction(lambda s: s["invoices"].append(dict(next(i for i in s["invoices"] if i["id"] == "INV-OVER"))))
    with pytest.raises(ActionError): stage(actions)

def test_manual_global_stale_guard(actions):
    p = stage(actions); approve(actions, p)
    actions.store.transaction(lambda s: s["billing_plans"][0].update(notes="changed concurrently"))
    assert actions.apply(p["key"])["status"] == "stale"
    assert actions.store.read()["revision"] == 0

def test_manual_credit_balance_and_immutable_originals(actions, root):
    original = (root / "data" / "credit_memos.json").read_bytes()
    p = actions.propose_record_edit("credit_memos", "CM-EXISTING", {"amount": "1000.00", "reason": "Review"}, "Adjust correction")
    approve(actions, p); actions.apply(p["key"])
    assert actions.store.read()["credit_memos"][0]["amount"] == "1000.00"
    assert (root / "data" / "credit_memos.json").read_bytes() == original
    with pytest.raises(ActionError): actions.propose_record_edit("credit_memos", "CM-EXISTING", {"amount": "999999.00"}, "Too large")

def test_manual_plan_terms_must_use_amendment(actions):
    with pytest.raises(ActionError): actions.propose_record_edit("billing_plans", "P-12345", {"amount": "10.00"}, "No effective date")
    p = actions.propose_record_edit("billing_plans", "P-12345", {"notes": "Verified contract"}, "Record annotation")
    approve(actions, p); actions.apply(p["key"])
    assert next(i for i in actions.store.read()["billing_plans"] if i["id"] == "P-12345")["notes"] == "Verified contract"

def test_manual_unsupported_ledger_and_wrong_thread(actions):
    with pytest.raises(ActionError): actions.propose_record_edit("exchange_rates", "USD", {"rate": "2"}, "Update")
    p = stage(actions)
    other = Actions(actions.store, actions.store.new_conversation())
    with pytest.raises(ActionError): other.apply(p["key"])

def test_manual_write_failure_leaves_no_mutation(actions, monkeypatch):
    p = stage(actions); approve(actions, p); before = actions.store.read()
    def fail(*args): raise OSError("disk failure")
    monkeypatch.setattr(actions.store, "atomic", fail)
    with pytest.raises(OSError): actions.apply(p["key"])
    assert actions.store.read() == before


def test_manual_notes_cannot_bypass_invalid_source_currency(actions):
    actions.store.transaction(lambda s: s["credit_memos"][0].update(currency="XYZ"))
    with pytest.raises(ActionError): actions.propose_record_edit("credit_memos", "CM-EXISTING", {"reason": "review"}, "Correction")

def test_manual_noop_and_blank_reason_blocked(actions):
    with pytest.raises(ActionError): actions.propose_record_edit("invoices", "INV-OVER", {"amount": "12000.00"}, "Unchanged")
    with pytest.raises(ActionError): actions.propose_record_edit("invoices", "INV-OVER", {"amount": "10000.00"}, " ")
