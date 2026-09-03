from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from papercraft.infrastructure.calculations import (
    AccountingPosting,
    CalculationSpec,
    FinancialCalculationError,
    LoanMethod,
    MonetaryAmount,
    PostingLeg,
    PostingSide,
    ReportingPeriod,
    break_even,
    build_trial_balance,
    financial_ratios,
    horizontal_analysis,
    investment_metrics,
    loan_schedule,
    vertical_analysis,
)


def _spec(operation: str) -> CalculationSpec:
    return CalculationSpec(
        operation=operation,
        inputs={"fixture": "1"},
        period=ReportingPeriod(date(2025, 1, 1), date(2025, 12, 31)),
    )


def _leg(account: str, side: PostingSide, amount: str) -> PostingLeg:
    return PostingLeg(account, side, MonetaryAmount(Decimal(amount)))


def test_ras_trial_balance_accepts_simple_and_compound_postings() -> None:
    postings = [
        AccountingPosting(
            "p1",
            date(2025, 1, 10),
            (_leg("51", PostingSide.DEBIT, "118"), _leg("62", PostingSide.CREDIT, "118")),
        ),
        AccountingPosting(
            "p2",
            date(2025, 1, 11),
            (
                _leg("20", PostingSide.DEBIT, "60"),
                _leg("19", PostingSide.DEBIT, "12"),
                _leg("60", PostingSide.CREDIT, "72"),
            ),
        ),
    ]
    balance = build_trial_balance(postings, period=_spec("trial_balance").period)
    assert balance.balanced
    assert balance.catalog_version == "ras-chart-accounts-2026.1"
    assert balance.total_debit_turnover == balance.total_credit_turnover == Decimal("190.00")
    assert next(line for line in balance.lines if line.account == "20").debit_turnover == Decimal(
        "60.00"
    )


@given(st.lists(st.integers(min_value=1, max_value=1_000_000), min_size=1, max_size=40))
def test_trial_balance_turnovers_remain_equal_for_any_balanced_postings(
    amounts: list[int],
) -> None:
    postings = [
        AccountingPosting(
            f"p{index}",
            date(2025, 1, 1),
            (
                _leg("51", PostingSide.DEBIT, str(amount)),
                _leg("62", PostingSide.CREDIT, str(amount)),
            ),
        )
        for index, amount in enumerate(amounts)
    ]
    balance = build_trial_balance(postings, period=_spec("trial_balance").period)
    assert balance.total_debit_turnover == balance.total_credit_turnover


def test_trial_balance_exposes_unbalanced_opening_and_closing_totals() -> None:
    balance = build_trial_balance(
        [],
        period=_spec("trial_balance").period,
        opening_balances={"51": "100.00"},
    )
    assert not balance.balanced


@pytest.mark.parametrize(
    "posting, message",
    [
        (
            AccountingPosting(
                "outside",
                date(2024, 12, 31),
                (_leg("51", PostingSide.DEBIT, "1"), _leg("62", PostingSide.CREDIT, "1")),
            ),
            "outside reporting period",
        ),
        (
            AccountingPosting(
                "unknown",
                date(2025, 1, 1),
                (_leg("999", PostingSide.DEBIT, "1"), _leg("62", PostingSide.CREDIT, "1")),
            ),
            "unknown or inactive",
        ),
    ],
)
def test_trial_balance_rejects_invalid_period_and_unknown_account(
    posting: AccountingPosting, message: str
) -> None:
    with pytest.raises(FinancialCalculationError, match=message):
        build_trial_balance([posting], period=_spec("trial_balance").period)


