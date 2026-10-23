"""Readable record formatting; never mutates financial data."""
from decimal import Decimal, InvalidOperation
from datetime import date, timedelta
LABELS = {"billing_plans":"Billing plans", "credit_memos":"Credit memos", "exchange_rates":"Exchange rates", "id":"Record", "plan_id":"Plan", "customer_id":"Customer", "invoice_id":"Invoice", "product_id":"Product", "period_end":"Service until (exclusive)", "period_start":"Service from"}
def label(key):
    return LABELS.get(key, key.replace("_", " ").capitalize())
def amount(value, currency=""):
    if value is None: return "—"
    try:
        number = Decimal(str(value))
        return f"{currency} {number:,.{max(2,-number.as_tuple().exponent)}f}".strip()
    except (InvalidOperation, ValueError): return str(value)
def period(value):
    if not value: return "Period unavailable"
    try:
        start, end = (date.fromisoformat(v) for v in value)
        return f"{start:%d %b %Y} – {end-timedelta(days=1):%d %b %Y}"
    except (ValueError, TypeError): return " – ".join(map(str,value))
def cell(value):
    if value is None: return "—"
    if isinstance(value,dict): return "; ".join(f"{label(k)}: {cell(v)}" for k,v in value.items()) or "None"
    if isinstance(value,list): return " • ".join(cell(v) for v in value) or "None"
    return str(value)
def record_rows(records):
    return [{label(k): amount(v,r.get("currency","")) if k=="amount" else cell(v) for k,v in r.items()} for r in records]
def diff_rows(before,after):
    return [{"Field":label(k),"Before":cell(before.get(k)),"After":cell(after.get(k))} for k in dict.fromkeys([*before,*after]) if before.get(k)!=after.get(k)]


def structured_content(text):
    """Recognize whole/trailing legacy JSON without changing saved history."""
    import json
    stripped = text.strip()
    if stripped.startswith("```json") and stripped.endswith("```"):
        stripped = stripped[7:-3].strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char not in "[{":
            continue
        try:
            value, end = decoder.raw_decode(stripped, index)
        except (ValueError, TypeError):
            continue
        trailing = stripped[end:].strip()
        if isinstance(value, (dict, list)) and trailing in ("", "```"):
            prefix = stripped[:index].strip()
            if prefix.endswith("```json"):
                prefix = prefix[:-7].strip()
            return prefix, value
    return None
