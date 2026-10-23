"""Pure reconciliation. No model arithmetic and no mutation."""
from decimal import Decimal
from .domain import Plan, Invoice, Credit, Rate, digest, money, schedule

def fx(snapshot, amount, from_ccy, to_ccy, on_date):
    value = Decimal(str(amount))
    if not value.is_finite():
        raise ValueError("Amount must be finite")
    if from_ccy == to_ccy:
        return {"amount": str(money(value, to_ccy)), "currency": to_ccy, "rate": "1", "source": "identity"}
    matches = [r for r in snapshot["exchange_rates"] if r["from_ccy"] == from_ccy and r["to_ccy"] == to_ccy and r["on_date"] == on_date]
    if len(matches) != 1:
        raise ValueError(f"Exactly one supplied {from_ccy}/{to_ccy} rate required for {on_date}")
    r = Rate.model_validate(matches[0])
    return {"amount": str(money(value*r.rate, to_ccy)), "currency": to_ccy, "rate": str(r.rate), "source": f"exchange_rates:{digest(matches[0])[:12]}"}

def reconcile(snapshot, plan_id):
    raw = next((p for p in snapshot["billing_plans"] if p["id"] == plan_id), None)
    if raw is None:
        raise ValueError("Unknown plan")
    base = {"plan_id": plan_id, "currency": raw.get("currency"), "snapshot_revision": snapshot["revision"], "source_fingerprint": digest({k: snapshot[k] for k in ("billing_plans", "invoices", "credit_memos", "exchange_rates")})}
    def finding(kind, period=None, **kwargs):
        row = {**base, "kind": kind, "period": period, "expected": None, "actual": None, "discrepancy": None, "validation": "unresolved", "source_refs": [f"billing_plans:{plan_id}"], "calculation": "", "assumptions": [], "supported_corrections": [], **kwargs}
        row["id"] = "F-" + digest([plan_id, kind, period, row["source_refs"]])[:16]
        return row
    try:
        plan = Plan.model_validate(raw)
        periods = schedule(raw)
    except ValueError as exc:
        return [finding("incomplete_contract", calculation=str(exc))]
    rows, anomalies = [], []
    seen = {}
    for r in snapshot["invoices"]:
        if r.get("plan_id") != plan_id:
            continue
        try:
            inv = Invoice.model_validate(r)
            if inv.product_id != plan.product_id or inv.customer_id != plan.customer_id:
                raise ValueError("Invoice customer/product does not match plan grain")
            if inv.id in seen:
                if seen[inv.id] == r:
                    anomalies.append(finding("duplicate_record", source_refs=[f"invoices:{inv.id}"], validation="confirmed", calculation="Identical repeated ID counted once"))
                    continue
                raise ValueError("Conflicting records share invoice ID")
            seen[inv.id] = r
            if inv.status != "void":
                rows.append(r)
        except ValueError as exc:
            anomalies.append(finding("inconsistent_invoice", source_refs=[f"invoices:{r.get('id', '?')}"], calculation=str(exc)))
    expected_keys = {(p["period_start"], p["period_end"]) for p in periods}
    outside = [r for r in rows if (r["period_start"], r["period_end"]) not in expected_keys]
    for r in outside:
        anomalies.append(finding("suspected_overlap_or_unmatched_period", period=[r["period_start"], r["period_end"]], source_refs=[f"invoices:{r['id']}"], calculation="Invoice service period does not match an explicit contract period; manual allocation required"))
    result = []
    for p in periods:
        period = [p["period_start"], p["period_end"]]
        invoices = [r for r in rows if [r["period_start"], r["period_end"]] == period]
        refs = [f"billing_plans:{plan_id}"]
        actual, errors, workings = Decimal(0), [], []
        if any(r["period_start"] < period[1] and r["period_end"] > period[0] for r in outside):
            errors.append("Unallocated overlapping invoice blocks a monetary conclusion")
        if any(a["kind"] == "inconsistent_invoice" for a in anomalies):
            errors.append("Inconsistent source records block correction")
        for inv in invoices:
            refs.append(f"invoices:{inv['id']}")
            net = Decimal(inv["amount"])
            for cr in snapshot["credit_memos"]:
                if cr.get("invoice_id") != inv["id"]:
                    continue
                try:
                    c = Credit.model_validate(cr)
                    if c.currency != inv["currency"]:
                        raise ValueError("Credit currency differs from invoice currency")
                    net -= c.amount
                    refs.append(f"credit_memos:{c.id}")
                except ValueError as exc:
                    errors.append(str(exc))
            if net < 0:
                errors.append("Credits exceed invoice amount")
            try:
                converted = fx(snapshot, net, inv["currency"], plan.currency, inv["issued_date"])
                actual += Decimal(converted["amount"])
                workings.append(f"{inv['id']}: net {net} {inv['currency']} × {converted['rate']} = {converted['amount']} {plan.currency}")
                refs.append(converted["source"])
            except ValueError as exc:
                errors.append(str(exc))
            if inv.get("duplicate_of"):
                original = next((r for r in rows if r["id"] == inv["duplicate_of"]), None)
                if not original or any(inv[k] != original[k] for k in ("period_start", "period_end", "amount", "currency", "product_id")):
                    errors.append("Invalid duplicate_of reference")
                else:
                    anomalies.append(finding("confirmed_duplicate_charge", period=period, source_refs=[f"invoices:{inv['id']}", f"invoices:{original['id']}"], validation="confirmed", calculation="Explicit duplicate_of reference matches the original charge; both issued balances remain in the ledger until corrected"))
        expected = Decimal(p["expected"])
        delta = money(expected-actual, plan.currency)
        kind = "unresolved" if errors else ("missing_period" if not invoices and delta else "underbilling" if delta > 0 else "overbilling" if delta < 0 else "matched")
        result.append(finding(kind, period, expected=str(expected), actual=None if errors else str(actual), discrepancy=None if errors else str(delta), validation="unresolved" if errors else "validated", source_refs=refs, calculation=f"Expected: {p['calculation']} = {expected} {plan.currency}. Actual: " + ("; ".join(workings) or "no matching invoices") + ". " + "; ".join(errors), supported_corrections=[] if errors or not delta else ["make_good" if delta > 0 else "credit"], assumptions=["Period end dates are exclusive", "Only explicit contract rules used"]))
    return result + anomalies

def portfolio(snapshot):
    groups = {}
    for plan in snapshot["billing_plans"]:
        for f in reconcile(snapshot, plan["id"]):
            groups.setdefault(f["currency"] or "unknown", []).append(f)
    plan_ids = {p["id"] for p in snapshot["billing_plans"]}
    invoice_ids = {i.get("id") for i in snapshot["invoices"]}
    for ledger, rows, ref_field, known, kind in [("invoices", snapshot["invoices"], "plan_id", plan_ids, "orphan_invoice"), ("credit_memos", snapshot["credit_memos"], "invoice_id", invoice_ids, "orphan_credit")]:
        for row in rows:
            if row.get(ref_field) not in known:
                currency = row.get("currency", "unknown")
                groups.setdefault(currency, []).append({"id": "F-"+digest([ledger,row])[:16], "plan_id": None, "kind": kind, "currency": currency, "period": None, "expected": None, "actual": None, "discrepancy": None, "validation": "unresolved", "source_refs": [f"{ledger}:{row.get('id', '?')}"], "calculation": "Source reference does not resolve; manual source repair required", "assumptions": [], "supported_corrections": [], "snapshot_revision": snapshot["revision"]})
    return {ccy: sorted(rows, key=lambda f: abs(Decimal(f["discrepancy"] or 0)), reverse=True) for ccy, rows in groups.items()}
