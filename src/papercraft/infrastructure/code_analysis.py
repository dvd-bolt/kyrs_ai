"""Static-only source analysis backed by Python AST and pinned Tree-sitter grammars."""

from __future__ import annotations

import ast
import hashlib
import importlib
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from papercraft.domain import (
    CodeDependency,
    CodeEndpoint,
    CodeFileAnalysis,
    CodeFinding,
    CodeLanguage,
    CodeSymbol,
    Locator,
    Source,
)

if TYPE_CHECKING:
    from papercraft.infrastructure.ingest.security import SecretScanner


_LANGUAGES: dict[str, CodeLanguage] = {
    ".py": CodeLanguage.PYTHON,
    ".pyi": CodeLanguage.PYTHON,
    ".js": CodeLanguage.JAVASCRIPT,
    ".jsx": CodeLanguage.JAVASCRIPT,
    ".ts": CodeLanguage.TYPESCRIPT,
    ".tsx": CodeLanguage.TYPESCRIPT,
    ".java": CodeLanguage.JAVA,
    ".c": CodeLanguage.C,
    ".h": CodeLanguage.C,
    ".cpp": CodeLanguage.CPP,
    ".cc": CodeLanguage.CPP,
    ".cxx": CodeLanguage.CPP,
    ".hpp": CodeLanguage.CPP,
    ".cs": CodeLanguage.CSHARP,
}
type SymbolKind = Literal[
    "class", "function", "method", "interface", "enum", "constructor", "symbol"
]
type DependencyKind = Literal["import", "include", "using", "require"]
type FindingSeverity = Literal["info", "warning", "error"]

_SYMBOL_TYPES: dict[str, SymbolKind] = {
    "class_definition": "class",
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "function_definition": "function",
    "function_declaration": "function",
    "function_signature": "function",
    "method_definition": "method",
    "method_declaration": "method",
    "method_signature": "method",
    "constructor_declaration": "constructor",
}
_IMPORT_TYPES = {"import_statement", "import_declaration", "import_from_statement"}
_ENTRYPOINT_NAMES = {"main", "Main"}
_INSTRUCTION_COMMENT = re.compile(
    r"(?i)(?:ignore (?:all |previous )?instructions|system prompt|you are chatgpt|jailbreak)"
)


