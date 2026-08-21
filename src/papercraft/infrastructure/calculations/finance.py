"""Deterministic double-entry accounting validation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from papercraft.domain import Dataset


class AccountKind(StrEnum):
    ACTIVE = "active"
    PASSIVE = "passive"
    ACTIVE_PASSIVE = "active_passive"


@dataclass(frozen=True, slots=True)
class JournalEntry:
    id: str
    debit_account: str
    credit_account: str
    amount: Decimal
    description: str = ""
    occurred_on: date | None = None
    source_id: str | None = None
    synthetic_seed: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _money(self.amount, label=f"entry {self.id}"))


@dataclass(frozen=True, slots=True)
class AccountTurnover:
    account: str
    opening_balance: Decimal
    debit_turnover: Decimal
    credit_turnover: Decimal
    closing_balance: Decimal
    kind: AccountKind


@dataclass(frozen=True, slots=True)
class FinanceIssue:
    code: str
    message: str
    entry_id: str | None = None
    account: str | None = None


@dataclass(frozen=True, slots=True)
class FinanceValidationResult:
    issues: tuple[FinanceIssue, ...]
    accounts: Mapping[str, AccountTurnover]
    total_debit: Decimal
    total_credit: Decimal
    report_year: int | None = None

    @property
    def is_valid(self) -> bool:
        return not self.issues and self.total_debit == self.total_credit


def _money(value: Any, *, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{label} must be finite")
    return number.quantize(Decimal("0.01"))


def validate_double_entry(
    entries: Iterable[JournalEntry],
    *,
    opening_balances: Mapping[str, Decimal | int | float | str] | None = None,
    account_kinds: Mapping[str, AccountKind | str] | None = None,
    expected_year: int | None = None,
) -> FinanceValidationResult:
    """Validate journal entries and derive every account balance.

    A signed account balance is used: positive means debit, negative means
    credit. This prevents the common false validation where the same amount is
    summed independently as both debit and credit without checking accounts.
    """

    entries = tuple(entries)
    opening_balances = opening_balances or {}
    account_kinds = account_kinds or {}
    issues: list[FinanceIssue] = []
    seen_ids: set[str] = set()
    debit: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    credit: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")

    for entry in entries:
        if not entry.id.strip():
            issues.append(FinanceIssue("missing_entry_id", "Entry id is empty"))
        elif entry.id in seen_ids:
            issues.append(
                FinanceIssue("duplicate_entry_id", f"Duplicate entry id {entry.id}", entry.id)
            )
        seen_ids.add(entry.id)
        debit_account = entry.debit_account.strip()
        credit_account = entry.credit_account.strip()
        if not debit_account or not credit_account:
            issues.append(
                FinanceIssue("missing_account", "Debit and credit accounts are required", entry.id)
            )
            continue
        if debit_account == credit_account:
            issues.append(
                FinanceIssue(
                    "same_account",
                    "Debit and credit accounts must differ",
                    entry.id,
                    debit_account,
                )
            )
        if entry.amount <= 0:
            issues.append(
                FinanceIssue("non_positive_amount", "Entry amount must be positive", entry.id)
            )
            continue
        if expected_year is not None:
            if entry.occurred_on is None:
                issues.append(
                    FinanceIssue("missing_date", "Entry date is required for year validation", entry.id)
                )
            elif entry.occurred_on.year != expected_year:
                issues.append(
                    FinanceIssue(
                        "year_mismatch",
                        f"Entry belongs to {entry.occurred_on.year}, expected {expected_year}",
                        entry.id,
                    )
                )
        debit[debit_account] += entry.amount
        credit[credit_account] += entry.amount
        total_debit += entry.amount
        total_credit += entry.amount

    if total_debit != total_credit:
        issues.append(FinanceIssue("journal_unbalanced", "Total debit differs from total credit"))

    account_names = set(debit) | set(credit) | set(opening_balances)
    accounts: dict[str, AccountTurnover] = {}
    for account in sorted(account_names):
        try:
            opening = _money(opening_balances.get(account, 0), label=f"opening {account}")
        except ValueError as exc:
            issues.append(FinanceIssue("invalid_opening", str(exc), account=account))
            opening = Decimal("0.00")
        raw_kind = account_kinds.get(account, AccountKind.ACTIVE_PASSIVE)
        try:
            kind = raw_kind if isinstance(raw_kind, AccountKind) else AccountKind(raw_kind)
        except ValueError:
            issues.append(
                FinanceIssue("invalid_account_kind", f"Unknown account kind {raw_kind}", account=account)
            )
            kind = AccountKind.ACTIVE_PASSIVE
        closing = opening + debit[account] - credit[account]
        if kind == AccountKind.ACTIVE and closing < 0:
            issues.append(
                FinanceIssue(
                    "invalid_credit_balance",
                    "Active account has a credit closing balance",
                    account=account,
                )
            )
        if kind == AccountKind.PASSIVE and closing > 0:
            issues.append(
                FinanceIssue(
                    "invalid_debit_balance",
                    "Passive account has a debit closing balance",
                    account=account,
                )
            )
        accounts[account] = AccountTurnover(
            account=account,
            opening_balance=opening,
            debit_turnover=debit[account],
            credit_turnover=credit[account],
            closing_balance=closing,
            kind=kind,
        )

    return FinanceValidationResult(
        issues=tuple(issues),
        accounts=accounts,
        total_debit=total_debit,
        total_credit=total_credit,
        report_year=expected_year,
    )


def validate_finance_dataset(
    dataset: Dataset,
    *,
    opening_balances: Mapping[str, Decimal | int | float | str] | None = None,
    account_kinds: Mapping[str, AccountKind | str] | None = None,
    expected_year: int | None = None,
) -> FinanceValidationResult:
    """Validate a dataset with debit_account, credit_account and amount columns."""

    required = {"debit_account", "credit_account", "amount"}
    available = {column.name for column in dataset.columns}
    missing = required - available
    if missing:
        issue = FinanceIssue(
            "missing_columns", f"Finance dataset lacks columns: {', '.join(sorted(missing))}"
        )
        return FinanceValidationResult(
            issues=(issue,),
            accounts={},
            total_debit=Decimal("0.00"),
            total_credit=Decimal("0.00"),
            report_year=expected_year,
        )

    entries: list[JournalEntry] = []
    conversion_issues: list[FinanceIssue] = []
    for index, row in enumerate(dataset.rows, start=1):
        entry_id = str(row.get("id") or f"{dataset.id}:{index}")
        occurred_on: date | None = None
        raw_date = row.get("date") or row.get("occurred_on")
        if raw_date:
            try:
                occurred_on = date.fromisoformat(str(raw_date))
            except ValueError:
                conversion_issues.append(
                    FinanceIssue("invalid_date", f"Invalid ISO date: {raw_date}", entry_id)
                )
        try:
            entries.append(
                JournalEntry(
                    id=entry_id,
                    debit_account=str(row.get("debit_account", "")),
                    credit_account=str(row.get("credit_account", "")),
                    amount=_money(row.get("amount"), label=f"entry {entry_id}"),
                    description=str(row.get("description", "")),
                    occurred_on=occurred_on,
                    source_id=dataset.source_ids[0] if dataset.source_ids else None,
                    synthetic_seed=dataset.synthetic_seed,
                )
            )
        except ValueError as exc:
            conversion_issues.append(FinanceIssue("invalid_amount", str(exc), entry_id))

    result = validate_double_entry(
        entries,
        opening_balances=opening_balances,
        account_kinds=account_kinds,
        expected_year=expected_year,
    )
    if not conversion_issues:
        return result
    return FinanceValidationResult(
        issues=tuple(conversion_issues) + result.issues,
        accounts=result.accounts,
        total_debit=result.total_debit,
        total_credit=result.total_credit,
        report_year=result.report_year,
    )
