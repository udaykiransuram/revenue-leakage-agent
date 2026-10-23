"""Read-only billing totals across explicit source-plan amendment links."""
from decimal import Decimal

from .challenge_data import ChallengeDataset, LEDGERS, SourceDataError
from .domain import digest, money


def agreement_summary(state, plan_id):
    """Return one agreement summary, or None for a plan without linked versions.

    Totals cover all issued history, not an approved service-period audit. They
    never supply an expected charge, discrepancy or financial action authority.
    """
    dataset = ChallengeDataset({key: state[key] for key in LEDGERS})
    selected = dataset.load_plan(plan_id)
    plans = {plan_id: selected}
    pending = [plan_id]
    errors = []
    while pending:
        current = plans[pending.pop()]
        neighbors = [p['plan_id'] for p in state['billing_plans']
                     if p.get('amends') == current['plan_id']]
        if current.get('amends'):
            neighbors.append(current['amends'])
        for pid in neighbors:
            if pid in plans:
                continue
            try:
                plans[pid] = dataset.load_plan(pid)
                pending.append(pid)
            except SourceDataError as exc:
                errors.append(f'{pid}: {exc}')
    if len(plans) == 1 and not errors and not selected.get('amends'):
        return None

    for plan in plans.values():
        visited = set()
        current = plan
        while current:
            pid = current['plan_id']
            if pid in visited:
                errors.append('Cyclic amendment links block agreement totals')
                break
            visited.add(pid)
            current = plans.get(current.get('amends'))
        if plan.get('customer_name') != selected.get('customer_name'):
            errors.append('Linked plans have conflicting customers')
    ordered = sorted(plans.values(), key=lambda p: (p.get('start_date', ''), p['plan_id']))
    roots = [p['plan_id'] for p in ordered if not p.get('amends')]
    root_id = roots[0] if len(roots) == 1 else None
    currency = plans[root_id]['currency'] if root_id else selected['currency']
    refs = [f"billing_plans:{p['plan_id']}" for p in ordered]

    def unique(rows, key, scope):
        """Identical source repeats count once; conflicting IDs block totals."""
        result = {}
        ids = {r.get(key) for r in rows if scope(r)}
        for row in rows:
            rid = row.get(key)
            if rid not in ids:
                continue
            if not rid or (rid in result and result[rid] != row):
                errors.append(f'Missing or conflicting {key}: {rid}')
            result[rid] = row
        return list(result.values())

    invoices = unique(state['invoices'], 'invoice_id', lambda r: r.get('plan_id') in plans)
    invoice_ids = {i.get('invoice_id') for i in invoices}
    credits = unique(state['credit_memos'], 'memo_id',
                     lambda r: r.get('invoice_id') in invoice_ids or r.get('plan_id') in plans)
    refs.extend(f"credit_memos:{c.get('memo_id')}" for c in credits)
    gross = Decimal(0)
    credited = Decimal(0)
    gross_by_currency, credits_by_currency = {}, {}
    breakdown = {pid: {'plan_id': pid, 'currency': currency, 'invoice_ids': [], 'credit_ids': [],
                       'gross_billed': Decimal(0), 'credits': Decimal(0)} for pid in plans}
    details = []
    workings = []
    excluded = []

    def convert(row, field):
        value = Decimal(str(row[field]))
        if not value.is_finite() or value < 0:
            raise SourceDataError('Amounts must be finite and nonnegative')
        return dataset.fx_convert(value, row['currency'], currency, row['issue_date'])

    for invoice in invoices:
        iid = invoice.get('invoice_id')
        refs.append(f'invoices:{iid}')
        attached = [c for c in credits if c.get('invoice_id') == iid]
        if invoice.get('status') == 'void':
            excluded.append(iid)
            continue
        pid = invoice.get('plan_id')
        if pid not in plans:
            errors.append(f'{iid}: conflicting invoice plan reference')
            continue
        part = breakdown[pid]
        part['invoice_ids'].append(iid)
        details.append({k: invoice.get(k) for k in
                        ('invoice_id', 'plan_id', 'amount_invoiced', 'currency', 'status', 'issue_date')})
        try:
            converted = convert(invoice, 'amount_invoiced')
            value = Decimal(converted['amount'])
            gross += value
            part['gross_billed'] += value
            refs.extend(converted['source_refs'])
            workings.append(f"{iid}: {converted['calculation']} = {value} {currency}")
            ccy = invoice['currency']
            gross_by_currency[ccy] = gross_by_currency.get(ccy, Decimal(0)) + Decimal(str(invoice['amount_invoiced']))
            invoice_credits = Decimal(0)
            for credit in attached:
                mid = credit.get('memo_id')
                refs.append(f'credit_memos:{mid}')
                if credit.get('plan_id', pid) != pid:
                    raise SourceDataError('Credit plan reference conflicts with linked invoice')
                adjusted = convert(credit, 'amount')
                credit_value = Decimal(adjusted['amount'])
                invoice_credits += credit_value
                credited += credit_value
                part['credits'] += credit_value
                part['credit_ids'].append(mid)
                refs.extend(adjusted['source_refs'])
                workings.append(f"{mid}: subtract {adjusted['calculation']} = {credit_value} {currency}")
                ccy = credit['currency']
                credits_by_currency[ccy] = credits_by_currency.get(ccy, Decimal(0)) + Decimal(str(credit['amount']))
            if invoice_credits > value:
                raise SourceDataError('Existing credits exceed the converted invoice balance')
        except (ValueError, KeyError, ArithmeticError) as exc:
            errors.append(f'{iid}: {exc}')
    for credit in credits:
        if credit.get('invoice_id') not in invoice_ids:
            refs.append(f"credit_memos:{credit.get('memo_id')}")
            errors.append(f"{credit.get('memo_id')}: credit has no invoice in this agreement")

    def total(value):
        return None if errors else str(money(value, currency))

    for part in breakdown.values():
        part['net_billed'] = total(part['gross_billed'] - part['credits'])
        part['gross_billed'], part['credits'] = total(part['gross_billed']), total(part['credits'])
    refs = list(dict.fromkeys(refs))
    return {
        'id': 'AGR-' + digest([root_id, sorted(plans)])[:16],
        'kind': 'agreement_billing_summary', 'plan_id': plan_id,
        'root_plan_id': root_id, 'related_plan_ids': [p['plan_id'] for p in ordered],
        'versions': [{k: p.get(k) for k in ('plan_id', 'amends', 'start_date', 'total_value', 'currency')} for p in ordered],
        'scope': 'All issued invoice history across explicitly linked plan versions',
        'currency': currency, 'period': None, 'expected': None, 'discrepancy': None,
        'actual': total(gross - credited), 'gross_billed': total(gross),
        'credits': total(credited), 'net_billed': total(gross - credited),
        'validation': 'unresolved' if errors else 'conditional',
        'supported_corrections': [], 'source_refs': refs,
        'source_fingerprint': dataset.fingerprint,
        'invoices': details, 'existing_credits': credits, 'excluded_void_invoice_ids': excluded,
        'by_plan': [breakdown[p['plan_id']] for p in ordered],
        'gross_by_source_currency': None if errors else {c: str(money(v, c)) for c, v in gross_by_currency.items()},
        'credits_by_source_currency': None if errors else {c: str(money(v, c)) for c, v in credits_by_currency.items()},
        'errors': list(dict.fromkeys(errors)),
        'balance_meaning': 'Net billed after credits, not cash collected or an outstanding balance. Paid and unpaid invoices both count.',
        'assumptions': ['Invoice and credit FX valued separately on their issue dates using exact supplied rates'],
        'calculation': '; '.join(workings) + '. ' + ('Totals withheld: ' + '; '.join(dict.fromkeys(errors)) if errors else
            f'Agreement gross {total(gross)} − credits {total(credited)} = net billed {total(gross - credited)} {currency}. '
            'Count each invoice and credit once. Expected charges require each version’s effective service periods; contract values are not added.'),
    }
