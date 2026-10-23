from __future__ import annotations

import calendar
import hashlib
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CURRENCY_DIGITS = {"USD": 2, "EUR": 2, "GBP": 2, "INR": 2, "CAD": 2, "AUD": 2, "JPY": 0, "KWD": 3, "CHF": 2}

def money(value, currency="USD") -> Decimal:
    if currency not in CURRENCY_DIGITS:
        raise ValueError(f"Unsupported currency precision: {currency}; configure it explicitly")
    d = Decimal(str(value))
    if not d.is_finite():
        raise ValueError("Money must be finite")
    return d.quantize(Decimal(1).scaleb(-CURRENCY_DIGITS[currency]), rounding=ROUND_HALF_UP)

def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()

def identifier(value):
    import re
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError("Invalid identifier")
    return value

class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Plan(Record):
    id: str
    customer_id: str
    product_id: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount: Decimal = Field(ge=0, allow_inf_nan=False)
    cadence: Literal["monthly", "quarterly", "annual"]
    start_date: date
    end_date: date
    amendments: list[dict] = Field(default_factory=list)
    discount_percent: Decimal = Field(default=Decimal(0), ge=0, le=100, allow_inf_nan=False)
    proration: Literal["none", "actual_days"] = "none"
    notes: str = ""
    _ids = field_validator("id", "customer_id", "product_id")(identifier)

    @model_validator(mode="after")
    def dates(self):
        if self.end_date <= self.start_date:
            raise ValueError("Plan end_date must be after start_date (exclusive)")
        money(self.amount, self.currency)
        return self

class Invoice(Record):
    id: str
    plan_id: str
    customer_id: str
    product_id: str
    amount: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    period_start: date
    period_end: date
    issued_date: date
    status: Literal["issued", "void"] = "issued"
    duplicate_of: str | None = None
    notes: str = ""
    _ids = field_validator("id", "plan_id", "customer_id", "product_id")(identifier)

    @model_validator(mode="after")
    def dates(self):
        if self.period_end <= self.period_start:
            raise ValueError("Invalid invoice service period")
        money(self.amount, self.currency)
        return self

class Credit(Record):
    id: str
    invoice_id: str
    amount: Decimal = Field(gt=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    issued_date: date
    reason: str = ""
    _ids = field_validator("id", "invoice_id")(identifier)

class Rate(Record):
    from_ccy: str = Field(pattern=r"^[A-Z]{3}$")
    to_ccy: str = Field(pattern=r"^[A-Z]{3}$")
    on_date: date
    rate: Decimal = Field(gt=0, allow_inf_nan=False)

class Filters(Record):
    plan_id: str | None = None
    customer_id: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    _ids = field_validator("plan_id", "customer_id")(lambda v: identifier(v) if v is not None else v)

class ChangeSet(Record):
    effective_date: date
    amount: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    discount_percent: Decimal | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def not_empty(self):
        if self.amount is None and self.discount_percent is None:
            raise ValueError("At least one commercial change is required")
        return self

def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, min(d.day, calendar.monthrange(d.year + m // 12, m % 12 + 1)[1]))

def effective_terms(plan: dict, on: date):
    terms = {k: plan[k] for k in ("amount", "discount_percent") if k in plan}
    terms.setdefault("discount_percent", "0")
    amendments = sorted(plan.get("amendments", []), key=lambda a: a["effective_date"])
    seen = set()
    for a in amendments:
        change = ChangeSet.model_validate(a)
        if change.effective_date in seen:
            raise ValueError("Conflicting amendments on the same effective date")
        seen.add(change.effective_date)
        if change.effective_date <= on:
            terms.update({k: str(v) for k, v in change.model_dump().items() if k in ("amount", "discount_percent") and v is not None})
    return terms

def schedule(raw: dict):
    p = Plan.model_validate(raw)
    step = {"monthly": 1, "quarterly": 3, "annual": 12}[p.cadence]
    if p.start_date.day != 1:
        raise ValueError("Non-calendar billing anchors need an explicit schedule adapter")
    result = []
    start = p.start_date
    while start < p.end_date:
        full_end = add_months(start, step)
        end = min(full_end, p.end_date)
        boundaries = sorted({start, end} | {date.fromisoformat(a["effective_date"]) for a in raw.get("amendments", []) if start < date.fromisoformat(a["effective_date"]) < end})
        if (end != full_end or len(boundaries) > 2) and p.proration != "actual_days":
            raise ValueError("Partial period / mid-period amendment requires an explicit proration rule")
        expected = Decimal(0)
        pieces = []
        for left, right in zip(boundaries, boundaries[1:]):
            terms = effective_terms(raw, left)
            price = Decimal(terms["amount"]) * (1 - Decimal(terms["discount_percent"]) / 100)
            fraction = Decimal((right-left).days) / Decimal((full_end-start).days)
            part = price * fraction
            expected += part
            pieces.append(f"{terms['amount']} × (1 − {terms['discount_percent']} / 100) × {(right-left).days}/{(full_end-start).days}")
        result.append({"period_start": str(start), "period_end": str(end), "expected": str(money(expected, p.currency)), "calculation": " + ".join(pieces)})
        start = full_end
        if len(result) > 1200:
            raise ValueError("Schedule exceeds 1200 periods; narrow the source contract")
    return result
