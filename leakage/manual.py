"""Validated sandbox corrections. Plan commercial terms require dated amendments."""
import copy
from decimal import Decimal
from .domain import Plan, Invoice, Credit, identifier, money
EDITABLE_FIELDS = {"billing_plans": ("notes",), "invoices": ("amount", "period_start", "period_end", "issued_date", "status", "notes", "duplicate_of"), "credit_memos": ("amount", "issued_date", "reason")}
MODELS = {"billing_plans": Plan, "invoices": Invoice, "credit_memos": Credit}

def unique(rows, key):
    found = [r for r in rows if r.get("id") == key]
    if len(found) != 1:
        raise ValueError("Correction requires one unambiguous record ID")
    return found[0]

def validate_edit(state, ledger, record_id, changes):
    if ledger not in EDITABLE_FIELDS:
        raise ValueError("Only plans, invoices and credit memos support manual corrections")
    identifier(record_id)
    if not isinstance(changes, dict) or not changes or set(changes) - set(EDITABLE_FIELDS[ledger]):
        raise ValueError("Use supported editable fields; identifiers, relationships and currency are fixed")
    before = unique(state[ledger], record_id)
    after = copy.deepcopy(before)
    after.update(copy.deepcopy(changes))
    for field in ("notes", "reason", "status", "period_start", "period_end", "issued_date"):
        if field in changes and (not isinstance(changes[field], str) or len(changes[field]) > 2000):
            raise ValueError(f"{field} must be text of at most 2000 characters")
    if "amount" in changes and (isinstance(changes["amount"], bool) or Decimal(str(changes["amount"])) != money(changes["amount"], after["currency"])):
        raise ValueError("Amount must be finite and already rounded to currency precision")
    validated = MODELS[ledger].model_validate(after).model_dump(mode="json")
    if Decimal(validated["amount"]) != money(validated["amount"], validated["currency"]):
        raise ValueError("The complete record must use the currency precision")
    if ledger == "billing_plans":
        plan = after
    elif ledger == "invoices":
        plan = unique(state["billing_plans"], after["plan_id"])
        if after["customer_id"] != plan["customer_id"] or after["product_id"] != plan["product_id"]:
            raise ValueError("Invoice customer and product must match its plan")
        if not plan["start_date"] <= validated["period_start"] < validated["period_end"] <= plan["end_date"]:
            raise ValueError("Invoice service period must be inside its contract")
        credits = [c for c in state["credit_memos"] if c["invoice_id"] == record_id]
        if any(c["currency"] != after["currency"] for c in credits) or sum((Decimal(c["amount"]) for c in credits), Decimal(0)) > Decimal(validated["amount"]):
            raise ValueError("Invoice correction conflicts with existing credits")
        if validated.get("duplicate_of"):
            target = unique(state["invoices"], identifier(validated["duplicate_of"]))
            if target["id"] == record_id or target.get("duplicate_of"):
                raise ValueError("Duplicate references cannot be self-references or chains")
            fields = ("plan_id", "customer_id", "product_id", "currency", "period_start", "period_end")
            if any(validated[f] != target[f] for f in fields) or Decimal(validated["amount"]) != Decimal(target["amount"]):
                raise ValueError("Confirmed duplicate must match the original charge and service period")
        for duplicate in [i for i in state["invoices"] if i.get("duplicate_of") == record_id]:
            if any(validated[f] != duplicate[f] for f in ("period_start", "period_end")) or Decimal(validated["amount"]) != Decimal(duplicate["amount"]):
                raise ValueError("Correction would invalidate a confirmed duplicate reference")
    else:
        inv = unique(state["invoices"], after["invoice_id"])
        Invoice.model_validate(inv)
        plan = unique(state["billing_plans"], inv["plan_id"])
        if inv["currency"] != after["currency"]:
            raise ValueError("Credit currency must match the invoice")
        others = sum((Decimal(c["amount"]) for c in state["credit_memos"] if c["invoice_id"] == inv["id"] and c["id"] != record_id), Decimal(0))
        if others + Decimal(validated["amount"]) > Decimal(inv["amount"]):
            raise ValueError("Credits must not exceed the invoice amount")
    Plan.model_validate(plan)
    if validated == MODELS[ledger].model_validate(before).model_dump(mode="json"):
        raise ValueError("Correction must change at least one field")
    return copy.deepcopy(before), validated, plan["id"]
