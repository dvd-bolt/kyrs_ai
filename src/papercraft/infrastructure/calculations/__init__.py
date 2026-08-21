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
    "AccountKind",
    "AccountTurnover",
    "CalculationError",
    "CalculationOperation",
    "CalculationRequest",
    "Distribution",
    "FactLedger",
    "FactLedgerError",
    "FactProvenanceError",
    "FinanceIssue",
    "FinanceValidationResult",
    "JournalEntry",
    "StatisticsSummary",
    "SyntheticColumnSpec",
    "SyntheticDataError",
    "SyntheticDatasetFactory",
    "SyntheticDatasetSpec",
    "TabularDatasetImporter",
    "TabularImportError",
    "correlation",
    "percentage",
    "summarize",
    "validate_double_entry",
    "validate_finance_dataset",
]
