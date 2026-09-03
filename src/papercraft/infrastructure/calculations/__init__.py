"""Deterministic calculations and synthetic data generation.

This package deliberately contains no expression evaluator.  Every supported
operation is selected from a small declarative vocabulary and receives typed
inputs from the project fact ledger.
"""

from .finance import (
    AccountKind,
    AccountTurnover,
    FinanceIssue,
    FinanceValidationResult,
    JournalEntry,
    validate_double_entry,
    validate_finance_dataset,
)
from .financial import (
    AccountingPosting,
    CalculationResult,
    CalculationSpec,
    FinancialCalculationError,
    LoanMethod,
    LoanPayment,
    MonetaryAmount,
    PostingLeg,
    PostingSide,
    ReportingPeriod,
    TrialBalance,
    TrialBalanceLine,
    break_even,
    build_trial_balance,
    financial_ratios,
    horizontal_analysis,
    investment_metrics,
    loan_schedule,
    vertical_analysis,
)
from .financial_catalog import RAS_2026, AccountDefinition, RASAccountCatalog
from .ledger import (
    CalculationError,
    CalculationOperation,
    CalculationRequest,
    FactLedger,
    FactLedgerError,
    FactProvenanceError,
)
from .statistics import StatisticsSummary, correlation, percentage, summarize
from .synthetic import (
    Distribution,
    SyntheticColumnSpec,
    SyntheticDataError,
    SyntheticDatasetFactory,
    SyntheticDatasetSpec,
)
from .tabular import TabularDatasetImporter, TabularImportError

__all__ = [
    "RAS_2026",
    "AccountDefinition",
    "AccountKind",
    "AccountTurnover",
    "AccountingPosting",
    "CalculationError",
    "CalculationOperation",
    "CalculationRequest",
    "CalculationResult",
    "CalculationSpec",
    "Distribution",
    "FactLedger",
    "FactLedgerError",
    "FactProvenanceError",
    "FinanceIssue",
    "FinanceValidationResult",
    "FinancialCalculationError",
    "JournalEntry",
    "LoanMethod",
    "LoanPayment",
    "MonetaryAmount",
    "PostingLeg",
    "PostingSide",
    "RASAccountCatalog",
    "ReportingPeriod",
    "StatisticsSummary",
    "SyntheticColumnSpec",
    "SyntheticDataError",
    "SyntheticDatasetFactory",
    "SyntheticDatasetSpec",
    "TabularDatasetImporter",
    "TabularImportError",
    "TrialBalance",
    "TrialBalanceLine",
    "break_even",
    "build_trial_balance",
    "correlation",
    "financial_ratios",
    "horizontal_analysis",
    "investment_metrics",
    "loan_schedule",
    "percentage",
    "summarize",
    "validate_double_entry",
    "validate_finance_dataset",
    "vertical_analysis",
]
