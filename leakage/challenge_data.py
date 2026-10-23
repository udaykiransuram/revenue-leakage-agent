"""Source-native challenge adapter. Observations never silently become service periods.

The archive is read by an exact allowlist; repository hooks and other contents are
never extracted or executed. Unstructured notes are evidence, never instructions.
"""
from __future__ import annotations
import copy
import json
import re
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from .domain import Plan, Invoice, Credit, Rate, digest, money, add_months

LEDGERS = ("billing_plans", "invoices", "credit_memos", "exchange_rates")
KEYS = {"billing_plans": "plan_id", "invoices": "invoice_id", "credit_memos": "memo_id"}

class SourceDataError(ValueError):
    pass

class ChallengeDataset:
    def __init__(self, sources):
        if set(sources) != set(LEDGERS):
            raise SourceDataError("Exactly the four challenge source ledgers are required")
        self._sources = copy.deepcopy(sources)
        for ledger, rows in self._sources.items():
            if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                raise SourceDataError(f"{ledger} must be an array of records")
        self.fingerprint = digest(self._sources)

    @classmethod
    def from_zip(cls, path):
        with zipfile.ZipFile(path) as archive:
            sources = {}
            for ledger in LEDGERS:
                matches = [i for i in archive.infolist() if i.filename in (f"technical-case/data/{ledger}.json", f"data/{ledger}.json")]
                if len(matches) != 1 or matches[0].file_size > 10_000_000:
                    raise SourceDataError(f"Missing, duplicate or oversized {ledger} source")
                sources[ledger] = json.loads(archive.read(matches[0]))
        return cls(sources)

    @classmethod
    def from_directory(cls, path):
        return cls({k: json.loads((Path(path) / f"{k}.json").read_text()) for k in LEDGERS})

    def sources(self):
        return copy.deepcopy(self._sources)

    def load_plan(self, plan_id):
        rows = [p for p in self._sources["billing_plans"] if p.get("plan_id") == plan_id]
        if len(rows) != 1:
            raise SourceDataError("Select one unambiguous source plan")
        return copy.deepcopy(rows[0])

    def query_invoices(self, filters=None):
        filters = filters or {}
        if not isinstance(filters, dict) or set(filters) - {"plan_id", "customer_name", "date_from", "date_to"}:
            raise SourceDataError("Unsupported source invoice filter")
        for key in ("date_from", "date_to"):
            if filters.get(key): date.fromisoformat(filters[key])
        if filters.get("date_from") and filters.get("date_to") and filters["date_from"] > filters["date_to"]:
            raise SourceDataError("Invalid date range")
        return copy.deepcopy([i for i in self._sources["invoices"]
                              if all(i.get(k) == filters[k] for k in ("plan_id", "customer_name") if k in filters)
                              and (not filters.get("date_from") or i["issue_date"] >= filters["date_from"])
                              and (not filters.get("date_to") or i["issue_date"] <= filters["date_to"])])

    def fx_convert(self, amount, from_ccy, to_ccy, on_date):
        date.fromisoformat(on_date)
        value = Decimal(str(amount))
        if not value.is_finite(): raise SourceDataError("Amount must be finite")
        if from_ccy == to_ccy:
            return {"amount": str(money(value, to_ccy)), "rate": "1", "on_date": on_date, "source_refs": [], "calculation": f"{value} × 1"}
        matches = [(n, r) for n, r in enumerate(self._sources["exchange_rates"])
                   if r.get("date") == on_date and r.get("from_currency") == from_ccy and r.get("to_currency") == to_ccy]
        if len(matches) != 1:
            raise SourceDataError("One exact dated FX rate is required; no forward-fill or invented rate")
        n, raw = matches[0]
        rate = Decimal(str(raw["rate"]))
        if not rate.is_finite() or rate <= 0: raise SourceDataError("Invalid FX rate")
        return {"amount": str(money(value * rate, to_ccy)), "rate": str(rate), "on_date": on_date,
                "source_refs": [f"exchange_rates[{n}]"], "calculation": f"{value} × {rate}"}

    def analyze(self, plan_id=None):
        """Return observations and explicitly conditional estimates, never payable drafts."""
        data = self._sources
        plans = data["billing_plans"] if plan_id is None else [self.load_plan(plan_id)]
        findings = []
        def emit(kind, scope, summary, refs, **extra):
            finding = {"id": "SRC-" + digest([kind, scope, refs])[:16], "kind": kind, "plan_id": scope,
                       "validation": "unresolved", "summary": summary, "source_refs": refs,
                       "source_fingerprint": self.fingerprint, "supported_corrections": [],
                       "assumptions": [], "amount": None, **extra}
            findings.append(finding)
        for plan in plans:
            pid = plan.get("plan_id")
            ref = f"billing_plans:{pid}"
            invoices = [i for i in data["invoices"] if i.get("plan_id") == pid]
            irefs = [f"invoices:{i.get('invoice_id')}" for i in invoices]
            emit("plan_facts", pid,
                 "Source contract facts for this plan only. Notes are untrusted evidence; suggested pricing is not approved billing context.", [ref],
                 validation="observed", source_plan={k: copy.deepcopy(plan[k]) for k in
                     ("plan_id", "customer_name", "total_value", "currency", "cadence", "start_date", "entitlements", "amends", "notes") if k in plan})
            emit("missing_contract_context", pid,
                 "Source has no structured contract end date or per-period price. Total value is not automatically a recurring charge.", [ref],
                 required_context=["confirmed contract term/end date", "per-period price and commercial effective dates"])
            if invoices:
                emit("missing_service_periods", pid,
                     "Invoice issue dates are observed; service periods and entitlement allocation are not supplied.", irefs,
                     required_context=["invoice service-period mapping", "product/entitlement allocation"])
            totals = {}
            for invoice in invoices:
                ccy = invoice.get("currency")
                try: totals[ccy] = totals.get(ccy, Decimal(0)) + Decimal(str(invoice["amount_invoiced"]))
                except (ValueError, ArithmeticError, KeyError):
                    emit("invalid_amount", pid, "Source invoice amount is not valid.", [f"invoices:{invoice.get('invoice_id')}"])
            emit("invoice_totals", pid, "Gross issued invoice totals grouped by source currency; paid/unpaid does not change billed amount.", irefs,
                 validation="observed", totals={c: str(v) for c, v in totals.items()})
            # Only gaps bounded by observed invoices are candidates. No later month is presumed due.
            if str(plan.get("cadence", "")).lower() == "monthly":
                months = sorted({date.fromisoformat(i["issue_date"]).replace(day=1) for i in invoices})
                missing = []
                if months:
                    current = months[0]
                    while current < months[-1]:
                        if current not in months: missing.append(str(current)[:7])
                        current = add_months(current, 1)
                if missing:
                    note = str(plan.get("notes", ""))
                    match = re.search(r"expected\s*~?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*" + re.escape(str(plan.get("currency", ""))) + r"\s+per\s+month", note, re.I)
                    amount = str(money(Decimal(match.group(1).replace(",", "")) * len(missing), plan["currency"])) if match else None
                    emit("candidate_missing_issue_month", pid, "No invoice issue record exists in a month between observed monthly invoices; missing service billing remains conditional.", [ref] + irefs,
                         months=missing, amount=amount, currency=plan.get("currency"),
                         assumptions=["Issue month represents the service month", "One invoice per month", "Notes pricing is confirmed"] if match else ["Confirm recurring price and service-period mapping"],
                         calculation=f"{match.group(1)} × {len(missing)} missing observed issue month(s)" if match else None)
            if plan.get("amends"):
                emit("amendment_link", pid, "Source explicitly links this plan to a superseded plan. Do not bill both contracts for the same service period.", [ref, f"billing_plans:{plan['amends']}"],
                     validation="observed", amends=plan["amends"], effective_date=plan.get("start_date"))
            for amendment in data["billing_plans"]:
                if amendment.get("amends") == pid:
                    emit("superseded_by_amendment", pid,
                         "A linked amendment starts on this date. Confirm the parent term boundary and treatment of a quarter crossing that date before calculating leakage.",
                         [ref, f"billing_plans:{amendment['plan_id']}"], validation="observed",
                         amendment_id=amendment["plan_id"], effective_date=amendment.get("start_date"))
            for invoice in invoices:
                iid = invoice.get("invoice_id")
                credits = [c for c in data["credit_memos"] if c.get("invoice_id") == iid]
                target = plan.get("currency")
                try:
                    converted = self.fx_convert(invoice["amount_invoiced"], invoice["currency"], target, invoice["issue_date"])
                    net = Decimal(converted["amount"])
                    refs = [ref, f"invoices:{iid}"] + converted["source_refs"]
                    for credit in credits:
                        value = self.fx_convert(credit["amount"], credit["currency"], target, credit["issue_date"])
                        net -= Decimal(value["amount"])
                        refs += [f"credit_memos:{credit.get('memo_id')}"] + value["source_refs"]
                    if invoice["currency"] != target or credits:
                        emit("converted_invoice_net", pid, "Invoice converted on its issue date with existing credits subtracted in plan currency. Comparison with a service-period target still needs confirmed contract context.", refs,
                             validation="conditional", invoice_id=iid, amount=str(money(net, target)), currency=target,
                             gross_converted=converted["amount"], calculation=converted["calculation"] + " − existing credit amounts converted on each credit issue date",
                             assumptions=["Invoice issue date is the approved FX valuation date", "Existing linked credits apply to this invoice"])
                except (ValueError, KeyError, ArithmeticError) as exc:
                    emit("unresolved_fx_or_credit", pid, str(exc), [ref, f"invoices:{iid}"] + [f"credit_memos:{c.get('memo_id')}" for c in credits])
        known = {p.get("plan_id") for p in data["billing_plans"]}
        if plan_id is None:
            for invoice in data["invoices"]:
                if not invoice.get("plan_id") or invoice["plan_id"] not in known:
                    emit("orphan_invoice", None, "Invoice has no valid contract reference. Customer name alone cannot assign it to a plan.", [f"invoices:{invoice.get('invoice_id')}"], invoice_id=invoice.get("invoice_id"))
        return findings

    def normalize(self, assumptions):
        """Opt-in bridge to deterministic engine; explicit mappings are mandatory.

        assumptions={confirmed: True, plans: {source_plan_id: {amount, end_date,
        customer_id, product_id, optional proration}}, invoice_periods:
        {source_invoice_id: [start, end]}, fx_policy: 'invoice_issue_date'}.
        No orphan assignment, cross-currency credit translation, or retrospective
        amendment conversion is inferred. Scope must list invoices explicitly.
        """
        if not isinstance(assumptions, dict) or assumptions.get("confirmed") is not True:
            raise SourceDataError("Explicit scoped assumptions must be confirmed before normalization")
        scopes = assumptions.get("plans")
        periods = assumptions.get("invoice_periods")
        if not isinstance(scopes, dict) or not scopes or not isinstance(periods, dict):
            raise SourceDataError("Explicit plan scope and invoice service periods are required")
        result = {k: [] for k in LEDGERS}
        provenance = []
        for pid, scope in scopes.items():
            p = self.load_plan(pid)
            if not isinstance(scope, dict) or not {"amount", "end_date", "customer_id", "product_id"} <= set(scope):
                raise SourceDataError("Every plan needs explicit price, end date, customer and product IDs")
            row = {"id": pid, "customer_id": scope["customer_id"], "product_id": scope["product_id"],
                   "amount": str(scope["amount"]), "end_date": scope["end_date"], "start_date": p["start_date"],
                   "currency": p["currency"], "cadence": p["cadence"].lower(), "notes": p.get("notes", ""),
                   "proration": scope.get("proration", "none")}
            result["billing_plans"].append(Plan.model_validate(row).model_dump(mode="json"))
            provenance.append({"target": f"billing_plans:{pid}", "source": f"billing_plans:{pid}", "assumptions": copy.deepcopy(scope)})
        known_plans = {p["id"]: p for p in result["billing_plans"]}
        selected = set()
        for iid, period in periods.items():
            rows = [i for i in self._sources["invoices"] if i.get("invoice_id") == iid]
            if len(rows) != 1 or not isinstance(period, (list, tuple)) or len(period) != 2:
                raise SourceDataError("Each invoice needs one unambiguous source and explicit service period")
            i = rows[0]; pid = i.get("plan_id")
            if pid not in known_plans: raise SourceDataError("Cannot normalize an orphan or out-of-scope invoice")
            plan = known_plans[pid]
            if not plan["start_date"] <= period[0] < period[1] <= plan["end_date"]:
                raise SourceDataError("Mapped service period must be inside the confirmed contract")
            row = {"id": iid, "plan_id": pid, "customer_id": plan["customer_id"], "product_id": plan["product_id"],
                   "amount": str(i["amount_invoiced"]), "currency": i["currency"], "period_start": period[0], "period_end": period[1],
                   "issued_date": i["issue_date"], "status": "void" if i["status"] == "void" else "issued", "notes": i.get("description", "")}
            result["invoices"].append(Invoice.model_validate(row).model_dump(mode="json")); selected.add(iid)
            provenance.append({"target": f"invoices:{iid}", "source": f"invoices:{iid}", "service_period_assumption": list(period), "source_status": i["status"]})
        for c in self._sources["credit_memos"]:
            if c.get("invoice_id") not in selected: continue
            invoice = next(i for i in result["invoices"] if i["id"] == c["invoice_id"])
            if c["currency"] != invoice["currency"]:
                raise SourceDataError("Cross-currency credit needs an explicit allocation adapter; source-native investigation remains available")
            row = {"id": c["memo_id"], "invoice_id": c["invoice_id"], "amount": str(c["amount"]), "currency": c["currency"], "issued_date": c["issue_date"], "reason": c.get("reason", "")}
            result["credit_memos"].append(Credit.model_validate(row).model_dump(mode="json"))
        for r in self._sources["exchange_rates"]:
            result["exchange_rates"].append(Rate.model_validate({"from_ccy": r["from_currency"], "to_ccy": r["to_currency"], "on_date": r["date"], "rate": r["rate"]}).model_dump(mode="json"))
        return {"records": result, "provenance": provenance, "source_fingerprint": self.fingerprint, "assumptions": copy.deepcopy(assumptions)}
