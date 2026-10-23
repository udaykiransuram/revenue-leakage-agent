"""Explicit scripted demo transport; not a substitute for live-model validation."""
import json
import re
import uuid
from decimal import Decimal
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def call(name, args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "demo-"+uuid.uuid4().hex}])

def respond(state, toolbox):
    messages = state["messages"]
    last = messages[-1]
    if isinstance(last, ToolMessage):
        result = json.loads(last.content)
        if isinstance(result, dict) and result.get("error"):
            return AIMessage(content="I could not complete that request: " + result["error"])
        if last.name == "select_period":
            plan_id = result["selected_plan"]
            period = result["selected_period"]
            f = next(f for f in toolbox.reconcile_plan(plan_id) if f["period"] == period)
            return call("propose_make_good_invoice", {"plan_id": plan_id, "amount": str(abs(Decimal(f["discrepancy"]))), "reason": f"Recover validated underbilling for {period[0]} to {period[1]}"})
        if last.name == "reconcile_plan":
            issues = [r for r in result if r["kind"] != "matched"]
            lines = [f"{r['id']}: {r['kind'].replace('_',' ')} · {r['discrepancy'] or 'unresolved'} {r['currency']} · {' to '.join(r['period']) if r['period'] else 'contract data'} · {r['validation']}" for r in issues]
            return AIMessage(content="\n\n".join(lines) if lines else "All explicit service periods reconcile. No remaining billing discrepancy was found.")
        if isinstance(result, dict) and result.get("key"):
            return AIMessage(content=f"Proposal {result['key']} is ready for review. It has not changed billing records. Inspect the preview below.")
        if last.name == "load_plan":
            return AIMessage(content=f"Plan {result['id']} bills {result['amount']} {result['currency']} {result['cadence']}. Its service window is {result['start_date']} through {result['end_date']} (exclusive).")
        return AIMessage(content=json.dumps(result, indent=2))
    text = next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), "")
    low = text.lower()
    plan_match = re.search(r"\bP-[A-Za-z0-9-]+", text, re.I)
    plan_id = plan_match.group(0).upper() if plan_match else state.get("selected_plan")
    if "portfolio" in low or "all plans" in low:
        return call("investigate_portfolio", {})
    if "list" in low and "plan" in low:
        return call("list_plans", {})
    if "rollback" in low or "roll back" in low or "undo" in low:
        history = toolbox.action_history()
        active = [h for h in history if h["kind"] != "rollback" and not h["rolled_back"]]
        if active:
            return call("request_rollback", {"action_id": active[-1]["action_id"]})
        return AIMessage(content="There is no eligible applied action in this conversation to roll back.")
    if not plan_id:
        return AIMessage(content="Which plan should I investigate? Try: Check P-12345, or list plans.")
    if any(w in low for w in ("revise", "instead", "change proposal")):
        pending = toolbox.actions.pending()
        value = re.search(r"\b(?:to|for)\s+([0-9][0-9,]*(?:\.\d+)?)", text)
        if len(pending) == 1 and value:
            return call("revise_proposal", {"key": pending[0]["key"], "amount": value.group(1).replace(",", ""), "reason": text})
        return AIMessage(content="Identify one pending proposal and the revised amount, such as: revise to 5000.")
    if "amend" in low or "simulate" in low:
        value = re.search(r"\bto\s+([0-9][0-9,]*(?:\.\d+)?)", text)
        effective = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if value and effective:
            return call("simulate_amendment" if "simulate" in low else "propose_plan_amendment", {"plan_id": plan_id, "change_set": {"amount": value.group(1).replace(",", ""), "effective_date": effective.group(), "reason": text}})
        return AIMessage(content="Provide explicit new terms and an effective date, for example: amend P-12345 to 12000 effective 2024-11-01.")
    if any(w in low for w in ("propose", "make-good", "make good", "credit", "recover")):
        findings = toolbox.reconcile_plan(plan_id)
        kind = "credit" if "credit" in low else "make_good"
        candidates = [f for f in findings if kind in f["supported_corrections"]]
        month_names = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
        months = [i+1 for i, name in enumerate(month_names) if name in low]
        if months:
            candidates = [f for f in candidates if int(f["period"][0][5:7]) in months]
        if len(candidates) != 1:
            return AIMessage(content="Choose one service period with a validated discrepancy before drafting a correction.")
        f = candidates[0]
        toolbox.period, toolbox.plan_id = f["period"], plan_id
        if kind == "make_good":
            # Period selection is persisted as a separate factual tool result first.
            if state.get("selected_period") != f["period"]:
                return call("select_period", {"plan_id": plan_id, "period_start": f["period"][0], "period_end": f["period"][1]})
            return call("propose_make_good_invoice", {"plan_id": plan_id, "amount": str(abs(Decimal(f["discrepancy"]))), "reason": f"Recover validated underbilling for {f['period'][0]} to {f['period'][1]}"})
        invs = [r for r in toolbox.query_invoices({"plan_id": plan_id}) if r["period_start"] == f["period"][0] and r["currency"] == f["currency"]]
        if not invs:
            return AIMessage(content="No invoice supports a credit in the plan currency; manual allocation is required.")
        return call("propose_credit_memo", {"invoice_id": invs[-1]["id"], "amount": str(abs(Decimal(f["discrepancy"]))), "reason": "Correct validated overbilling after existing credits"})
    if any(w in low for w in ("currency", "cadence", "contract", "show plan")):
        return call("load_plan", {"plan_id": plan_id})
    if "invoice" in low:
        return call("query_invoices", {"filters": {"plan_id": plan_id}})
    if "history" in low or "receipt" in low:
        return call("action_history", {})
    return call("reconcile_plan", {"plan_id": plan_id})
