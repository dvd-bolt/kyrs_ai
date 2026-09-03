"""Typed, provenance-preserving results of static source-code analysis."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from .models import DomainModel, Locator


class CodeLanguage(StrEnum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    JAVA = "java"
    C = "c"
    CPP = "cpp"
    CSHARP = "csharp"
    UNKNOWN = "unknown"


class CodeSymbol(DomainModel):
    name: str = Field(min_length=1)
    kind: Literal["class", "function", "method", "interface", "enum", "constructor", "symbol"]
    locator: Locator


class CodeDependency(DomainModel):
    name: str = Field(min_length=1)
    kind: Literal["import", "include", "using", "require"] = "import"
    locator: Locator


class CodeEndpoint(DomainModel):
    name: str = Field(min_length=1)
    method: str | None = None
    path: str | None = None
    locator: Locator


class CodeFinding(DomainModel):
    rule_id: str = Field(min_length=1)
    severity: Literal["info", "warning", "error"]
    message: str = Field(min_length=1)
    locator: Locator


class CodeFileAnalysis(DomainModel):
    """One file's static result, always tied to exact imported bytes."""

    source_id: str = Field(min_length=1)
    file: str = Field(min_length=1)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    language: CodeLanguage
    parser: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    symbols: list[CodeSymbol] = Field(default_factory=list)
    dependencies: list[CodeDependency] = Field(default_factory=list)
    entrypoints: list[CodeSymbol] = Field(default_factory=list)
    tests: list[CodeSymbol] = Field(default_factory=list)
    endpoints: list[CodeEndpoint] = Field(default_factory=list)
    findings: list[CodeFinding] = Field(default_factory=list)
