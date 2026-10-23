"""Suggestions expose plausible customer/date matches without financial effects."""
import copy
import json
from pathlib import Path

import pytest

from leakage.challenge_data import LEDGERS
from leakage.orphans import orphan_invoice_suggestions


@pytest.fixture
def source():
    root = Path(__file__).resolve().parents[1] / 'data'
    return {key: json.loads((root / f'{key}.json').read_text()) for key in LEDGERS}


def orphan(source):
    return next(i for i in source['invoices'] if i['invoice_id'] == 'I-9202')


@pytest.mark.parametrize('pid', ['C-1007', 'C-1007-A1', None])
def test_globex_orphan_suggests_amended_plan_from_either_version(source, pid):
    before = copy.deepcopy(source)
    finding, = orphan_invoice_suggestions(source, pid)
    assert finding['invoice_id'] == 'I-9202'
    assert finding['invoice_amount'] == 15000
    assert finding['currency'] == 'USD'
    assert finding['original_invoice']['status'] == 'paid'
    assert finding['original_invoice']['issue_date'] == '2025-12-10'
    assert finding['candidate_plan_ids'] == ['C-1007-A1']
    assert finding['matching_customer_plan_ids'] == ['C-1007', 'C-1007-A1']
    assert not finding['included_in_totals']
    assert finding['actual'] is finding['discrepancy'] is None
    assert finding['supported_corrections'] == []
    assert 'excluded' in finding['calculation']
    assert 'unconfirmed' in finding['calculation']
    assert {'invoices:I-9202', 'billing_plans:C-1007-A1'} <= set(finding['source_refs'])
    assert source == before


@pytest.mark.parametrize('issued,expected', [('2025-06-10', ['C-1007']),
                                           ('2025-07-01', ['C-1007-A1']),
                                           ('2025-02-01', []), ('invalid', [])])
def test_issue_date_suggests_version_but_does_not_supply_service_period(source, issued, expected):
    orphan(source)['issue_date'] = issued
    finding, = orphan_invoice_suggestions(source, 'C-1007')
    assert finding['candidate_plan_ids'] == expected
    assert finding['period'] is None
    assert finding['validation'] == 'unresolved'


def test_unrelated_and_void_invoices_are_not_suggested(source):
    assert orphan_invoice_suggestions(source, 'C-1001') == []
    orphan(source)['status'] = 'void'
    assert orphan_invoice_suggestions(source, 'C-1007') == []


@pytest.mark.parametrize('reference', ['', None, 'UNKNOWN'])
def test_missing_or_invalid_reference_is_suggested_with_normalized_customer_name(source, reference):
    orphan(source)['plan_id'] = reference
    orphan(source)['customer_name'] = '  GLOBEX   Ltd '
    finding, = orphan_invoice_suggestions(source, 'C-1007')
    assert finding['candidate_plan_ids'] == ['C-1007-A1']


def test_known_invoice_assignment_is_not_overridden(source):
    orphan(source)['plan_id'] = 'C-1001'
    assert orphan_invoice_suggestions(source, 'C-1007') == []


def test_current_confirmed_term_end_limits_suggestion_but_stale_context_does_not(source):
    from leakage.domain import digest
    source['billing_contexts'] = {'C-1007-A1': {
        'status': 'approved', 'term_end': '2025-10-01',
        'source_fingerprint': digest({k: source[k] for k in LEDGERS}),
    }}
    finding, = orphan_invoice_suggestions(source, 'C-1007')
    assert finding['candidate_plan_ids'] == []
    assert finding['date_evidence'][1]['confirmed_term_end'] == '2025-10-01'
    source['billing_contexts']['C-1007-A1']['source_fingerprint'] = 'stale'
    finding, = orphan_invoice_suggestions(source, 'C-1007')
    assert finding['candidate_plan_ids'] == ['C-1007-A1']
    assert finding['date_evidence'][1]['confirmed_term_end'] is None


def test_multiple_customer_agreements_remain_ambiguous(source):
    source['billing_plans'].append({**source['billing_plans'][1], 'plan_id': 'C-OTHER'})
    finding, = orphan_invoice_suggestions(source, 'C-1007')
    assert set(finding['candidate_plan_ids']) == {'C-1007-A1', 'C-OTHER'}
    assert finding['ambiguous']
    assert 'multiple' in finding['summary']


def test_identical_repeats_have_one_suggestion_conflicting_ids_block_plan_hint(source):
    source['invoices'].append(copy.deepcopy(orphan(source)))
    finding, = orphan_invoice_suggestions(source)
    assert finding['candidate_plan_ids'] == ['C-1007-A1']
    source['invoices'][-1]['amount_invoiced'] = 16000
    finding, = orphan_invoice_suggestions(source)
    assert finding['candidate_plan_ids'] == []
    assert finding['record_issues']


def test_chat_and_evidence_show_suggestion_without_changing_total_or_records(tmp_path, monkeypatch):
    from langchain_core.messages import AIMessage
    from leakage.agent import Agent
    from leakage.tools import Toolbox
    from test_source_conversation import Model, call, case

    root = case(tmp_path, monkeypatch)
    agent = Agent(root, mode='live', model=Model([
        call('reconcile_plan', {'plan_id': 'C-1007-A1'}),
        AIMessage(content='I-9202 is a possible USD 15,000 match requiring contract confirmation.'),
    ]))
    thread = agent.store.new_conversation()
    before = copy.deepcopy(agent.store.read())
    result = agent.send(thread, 'Check Globex including potentially related orphan invoices')
    finding, = [f for f in result['findings'] if f['kind'] == 'possible_orphan_invoice_match']
    total, = [f for f in result['findings'] if f['kind'] == 'agreement_billing_summary']
    assert total['net_billed'] == '45000.00'
    assert 'invoices:I-9202' not in total['source_refs']
    assert agent.store.read()['invoices'] == before['invoices']
    assert agent.store.read()['proposals'] == {}
    assert agent.store.read().get('billing_contexts', {}) == before.get('billing_contexts', {})
    box = Toolbox(agent.store, thread)
    assert box.inspect_evidence(finding['id'])['candidate_plan_ids'] == ['C-1007-A1']
    suggestions = [f for group in box.investigate_portfolio().values() for f in group
                   if f['kind'] == 'possible_orphan_invoice_match']
    assert len(suggestions) == 1
