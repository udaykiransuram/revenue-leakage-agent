"""Customer/date suggestions for unassigned invoices; never financial authority."""
from datetime import date

from .challenge_data import ChallengeDataset, LEDGERS
from .domain import digest, money


def orphan_invoice_suggestions(state, plan_id=None):
    """Suggest customer matches and plan versions without changing records/totals.

    Issue dates rank versions, never establish service periods or term coverage.
    """
    dataset = ChallengeDataset({key: state[key] for key in LEDGERS})
    plans = state['billing_plans']
    known = {p['plan_id'] for p in plans}

    def name(value):
        return ' '.join(str(value or '').split()).casefold()

    selected_customer = name(dataset.load_plan(plan_id).get('customer_name')) if plan_id else None
    seen = set()
    findings = []
    for invoice in state['invoices']:
        if invoice.get('plan_id') in known or invoice.get('status') == 'void':
            continue
        customer = name(invoice.get('customer_name'))
        if not customer or (plan_id and customer != selected_customer):
            continue
        matches = [p for p in plans if name(p.get('customer_name')) == customer]
        if not matches:
            continue
        iid = invoice.get('invoice_id')
        key = iid or digest(invoice)
        if key in seen:
            continue
        seen.add(key)
        variants = {digest(i) for i in state['invoices'] if i.get('invoice_id') == iid}
        blockers = []
        if not iid or len(variants) != 1:
            blockers.append('Missing or conflicting invoice ID requires record review')
        candidates, dates = [], []
        try:
            issued = date.fromisoformat(invoice['issue_date'])
        except (ValueError, KeyError, TypeError):
            issued = None
            blockers.append('Invoice issue date is missing or invalid')
        for plan in sorted(matches, key=lambda p: (p.get('start_date', ''), p['plan_id'])):
            pid = plan['plan_id']
            try:
                start = date.fromisoformat(plan['start_date'])
                child_starts = [date.fromisoformat(p['start_date']) for p in plans if p.get('amends') == pid]
                superseded = min(child_starts) if child_starts else None
                saved = state.get('billing_contexts', {}).get(pid, {})
                context = saved.get('context', saved)
                approved = saved.get('approved') is True or saved.get('status') in {'approved', 'Approved', 'Applied'}
                term_end = (date.fromisoformat(context['term_end']) if approved and
                            context.get('source_fingerprint') == dataset.fingerprint else None)
                plausible = (issued is not None and start <= issued and
                             (superseded is None or issued < superseded) and
                             (term_end is None or issued < term_end))
                dates.append({'plan_id': pid, 'start_date': str(start),
                              'superseded_from': str(superseded) if superseded else None,
                              'confirmed_term_end': str(term_end) if term_end else None,
                              'issue_date_within_version_window': plausible})
                if plausible:
                    candidates.append(pid)
            except (ValueError, KeyError, TypeError):
                blockers.append(f'{pid}: plan effective dates require review')
        if blockers:
            candidates = []
        if not candidates:
            reason = 'Customer matches, but the date or record evidence does not identify a candidate plan.'
        elif len(candidates) > 1:
            reason = 'Customer and issue date match multiple possible plans; confirm which agreement applies.'
        else:
            reason = f'Customer matches and the issue date fits the recorded effective-date window for {candidates[0]}.'
        try:
            displayed_amount = f"{money(invoice['amount_invoiced'], invoice['currency'])} {invoice['currency']}"
        except (ValueError, KeyError, ArithmeticError):
            displayed_amount = 'an unverified amount'
        calculation = (f"Invoice {iid}: {displayed_amount}, issued {invoice.get('issue_date')}, "
                       f"status {invoice.get('status')}. Possible customer match: {invoice.get('customer_name')}. "
                       f"{reason} Confirm the contract reference and service period before including this invoice in agreement totals. "
                       'Issue date is not a service date; the invoice service period and renewal applicability remain unconfirmed. '
                       'This invoice is excluded from the linked agreement total.')
        findings.append({
            'id': 'ORPH-' + digest([key, customer])[:16],
            'kind': 'possible_orphan_invoice_match', 'plan_id': plan_id,
            'invoice_id': iid, 'customer_name': invoice.get('customer_name'),
            'original_invoice': dict(invoice), 'currency': invoice.get('currency'),
            'invoice_amount': invoice.get('amount_invoiced'),
            'matching_customer_plan_ids': sorted(p['plan_id'] for p in matches),
            'candidate_plan_ids': candidates, 'date_evidence': dates,
            'ambiguous': len(candidates) != 1, 'record_issues': blockers,
            'validation': 'unresolved', 'included_in_totals': False,
            'period': None, 'expected': None, 'actual': None, 'discrepancy': None,
            'supported_corrections': [], 'summary': reason, 'calculation': calculation,
            'source_refs': [f'invoices:{iid}'] + [f"billing_plans:{p['plan_id']}" for p in matches],
            'source_fingerprint': dataset.fingerprint,
            'required_context': ['Confirmed contract reference', 'Invoice service-period mapping', 'Contract term or renewal coverage'],
            'assumptions': ['Customer names compared ignoring case and repeated whitespace',
                            'Issue date used only to suggest a plan version; no service period inferred'],
        })
    return findings
