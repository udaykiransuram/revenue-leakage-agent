"""Agreement-wide totals use explicit amendment links and unique financial records."""
import copy
import json
from pathlib import Path

import pytest

from leakage.agreement import agreement_summary
from leakage.challenge_data import LEDGERS


@pytest.fixture
def source():
    root = Path(__file__).resolve().parents[1] / 'data'
    return {key: json.loads((root / f'{key}.json').read_text()) for key in LEDGERS}


@pytest.mark.parametrize('pid', ['C-1007', 'C-1007-A1'])
def test_either_version_includes_parent_invoice_amendment_fx_and_credit(source, pid):
    before = copy.deepcopy(source)
    result = agreement_summary(source, pid)
    assert result['root_plan_id'] == 'C-1007'
    assert result['related_plan_ids'] == ['C-1007', 'C-1007-A1']
    assert result['gross_billed'] == '47000.00'
    assert result['credits'] == '2000.00'
    assert result['net_billed'] == result['actual'] == '45000.00'
    assert result['gross_by_source_currency'] == {'USD': '20000.00', 'EUR': '25000.00'}
    assert [p['net_billed'] for p in result['by_plan']] == ['20000.00', '25000.00']
    assert {'invoices:I-9110', 'invoices:I-9123', 'credit_memos:M-300', 'exchange_rates[0]'} <= set(result['source_refs'])
    assert result['supported_corrections'] == []
    assert result['expected'] is result['discrepancy'] is None
    assert source == before


def test_recursive_links_include_later_versions_but_not_customer_name_matches(source):
    source['billing_plans'].append({**source['billing_plans'][2], 'plan_id': 'C-1007-A2',
                                    'amends': 'C-1007-A1', 'start_date': '2025-10-01'})
    template = next(i for i in source['invoices'] if i['invoice_id'] == 'I-9110')
    source['invoices'].extend([
        {**template, 'invoice_id': 'I-LATER', 'plan_id': 'C-1007-A2', 'amount_invoiced': 3000, 'status': 'unpaid'},
        {**template, 'invoice_id': 'I-ORPHAN', 'plan_id': '', 'amount_invoiced': 9000},
    ])
    result = agreement_summary(source, 'C-1007-A1')
    assert result['related_plan_ids'] == ['C-1007', 'C-1007-A1', 'C-1007-A2']
    assert result['net_billed'] == '48000.00'
    assert 'invoices:I-ORPHAN' not in result['source_refs']
    assert next(i for i in result['invoices'] if i['invoice_id'] == 'I-LATER')['status'] == 'unpaid'


def test_identical_duplicates_count_once_and_void_invoices_are_excluded(source):
    invoice = next(i for i in source['invoices'] if i['invoice_id'] == 'I-9110')
    source['invoices'].extend([copy.deepcopy(invoice), {**invoice, 'invoice_id': 'I-VOID', 'status': 'void'}])
    source['credit_memos'].append(copy.deepcopy(source['credit_memos'][0]))
    result = agreement_summary(source, 'C-1007')
    assert result['net_billed'] == '45000.00'
    assert result['excluded_void_invoice_ids'] == ['I-VOID']
    assert len(result['invoices']) == 2
    assert len(result['existing_credits']) == 1


@pytest.mark.parametrize('problem', ['missing_fx', 'conflicting_invoice', 'conflicting_credit',
                                    'credit_plan', 'orphan_credit', 'cycle', 'missing_parent', 'customer'])
def test_incomplete_or_conflicting_evidence_withholds_combined_total(source, problem):
    if problem == 'missing_fx':
        source['exchange_rates'] = []
    elif problem == 'conflicting_invoice':
        invoice = next(i for i in source['invoices'] if i['invoice_id'] == 'I-9110')
        source['invoices'].append({**invoice, 'amount_invoiced': 30000})
    elif problem == 'conflicting_credit':
        source['credit_memos'].append({**source['credit_memos'][0], 'amount': 1000})
    elif problem == 'credit_plan':
        source['credit_memos'][0]['plan_id'] = 'C-1007'
    elif problem == 'orphan_credit':
        source['credit_memos'][0]['invoice_id'] = 'UNKNOWN'
    elif problem == 'cycle':
        source['billing_plans'][1]['amends'] = 'C-1007-A1'
    elif problem == 'missing_parent':
        source['billing_plans'][1]['amends'] = 'UNKNOWN'
    elif problem == 'customer':
        source['billing_plans'][2]['customer_name'] = 'Other customer'
    result = agreement_summary(source, 'C-1007-A1')
    assert result['validation'] == 'unresolved'
    assert result['errors']
    assert result['net_billed'] is result['gross_billed'] is result['credits'] is result['actual'] is None
    assert result['supported_corrections'] == []


def test_singleton_keeps_existing_plan_reconciliation(source):
    assert agreement_summary(source, 'C-1001') is None


def test_summary_reaches_chat_evidence_and_portfolio_without_double_counting(tmp_path, monkeypatch):
    from langchain_core.messages import AIMessage
    from leakage.agent import Agent
    from leakage.tools import Toolbox
    from test_source_conversation import Model, call, case

    root = case(tmp_path, monkeypatch)
    model = Model([call('reconcile_plan', {'plan_id': 'C-1007-A1'}), AIMessage(content='Net billed across both versions is USD 45,000.')])
    agent = Agent(root, mode='live', model=model)
    thread = agent.store.new_conversation()
    before = copy.deepcopy(agent.store.read())
    result = agent.send(thread, 'What is the total billed including the previous C-1007 plan?')
    summary, = [f for f in result['findings'] if f['kind'] == 'agreement_billing_summary']
    assert summary['net_billed'] == '45000.00'
    assert result['selected_plan'] == 'C-1007-A1'
    assert agent.store.read()['invoices'] == before['invoices']
    assert agent.store.read()['proposals'] == {}
    box = Toolbox(agent.store, thread)
    assert box.inspect_evidence(summary['id'])['net_billed'] == '45000.00'
    summaries = [f for group in box.investigate_portfolio().values() for f in group if f['kind'] == 'agreement_billing_summary']
    assert len(summaries) == 1
    assert summaries[0]['net_billed'] == '45000.00'

    # A new read uses current ledger data rather than a cached dataset.
    def change_invoice(state):
        next(i for i in state['invoices'] if i['invoice_id'] == 'I-9110')['amount_invoiced'] = 21000
    agent.store.transaction(change_invoice)
    refreshed = next(f for f in box.reconcile_plan('C-1007') if f['kind'] == 'agreement_billing_summary')
    assert refreshed['net_billed'] == '46000.00'
