"""Source-native, approval-bound sandbox actions; original input files stay immutable."""
from __future__ import annotations
import copy
import uuid
from datetime import date
from decimal import Decimal
from .actions import Actions, ActionError, fingerprint, records
from .domain import identifier, money
from .store import LEDGERS, now
from .challenge_data import ChallengeDataset


def billing():
    # Lazy import lets the source billing/calculation module evolve independently.
    from .source_billing import validate_context, reconcile_source
    return validate_context, reconcile_source


def one(rows, field, value):
    matches = [r for r in rows if r.get(field) == value]
    if len(matches) != 1:
        raise ActionError("Select one unambiguous source record")
    return matches[0]


def reason_text(reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ActionError("A clear reason of 1–2000 characters is required")
    return reason.strip()


def precise(value, currency, positive=True):
    if isinstance(value, bool):
        raise ActionError("Boolean values are not monetary amounts")
    try:
        result = money(value, currency)
        if Decimal(str(value)) != result or (result <= 0 if positive else result < 0):
            raise ValueError()
    except (ValueError, ArithmeticError, TypeError):
        raise ActionError("Amount must be finite, positive and match currency precision") from None
    return result


def snapshot(state):
    return copy.deepcopy({**records(state), "billing_contexts": state.get("billing_contexts", {})})


class SourceActions(Actions):
    def _proposal(self, state, kind, plan_id, reason, preview, *, supersedes=None, **fields):
        plan = one(state["billing_plans"], "plan_id", plan_id)
        proposal_id, revision = "PROP-" + uuid.uuid4().hex[:12], 1
        if supersedes:
            old = self.get(supersedes, state)
            if old["status"] not in ("Awaiting approval", "Approved") or old["kind"] != kind or old["plan_id"] != plan_id:
                raise ActionError("Only a pending proposal for the same action and plan can be revised")
            old["status"] = "Superseded"
            proposal_id, revision = old["id"], old["revision"] + 1
        key = f"{proposal_id}:{revision}"
        p = {"id": proposal_id, "revision": revision, "key": key, "thread": self.thread,
             "kind": kind, "plan_id": plan_id, "invoice_id": None, "amount": None,
             "currency": plan["currency"], "period": None, "change_set": None,
             "reason": reason_text(reason), "evidence": [], "preview": copy.deepcopy(preview),
             "fingerprint": fingerprint(state), "status": "Awaiting approval", "created_at": now(), **copy.deepcopy(fields)}
        for old in state["proposals"].values():
            if old["thread"] == self.thread and old["plan_id"] == plan_id and old["kind"] == kind and old["status"] == "Draft: needs details":
                old["status"] = "Superseded"
        state["proposals"][key] = p
        self.store.event(state, "proposal_created", proposal=key, thread=self.thread)
        return p

    def propose_billing_context(self, plan_id, context, reason, supersedes=None):
        identifier(plan_id)
        def create(state):
            validate_context, _ = billing()
            validated = validate_context(state, plan_id, context)
            return self._proposal(state, "billing_context", plan_id, reason,
                                  {"before": state.get("billing_contexts", {}).get(plan_id), "after": validated,
                                   "impact": "Confirms billing scope and service mapping; creates no invoices or credits."},
                                  context=validated, change_set=validated, supersedes=supersedes,
                                  evidence=[{"source_ref": f"billing_plans:{plan_id}"}])
        return self.store.transaction(create)

    def _financial(self, state, kind, plan_id, amount, reason, invoice_id, period, proposed_context=None):
        validate_context, reconcile_source = billing()
        reviewed_context = None
        derived_assumptions = []
        if proposed_context is not None:
            from .source_billing import derive_proposal_term
            plan = one(state["billing_plans"], "plan_id", plan_id)
            proposed_context, derived_assumptions = derive_proposal_term(plan, proposed_context)
            reviewed_context = validate_context(state, plan_id, proposed_context)
            # Calculate a draft against proposed assumptions without approving or
            # persisting them. Only application of the reviewed revision saves them.
            state = copy.deepcopy(state)
            state.setdefault("billing_contexts", {})[plan_id] = {**reviewed_context, "status": "approved"}
        plan = one(state["billing_plans"], "plan_id", plan_id)
        value = precise(amount, plan["currency"])
        reason_text(reason)
        candidates = [f for f in reconcile_source(state, plan_id)
                      if f.get("validation") == "validated" and kind in f.get("supported_corrections", [])]
        if period is not None:
            if not isinstance(period, (list, tuple)) or len(period) != 2:
                raise ActionError("Select an explicit service-period pair")
            candidates = [f for f in candidates if f.get("period") == list(period)]
        if invoice_id:
            identifier(invoice_id)
            candidates = [f for f in candidates if f"invoices:{invoice_id}" in f.get("source_refs", [])]
        candidates = [f for f in candidates if abs(Decimal(str(f["discrepancy"]))) >= value]
        if len(candidates) != 1:
            raise ActionError("Select one validated service period; amount must fit its unresolved discrepancy. Confirm billing context first.")
        f = candidates[0]
        selected = f.get("period")
        if not isinstance(selected, list) or len(selected) != 2:
            raise ActionError("Validated evidence must identify a service period")
        if kind == "credit":
            if not invoice_id:
                raise ActionError("A credit must identify one invoice")
            invoice = one(state["invoices"], "invoice_id", invoice_id)
            if invoice.get("plan_id") != plan_id:
                raise ActionError("Invoice does not belong to the selected plan")
            context = state.get("billing_contexts", {}).get(plan_id)
            if not context:
                raise ActionError("Approved billing context is required")
            # Match the supplied dataset's plan-currency credit semantics. Only use
            # exact approved valuation dates and already-recorded credits.
            source = ChallengeDataset(records(state))
            converted = source.fx_convert(invoice["amount_invoiced"], invoice["currency"], plan["currency"], invoice["issue_date"])
            available = Decimal(converted["amount"])
            for credit in state["credit_memos"]:
                if credit.get("invoice_id") == invoice_id:
                    existing = source.fx_convert(credit["amount"], credit["currency"], plan["currency"], credit["issue_date"])
                    available -= Decimal(existing["amount"])
            if value > available:
                raise ActionError("Credit exceeds the remaining net invoice amount in plan currency")
            preview = {"before": copy.deepcopy(invoice), "after": {**copy.deepcopy(invoice), "net_after_credit": str(money(available-value, plan["currency"]))}, "period": selected, "currency": plan["currency"]}
        else:
            preview = {"before": {"period_net_billed": f["actual"]},
                       "after": {"period_net_billed": str(money(Decimal(f["actual"])+value, plan["currency"])), "new_invoice": str(value)},
                       "period": selected, "currency": plan["currency"]}
        if reviewed_context is not None:
            preview["billing_assumptions"] = copy.deepcopy(reviewed_context)
            if derived_assumptions:
                preview["derived_assumptions"] = derived_assumptions
            preview["approval_scope"] = "Approval confirms these billing assumptions and applies this exact correction together. Rejecting changes neither billing assumptions nor financial records."
            f = {**f, "validation": "conditional", "supported_corrections": [],
                 "assumptions": ["Proposed billing assumptions shown in this revision; not yet approved"]}
        return value, f, preview

    def _amendment(self, state, plan_id, change_set, child_id=None):
        plan = one(state["billing_plans"], "plan_id", plan_id)
        if not isinstance(change_set, dict) or set(change_set) - {"total_value", "cadence", "entitlements", "effective_date", "reason"}:
            raise ActionError("Amendment fields are total_value, cadence, entitlements, effective_date and reason")
        if not {"effective_date", "reason"} <= set(change_set) or not set(change_set) & {"total_value", "cadence", "entitlements"}:
            raise ActionError("Explicit commercial changes, effective date and reason are required")
        changes = copy.deepcopy(change_set)
        reason_text(changes["reason"])
        try:
            effective = date.fromisoformat(changes["effective_date"])
        except (ValueError, TypeError):
            raise ActionError("Supply a valid amendment effective date") from None
        if effective <= date.fromisoformat(plan["start_date"]):
            raise ActionError("Amendment must take effect after the original plan start")
        context = state.get("billing_contexts", {}).get(plan_id)
        if context and str(effective) >= context["term_end"]:
            raise ActionError("Amendment effective date must precede the confirmed term end")
        if any(p.get("amends") == plan_id for p in state["billing_plans"]):
            raise ActionError("This plan is already superseded; amend its latest child plan")
        after = copy.deepcopy(plan)
        after.update({"plan_id": child_id or "PLAN-AM-"+uuid.uuid4().hex[:12], "amends": plan_id,
                      "start_date": str(effective), "notes": changes["reason"]})
        if "total_value" in changes:
            changes["total_value"] = str(precise(changes["total_value"], plan["currency"], positive=False))
            after["total_value"] = changes["total_value"]
        if "cadence" in changes:
            if not isinstance(changes["cadence"], str) or changes["cadence"].lower() not in ("monthly", "quarterly", "annual"):
                raise ActionError("Cadence must be Monthly, Quarterly or Annual")
            changes["cadence"] = changes["cadence"].title()
            after["cadence"] = changes["cadence"]
        if "entitlements" in changes:
            values = changes["entitlements"]
            if not isinstance(values, list) or not values or len(values) > 100 or any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in values) or len(set(values)) != len(values):
                raise ActionError("Entitlements must be a nonempty unique list of names")
            after["entitlements"] = values
        if (Decimal(str(after.get("total_value"))) == Decimal(str(plan.get("total_value")))
                and str(after.get("cadence")).lower() == str(plan.get("cadence")).lower()
                and after.get("entitlements") == plan.get("entitlements")):
            raise ActionError("Amendment must change commercial terms")
        return changes, after

    def draft(self, kind, plan_id, amount=None, reason="", invoice_id=None, period=None, change_set=None, supersedes=None, billing_context=None):
        identifier(plan_id)
        if kind not in ("make_good", "credit", "amendment"):
            raise ActionError("Unknown action type")
        if billing_context is not None and kind == "amendment":
            raise ActionError("Proposed billing assumptions belong to an invoice or credit correction")
        def create(state):
            if kind == "amendment":
                changes, after = self._amendment(state, plan_id, change_set)
                return self._proposal(state, kind, plan_id, changes["reason"],
                    {"before": one(state["billing_plans"], "plan_id", plan_id), "after": after,
                     "impact": "Creates a linked amendment plan. Prior terms and invoices remain intact; related billing contexts must be confirmed again."},
                    change_set=changes, child_plan_id=after["plan_id"], supersedes=supersedes,
                    evidence=[{"source_ref": f"billing_plans:{plan_id}"}])
            value, finding, preview = self._financial(state, kind, plan_id, amount, reason, invoice_id, period, billing_context)
            return self._proposal(state, kind, plan_id, reason, preview, amount=str(value), invoice_id=invoice_id,
                                  period=finding["period"], evidence=[finding], supersedes=supersedes,
                                  **({"billing_context": preview["billing_assumptions"]} if billing_context is not None else {}))
        return self.store.transaction(create)

    def propose_unresolved(self, kind, plan_id, amount, reason, issue, invoice_id=None, period=None, billing_context=None):
        """Save a reviewable idea without asserting financial validation or authority."""
        if kind not in ("make_good", "credit"):
            raise ActionError("Only invoice and credit ideas can be saved as unresolved drafts")
        identifier(plan_id)
        def create(state):
            plan = one(state["billing_plans"], "plan_id", plan_id)
            value = precise(amount, plan["currency"])
            reason_text(reason)
            if invoice_id:
                invoice = one(state["invoices"], "invoice_id", invoice_id)
                if invoice["plan_id"] != plan_id:
                    raise ActionError("Invoice does not belong to the selected plan")
            return self._proposal(state, kind, plan_id, reason,
                {"suggested_amount": str(value), "proposed_period": period,
                 "proposed_billing_assumptions": billing_context,
                 "unresolved_items": [str(issue)],
                 "impact": "Draft idea only. Amount and billing assumptions are unverified; no invoice, credit or billing context has been applied."},
                amount=str(value), invoice_id=invoice_id, period=period,
                status="Draft: needs details", validation_issues=[str(issue)])
        return self.store.transaction(create)

    def propose_contract(self, contract, reason):
        raise ActionError("Source contract creation requires the source-native commercial form; do not mix normalized contract fields into the supplied records")

    def propose_record_edit(self, ledger, record_id, change_set, reason, supersedes=None):
        raise ActionError("Use a source amendment for commercial terms or a verified financial correction; raw record rewriting requires a source-specific reviewed adapter")

    def _invalidate_contexts(self, state, plan_id):
        related = {plan_id}
        changed = True
        while changed:
            changed = False
            for p in state["billing_plans"]:
                if p.get("plan_id") in related or p.get("amends") in related:
                    old = len(related)
                    related.add(p["plan_id"])
                    if p.get("amends"): related.add(p["amends"])
                    changed |= len(related) != old
        for pid in related:
            state.setdefault("billing_contexts", {}).pop(pid, None)

    def apply(self, action_draft):
        key = action_draft if isinstance(action_draft, str) else action_draft.get("key") if isinstance(action_draft, dict) else None
        if not isinstance(key, str):
            raise ActionError("A stored proposal revision key is required")
        def execute(state):
            p = self.get(key, state)
            if key in state["receipts"]:
                return state["receipts"][key]
            if p["status"] != "Approved":
                raise ActionError("Explicit approval of this displayed revision is required")
            if p["fingerprint"] != fingerprint(state):
                p["status"] = "Superseded"
                self.store.event(state, "stale_proposal_blocked", proposal=key, thread=self.thread)
                return {"status": "stale", "proposal": key, "message": "Source records or approved billing context changed; review a fresh proposal"}
            before = snapshot(state)
            action_id = "ACT-"+uuid.uuid4().hex[:12]
            plan = one(state["billing_plans"], "plan_id", p["plan_id"])
            if p["kind"] == "billing_context":
                validate_context, _ = billing()
                validated = validate_context(state, p["plan_id"], p["context"])
                if validated != p["preview"]["after"]:
                    raise ActionError("Billing context differs from the reviewed revision")
                state.setdefault("billing_contexts", {})[p["plan_id"]] = {**copy.deepcopy(validated), "status": "approved"}
            elif p["kind"] in ("make_good", "credit"):
                value, finding, preview = self._financial(state, p["kind"], p["plan_id"], p["amount"], p["reason"], p.get("invoice_id"), p.get("period"), p.get("billing_context"))
                if preview != p["preview"]:
                    raise ActionError("Current financial preview differs from the reviewed revision")
                if p.get("billing_context") is not None:
                    state.setdefault("billing_contexts", {})[p["plan_id"]] = {**copy.deepcopy(preview["billing_assumptions"]), "status": "approved"}
                if p["kind"] == "make_good":
                    iid = "INV-MG-"+action_id[4:]
                    state["invoices"].append({"invoice_id": iid, "plan_id": p["plan_id"], "customer_name": plan["customer_name"],
                        "issue_date": str(date.today()), "due_date": None, "amount_invoiced": str(value), "currency": plan["currency"],
                        "status": "unpaid", "description": p["reason"], "period_start": p["period"][0], "period_end": p["period"][1]})
                    state["billing_contexts"][p["plan_id"]]["invoice_periods"][iid] = copy.deepcopy(p["period"])
                else:
                    state["credit_memos"].append({"memo_id": "CM-"+action_id[4:], "invoice_id": p["invoice_id"], "plan_id": p["plan_id"],
                        "amount": str(value), "currency": plan["currency"], "issue_date": str(date.today()), "reason": p["reason"]})
                validate_context, _ = billing()
                refreshed = validate_context(state, p["plan_id"], state["billing_contexts"][p["plan_id"]])
                state["billing_contexts"][p["plan_id"]] = {**refreshed, "status": "approved"}
            elif p["kind"] == "amendment":
                _, after = self._amendment(state, p["plan_id"], p["change_set"], p["child_plan_id"])
                if after != p["preview"]["after"] or any(r["plan_id"] == after["plan_id"] for r in state["billing_plans"]):
                    raise ActionError("Amendment differs from reviewed terms or identifier already exists")
                state["billing_plans"].append(after)
                self._invalidate_contexts(state, p["plan_id"])
            elif p["kind"] == "rollback":
                prior = state["receipts"][p["target_action_key"]]
                if self.latest(state) != prior:
                    raise ActionError("Later dependent changes prevent rollback")
                for ledger in LEDGERS:
                    state[ledger] = copy.deepcopy(prior["before"][ledger])
                state["billing_contexts"] = copy.deepcopy(prior["before"].get("billing_contexts", {}))
                prior["rolled_back"] = True
                state["proposals"][p["target_action_key"]]["status"] = "Rolled back"
            else:
                raise ActionError("Unsupported source action")
            state["revision"] += 1
            p["status"] = "Applied"
            receipt = {"action_id": action_id, "proposal": key, "kind": p["kind"], "status": "Applied", "revision": state["revision"],
                       "at": now(), "before": before, "after_fingerprint": fingerprint(state), "rolled_back": False, "plan_id": p["plan_id"]}
            state["receipts"][key] = receipt
            self.store.event(state, "action_applied", action_id=action_id, proposal=key, kind_applied=p["kind"], thread=self.thread)
            return receipt
        receipt = self.store.transaction(execute)
        if receipt.get("status") != "Applied":
            return receipt
        current = self.store.read()
        _, reconcile_source = billing()
        return {**receipt, "verification": reconcile_source(current, receipt["plan_id"]), "verification_revision": current["revision"],
                "source_context_status": "confirmed" if receipt["plan_id"] in current.get("billing_contexts", {}) else "needs confirmation",
                "contract_exists": any(r["plan_id"] == receipt["plan_id"] for r in current["billing_plans"]), "projection_warning": self.store.projection_error}

    def request_rollback(self, action_id):
        def create(state):
            receipt = one(list(state["receipts"].values()), "action_id", action_id)
            if self.latest(state) != receipt or fingerprint(state) != receipt["after_fingerprint"]:
                raise ActionError("Only the latest eligible unchanged action can be rolled back")
            original = self.get(receipt["proposal"], state)
            return self._proposal(state, "rollback", original["plan_id"], "Restore the stored source records and billing contexts before the latest action",
                                  {"before": snapshot(state), "after": receipt["before"], "target_action": action_id},
                                  target_action_key=receipt["proposal"], currency=original["currency"], period=original.get("period"))
        return self.store.transaction(create)
