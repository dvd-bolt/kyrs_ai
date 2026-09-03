"""Reproducible synthetic datasets with explicit provenance."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import Any, cast

from pydantic import JsonValue

from papercraft.domain import Dataset, DatasetColumn, DataType, FactOrigin


class SyntheticDataError(ValueError):
    pass


class Distribution(StrEnum):
    SEQUENCE = "sequence"
    INTEGER = "integer"
    UNIFORM = "uniform"
    NORMAL = "normal"
    CHOICE = "choice"
    BERNOULLI = "bernoulli"
    DATE_SEQUENCE = "date_sequence"


@dataclass(frozen=True, slots=True)
class SyntheticColumnSpec:
    name: str
    data_type: DataType
    distribution: Distribution
    unit: str | None = None
    nullable: bool = False
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise SyntheticDataError("column name must be non-empty")


@dataclass(frozen=True, slots=True)
class SyntheticDatasetSpec:
    project_id: str
    name: str
    row_count: int
    seed: int
    columns: tuple[SyntheticColumnSpec, ...]
    purpose: str

    def __post_init__(self) -> None:
        if not self.project_id.strip() or not self.name.strip() or not self.purpose.strip():
            raise SyntheticDataError("project_id, name and purpose must be non-empty")
        if not 1 <= self.row_count <= 1_000_000:
            raise SyntheticDataError("row_count must be between 1 and 1,000,000")
        names = [column.name for column in self.columns]
        if not names or len(names) != len(set(names)):
            raise SyntheticDataError("columns must be non-empty and uniquely named")


class SyntheticDatasetFactory:
    """Generate data using an isolated ``random.Random`` instance."""

    ALGORITHM = "python.random.Random:declarative-v1"

    def generate(self, spec: SyntheticDatasetSpec) -> Dataset:
        rng = random.Random(spec.seed)
        rows: list[dict[str, Any]] = []
        for row_index in range(spec.row_count):
            row: dict[str, Any] = {}
            for column in spec.columns:
                row[column.name] = self._value(column, row_index, rng)
            rows.append(row)

        metadata_spec: list[JsonValue] = [
            cast(
                JsonValue,
                {
                    "name": column.name,
                    "data_type": column.data_type.value,
                    "distribution": column.distribution.value,
                    "parameters": _json_safe_parameters(column.parameters),
                },
            )
            for column in spec.columns
        ]
        return Dataset(
            project_id=spec.project_id,
            name=spec.name,
            columns=[
                DatasetColumn(
                    name=column.name,
                    data_type=column.data_type,
                    unit=column.unit,
                    nullable=column.nullable,
                )
                for column in spec.columns
            ],
            rows=rows,
            origin=FactOrigin.SYNTHETIC,
            synthetic_seed=spec.seed,
            generation_method=self.ALGORITHM,
            metadata={
                "synthetic": True,
                "purpose": spec.purpose,
                "disclosure": (
                    "Модельные (синтетические) данные сформированы для учебной демонстрации; "
                    "они не являются наблюдениями реальной организации."
                ),
                "observation_status": "modelled_not_observed",
                "generator_version": 1,
                "column_specs": metadata_spec,
            },
        )

    def _value(self, column: SyntheticColumnSpec, row_index: int, rng: random.Random) -> Any:
        params = column.parameters
        distribution = column.distribution
        if distribution == Distribution.SEQUENCE:
            start = _number(params.get("start", 1), "start")
            step = _number(params.get("step", 1), "step")
            return self._cast(start + row_index * step, column.data_type)
        if distribution == Distribution.INTEGER:
            low = int(_number(params.get("minimum", 0), "minimum"))
            high = int(_number(params.get("maximum", 100), "maximum"))
            if low > high:
                raise SyntheticDataError(f"{column.name}: minimum exceeds maximum")
            return self._cast(rng.randint(low, high), column.data_type)
        if distribution == Distribution.UNIFORM:
            uniform_minimum = _number(params.get("minimum", 0), "minimum")
            uniform_maximum = _number(params.get("maximum", 1), "maximum")
            if uniform_minimum > uniform_maximum:
                raise SyntheticDataError(f"{column.name}: minimum exceeds maximum")
            value = rng.uniform(uniform_minimum, uniform_maximum)
            return self._cast(round(value, int(params.get("decimals", 2))), column.data_type)
        if distribution == Distribution.NORMAL:
            mean = _number(params.get("mean", 0), "mean")
            deviation = _number(params.get("standard_deviation", 1), "standard_deviation")
            if deviation < 0:
                raise SyntheticDataError(f"{column.name}: standard deviation is negative")
            value = rng.gauss(mean, deviation)
            if "minimum" in params:
                value = max(value, _number(params["minimum"], "minimum"))
            if "maximum" in params:
                value = min(value, _number(params["maximum"], "maximum"))
            return self._cast(round(value, int(params.get("decimals", 2))), column.data_type)
        if distribution == Distribution.CHOICE:
            choices = params.get("choices")
            if not isinstance(choices, (list, tuple)) or not choices:
                raise SyntheticDataError(f"{column.name}: choices must be a non-empty list")
            weights = params.get("weights")
            if weights is not None:
                if not isinstance(weights, (list, tuple)) or len(weights) != len(choices):
                    raise SyntheticDataError(f"{column.name}: invalid choice weights")
                if any(_number(weight, "weight") < 0 for weight in weights):
                    raise SyntheticDataError(f"{column.name}: choice weights cannot be negative")
                value = rng.choices(list(choices), weights=list(weights), k=1)[0]
            else:
                value = rng.choice(list(choices))
            return self._cast(value, column.data_type)
        if distribution == Distribution.BERNOULLI:
            probability = _number(params.get("probability", 0.5), "probability")
            if not 0 <= probability <= 1:
                raise SyntheticDataError(f"{column.name}: probability must be in [0, 1]")
            return self._cast(rng.random() < probability, column.data_type)
        if distribution == Distribution.DATE_SEQUENCE:
            try:
                start_date = date.fromisoformat(str(params.get("start", "2024-01-01")))
            except ValueError as exc:
                raise SyntheticDataError(f"{column.name}: start must be an ISO date") from exc
            step_days = int(_number(params.get("step_days", 1), "step_days"))
            return (start_date + timedelta(days=row_index * step_days)).isoformat()
        raise SyntheticDataError(f"unsupported distribution: {distribution}")

    @staticmethod
    def _cast(value: Any, data_type: DataType) -> Any:
        if data_type == DataType.STRING:
            return str(value)
        if data_type == DataType.INTEGER:
            return int(value)
        if data_type == DataType.NUMBER:
            return float(value)
        if data_type == DataType.BOOLEAN:
            return bool(value)
        if data_type in {DataType.DATE, DataType.DATETIME}:
            return str(value)
        raise SyntheticDataError(f"unsupported data type: {data_type}")


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise SyntheticDataError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SyntheticDataError(f"{label} must be numeric") from exc
    if result != result or result in {float("inf"), float("-inf")}:
        raise SyntheticDataError(f"{label} must be finite")
    return result


def _json_safe_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in parameters.items():
        if isinstance(value, tuple):
            result[key] = list(value)
        elif isinstance(value, (str, int, float, bool, list, dict)) or value is None:
            result[key] = value
        else:
            result[key] = str(value)
    return result