def _decode(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            return data.decode(encoding), encoding
        except UnicodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


class StaticCodeAnalyzer:
    """Analyze imported bytes without loading, importing, or executing them."""

    def __init__(self, *, secret_scanner: SecretScanner | None = None) -> None:
        if secret_scanner is None:
            from papercraft.infrastructure.ingest.security import SecretScanner

            secret_scanner = SecretScanner()
        self._secret_scanner = secret_scanner

    def analyze(self, source: Source) -> CodeFileAnalysis:
        path = Path(source.stored_path)
        data = path.read_bytes()
        text, _ = _decode(data)
        source_hash = hashlib.sha256(data).hexdigest()
        language = _LANGUAGES.get(path.suffix.casefold(), CodeLanguage.UNKNOWN)
        if language == CodeLanguage.PYTHON:
            result = self._analyze_python(source, text, source_hash)
        elif language == CodeLanguage.UNKNOWN:
            result = self._fallback(source, source_hash, "Unsupported source language.")
        else:
            result = self._analyze_tree_sitter(source, text, source_hash, language)
        if source.sha256 != source_hash:
            result.findings.append(
                self._finding(
                    source,
                    source_hash,
                    "source_hash_mismatch",
                    "error",
                    "Imported source bytes do not match the recorded source hash.",
                    1,
                )
            )
        result.findings.extend(self._security_findings(source, text, source_hash))
        result.findings.sort(key=lambda item: (item.locator.line_start or 0, item.rule_id))
        return result

    def _analyze_python(self, source: Source, text: str, source_hash: str) -> CodeFileAnalysis:
        try:
            tree = ast.parse(text, filename=source.original_name, mode="exec")
        except SyntaxError as error:
            analysis = self._base(source, source_hash, CodeLanguage.PYTHON, "python-ast", 0.65)
            analysis.findings.append(
                self._finding(
                    source,
                    source_hash,
                    "syntax_error",
                    "error",
                    "Python syntax error; analysis is incomplete.",
                    error.lineno or 1,
                    error.end_lineno or error.lineno or 1,
                )
            )
            return analysis

        analysis = self._base(source, source_hash, CodeLanguage.PYTHON, "python-ast", 1.0)
        self._mark_python_module_parents(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                kind: SymbolKind = "class" if isinstance(node, ast.ClassDef) else "function"
                symbol = self._symbol(
                    source,
                    source_hash,
                    node.name,
                    kind,
                    node.lineno,
                    node.end_lineno or node.lineno,
                )
                analysis.symbols.append(symbol)
                if node.name in _ENTRYPOINT_NAMES:
                    analysis.entrypoints.append(symbol)
                if node.name.startswith("test_"):
                    analysis.tests.append(symbol)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._python_function_findings(analysis, source, source_hash, node)
                    endpoint = self._python_endpoint(source, source_hash, node)
                    if endpoint is not None:
                        analysis.endpoints.append(endpoint)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    analysis.dependencies.append(
                        self._dependency(
                            source,
                            source_hash,
                            alias.name,
                            "import",
                            node.lineno,
                            node.end_lineno or node.lineno,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                name = "." * node.level + (node.module or "")
                analysis.dependencies.append(
                    self._dependency(
                        source,
                        source_hash,
                        name or ".",
                        "import",
                        node.lineno,
                        node.end_lineno or node.lineno,
                    )
                )
            elif isinstance(node, ast.ExceptHandler) and node.type is None:
                analysis.findings.append(
                    self._finding(
                        source,
                        source_hash,
                        "bare_except",
                        "warning",
                        "Bare except catches system-exit exceptions.",
                        node.lineno,
                        node.end_lineno or node.lineno,
                    )
                )
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(
                getattr(node, "parent", None), ast.Module
            ):
                value = getattr(node, "value", None)
                if isinstance(value, (ast.List, ast.Dict, ast.Set)):
                    analysis.findings.append(
                        self._finding(
                            source,
                            source_hash,
                            "module_global_mutable",
                            "warning",
                            "Module-level mutable state can leak across calls.",
                            node.lineno,
                            node.end_lineno or node.lineno,
                        )
                    )
        analysis.symbols.sort(key=self._symbol_key)
        analysis.dependencies.sort(key=lambda item: (item.locator.line_start or 0, item.name))
        return analysis

    @staticmethod
    def _mark_python_module_parents(tree: ast.AST) -> None:
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                child.parent = parent  # type: ignore[attr-defined]

    def _python_function_findings(
        self,
        analysis: CodeFileAnalysis,
        source: Source,
        source_hash: str,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        end = node.end_lineno or node.lineno
        if end - node.lineno + 1 > 60:
            analysis.findings.append(
                self._finding(
                    source,
                    source_hash,
                    "long_function",
                    "warning",
                    "Function exceeds 60 lines.",
                    node.lineno,
                    end,
                )
            )
        parameters = len(node.args.posonlyargs) + len(node.args.args) + len(node.args.kwonlyargs)
        if parameters > 5:
            analysis.findings.append(
                self._finding(
                    source,
                    source_hash,
                    "too_many_parameters",
                    "warning",
                    "Function has more than five parameters.",
                    node.lineno,
                    end,
                )
            )

    def _python_endpoint(
        self, source: Source, source_hash: str, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> CodeEndpoint | None:
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr.upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "ROUTE"}:
                continue
            route = (
                decorator.args[0].value
                if decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and isinstance(decorator.args[0].value, str)
                else None
            )
            return CodeEndpoint(
                name=node.name,
                method=method,
                path=route,
                locator=self._locator(
                    source, source_hash, node.lineno, node.end_lineno or node.lineno
                ),
            )
        return None

    def _analyze_tree_sitter(
        self, source: Source, text: str, source_hash: str, language: CodeLanguage
    ) -> CodeFileAnalysis:
        try:
            parser = self._tree_sitter_parser(language)
        except (ImportError, ValueError):
            return self._fallback(source, source_hash, "Pinned Tree-sitter grammar is unavailable.")
        tree = parser.parse(text.encode("utf-8"))
        analysis = self._base(source, source_hash, language, "tree-sitter", 1.0)
        root = tree.root_node
        if root.has_error:
            error_node = next((node for node in self._nodes(root) if node.type == "ERROR"), root)
            analysis.confidence = 0.65
            analysis.findings.append(
                self._finding(
                    source,
                    source_hash,
                    "syntax_error",
                    "error",
                    "Parser reported a syntax error; analysis is incomplete.",
                    error_node.start_point.row + 1,
                    max(error_node.start_point.row + 1, error_node.end_point.row + 1),
                )
            )
        for node in self._nodes(root):
            symbol_kind = _SYMBOL_TYPES.get(node.type)
            if symbol_kind is not None:
                name = self._node_name(node, text)
                if name:
                    symbol = self._symbol(
                        source,
                        source_hash,
                        name,
                        symbol_kind,
                        node.start_point.row + 1,
                        max(node.start_point.row + 1, node.end_point.row + 1),
                    )
                    analysis.symbols.append(symbol)
                    if name in _ENTRYPOINT_NAMES:
                        analysis.entrypoints.append(symbol)
                    if name.startswith(("test", "Test")):
                        analysis.tests.append(symbol)
                    if (
                        symbol.kind in {"function", "method"}
                        and (symbol.locator.line_end or 0) - (symbol.locator.line_start or 0) + 1
                        > 60
                    ):
                        analysis.findings.append(
                            self._finding(
                                source,
                                source_hash,
                                "long_function",
                                "warning",
                                "Function exceeds 60 lines.",
                                symbol.locator.line_start or 1,
                                symbol.locator.line_end or 1,
                            )
                        )
            if node.type in _IMPORT_TYPES:
                raw = text.encode("utf-8")[node.start_byte : node.end_byte].decode(
                    "utf-8", errors="replace"
                )
                for name, kind in self._tree_dependencies(raw):
                    analysis.dependencies.append(
                        self._dependency(
                            source,
                            source_hash,
                            name,
                            kind,
                            node.start_point.row + 1,
                            max(node.start_point.row + 1, node.end_point.row + 1),
                        )
                    )
            elif node.type == "preproc_include":
                raw = text.encode("utf-8")[node.start_byte : node.end_byte].decode(
                    "utf-8", errors="replace"
                )
                include = re.search(r"[<\"]([^>\"]+)[>\"]", raw)
                if include:
                    analysis.dependencies.append(
                        self._dependency(
                            source,
                            source_hash,
                            include.group(1),
                            "include",
                            node.start_point.row + 1,
                            max(node.start_point.row + 1, node.end_point.row + 1),
                        )
                    )
            elif node.type == "using_directive":
                raw = text.encode("utf-8")[node.start_byte : node.end_byte].decode(
                    "utf-8", errors="replace"
                )
                name = raw.removeprefix("using").strip().rstrip(";")
                if name:
                    analysis.dependencies.append(
                        self._dependency(
                            source,
                            source_hash,
                            name,
                            "using",
                            node.start_point.row + 1,
                            max(node.start_point.row + 1, node.end_point.row + 1),
                        )
                    )
        self._tree_endpoints(analysis, source, source_hash, text)
        analysis.symbols = self._deduplicate(analysis.symbols, self._symbol_key)
        analysis.dependencies = self._deduplicate(
            analysis.dependencies, lambda item: (item.locator.line_start or 0, item.name, item.kind)
        )
        return analysis

    @staticmethod
    def _tree_sitter_parser(language: CodeLanguage) -> Any:
        from tree_sitter import Language, Parser

        grammar_name = {
            CodeLanguage.JAVASCRIPT: "tree_sitter_javascript",
            CodeLanguage.TYPESCRIPT: "tree_sitter_typescript",
            CodeLanguage.JAVA: "tree_sitter_java",
            CodeLanguage.C: "tree_sitter_c",
            CodeLanguage.CPP: "tree_sitter_cpp",
            CodeLanguage.CSHARP: "tree_sitter_c_sharp",
        }.get(language)
        if grammar_name is None:
            raise ValueError(f"No Tree-sitter grammar for {language}")
        grammar = cast(Any, importlib.import_module(grammar_name))
        if language == CodeLanguage.TYPESCRIPT:
            capsule = grammar.language_typescript()
        else:
            capsule = grammar.language()
        return Parser(Language(capsule))

    @staticmethod
    def _nodes(node: Any) -> Iterator[Any]:
        yield node
        for child in node.children:
            yield from StaticCodeAnalyzer._nodes(child)

    @staticmethod
    def _node_name(node: Any, text: str) -> str | None:
        candidate = node.child_by_field_name("name")
        if candidate is None:
            declarator = node.child_by_field_name("declarator")
            if declarator is not None:
                candidate = next(
                    (
                        child
                        for child in StaticCodeAnalyzer._nodes(declarator)
                        if child.type in {"identifier", "type_identifier"}
                    ),
                    None,
                )
        if candidate is None:
            candidate = next(
                (
                    child
                    for child in node.children
                    if child.type in {"identifier", "type_identifier", "property_identifier"}
                ),
                None,
            )
        if candidate is None:
            return None
        return (
            text.encode("utf-8")[candidate.start_byte : candidate.end_byte]
            .decode("utf-8", errors="replace")
            .strip()
            or None
        )

    @staticmethod
    def _tree_dependencies(raw: str) -> list[tuple[str, DependencyKind]]:
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", raw)
        if quoted:
            return [(name, "require" if "require(" in raw else "import") for name in quoted]
        name = re.sub(r"^(?:import|from)\s+", "", raw).split()[0] if raw.strip() else ""
        return [(name.rstrip(";"), "import")] if name else []

    def _tree_endpoints(
        self, analysis: CodeFileAnalysis, source: Source, source_hash: str, text: str
    ) -> None:
        for match in re.finditer(
            r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|RequestMapping)\s*\(\s*['\"]?([^'\"),\s]+)?",
            text,
        ):
            line = text.count("\n", 0, match.start()) + 1
            analysis.endpoints.append(
                CodeEndpoint(
                    name="endpoint",
                    method=match.group(1).removesuffix("Mapping").upper(),
                    path=match.group(2),
                    locator=self._locator(source, source_hash, line, line),
                )
            )

    def _fallback(self, source: Source, source_hash: str, message: str) -> CodeFileAnalysis:
        analysis = self._base(
            source,
            source_hash,
            _LANGUAGES.get(Path(source.stored_path).suffix.casefold(), CodeLanguage.UNKNOWN),
            "fallback-text",
            0.4,
        )
        analysis.findings.append(
            self._finding(source, source_hash, "parser_fallback", "warning", message, 1)
        )
        return analysis

    @staticmethod
    def _base(
        source: Source, source_hash: str, language: CodeLanguage, parser: str, confidence: float
    ) -> CodeFileAnalysis:
        return CodeFileAnalysis(
            source_id=source.id,
            file=source.original_name,
            source_hash=source_hash,
            language=language,
            parser=parser,
            confidence=confidence,
        )

    def _locator(
        self, source: Source, source_hash: str, start: int, end: int | None = None
    ) -> Locator:
        return Locator(
            source_id=source.id,
            line_start=max(1, start),
            line_end=max(1, end or start),
            details={"path": source.original_name, "source_hash": source_hash},
        )

    def _symbol(
        self, source: Source, source_hash: str, name: str, kind: SymbolKind, start: int, end: int
    ) -> CodeSymbol:
        return CodeSymbol(
            name=name, kind=kind, locator=self._locator(source, source_hash, start, end)
        )

    def _dependency(
        self,
        source: Source,
        source_hash: str,
        name: str,
        kind: DependencyKind,
        start: int,
        end: int,
    ) -> CodeDependency:
        return CodeDependency(
            name=name, kind=kind, locator=self._locator(source, source_hash, start, end)
        )

    def _finding(
        self,
        source: Source,
        source_hash: str,
        rule_id: str,
        severity: FindingSeverity,
        message: str,
        start: int,
        end: int | None = None,
    ) -> CodeFinding:
        return CodeFinding(
            rule_id=rule_id,
            severity=severity,
            message=message,
            locator=self._locator(source, source_hash, start, end),
        )

    def _security_findings(self, source: Source, text: str, source_hash: str) -> list[CodeFinding]:
        findings = [
            self._finding(
                source,
                source_hash,
                "embedded_secret",
                "error",
                f"Potential {item.kind.replace('_', ' ')} detected; value is redacted.",
                item.line,
            )
            for item in self._secret_scanner.scan_text(text)
        ]
        for match in _INSTRUCTION_COMMENT.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(
                self._finding(
                    source,
                    source_hash,
                    "untrusted_instruction_comment",
                    "warning",
                    "Instruction-like comment ignored during static analysis.",
                    line,
                )
            )
        return findings

    @staticmethod
    def _deduplicate[T](items: Iterable[T], key: Any) -> list[T]:
        return [
            item
            for _, item in sorted(
                {key(item): item for item in items}.items(), key=lambda pair: pair[0]
            )
        ]

    @staticmethod
    def _symbol_key(item: CodeSymbol) -> tuple[int, str, str]:
        return (item.locator.line_start or 0, item.name, item.kind)


def legacy_code_metadata(analysis: CodeFileAnalysis) -> dict[str, Any]:
    """Expose a safe compatibility projection for existing ingest fragments."""

    def symbol(item: CodeSymbol) -> dict[str, Any]:
        return {
            "kind": item.kind,
            "name": item.name,
            "line": item.locator.line_start,
            "end_line": item.locator.line_end,
        }

    def dependency(item: CodeDependency) -> dict[str, Any]:
        return {"name": item.name, "line": item.locator.line_start, "kind": item.kind}

    def endpoint(item: CodeEndpoint) -> dict[str, Any]:
        return {
            "name": item.name,
            "line": item.locator.line_start,
            "method": item.method,
            "path": item.path,
        }

    return {
        "language": analysis.language.value,
        "source_hash": analysis.source_hash,
        "parser": analysis.parser,
        "confidence": analysis.confidence,
        "symbols": [symbol(item) for item in analysis.symbols],
        "code_analysis": {
            "dependencies": [dependency(item) for item in analysis.dependencies],
            "entrypoints": [symbol(item) for item in analysis.entrypoints],
            "api_endpoints": [endpoint(item) for item in analysis.endpoints],
            "classes": [symbol(item) for item in analysis.symbols if item.kind == "class"],
            "functions": [
                symbol(item) for item in analysis.symbols if item.kind in {"function", "method"}
            ],
            "tests": [symbol(item) for item in analysis.tests],
            "findings": [
                {
                    "rule_id": item.rule_id,
                    "severity": item.severity,
                    "line": item.locator.line_start,
                }
                for item in analysis.findings
            ],
        },
    }
