import copy
from decimal import Decimal
import pytest
from leakage.domain import money, schedule
from leakage.reconcile import reconcile, fx, portfolio

@pytest.mark.parametrize('plan,kind,delta',[('P-12345','missing_period','10000.00'),('P-PARTIAL','underbilling','2000.00'),('P-OVER','overbilling','-2000.00'),('P-CREDIT','matched','0.00'),('P-DISCOUNT','matched','0.00'),('P-FX','matched','0.00'),('P-FX-MISSING','unresolved',None),('P-AMEND','matched','0.00'),('P-OVERLAP','unresolved',None),('P-UNTRUSTED','matched','0.00')])
def test_known_answers(store,plan,kind,delta):
    results=reconcile(store.read(),plan)
    matches=[f for f in results if f['kind']==kind]
    assert matches and all(f['discrepancy']==delta for f in matches)
    assert all(f['id'] and f['source_refs'] and f['calculation'] for f in results)
    assert results==reconcile(store.read(),plan)

def test_duplicates_counted_once(store):
    rows=reconcile(store.read(),'P-DUP')
    assert {r['kind'] for r in rows}=={'matched','duplicate_record'}

def test_split_invoices_aggregate(store):
    s=store.read();inv=next(i for i in s['invoices'] if i['id']=='INV-PARTIAL');inv['amount']='4000.00';other={**inv,'id':'INV-SPLIT','amount':'6000.00'};s['invoices'].append(other)
    assert reconcile(s,'P-PARTIAL')[0]['kind']=='matched'

def test_conflicting_ids_block_correction(store):
    s=store.read();inv=next(i for i in s['invoices'] if i['id']=='INV-PARTIAL');s['invoices'].append({**inv,'amount':'9000.00'})
    rows=reconcile(s,'P-PARTIAL')
    assert not any(f['supported_corrections'] for f in rows)

def test_credit_currency_invalid(store):
    s=store.read();s['credit_memos'][0]['currency']='EUR'
    assert reconcile(s,'P-CREDIT')[0]['validation']=='unresolved'

def test_money_rounding():
    assert money('1.005')==Decimal('1.01')
    assert money('1.5','JPY')==Decimal('2')
    assert money('1.0005','KWD')==Decimal('1.001')
    for bad in ('NaN','Infinity'):
        with pytest.raises(ValueError):money(bad)

def test_fx_no_invention(store):
    s=store.read()
    assert fx(s,'100','USD','EUR','2024-10-01')['amount']=='90.00'
    with pytest.raises(ValueError):fx(s,'100','EUR','USD','2024-10-01')
    s['exchange_rates']*=2
    with pytest.raises(ValueError):fx(s,'100','USD','EUR','2024-10-01')

def test_effective_dates_proration(store):
    p=copy.deepcopy(store.read()['billing_plans'][1]);p['amendments']=[{'effective_date':'2024-10-16','amount':'20000','reason':'upgrade'}]
    with pytest.raises(ValueError):schedule(p)
    p['proration']='actual_days'
    assert schedule(p)[0]['expected']=='15161.29'

def test_quarterly_annual_and_partial(store):
    p=copy.deepcopy(store.read()['billing_plans'][0]);p.update(start_date='2024-01-01',end_date='2025-01-01',cadence='quarterly')
    assert len(schedule(p))==4
    p['cadence']='annual';assert len(schedule(p))==1
    p.update(end_date='2024-02-01',cadence='quarterly')
    with pytest.raises(ValueError):schedule(p)
    p['proration']='actual_days';assert schedule(p)[0]['expected']=='3406.59'

def test_portfolio_grouping(store):
    result=portfolio(store.read());assert set(result)=={'USD','EUR'}
    for rows in result.values():
        amounts=[abs(Decimal(r['discrepancy'] or 0)) for r in rows];assert amounts==sorted(amounts,reverse=True)

def test_explicit_duplicate_charge_and_orphan_records(store):
    s=store.read();original=next(i for i in s['invoices'] if i['id']=='INV-PARTIAL')
    s['invoices'].append({**original,'id':'INV-CONFIRMED','duplicate_of':original['id']})
    assert any(f['kind']=='confirmed_duplicate_charge' for f in reconcile(s,'P-PARTIAL'))
    s['credit_memos'].append({'id':'CM-ORPHAN','invoice_id':'UNKNOWN','amount':'10','currency':'USD','issued_date':'2024-10-01'})
    s['invoices'].append({**original,'id':'INV-ORPHAN','plan_id':'UNKNOWN'})
    findings=portfolio(s)['USD'];assert {'orphan_credit','orphan_invoice'} <= {f['kind'] for f in findings}


def test_missing_contract_data_blocks_conclusion(store):
    s=store.read();del s['billing_plans'][0]['cadence']
    f=reconcile(s,'P-12345')[0]
    assert f['kind']=='incomplete_contract' and f['discrepancy'] is None and not f['supported_corrections']
