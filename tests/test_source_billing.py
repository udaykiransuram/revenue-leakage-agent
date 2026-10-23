"""Focused evidence-gating and actual challenge-data reconciliation checks."""
import copy
import json
from pathlib import Path
import pytest

from leakage.source_billing import validate_context, reconcile_source, source_fingerprint
from leakage.challenge_data import LEDGERS, SourceDataError

@pytest.fixture
def source():
    root = Path(__file__).resolve().parents[1] / 'data'
    return {**{key: json.loads((root / f'{key}.json').read_text()) for key in LEDGERS}, 'revision': 0}


def acme_context(source):
    # These mappings are explicit human-confirmed fixture answers, never inferred by implementation.
    periods = {}
    for invoice in source['invoices']:
        if invoice.get('plan_id') == 'C-1001':
            month = int(invoice['issue_date'][5:7])
            periods[invoice['invoice_id']] = [f'2025-{month:02}-01', f'2025-{month+1:02}-01']
    return dict(period_amount='8000.00', term_end='2025-11-01', invoice_periods=periods,
                proration='none', fx_date_policy='invoice_issue_date', entitlement_scope='all_plan_entitlements')


def approve(source, pid, context):
    result = validate_context(source, pid, context)
    result['status'] = 'approved'
    source.setdefault('billing_contexts', {})[pid] = result
    return result


def test_no_context_preserves_candidates_without_financial_authority(source):
    before = copy.deepcopy(source)
    findings = reconcile_source(source, 'C-1001')
    assert any(f['kind'] == 'candidate_missing_issue_month' for f in findings)
    assert all(not f['supported_corrections'] for f in findings)
    assert all({'id', 'kind', 'currency', 'expected', 'actual', 'discrepancy', 'period', 'calculation'} <= f.keys() for f in findings)
    assert source == before


def test_plan_switch_returns_own_contract_facts_and_parent_amendment(source):
    acme = reconcile_source(source, 'C-1001')
    globex = reconcile_source(source, 'C-1007')
    acme_facts = next(f['source_plan'] for f in acme if f['kind'] == 'plan_facts')
    facts = next(f['source_plan'] for f in globex if f['kind'] == 'plan_facts')
    assert acme_facts['total_value'] == 96000
    assert facts['plan_id'] == 'C-1007'
    assert facts['total_value'] == 90000
    assert facts['cadence'] == 'Quarterly'
    amendment = next(f for f in globex if f['kind'] == 'superseded_by_amendment')
    assert amendment['amendment_id'] == 'C-1007-A1'
    assert amendment['effective_date'] == '2025-07-01'
    assert all(not f['supported_corrections'] for f in globex)


def test_acme_confirmed_mapping_finds_only_september(source):
    approve(source, 'C-1001', acme_context(source))
    findings = reconcile_source(source, 'C-1001')
    differences = [f for f in findings if f['supported_corrections']]
    assert len(differences) == 1
    assert differences[0]['period'] == ['2025-09-01', '2025-10-01']
    assert differences[0]['discrepancy'] == '8000.00'
    assert differences[0]['supported_corrections'] == ['make_good']


def test_cross_currency_credit_already_resolves_globex_overbilling(source):
    context = dict(period_amount='25000', term_end='2025-10-01', invoice_periods={'I-9123': ['2025-07-01', '2025-10-01']},
                   proration='none', fx_date_policy='invoice_issue_date', entitlement_scope='all_plan_entitlements')
    approve(source, 'C-1007-A1', context)
    finding, = reconcile_source(source, 'C-1007-A1')
    assert finding['kind'] == 'matched'
    assert finding['actual'] == '25000.00'
    assert finding['supported_corrections'] == []
    assert 'credit_memos:M-300' in finding['source_refs']
    assert 'exchange_rates[0]' in finding['source_refs']


@pytest.mark.parametrize('change', ['missing_mapping', 'partial_scope', 'unapproved', 'customer_mismatch'])
def test_confirmation_gates(source, change):
    context = acme_context(source)
    if change == 'missing_mapping': context['invoice_periods'].pop('I-9001')
    if change == 'partial_scope': context['entitlement_scope'] = 'Analytics'
    if change == 'customer_mismatch': source['invoices'][0]['customer_name'] = 'Other customer'
    if change == 'unapproved':
        source['billing_contexts'] = {'C-1001': validate_context(source, 'C-1001', context)}
        assert all(not f['supported_corrections'] for f in reconcile_source(source, 'C-1001'))
    else:
        with pytest.raises(SourceDataError): validate_context(source, 'C-1001', context)


