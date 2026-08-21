"""Small deterministic statistics layer with unit-aware invariants."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import median


@dataclass(frozen=True, slots=True)
class StatisticsSummary:
    count: int
    total: float
    mean: float
    median: float
    minimum: float
    maximum: float


def summarize(values: list[float]) -> StatisticsSummary:
    if not values:
        raise ValueError("statistics require at least one value")
    total = sum(values)
    return StatisticsSummary(
        count=len(values), total=total, mean=total / len(values), median=float(median(values)),
        minimum=min(values), maximum=max(values)
    )


def percentage(part: float, total: float) -> float:
    if total == 0:
        raise ValueError("percentage denominator must not be zero")
    return part / total * 100


def correlation(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("correlation requires equal series of at least two values")
    left_mean, right_mean = sum(left) / len(left), sum(right) / len(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True))
    left_scale = sqrt(sum((x - left_mean) ** 2 for x in left))
    right_scale = sqrt(sum((y - right_mean) ** 2 for y in right))
    if not left_scale or not right_scale:
        raise ValueError("correlation is undefined for a constant series")
    return numerator / (left_scale * right_scale)


__all__ = ["StatisticsSummary", "correlation", "percentage", "summarize"]
