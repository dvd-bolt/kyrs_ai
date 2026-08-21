"""A provenance-preserving, declarative fact ledger."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
from typing import Any

from papercraft.domain import Calculation, FactOrigin, FactRecord, new_id


class FactLedgerError(ValueError):
    """Base error raised for invalid ledger operations."""


class FactProvenanceError(FactLedgerError):
    """A fact does not contain the provenance required for its origin."""


class CalculationError(FactLedgerError):
    """A declarative calculation could not be completed safely."""


class CalculationOperation(StrEnum):
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"
    SUM = "sum"
    MEAN = "mean"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    RATIO = "ratio"
    PERCENT_CHANGE = "percent_change"


@dataclass(frozen=True, slots=True)
class CalculationRequest:
    """Declarative request whose inputs are existing fact identifiers."""

    name: str
    operation: CalculationOperation
    input_fact_ids: tuple[str, ...]
    output_name: str
    output_unit: str | None = None
    precision: int | None = None
    constraints: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.output_name.strip():
            raise CalculationError("calculation and output names must be non-empty")
        if not self.input_fact_ids:
            raise CalculationError("at least one input fact is required")
        if self.precision is not None and not 0 <= self.precision <= 12:
            raise CalculationError("precision must be between 0 and 12")


def _decimal(value: Any, *, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise CalculationError(f"{label} is not numeric")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CalculationError(f"{label} is not numeric: {value!r}") from exc
    if not number.is_finite():
        raise CalculationError(f"{label} must be finite")
    return number


def _json_number(value: Decimal) -> int | float:
    """Convert a finite Decimal into a Pydantic JsonValue-compatible number."""

    if value == value.to_integral_value():
        return int(value)
    converted = float(value)
    if not math.isfinite(converted):
        raise CalculationError("calculation result is outside JSON numeric range")
    return converted


class FactLedger:
    """In-memory authoritative index of facts for one project.

    The class performs calculations but never guesses missing facts and never
    parses or evaluates user/model supplied expressions.
    """

    def __init__(self, project_id: str, facts: Iterable[FactRecord] = ()) -> None:
        if not project_id.strip():
            raise FactLedgerError("project_id must be non-empty")
        self.project_id = project_id
        self._facts: dict[str, FactRecord] = {}
        self._calculations: dict[str, Calculation] = {}
        for fact in facts:
            self.add(fact)

    def __len__(self) -> int:
        return len(self._facts)

    def __contains__(self, fact_id: object) -> bool:
        return fact_id in self._facts

    @property
    def facts(self) -> tuple[FactRecord, ...]:
        return tuple(self._facts.values())

    @property
    def calculations(self) -> tuple[Calculation, ...]:
        return tuple(self._calculations.values())

    def get(self, fact_id: str) -> FactRecord:
        try:
            return self._facts[fact_id]
        except KeyError as exc:
            raise FactLedgerError(f"unknown fact id: {fact_id}") from exc

    def find_by_name(self, name: str) -> tuple[FactRecord, ...]:
        return tuple(fact for fact in self._facts.values() if fact.name == name)

    def add(self, fact: FactRecord, *, replace: bool = False) -> FactRecord:
        if fact.project_id != self.project_id:
            raise FactLedgerError("a fact cannot be added to a different project ledger")
        self.validate_provenance(fact)
        if fact.id in self._facts and not replace:
            raise FactLedgerError(f"duplicate fact id: {fact.id}")
        self._facts[fact.id] = fact
        return fact

    @staticmethod
    def validate_provenance(fact: FactRecord) -> None:
        if fact.origin == FactOrigin.VERIFIED_SOURCE and not (
            fact.source_id or fact.evidence_id
        ):
            raise FactProvenanceError(
                f"verified fact {fact.id} requires source_id or evidence_id"
            )
        if fact.origin == FactOrigin.CALCULATED and not fact.calculation_id:
            raise FactProvenanceError(f"calculated fact {fact.id} requires calculation_id")
        if fact.origin == FactOrigin.SYNTHETIC:
            if fact.synthetic_seed is None:
                raise FactProvenanceError(f"synthetic fact {fact.id} requires synthetic_seed")
            if not fact.generation_method:
                raise FactProvenanceError(
                    f"synthetic fact {fact.id} requires generation_method"
                )

    def calculate(self, request: CalculationRequest) -> tuple[Calculation, FactRecord]:
        input_facts = [self.get(fact_id) for fact_id in request.input_fact_ids]
        values = [_decimal(fact.value, label=f"fact {fact.id}") for fact in input_facts]
        result = self._apply(request.operation, values)
        if request.precision is not None:
            quantum = Decimal(1).scaleb(-request.precision)
            result = result.quantize(quantum)

        calculation_id = new_id()
        expression = (
            f"{request.operation.value}("
            + ", ".join(f"fact:{fact_id}" for fact_id in request.input_fact_ids)
            + ")"
        )
        output = FactRecord(
            id=new_id(),
            project_id=self.project_id,
            name=request.output_name,
            value=_json_number(result),
            unit=request.output_unit,
            origin=FactOrigin.CALCULATED,
            calculation_id=calculation_id,
            generation_method=f"declarative:{request.operation.value}:v1",
            constraints=dict(request.constraints),
            metadata={"input_fact_ids": list(request.input_fact_ids)},
        )
        calculation = Calculation(
            id=calculation_id,
            project_id=self.project_id,
            name=request.name,
            expression=expression,
            input_fact_ids=list(request.input_fact_ids),
            output_fact_id=output.id,
            result=output.value,
            checks={"finite": True, "provenance_complete": True},
            metadata={"operation": request.operation.value},
        )
        self._calculations[calculation.id] = calculation
        self.add(output)
        return calculation, output

    @staticmethod
    def _apply(operation: CalculationOperation, values: list[Decimal]) -> Decimal:
        binary = {
            CalculationOperation.SUBTRACT,
            CalculationOperation.DIVIDE,
            CalculationOperation.RATIO,
            CalculationOperation.PERCENT_CHANGE,
        }
        if operation in binary and len(values) != 2:
            raise CalculationError(f"{operation.value} requires exactly two inputs")
        if operation == CalculationOperation.MULTIPLY and len(values) < 2:
            raise CalculationError("multiply requires at least two inputs")

        with localcontext() as context:
            context.prec = 34
            if operation in {CalculationOperation.ADD, CalculationOperation.SUM}:
                return sum(values, Decimal(0))
            if operation == CalculationOperation.SUBTRACT:
                return values[0] - values[1]
            if operation == CalculationOperation.MULTIPLY:
                result = Decimal(1)
                for value in values:
                    result *= value
                return result
            if operation in {CalculationOperation.DIVIDE, CalculationOperation.RATIO}:
                if values[1] == 0:
                    raise CalculationError("division by zero")
                return values[0] / values[1]
            if operation == CalculationOperation.PERCENT_CHANGE:
                if values[0] == 0:
                    raise CalculationError("percent change has a zero baseline")
                return (values[1] - values[0]) / values[0] * Decimal(100)
            if operation == CalculationOperation.MEAN:
                return sum(values, Decimal(0)) / Decimal(len(values))
            if operation == CalculationOperation.MINIMUM:
                return min(values)
            if operation == CalculationOperation.MAXIMUM:
                return max(values)
        raise CalculationError(f"unsupported operation: {operation}")

    def validate_constraints(self) -> list[str]:
        """Return deterministic constraint violations without mutating facts.

        Supported constraints are ``minimum``, ``maximum``,
        ``equals_fact_id`` and ``sum_of_fact_ids``. Unknown keys are preserved
        for other validators and intentionally ignored here.
        """

        violations: list[str] = []
        for fact in self._facts.values():
            try:
                value = _decimal(fact.value, label=f"fact {fact.id}")
            except CalculationError:
                continue
            constraints = fact.constraints
            if "minimum" in constraints and value < _decimal(
                constraints["minimum"], label="minimum"
            ):
                violations.append(f"{fact.id}: value is below minimum")
            if "maximum" in constraints and value > _decimal(
                constraints["maximum"], label="maximum"
            ):
                violations.append(f"{fact.id}: value is above maximum")
            if "equals_fact_id" in constraints:
                other_id = str(constraints["equals_fact_id"])
                try:
                    other = _decimal(self.get(other_id).value, label=f"fact {other_id}")
                except FactLedgerError:
                    violations.append(f"{fact.id}: referenced fact {other_id} is missing")
                else:
                    tolerance = _decimal(constraints.get("tolerance", 0), label="tolerance")
                    if abs(value - other) > tolerance:
                        violations.append(f"{fact.id}: does not equal {other_id}")
            if "sum_of_fact_ids" in constraints:
                ids = constraints["sum_of_fact_ids"]
                if not isinstance(ids, list):
                    violations.append(f"{fact.id}: sum_of_fact_ids must be a list")
                    continue
                try:
                    expected = sum(
                        (_decimal(self.get(str(item)).value, label=str(item)) for item in ids),
                        Decimal(0),
                    )
                except FactLedgerError as exc:
                    violations.append(f"{fact.id}: {exc}")
                else:
                    tolerance = _decimal(constraints.get("tolerance", 0), label="tolerance")
                    if abs(value - expected) > tolerance:
                        violations.append(f"{fact.id}: does not equal referenced sum")
        return violations
