"""Pure source-native reconciliation of human-confirmed commercial context.

Issue dates never stand in for service dates. Approval authorizes assumptions,
not source mutation. Every monetary conclusion is reproducible from its evidence.
"""
from __future__ import annotations

import copy
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .challenge_data import ChallengeDataset, LEDGERS, SourceDataError
from .domain import add_months, digest, money


def source_fingerprint(state, plan_id=None):
    # Deliberately conservative: any source ledger change invalidates context.
    return digest({key: state[key] for key in LEDGERS})


def _date(value):
    if not isinstance(value, str):
        raise SourceDataError("Dates must be explicit ISO calendar dates")
    parsed = date.fromisoformat(value)
    if str(parsed) != value:
        raise SourceDataError("Dates must be explicit ISO calendar dates")
    return parsed


def _amount(value, currency, *, positive=False):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or (positive and amount == 0):
            raise ValueError("invalid")
        if amount != money(amount, currency):
            raise ValueError("precision")
        return money(amount, currency)
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise SourceDataError("Amount must be finite, nonnegative and exact at currency precision") from exc


def derive_proposal_term(plan, context):
    """Normalize a proposal-only term; any source-note interpretation stays reviewable."""
    proposed = copy.deepcopy(context)
    months = plan.get("term_months")
    source = "structured contract duration"
    if not isinstance(months, int) or isinstance(months, bool) or not 1 <= months <= 1200:
        matches = set(re.findall(r"\b([1-9][0-9]{0,2})[ -]months?\b", str(plan.get("notes", "")), flags=re.I))
        if len(matches) != 1:
            return proposed, []
        months = int(next(iter(matches)))
        source = "stated duration in contract notes (proposed interpretation)"
    end = add_months(_date(plan["start_date"]), months)
    supplied = proposed.get("term_end")
    # Fix the inclusive-last-day representation only for full periods. Keep an
    # explicitly prorated or genuinely different contract term for human review.
    if supplied and (supplied != str(end - timedelta(days=1)) or proposed.get("proration") != "none"):
        return proposed, []
    proposed["term_end"] = str(end)
    return proposed, [f"Contract end derived from {months} months starting {plan['start_date']}: {end} exclusive. Source: {source}. Review this interpretation with the proposal."]


def source_schedule(plan, context):
    """Use source start as an explicit billing anchor, including non-calendar dates."""
    origin, term_end = _date(plan['start_date']), _date(context['term_end'])
    cadence = str(plan.get('cadence', '')).lower()
    if cadence not in {'monthly', 'quarterly', 'annual'}:
        raise SourceDataError("Unsupported cadence requires an explicit calculation adapter")
    step = {'monthly': 1, 'quarterly': 3, 'annual': 12}[cadence]
    amount = _amount(context['period_amount'], plan['currency'])
    result = []
    n = 0
    while True:
        start = add_months(origin, n * step)
        if start >= term_end:
            break
        full_end = add_months(origin, (n + 1) * step)
        end = min(term_end, full_end)
        if end != full_end and context['proration'] != 'actual_days':
            raise SourceDataError("Partial billing period requires explicit actual_days proration. "
                                  + f"term_end={term_end} is exclusive and truncates the period starting {start}; its full exclusive end is {full_end}. "
                                  + "Confirm the intended contract end. Do not switch proration merely to bypass validation; use actual_days only for an intended partial term.")
        days, total = (end - start).days, (full_end - start).days
        expected = money(amount * Decimal(days) / Decimal(total), plan['currency'])
        result.append({'period_start': str(start), 'period_end': str(end),
                       'expected': str(expected), 'calculation': f'{amount} × {days}/{total}'})
        n += 1
        if n > 1200:
            raise SourceDataError("Confirmed term exceeds 1200 billing periods")
    return result