def test_source_amendment_supersedes_parent_and_requires_explicit_proration(source):
    context = dict(period_amount='22500', term_end='2025-09-01', invoice_periods={'I-9110': ['2025-03-01','2025-06-01']},
                   proration='none', fx_date_policy='invoice_issue_date', entitlement_scope='all_plan_entitlements')
    with pytest.raises(SourceDataError, match='amendment'): validate_context(source, 'C-1007', context)
    context['term_end'] = '2025-07-01'
    with pytest.raises(SourceDataError, match='proration'): validate_context(source, 'C-1007', context)
    context['proration'] = 'actual_days'
    approve(source, 'C-1007', context)
    assert len(reconcile_source(source, 'C-1007')) == 2


def test_non_calendar_annual_anchor_is_explicit_source_date(source):
    context = dict(period_amount='120000', term_end='2026-01-15', invoice_periods={'I-9201': ['2025-01-15','2026-01-15']},
                   proration='none', fx_date_policy='invoice_issue_date', entitlement_scope='all_plan_entitlements')
    approve(source, 'C-1010', context)
    finding, = reconcile_source(source, 'C-1010')
    assert finding['period'] == ['2025-01-15','2026-01-15']
    assert finding['discrepancy'] == '20000.00'


def test_stale_context_and_missing_exact_fx_block_correction(source):
    context = dict(period_amount='25000', term_end='2025-10-01', invoice_periods={'I-9123': ['2025-07-01','2025-10-01']},
                   proration='none', fx_date_policy='invoice_issue_date', entitlement_scope='all_plan_entitlements')
    approve(source, 'C-1007-A1', context)
    source['exchange_rates'][0]['date'] = '2025-09-11'
    assert reconcile_source(source, 'C-1007-A1')[0]['kind'] == 'invalid_or_stale_context'
    approve(source, 'C-1007-A1', context)
    finding, = reconcile_source(source, 'C-1007-A1')
    assert finding['validation'] == 'unresolved'
    assert finding['actual'] is None
    assert not finding['supported_corrections']


def test_unallocated_overlap_blocks_amounts(source):
    context = acme_context(source)
    context['invoice_periods']['I-9001'] = ['2025-01-01','2025-03-01']
    approve(source, 'C-1001', context)
    findings = reconcile_source(source, 'C-1001')
    jan = next(f for f in findings if f['period'] == ['2025-01-01','2025-02-01'])
    assert jan['validation'] == 'unresolved'
    assert not jan['supported_corrections']


def test_reconciliation_does_not_mutate_source_or_context(source):
    approve(source, 'C-1001', acme_context(source))
    before = copy.deepcopy(source)
    fingerprint = source_fingerprint(source)
    reconcile_source(source, 'C-1001')
    assert source == before and fingerprint == source_fingerprint(source)


def test_audit_cutoff_preserves_full_contract_without_future_period_findings(source):
    context = acme_context(source)
    context['term_end'] = '2026-01-01'
    context['reconcile_through'] = '2025-11-01'
    approved = approve(source, 'C-1001', context)
    findings = reconcile_source(source, 'C-1001')
    assert approved['term_end'] == '2026-01-01'
    assert len(findings) == 10
    differences = [f for f in findings if f['supported_corrections']]
    assert [(f['period'], f['discrepancy']) for f in differences] == [(['2025-09-01', '2025-10-01'], '8000.00')]
    context['reconcile_through'] = '2025-10-15'
    approve(source, 'C-1001', context)
    assert all(f['period'][1] <= '2025-10-15' for f in reconcile_source(source, 'C-1001'))
    context['reconcile_through'] = '2026-02-01'
    with pytest.raises(SourceDataError, match='cutoff'):
        validate_context(source, 'C-1001', context)


def test_exclusive_year_end_error_explains_full_period_without_mutating(source):
    context = acme_context(source)
    context['term_end'] = '2025-12-31'
    before = copy.deepcopy(source)
    with pytest.raises(SourceDataError, match='full exclusive end is 2026-01-01'):
        validate_context(source, 'C-1001', context)
    assert source == before
    context['term_end'] = '2026-01-01'
    assert validate_context(source, 'C-1001', context)['term_end'] == '2026-01-01'


def test_proposed_term_derives_duration_and_fixes_inclusive_last_day():
    from leakage.source_billing import derive_proposal_term
    plan = {'start_date':'2025-01-01', 'notes':'12-month SaaS; expected ~8000 USD per month.'}
    for supplied in (None, '2025-12-31'):
        original = {'proration':'none', **({'term_end':supplied} if supplied else {})}
        derived, assumptions = derive_proposal_term(plan, original)
        assert derived['term_end'] == '2026-01-01' and assumptions
        assert original.get('term_end') == supplied
    explicit = {'term_end':'2025-11-01', 'proration':'none'}
    assert derive_proposal_term(plan, explicit) == (explicit, [])
    partial = {'term_end':'2025-12-31', 'proration':'actual_days'}
    assert derive_proposal_term(plan, partial) == (partial, [])
    assert derive_proposal_term({'start_date':'2025-01-01','notes':'Monthly subscription'}, {}) == ({}, [])