def test_financial_oracles_for_analysis_investment_and_credit() -> None:
    horizontal = horizontal_analysis(
        {"revenue": "120"}, {"revenue": "100"}, spec=_spec("horizontal")
    )
    assert horizontal.values == {
        "revenue_absolute_change": Decimal("20.00"),
        "revenue_growth_percent": Decimal("20.00"),
    }
    assert horizontal.spec.inputs == {
        "current.revenue": "120",
        "previous.revenue": "100",
    }
    vertical = vertical_analysis(
        {"assets": "200", "cash": "50", "inventory": "30"},
        total_key="assets",
        spec=_spec("vertical"),
    )
    assert vertical.values["cash_share_percent"] == Decimal("25.00")
    ratios = financial_ratios(
        {
            "current_assets": "300",
            "current_liabilities": "150",
            "inventory": "60",
            "cash": "30",
            "equity": "400",
            "total_assets": "800",
            "total_liabilities": "400",
            "net_profit": "80",
            "revenue": "1000",
            "average_assets": "750",
            "average_equity": "350",
            "cost_of_sales": "600",
            "average_inventory": "75",
            "average_receivables": "125",
        },
        spec=_spec("ratios"),
    )
    assert ratios.values["current_ratio"] == Decimal("2.00")
    assert ratios.values["return_on_sales_percent"] == Decimal("8.00")
    breakeven = break_even(
        fixed_costs="100", price="10", variable_cost_per_unit="6", spec=_spec("break_even")
    )
    assert breakeven.values["break_even_units"] == Decimal("25.00")
    investment = investment_metrics(
        ["-100", "60", "60"], discount_rate="0.1", spec=_spec("investment")
    )
    assert investment.values["npv"] == Decimal("4.13")
    assert investment.values["irr_percent"] == Decimal("13.07")
    assert investment.to_payload()["spec"] == investment.spec.to_payload()
    persisted = investment.to_domain_calculation(
        project_id="p1", name="Investment oracle", input_fact_ids=("initial", "year-1", "year-2")
    )
    assert persisted.metadata["calculation_spec"] == investment.spec.to_payload()
    assert '"npv":"4.13"' in persisted.model_dump_json()
    schedule = loan_schedule(
        principal="1200",
        annual_rate="0.12",
        periods=12,
        start_date=date(2025, 1, 31),
        method=LoanMethod.ANNUITY,
    )
    assert len(schedule) == 12
    assert schedule[-1].balance == Decimal("0.00")
    assert sum(payment.principal for payment in schedule) == Decimal("1200.00")


@pytest.mark.parametrize(
    "call",
    [
        lambda: break_even(
            fixed_costs="1", price="1", variable_cost_per_unit="1", spec=_spec("break_even")
        ),
        lambda: vertical_analysis(
            {"assets": "0", "cash": "1"}, total_key="assets", spec=_spec("vertical")
        ),
        lambda: investment_metrics(["-100", "20"], discount_rate="0.1", spec=_spec("investment")),
    ],
)
def test_financial_calculations_fail_closed_on_zero_or_unpaid_values(
    call: Callable[[], object],
) -> None:
    with pytest.raises(FinancialCalculationError):
        call()


def test_currency_and_period_are_explicit_and_mixed_currency_is_rejected() -> None:
    with pytest.raises(FinancialCalculationError, match="mix currencies"):
        AccountingPosting(
            "mixed",
            date(2025, 1, 1),
            (
                PostingLeg("51", PostingSide.DEBIT, MonetaryAmount(Decimal("1"), "RUB")),
                PostingLeg("62", PostingSide.CREDIT, MonetaryAmount(Decimal("1"), "USD")),
            ),
        )
    with pytest.raises(FinancialCalculationError, match="end precedes"):
        ReportingPeriod(date(2025, 2, 1), date(2025, 1, 1))


@given(
    principal=st.integers(min_value=1, max_value=10_000_000),
    periods=st.integers(min_value=1, max_value=120),
    annual_basis_points=st.integers(min_value=0, max_value=5000),
    method=st.sampled_from(list(LoanMethod)),
)
def test_loan_schedule_preserves_principal_and_never_has_negative_balance(
    principal: int,
    periods: int,
    annual_basis_points: int,
    method: LoanMethod,
) -> None:
    schedule = loan_schedule(
        principal=principal,
        annual_rate=Decimal(annual_basis_points) / Decimal(10_000),
        periods=periods,
        start_date=date(2025, 1, 31),
        method=method,
    )
    assert sum((row.principal for row in schedule), Decimal()) == Decimal(principal).quantize(
        Decimal("0.01")
    )
    assert schedule[-1].balance == Decimal("0.00")
    assert all(row.balance >= 0 and row.payment == row.principal + row.interest for row in schedule)