def validate_context(state, plan_id, context):
    """Validate an unapproved proposal; no defaults invent commercial facts."""
    if not isinstance(context, dict):
        raise SourceDataError("Billing context must be an object")
    dataset = ChallengeDataset({key: state[key] for key in LEDGERS})
    plan = dataset.load_plan(plan_id)
    required = {'period_amount', 'term_end', 'invoice_periods', 'proration',
                'fx_date_policy', 'entitlement_scope'}
    if not required <= context.keys():
        raise SourceDataError('Explicit context required: ' + ', '.join(sorted(required - context.keys())))
    if context['proration'] not in {'none', 'actual_days'}:
        raise SourceDataError("Proration must be none or actual_days")
    if context['fx_date_policy'] != 'invoice_issue_date':
        raise SourceDataError("Only explicitly confirmed invoice_issue_date FX valuation is supported")
    if context['entitlement_scope'] != 'all_plan_entitlements':
        raise SourceDataError("Partial or unknown entitlement scope cannot support financial conclusions")
    start, end = _date(plan['start_date']), _date(context['term_end'])
    if end <= start:
        raise SourceDataError("Confirmed term end must be after source start, exclusive")
    if context.get('reconcile_through') is not None:
        cutoff = _date(context['reconcile_through'])
        if not start < cutoff <= end:
            raise SourceDataError("Exclusive reconciliation cutoff must be after source start and no later than confirmed term end")
    child_starts = [_date(p['start_date']) for p in state['billing_plans'] if p.get('amends') == plan_id]
    if child_starts and end > min(child_starts):
        raise SourceDataError("Parent contract cannot extend beyond its source amendment effective date")
    mapping = context['invoice_periods']
    if not isinstance(mapping, dict):
        raise SourceDataError("Invoice service-period mapping must be an object")
    linked = [i for i in state['invoices'] if i.get('plan_id') == plan_id and i.get('status') != 'void']
    ids = [i.get('invoice_id') for i in linked]
    if len(ids) != len(set(ids)):
        raise SourceDataError("Duplicate invoice IDs require source repair before confirmation")
    if set(mapping) != set(ids):
        missing = sorted(set(ids) - set(mapping))
        unrelated = sorted(set(mapping) - set(ids))
        raise SourceDataError("Map EVERY nonvoid invoice linked to this plan, and no unrelated invoices. "
                              + "Missing existing invoice IDs: " + ", ".join(missing)
                              + ". Unknown or unrelated IDs to remove: " + ", ".join(unrelated)
                              + ". Map existing invoices to service periods; do not create an ID for the missing invoice.")
    periods = {}
    for invoice in linked:
        iid = invoice['invoice_id']
        if invoice.get('customer_name') != plan.get('customer_name'):
            raise SourceDataError("Invoice customer does not match its source plan")
        period = mapping[iid]
        if not isinstance(period, (list, tuple)) or len(period) != 2:
            raise SourceDataError("Each invoice needs an explicit [start, end] service period")
        left, right = _date(period[0]), _date(period[1])
        if not start <= left < right <= end:
            raise SourceDataError("Invoice service period must lie within confirmed term")
        periods[iid] = [str(left), str(right)]
        _amount(invoice['amount_invoiced'], invoice['currency'])
    result = copy.deepcopy(context)
    # Caller sets approval metadata only through the trusted UI action boundary.
    result['period_amount'] = str(_amount(context['period_amount'], plan['currency']))
    result['invoice_periods'] = periods
    result['source_fingerprint'] = source_fingerprint(state)
    result['plan_id'] = plan_id
    source_schedule(plan, result)
    return result


def _base_finding(state, plan_id, kind, **extra):
    plan = next((p for p in state['billing_plans'] if p.get('plan_id') == plan_id), {})
    row = {'kind': kind, 'plan_id': plan_id, 'currency': plan.get('currency'),
           'period': None, 'expected': None, 'actual': None, 'discrepancy': None,
           'validation': 'unresolved', 'source_refs': [f'billing_plans:{plan_id}'],
           'calculation': '', 'supported_corrections': [], 'assumptions': [],
           'source_fingerprint': source_fingerprint(state), 'snapshot_revision': state.get('revision', 0), **extra}
    row.setdefault('id', 'SRC-' + digest([plan_id, kind, row['period'], row['source_refs']])[:16])
    return row


