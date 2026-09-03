"""Deterministic RAS accounting and finance calculations.

No formula string is evaluated here.  Inputs, period, currency, rounding and
catalogue version are captured in ``CalculationSpec`` so a manuscript number
can be independently rebuilt from persisted data.
"""

from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from pydantic import JsonValue

from papercraft.domain import Calculation

from .finance import AccountKind
from .financial_catalog import RAS_2026, RASAccountCatalog


class FinancialCalculationError(ValueError):
    pass


class PostingSide(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class LoanMethod(StrEnum):
    ANNUITY = "annuity"
    DIFFERENTIATED = "differentiated"


@dataclass(frozen=True, slots=True)
class ReportingPeriod:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise FinancialCalculationError("period end precedes start")

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end


@dataclass(frozen=True, slots=True)
class MonetaryAmount:
    amount: Decimal
    currency: str = "RUB"
    unit: str = "RUB"
    scale: int = 2

    def __post_init__(self) -> None:
        amount = _decimal(self.amount, "amount")
        currency = self.currency.upper()
        if len(currency) != 3 or not currency.isalpha():
            raise FinancialCalculationError("currency must be a three-letter code")
        if self.unit.strip() == "" or not 0 <= self.scale <= 8:
            raise FinancialCalculationError("unit must be non-empty and scale must be 0..8")
        object.__setattr__(self, "amount", _round(amount, self.scale))
        object.__setattr__(self, "currency", currency)


@dataclass(frozen=True, slots=True)
class PostingLeg:
    account: str
    side: PostingSide
    money: MonetaryAmount

    def __post_init__(self) -> None:
        if not self.account.strip():
            raise FinancialCalculationError("posting account must be non-empty")


@dataclass(frozen=True, slots=True)
class AccountingPosting:
    id: str
    occurred_on: date
    legs: tuple[PostingLeg, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip() or len(self.legs) < 2:
            raise FinancialCalculationError("posting requires an id and at least two legs")
        dimensions = {(leg.money.currency, leg.money.unit, leg.money.scale) for leg in self.legs}
        if len(dimensions) != 1:
            raise FinancialCalculationError("posting legs cannot mix currencies, units or scales")
        debit = sum(
            (leg.money.amount for leg in self.legs if leg.side is PostingSide.DEBIT), Decimal()
        )
        credit = sum(
            (leg.money.amount for leg in self.legs if leg.side is PostingSide.CREDIT), Decimal()
        )
        if debit != credit or debit <= 0:
            raise FinancialCalculationError("posting debit and credit legs must balance positively")

    @property
    def currency(self) -> str:
        return self.legs[0].money.currency


@dataclass(frozen=True, slots=True)
class TrialBalanceLine:
    account: str
    kind: AccountKind
    opening_debit: Decimal
    opening_credit: Decimal
    debit_turnover: Decimal
    credit_turnover: Decimal
    closing_debit: Decimal
    closing_credit: Decimal


@dataclass(frozen=True, slots=True)
class TrialBalance:
    period: ReportingPeriod
    currency: str
    catalog_version: str
    lines: tuple[TrialBalanceLine, ...]
    balanced: bool

    @property
    def total_debit_turnover(self) -> Decimal:
        return sum((line.debit_turnover for line in self.lines), Decimal())

    @property
    def total_credit_turnover(self) -> Decimal:
        return sum((line.credit_turnover for line in self.lines), Decimal())


@dataclass(frozen=True, slots=True)
class CalculationSpec:
    operation: str
    inputs: Mapping[str, Decimal | int | str]
    period: ReportingPeriod
    currency: str = "RUB"
    unit: str = "RUB"
    scale: int = 2
    algorithm_version: str = "finance-v1"

    def __post_init__(self) -> None:
        if not self.operation.strip() or not self.inputs:
            raise FinancialCalculationError("calculation operation and inputs are required")
        dimension = MonetaryAmount(Decimal(0), self.currency, self.unit, self.scale)
        object.__setattr__(self, "currency", dimension.currency)
        object.__setattr__(
            self,
            "inputs",
            MappingProxyType(dict(sorted(self.inputs.items()))),
        )

    def to_payload(self) -> dict[str, object]:
        """Return the complete JSON-safe recipe persisted with a calculation."""

        return {
            "operation": self.operation,
            "inputs": {key: str(value) for key, value in sorted(self.inputs.items())},
            "period": {
                "start": self.period.start.isoformat(),
                "end": self.period.end.isoformat(),
            },
            "currency": self.currency.upper(),
            "unit": self.unit,
            "scale": self.scale,
            "algorithm_version": self.algorithm_version,
        }


@dataclass(frozen=True, slots=True)
class CalculationResult:
    spec: CalculationSpec
    values: Mapping[str, Decimal]
    checks: Mapping[str, bool]
    disclosure: str = ""

    def __post_init__(self) -> None:
        if any(not value.is_finite() for value in self.values.values()):
            raise FinancialCalculationError("calculation result values must be finite")
        object.__setattr__(self, "values", MappingProxyType(dict(sorted(self.values.items()))))
        object.__setattr__(self, "checks", MappingProxyType(dict(sorted(self.checks.items()))))

    def to_payload(self) -> dict[str, object]:
        return {
            "spec": self.spec.to_payload(),
            "values": {key: str(value) for key, value in sorted(self.values.items())},
            "checks": dict(sorted(self.checks.items())),
            "disclosure": self.disclosure,
        }

    def to_domain_calculation(
        self,
        *,
        project_id: str,
        name: str,
        input_fact_ids: Sequence[str] = (),
        output_fact_id: str | None = None,
    ) -> Calculation:
        """Create the schema-5 domain object persisted by the repository."""

        spec_payload: dict[str, JsonValue] = {
            "operation": self.spec.operation,
            "inputs": {key: str(value) for key, value in sorted(self.spec.inputs.items())},
            "period": {
                "start": self.spec.period.start.isoformat(),
                "end": self.spec.period.end.isoformat(),
            },
            "currency": self.spec.currency,
            "unit": self.spec.unit,
            "scale": self.spec.scale,
            "algorithm_version": self.spec.algorithm_version,
        }
        result_payload: dict[str, JsonValue] = {
            key: str(value) for key, value in sorted(self.values.items())
        }
        return Calculation(
            project_id=project_id,
            name=name,
            expression=f"{self.spec.operation}@{self.spec.algorithm_version}",
            input_fact_ids=list(input_fact_ids),
            output_fact_id=output_fact_id,
            result=result_payload,
            checks=dict(self.checks),
            metadata={
                "calculation_spec": spec_payload,
                "currency": self.spec.currency,
                "unit": self.spec.unit,
                "period_start": self.spec.period.start.isoformat(),
                "period_end": self.spec.period.end.isoformat(),
                "rounding": "ROUND_HALF_UP",
                "disclosure": self.disclosure,
            },
        )


@dataclass(frozen=True, slots=True)
class LoanPayment:
    number: int
    payment_date: date
    payment: Decimal
    principal: Decimal
    interest: Decimal
    balance: Decimal


def build_trial_balance(
    postings: Iterable[AccountingPosting],
    *,
    period: ReportingPeriod,
    opening_balances: Mapping[str, Decimal | int | str] = {},
    catalog: RASAccountCatalog = RAS_2026,
    currency: str = "RUB",
    unit: str = "RUB",
    scale: int = 2,
) -> TrialBalance:
    """Build an оборотно-сальдовая ведомость from simple or compound postings."""

    dimension = MonetaryAmount(Decimal(0), currency, unit, scale)
    currency = dimension.currency
    debit: defaultdict[str, Decimal] = defaultdict(Decimal)
    credit: defaultdict[str, Decimal] = defaultdict(Decimal)
    names = set(opening_balances)
    seen: set[str] = set()
    for posting in postings:
        if posting.id in seen:
            raise FinancialCalculationError(f"duplicate posting id: {posting.id}")
        seen.add(posting.id)
        if not period.contains(posting.occurred_on):
            raise FinancialCalculationError(f"posting {posting.id} is outside reporting period")
        if posting.currency != currency:
            raise FinancialCalculationError("postings cannot mix currencies in one trial balance")
        for leg in posting.legs:
            if leg.money.unit != unit or leg.money.scale != scale:
                raise FinancialCalculationError(
                    "postings cannot mix units or rounding scales in one trial balance"
                )
            definition = catalog.get(leg.account, on=posting.occurred_on)
            if definition is None:
                raise FinancialCalculationError(f"unknown or inactive RAS account: {leg.account}")
            names.add(leg.account)
            target = debit if leg.side is PostingSide.DEBIT else credit
            target[leg.account] += leg.money.amount
    lines: list[TrialBalanceLine] = []
    for account in sorted(names):
        definition = catalog.get(account, on=period.end)
        if definition is None:
            raise FinancialCalculationError(f"unknown or inactive RAS account: {account}")
        opening = _decimal(opening_balances.get(account, 0), f"opening {account}")
        closing = opening + debit[account] - credit[account]
        lines.append(
            TrialBalanceLine(
                account,
                definition.kind,
                _positive(opening),
                _positive(-opening),
                _round(debit[account], scale),
                _round(credit[account], scale),
                _positive(closing),
                _positive(-closing),
            )
        )
    total_debit = sum((line.debit_turnover for line in lines), Decimal())
    total_credit = sum((line.credit_turnover for line in lines), Decimal())
    opening_debit = sum((line.opening_debit for line in lines), Decimal())
    opening_credit = sum((line.opening_credit for line in lines), Decimal())
    closing_debit = sum((line.closing_debit for line in lines), Decimal())
    closing_credit = sum((line.closing_credit for line in lines), Decimal())
    return TrialBalance(
        period,
        currency,
        catalog.version,
        tuple(lines),
        total_debit == total_credit
        and opening_debit == opening_credit
        and closing_debit == closing_credit,
    )


def horizontal_analysis(
    current: Mapping[str, Any],
    previous: Mapping[str, Any],
    *,
    spec: CalculationSpec,
) -> CalculationResult:
    _require_same_keys(current, previous)
    values: dict[str, Decimal] = {}
    for key in current:
        now, before = _decimal(current[key], key), _decimal(previous[key], key)
        values[f"{key}_absolute_change"] = _round(now - before, spec.scale)
        values[f"{key}_growth_percent"] = _round(
            _divide(now - before, before, f"{key} baseline") * 100, spec.scale
        )
    inputs = {
        **{f"current.{key}": current[key] for key in current},
        **{f"previous.{key}": previous[key] for key in previous},
    }
    return _result(spec, values, inputs=inputs, operation="horizontal_analysis")


def vertical_analysis(
    values: Mapping[str, Any], *, total_key: str, spec: CalculationSpec
) -> CalculationResult:
    total = _decimal(values.get(total_key), total_key)
    result = {
        f"{key}_share_percent": _round(
            _divide(_decimal(value, key), total, total_key) * 100, spec.scale
        )
        for key, value in values.items()
        if key != total_key
    }
    return _result(
        spec,
        result,
        inputs={**values, "total_key": total_key},
        operation="vertical_analysis",
    )


def financial_ratios(values: Mapping[str, Any], *, spec: CalculationSpec) -> CalculationResult:
    """Liquidity, stability, profitability and turnover indicators by named inputs."""

    def decimal_input(key: str) -> Decimal:
        return _decimal(values.get(key), key)

    result = {
        "current_ratio": _divide(
            decimal_input("current_assets"),
            decimal_input("current_liabilities"),
            "current_liabilities",
        ),
        "quick_ratio": _divide(
            decimal_input("current_assets") - decimal_input("inventory"),
            decimal_input("current_liabilities"),
            "current_liabilities",
        ),
        "absolute_liquidity_ratio": _divide(
            decimal_input("cash"), decimal_input("current_liabilities"), "current_liabilities"
        ),
        "equity_ratio": _divide(
            decimal_input("equity"), decimal_input("total_assets"), "total_assets"
        ),
        "debt_to_equity": _divide(
            decimal_input("total_liabilities"), decimal_input("equity"), "equity"
        ),
        "return_on_sales_percent": _divide(
            decimal_input("net_profit"), decimal_input("revenue"), "revenue"
        )
        * 100,
        "return_on_assets_percent": _divide(
            decimal_input("net_profit"), decimal_input("average_assets"), "average_assets"
        )
        * 100,
        "return_on_equity_percent": _divide(
            decimal_input("net_profit"), decimal_input("average_equity"), "average_equity"
        )
        * 100,
        "asset_turnover": _divide(
            decimal_input("revenue"), decimal_input("average_assets"), "average_assets"
        ),
        "inventory_turnover": _divide(
            decimal_input("cost_of_sales"), decimal_input("average_inventory"), "average_inventory"
        ),
        "receivables_turnover": _divide(
            decimal_input("revenue"), decimal_input("average_receivables"), "average_receivables"
        ),
    }
    return _result(
        spec,
        {key: _round(value, spec.scale) for key, value in result.items()},
        inputs=values,
        operation="financial_ratios",
    )


def break_even(
    *,
    fixed_costs: Any,
    price: Any,
    variable_cost_per_unit: Any,
    spec: CalculationSpec,
) -> CalculationResult:
    contribution = _decimal(price, "price") - _decimal(
        variable_cost_per_unit, "variable_cost_per_unit"
    )
    units = _divide(_decimal(fixed_costs, "fixed_costs"), contribution, "contribution margin")
    revenue = units * _decimal(price, "price")
    price_decimal = _decimal(price, "price")
    return _result(
        spec,
        {
            "contribution_margin_per_unit": _round(contribution, spec.scale),
            "contribution_margin_ratio_percent": _round(
                _divide(contribution, price_decimal, "price") * 100, spec.scale
            ),
            "break_even_units": _round(units, spec.scale),
            "break_even_revenue": _round(revenue, spec.scale),
        },
        inputs={
            "fixed_costs": fixed_costs,
            "price": price,
            "variable_cost_per_unit": variable_cost_per_unit,
        },
        operation="break_even",
    )


def investment_metrics(
    cash_flows: Sequence[Any], *, discount_rate: Any, spec: CalculationSpec
) -> CalculationResult:
    if len(cash_flows) < 2:
        raise FinancialCalculationError("investment requires initial and future cash flows")
    flows = [_decimal(value, f"cash_flow[{index}]") for index, value in enumerate(cash_flows)]
    rate = _decimal(discount_rate, "discount_rate")
    if rate <= Decimal("-1"):
        raise FinancialCalculationError("discount rate must exceed -100%")
    npv = sum(
        (flow / ((Decimal(1) + rate) ** index) for index, flow in enumerate(flows)), Decimal()
    )
    investment = -flows[0]
    if investment <= 0:
        raise FinancialCalculationError("initial cash flow must be negative")
    discounted_inflows = sum(
        (
            flow / ((Decimal(1) + rate) ** index)
            for index, flow in enumerate(flows[1:], 1)
            if flow > 0
        ),
        Decimal(),
    )
    pp = _payback_period(flows)
    dpp = _payback_period(
        [flow / ((Decimal(1) + rate) ** index) for index, flow in enumerate(flows)]
    )
    irr = _irr(flows)
    inputs = {f"cash_flow.{index}": value for index, value in enumerate(cash_flows)}
    inputs["discount_rate"] = discount_rate
    return _result(
        spec,
        {
            "npv": _round(npv, spec.scale),
            "irr_percent": _round(irr * 100, spec.scale),
            "profitability_index": _round(discounted_inflows / investment, spec.scale),
            "payback_period": _round(pp, spec.scale),
            "discounted_payback_period": _round(dpp, spec.scale),
        },
        inputs=inputs,
        operation="investment_metrics",
    )


def loan_schedule(
    *,
    principal: Any,
    annual_rate: Any,
    periods: int,
    start_date: date,
    method: LoanMethod,
    scale: int = 2,
) -> tuple[LoanPayment, ...]:
    debt, annual = _decimal(principal, "principal"), _decimal(annual_rate, "annual_rate")
    if debt <= 0 or periods < 1 or annual < 0 or not 0 <= scale <= 8:
        raise FinancialCalculationError(
            "principal and periods must be positive; annual rate and scale must be valid"
        )
    debt = _round(debt, scale)
    monthly = annual / Decimal(12)
    if method is LoanMethod.ANNUITY:
        payment = (
            debt / periods
            if monthly == 0
            else debt
            * monthly
            * (Decimal(1) + monthly) ** periods
            / ((Decimal(1) + monthly) ** periods - 1)
        )
    else:
        payment = Decimal(0)
    remaining = debt
    displayed_principal = Decimal()
    schedule: list[LoanPayment] = []
    for number in range(1, periods + 1):
        interest = remaining * monthly
        principal_part = (
            (debt / periods) if method is LoanMethod.DIFFERENTIATED else payment - interest
        )
        rounded_interest = _round(interest, scale)
        rounded_principal = (
            debt - displayed_principal
            if number == periods
            else min(_round(principal_part, scale), debt - displayed_principal)
        )
        displayed_principal += rounded_principal
        remaining = debt - displayed_principal
        schedule.append(
            LoanPayment(
                number,
                _add_months(start_date, number),
                rounded_principal + rounded_interest,
                rounded_principal,
                rounded_interest,
                remaining,
            )
        )
    return tuple(schedule)


def _result(
    spec: CalculationSpec,
    values: Mapping[str, Decimal],
    *,
    inputs: Mapping[str, Any],
    operation: str,
) -> CalculationResult:
    normalized_inputs = {
        key: str(value) if not isinstance(value, Decimal) else value
        for key, value in inputs.items()
    }
    actual_spec = replace(spec, operation=operation, inputs=normalized_inputs)
    return CalculationResult(
        actual_spec,
        values,
        {
            "finite": all(value.is_finite() for value in values.values()),
            "reproducible": True,
        },
    )


def _decimal(value: Any, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise FinancialCalculationError(f"{label} must be a finite decimal")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise FinancialCalculationError(f"{label} must be a finite decimal") from exc
    if not number.is_finite():
        raise FinancialCalculationError(f"{label} must be a finite decimal")
    return number


def _round(value: Decimal, scale: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)


def _divide(numerator: Decimal, denominator: Decimal, label: str) -> Decimal:
    if denominator == 0:
        raise FinancialCalculationError(f"division by zero: {label}")
    with localcontext() as context:
        context.prec = 34
        return numerator / denominator


def _positive(value: Decimal) -> Decimal:
    return value if value > 0 else Decimal()


def _require_same_keys(left: Mapping[str, Any], right: Mapping[str, Any]) -> None:
    if set(left) != set(right):
        raise FinancialCalculationError("horizontal analysis periods must contain equal indicators")


def _payback_period(flows: Sequence[Decimal]) -> Decimal:
    cumulative = Decimal()
    for index, flow in enumerate(flows):
        previous = cumulative
        cumulative += flow
        if cumulative >= 0:
            return Decimal(index) if flow == 0 else Decimal(index - 1) + (-previous / flow)
    raise FinancialCalculationError("cash flows do not reach payback")


def _irr(flows: Sequence[Decimal]) -> Decimal:
    low, high = Decimal("-0.999999"), Decimal("10")

    def value(rate: Decimal) -> Decimal:
        return sum(
            (flow / ((Decimal(1) + rate) ** index) for index, flow in enumerate(flows)), Decimal()
        )

    if value(low) * value(high) > 0:
        raise FinancialCalculationError("IRR is undefined for supplied cash flows")
    for _ in range(160):
        middle = (low + high) / 2
        current = value(middle)
        if abs(current) < Decimal("1e-20"):
            return middle
        if value(low) * current <= 0:
            high = middle
        else:
            low = middle
    return (low + high) / 2


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year, month = value.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


__all__ = [
    "AccountingPosting",
    "CalculationResult",
    "CalculationSpec",
    "FinancialCalculationError",
    "LoanMethod",
    "LoanPayment",
    "MonetaryAmount",
    "PostingLeg",
    "PostingSide",
    "ReportingPeriod",
    "TrialBalance",
    "TrialBalanceLine",
    "break_even",
    "build_trial_balance",
    "financial_ratios",
    "horizontal_analysis",
    "investment_metrics",
    "loan_schedule",
    "vertical_analysis",
]
