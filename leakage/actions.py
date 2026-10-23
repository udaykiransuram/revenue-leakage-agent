"""Trusted authority boundary. Models can draft; only UI events approve."""
from __future__ import annotations
import copy
import uuid
from datetime import date
from decimal import Decimal
from .domain import ChangeSet, Plan, digest, identifier, money, schedule
from .reconcile import reconcile
from .manual import validate_edit
from .store import LEDGERS, now

class ActionError(ValueError):
    pass

def records(state):
    return {k: state[k] for k in LEDGERS}

def fingerprint(state, plan_id=None):
    if any("plan_id" in p and "id" not in p for p in state["billing_plans"]):
        return digest({**records(state), "billing_contexts": state.get("billing_contexts", {})})
    if plan_id is None:
        return digest(records(state))
    invoices = [r for r in state["invoices"] if r.get("plan_id") == plan_id]
    ids = {r["id"] for r in invoices}
    return digest({"billing_plans": [p for p in state["billing_plans"] if p["id"] == plan_id], "invoices": invoices, "credit_memos": [c for c in state["credit_memos"] if c.get("invoice_id") in ids], "exchange_rates": state["exchange_rates"]})

class Actions:
    def __new__(cls, store, thread):
        if cls is Actions and any("plan_id" in p and "id" not in p for p in store.read()["billing_plans"]):
            from .source_actions import SourceActions
            # A reload can leave SourceActions inheriting the previous Actions
            # class. Python then skips this factory's __init__ on the returned
            # object, so construct the adapter with its dependencies explicitly.
            return SourceActions(store, thread)
        return object.__new__(cls)

    def __init__(self, store, thread):
        self.store, self.thread = store, thread

    def pending(self):
        return [p for p in self.store.read()["proposals"].values() if p["thread"] == self.thread and p["status"] in ("Awaiting approval", "Approved")]

    def get(self, key, state=None):
        state = state or self.store.read()
        p = state["proposals"].get(key)
        if not p or p["thread"] != self.thread:
            raise ActionError("Proposal not found in this conversation")
        return p

    def draft(self, kind, plan_id, amount=None, reason="", invoice_id=None, period=None, change_set=None, supersedes=None):
        identifier(plan_id)
        if kind not in ("make_good", "credit", "amendment"):
            raise ActionError("Unknown action type")
        def build(state):
            selected_period = period
            plan = next((p for p in state["billing_plans"] if p["id"] == plan_id), None)
            if plan is None:
                raise ActionError("Unknown plan")
            Plan.model_validate(plan)
            evidence = []
            before, after = None, None
            if kind == "amendment":
                changes = ChangeSet.model_validate(change_set).model_dump(mode="json", exclude_none=True)
                effective = changes["effective_date"]
                if not plan["start_date"] <= effective < plan["end_date"]:
                    raise ActionError("Amendment effective date must be inside the contract")
                if any(a["effective_date"] == effective for a in plan.get("amendments", [])):
                    raise ActionError("An amendment already exists at this date; supply a new effective date")
                before = copy.deepcopy(plan)
                after = copy.deepcopy(plan)
                after.setdefault("amendments", []).append(changes)
                before_schedule, after_schedule = schedule(before), schedule(after)
                preview = {"before": before, "after": after, "billing_before": before_schedule, "billing_after": after_schedule, "impact": "Commercial terms change prospectively; existing invoices remain unchanged"}
                currency = plan["currency"]
                amount_value = None
                reason_value = changes["reason"]
            else:
                if not reason.strip() or len(reason) > 2000:
                    raise ActionError("A clear reason of 1–2000 characters is required")
                amount_value = money(amount, plan["currency"])
                if amount_value <= 0 or Decimal(str(amount)) != amount_value:
                    raise ActionError("Amount must be positive and already rounded to currency precision")
                eligible = [f for f in reconcile(state, plan_id) if f["validation"] == "validated" and ("make_good" if kind == "make_good" else "credit") in f["supported_corrections"]]
                if selected_period:
                    eligible = [f for f in eligible if f["period"] == selected_period]
                if invoice_id:
                    identifier(invoice_id)
                    eligible = [f for f in eligible if f"invoices:{invoice_id}" in f["source_refs"]]
                eligible = [f for f in eligible if abs(Decimal(f["discrepancy"])) >= amount_value]
                if len(eligible) != 1:
                    raise ActionError("Select one validated service period; amount must not exceed its supported discrepancy")
                f = eligible[0]
                selected_period = f["period"]
                evidence = [f]
                currency = plan["currency"]
                reason_value = reason
                if kind == "credit":
                    invs = [i for i in state["invoices"] if i["id"] == invoice_id]
                    if len(invs) != 1:
                        raise ActionError("Credit requires one unambiguous invoice ID")
                    inv = invs[0]
                    if inv["currency"] != currency:
                        raise ActionError("Cross-currency credit requires an explicit invoice-currency allocation adapter")
                    available = Decimal(inv["amount"]) - sum((Decimal(c["amount"]) for c in state["credit_memos"] if c["invoice_id"] == invoice_id), Decimal(0))
                    if amount_value > available:
                        raise ActionError("Credit exceeds the remaining invoice balance")
                    before = copy.deepcopy(inv)
                    after = {**before, "net_after_credit": str(available-amount_value)}
                else:
                    before = {"period_net_billed": f["actual"]}
                    after = {"period_net_billed": str(Decimal(f["actual"])+amount_value), "new_invoice": str(amount_value)}
                preview = {"before": before, "after": after, "period": selected_period, "currency": currency}
                changes = None
            proposal_id = "PROP-" + uuid.uuid4().hex[:12]
            revision = 1
            if supersedes:
                old = self.get(supersedes, state)
                if old["status"] not in ("Awaiting approval", "Approved"):
                    raise ActionError("Only pending proposals can be revised")
                old["status"] = "Superseded"
                proposal_id, revision = old["id"], old["revision"]+1
            key = f"{proposal_id}:{revision}"
            proposal = {"id": proposal_id, "revision": revision, "key": key, "thread": self.thread, "kind": kind, "plan_id": plan_id, "invoice_id": invoice_id, "amount": str(amount_value) if amount_value is not None else None, "currency": currency, "period": selected_period, "change_set": changes, "reason": reason_value, "evidence": evidence, "preview": preview, "fingerprint": fingerprint(state, plan_id), "status": "Awaiting approval", "created_at": now()}
            state["proposals"][key] = proposal
            self.store.event(state, "proposal_created", proposal=key, thread=self.thread)
            return proposal
        return self.store.transaction(build)

    def propose_record_edit(self, ledger, record_id, change_set, reason, supersedes=None):
        """Stage a correction without granting execution authority."""
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ActionError("A clear reason of 1–2000 characters is required")
        def create(state):
            try:
                before, after, plan_id = validate_edit(state, ledger, record_id, change_set)
            except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
                raise ActionError(str(exc)) from exc
            proposal_id, revision = "PROP-" + uuid.uuid4().hex[:12], 1
            if supersedes:
                old = self.get(supersedes, state)
                if old["kind"] != "manual_edit" or old["status"] not in ("Awaiting approval", "Approved"):
                    raise ActionError("Only a pending record correction can be revised")
                if old["ledger"] != ledger or old["record_id"] != record_id:
                    raise ActionError("A revision must correct the same record")
                old["status"] = "Superseded"
                proposal_id, revision = old["id"], old["revision"] + 1
            key = f"{proposal_id}:{revision}"
            period = [after["period_start"], after["period_end"]] if ledger == "invoices" else None
            p = {"id": proposal_id, "revision": revision, "key": key, "thread": self.thread,
                 "kind": "manual_edit", "ledger": ledger, "record_id": record_id,
                 "plan_id": plan_id, "invoice_id": after.get("invoice_id", record_id if ledger == "invoices" else None),
                 "amount": after.get("amount"), "currency": after["currency"], "period": period,
                 "change_set": copy.deepcopy(change_set), "reason": reason.strip(),
                 "evidence": [{"source_ref": f"{ledger}:{record_id}", "source_record": before}],
                 "preview": {"before": before, "after": after}, "fingerprint": fingerprint(state),
                 "status": "Awaiting approval", "created_at": now()}
            state["proposals"][key] = p
            self.store.event(state, "proposal_created", proposal=key, thread=self.thread, ledger=ledger, record_id=record_id)
            return p
        return self.store.transaction(create)

    def propose_contract(self, contract, reason):
        """Billing plans are the authoritative contract records; creation is reviewed."""
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ActionError("A clear reason of 1–2000 characters is required")
        plan = Plan.model_validate(contract)
        if plan.amendments:
            raise ActionError("Create the initial terms first; propose dated amendments separately")
        if money(plan.amount, plan.currency) != plan.amount:
            raise ActionError("Contract amount must match currency precision")
        after = plan.model_dump(mode="json")
        billing = schedule(after)
        def create(state):
            if any(p["id"] == plan.id for p in state["billing_plans"]):
                raise ActionError("A contract with this identifier already exists")
            if any(i.get("plan_id") == plan.id for i in state["invoices"]):
                raise ActionError("Existing invoices reference this identifier; resolve their source contract first")
            proposal_id = "PROP-" + uuid.uuid4().hex[:12]
            key = proposal_id + ":1"
            p = {"id": proposal_id, "revision": 1, "key": key, "thread": self.thread,
                 "kind": "contract_create", "plan_id": plan.id, "invoice_id": None,
                 "amount": str(plan.amount), "currency": plan.currency, "period": None,
                 "change_set": None, "reason": reason.strip(), "evidence": [],
                 "preview": {"before": None, "after": after, "billing_after": billing},
                 "fingerprint": fingerprint(state, plan.id), "status": "Awaiting approval", "created_at": now()}
            state["proposals"][key] = p
            self.store.event(state, "proposal_created", proposal=key, thread=self.thread)
            return p
        return self.store.transaction(create)

    def reject(self, key):
        def reject(state):
            p = self.get(key, state)
            if p["status"] != "Awaiting approval":
                raise ActionError("Only an awaiting proposal can be rejected")
            p["status"] = "Rejected"
            self.store.event(state, "proposal_rejected", proposal=key, thread=self.thread)
            return p
        return self.store.transaction(reject)

    def approve(self, keys):
        """Call only from trusted UI confirmation handling, never a model tool."""
        def approve(state):
            conv = state["conversations"][self.thread]
            if not keys or len(keys) != len(set(keys)) or any(k not in conv["displayed"] for k in keys):
                raise ActionError("Approval must identify a displayed immutable revision")
            proposals = [self.get(k, state) for k in keys]
            for p in proposals:
                if p["key"] in state["receipts"]:
                    continue
                if p["status"] not in ("Awaiting approval", "Approved"):
                    raise ActionError("Proposal is not awaiting approval")
                if p["fingerprint"] != fingerprint(state, p["plan_id"] if p["kind"] not in ("rollback", "manual_edit") else None):
                    raise ActionError("Source records changed. Create and review a fresh proposal")
            for p in proposals:
                if p["key"] not in state["receipts"]:
                    p["status"] = "Approved"
                    self.store.event(state, "human_approved", proposal=p["key"], thread=self.thread)
            conv["active_question"] = None
            return keys
        return self.store.transaction(approve)

    def apply(self, action_draft):
        # Ignore all replacement financial fields and approval flags from caller.
        key = action_draft if isinstance(action_draft, str) else action_draft.get("key")
        def execute(state):
            p = self.get(key, state)
            if key in state["receipts"]:
                return state["receipts"][key]
            if p["status"] != "Approved":
                raise ActionError("Explicit approval of this displayed revision is required")
            if p["fingerprint"] != fingerprint(state, p["plan_id"] if p["kind"] not in ("rollback", "manual_edit") else None):
                p["status"] = "Superseded"
                self.store.event(state, "stale_proposal_blocked", proposal=key)
                return {"status": "stale", "proposal": key, "message": "Records changed; review a fresh proposal"}
            before = copy.deepcopy(records(state))
            action_id = "ACT-" + uuid.uuid4().hex[:12]
            if p["kind"] == "rollback":
                prior = state["receipts"][p["target_action_key"]]
                latest = self.latest(state)
                if not latest or latest["action_id"] != prior["action_id"]:
                    raise ActionError("Later dependent changes prevent rollback")
                for name in LEDGERS:
                    state[name] = copy.deepcopy(prior["before"][name])
                prior["rolled_back"] = True
            elif p["kind"] == "contract_create":
                if any(r["id"] == p["plan_id"] for r in state["billing_plans"]):
                    raise ActionError("A contract with this identifier already exists")
                created = Plan.model_validate(p["preview"]["after"]).model_dump(mode="json")
                schedule(created)
                state["billing_plans"].append(created)
            elif p["kind"] == "manual_edit":
                try:
                    _, updated, plan_id = validate_edit(state, p["ledger"], p["record_id"], p["change_set"])
                except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
                    raise ActionError(str(exc)) from exc
                if updated != p["preview"]["after"] or plan_id != p["plan_id"]:
                    raise ActionError("Stored correction no longer matches its reviewed preview")
                index = next(i for i, row in enumerate(state[p["ledger"]]) if row["id"] == p["record_id"])
                state[p["ledger"]][index] = updated
            elif p["kind"] == "make_good":
                plan = next(r for r in state["billing_plans"] if r["id"] == p["plan_id"])
                state["invoices"].append({"id": "INV-MG-"+action_id[4:], "plan_id": p["plan_id"], "customer_id": plan["customer_id"], "product_id": plan["product_id"], "amount": p["amount"], "currency": p["currency"], "period_start": p["period"][0], "period_end": p["period"][1], "issued_date": str(date.today()), "status": "issued", "notes": p["reason"]})
            elif p["kind"] == "credit":
                state["credit_memos"].append({"id": "CM-"+action_id[4:], "invoice_id": p["invoice_id"], "amount": p["amount"], "currency": p["currency"], "issued_date": str(date.today()), "reason": p["reason"]})
            elif p["kind"] == "amendment":
                plan = next(r for r in state["billing_plans"] if r["id"] == p["plan_id"])
                plan.setdefault("amendments", []).append(p["change_set"])
            else:
                raise ActionError("Unsupported stored action")
            state["revision"] += 1
            p["status"] = "Applied"
            if p["kind"] == "rollback":
                state["proposals"][p["target_action_key"]]["status"] = "Rolled back"
            receipt = {"action_id": action_id, "proposal": key, "kind": p["kind"], "status": "Applied", "revision": state["revision"], "at": now(), "before": before, "after_fingerprint": fingerprint(state), "rolled_back": False, "plan_id": p["plan_id"]}
            state["receipts"][key] = receipt
            self.store.event(state, "action_applied", action_id=action_id, proposal=key, kind_applied=p["kind"], thread=self.thread)
            return receipt
        receipt = self.store.transaction(execute)
        if receipt.get("status") == "Applied":
            current = self.store.read()
            exists = any(p["id"] == receipt["plan_id"] for p in current["billing_plans"])
            return {**receipt, "verification": reconcile(current, receipt["plan_id"]) if exists else [], "contract_exists": exists, "verification_revision": current["revision"], "projection_warning": self.store.projection_error}
        return receipt

    @staticmethod
    def latest(state):
        eligible = [r for r in state["receipts"].values() if r["kind"] != "rollback" and not r["rolled_back"]]
        return max(eligible, key=lambda r: r["revision"], default=None)

    def request_rollback(self, action_id):
        def create(state):
            receipt = next((r for r in state["receipts"].values() if r["action_id"] == action_id), None)
            if not receipt or self.latest(state) != receipt:
                raise ActionError("Only the latest eligible action can be rolled back")
            original = self.get(receipt["proposal"], state)
            if fingerprint(state) != receipt["after_fingerprint"]:
                # A prior rollback can restore the relevant image; compare records.
                raise ActionError("Current records differ from this action's after-image")
            proposal_id = "PROP-" + uuid.uuid4().hex[:12]
            key = proposal_id+":1"
            p = {"id": proposal_id, "revision": 1, "key": key, "thread": self.thread, "kind": "rollback", "plan_id": original["plan_id"], "target_action_key": receipt["proposal"], "reason": "Restore stored before-images of latest action", "fingerprint": fingerprint(state), "preview": {"before": records(state), "after": receipt["before"], "target_action": action_id}, "status": "Awaiting approval", "created_at": now(), "amount": None, "currency": original["currency"], "period": original.get("period"), "evidence": []}
            state["proposals"][key] = p
            self.store.event(state, "rollback_proposed", action_id=action_id, proposal=key, thread=self.thread)
            return p
        return self.store.transaction(create)

    def rollback(self, action_id):
        state = self.store.read()
        pending = [p for p in state["proposals"].values() if p["thread"] == self.thread and p["kind"] == "rollback" and state["receipts"].get(p["target_action_key"], {}).get("action_id") == action_id and p["status"] == "Approved"]
        if len(pending) != 1:
            raise ActionError("Display a rollback proposal and explicitly approve it first")
        return self.apply(pending[0]["key"])