def reconcile_source(state, plan_id):
    """Return source observations until context is approved, then exact findings."""
    dataset = ChallengeDataset({key: state[key] for key in LEDGERS})
    plan = dataset.load_plan(plan_id)
    saved = state.get('billing_contexts', {}).get(plan_id)
    # Accommodate storage wrappers without giving the model approval authority.
    context = saved.get('context', saved) if isinstance(saved, dict) else None
    approved = isinstance(saved, dict) and (saved.get('approved') is True or saved.get('status') in {'approved', 'Approved', 'Applied'})
    if not approved:
        return [_base_finding(state, plan_id, f['kind'], **{k: v for k, v in f.items() if k not in {'kind', 'plan_id'}})
                for f in dataset.analyze(plan_id)]
    try:
        if context.get('source_fingerprint') != source_fingerprint(state):
            raise SourceDataError("Source records changed; confirm refreshed billing context before further financial conclusions")
        validated = validate_context(state, plan_id, context)
        schedule = source_schedule(plan, validated)
        cutoff = validated.get('reconcile_through', validated['term_end'])
        schedule = [period for period in schedule if period['period_end'] <= cutoff]
    except (ValueError, KeyError, TypeError) as exc:
        return [_base_finding(state, plan_id, 'invalid_or_stale_context', calculation=str(exc))]
    invoices = [i for i in state['invoices'] if i.get('plan_id') == plan_id and i.get('status') != 'void']
    mapping = validated['invoice_periods']
    expected_periods = [[p['period_start'], p['period_end']] for p in source_schedule(plan, validated)]
    outside = [i for i in invoices if mapping[i['invoice_id']] not in expected_periods]
    results = []
    for invoice in outside:
        results.append(_base_finding(state, plan_id, 'suspected_overlap_or_unmatched_period',
            period=mapping[invoice['invoice_id']], source_refs=[f"invoices:{invoice['invoice_id']}"],
            calculation='Mapped period does not match an anchored billing period; explicit allocation is required'))
    for period in schedule:
        key = [period['period_start'], period['period_end']]
        selected = [i for i in invoices if mapping[i['invoice_id']] == key]
        refs = [f'billing_plans:{plan_id}', f'billing_contexts:{plan_id}']
        errors, workings = [], []
        if any(mapping[i['invoice_id']][0] < key[1] and mapping[i['invoice_id']][1] > key[0] for i in outside):
            errors.append('Overlapping unallocated invoice blocks a monetary conclusion')
        actual = Decimal(0)
        for invoice in selected:
            iid = invoice['invoice_id']
            refs.append(f'invoices:{iid}')
            try:
                gross = dataset.fx_convert(_amount(invoice['amount_invoiced'], invoice['currency']), invoice['currency'], plan['currency'], invoice['issue_date'])
                net = Decimal(gross['amount'])
                refs.extend(gross['source_refs'])
                detail = f"{iid}: {gross['calculation']} = {gross['amount']} {plan['currency']}"
                credits = [c for c in state['credit_memos'] if c.get('invoice_id') == iid]
                credit_ids = [c.get('memo_id') for c in credits]
                if len(credit_ids) != len(set(credit_ids)):
                    raise SourceDataError('Duplicate credit IDs block reconciliation')
                for credit in credits:
                    if credit.get('plan_id', plan_id) != plan_id:
                        raise SourceDataError('Credit plan reference conflicts with linked invoice')
                    converted = dataset.fx_convert(_amount(credit['amount'], credit['currency'], positive=True), credit['currency'], plan['currency'], credit['issue_date'])
                    net -= Decimal(converted['amount'])
                    refs.extend([f"credit_memos:{credit['memo_id']}"] + converted['source_refs'])
                    detail += f" − {credit['memo_id']}: {converted['calculation']} = {converted['amount']} {plan['currency']}"
                if net < 0:
                    raise SourceDataError('Existing credits exceed the converted invoice balance')
                actual += net
                workings.append(detail)
            except (ValueError, KeyError, ArithmeticError) as exc:
                errors.append(str(exc))
        expected = Decimal(period['expected'])
        delta = money(expected - actual, plan['currency'])
        kind = 'unresolved' if errors else ('missing_period' if not selected and delta else 'underbilling' if delta > 0 else 'overbilling' if delta < 0 else 'matched')
        results.append(_base_finding(state, plan_id, kind, period=key,
            expected=str(expected), actual=None if errors else str(money(actual, plan['currency'])),
            discrepancy=None if errors else str(delta), validation='unresolved' if errors else 'validated',
            source_refs=list(dict.fromkeys(refs)),
            context_fingerprint=digest(saved),
            calculation=f"Expected: {period['calculation']} = {expected} {plan['currency']}. Actual: " + ('; '.join(workings) or 'no mapped invoices') + '. ' + '; '.join(errors),
            supported_corrections=[] if errors or not delta else ['make_good' if delta > 0 else 'credit'],
            assumptions=['Human-approved per-period charge, exclusive term end and invoice service mapping', 'All plan entitlements included', 'Invoice and credit FX valued separately on their issue dates']))
    return results
