"""Secret and unsafe-file detection used before project import."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SecretFinding:
    kind: str
    line: int
    start: int
    end: int
    redacted: str
    confidence: float


_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), 1.0),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), 0.99),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), 0.99),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"), 0.99),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), 0.94),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*"
            r"[\"']?([A-Za-z0-9_./+\-=]{12,})"
        ),
        0.90,
    ),
)

_SECRET_FILENAMES = {
    ".env", ".env.local", ".env.production", ".npmrc", ".pypirc",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials",
    "credentials.json", "secrets.json",
}
_SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}
_EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "build",
    "dist", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".idea", ".vs",
}


def is_secret_path(path: str | Path) -> bool:
    candidate = Path(path)
    lower_name = candidate.name.casefold()
    return lower_name in _SECRET_FILENAMES or candidate.suffix.casefold() in _SECRET_SUFFIXES


def is_excluded_path(path: str | Path) -> bool:
    return any(part.casefold() in _EXCLUDED_DIRS for part in Path(path).parts)


def looks_binary(data: bytes) -> bool:
    """Conservatively identify opaque binary content.

    UTF-8/UTF-16 and common textual source files are not classified as binary.
    """

    if not data:
        return False
    if data.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
        return False
    if b"\x00" in data:
        # UTF-16 typically has a NUL in every other byte.
        even_nuls = data[::2].count(0) / max(1, len(data[::2]))
        odd_nuls = data[1::2].count(0) / max(1, len(data[1::2]))
        return max(even_nuls, odd_nuls) <= 0.6
    allowed = sum(byte in b"\t\n\r\f\b" or 32 <= byte <= 126 or byte >= 128 for byte in data)
    return allowed / len(data) < 0.85


def _redact(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}{'*' * min(12, len(value) - 6)}{value[-3:]}"


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    frequencies = {character: value.count(character) / len(value) for character in set(value)}
    return -sum(probability * math.log2(probability) for probability in frequencies.values())


class SecretScanner:
    """Find high-confidence credentials without ever returning their clear text."""

    def scan_text(self, text: str) -> list[SecretFinding]:
        findings: list[SecretFinding] = []
        for kind, pattern, confidence in _PATTERNS:
            for match in pattern.finditer(text):
                captured = match.group(1) if match.lastindex else match.group(0)
                if kind == "assigned_secret" and _shannon_entropy(captured) < 2.8:
                    continue
                start = match.start(1) if match.lastindex else match.start()
                findings.append(
                    SecretFinding(
                        kind=kind,
                        line=text.count("\n", 0, start) + 1,
                        start=start,
                        end=match.end(1) if match.lastindex else match.end(),
                        redacted=_redact(captured),
                        confidence=confidence,
                    )
                )
        return sorted(findings, key=lambda finding: (finding.start, finding.kind))

    def scan_bytes(self, data: bytes) -> list[SecretFinding]:
        if looks_binary(data):
            return []
        for encoding in ("utf-8-sig", "utf-16", "cp1251"):
            try:
                return self.scan_text(data.decode(encoding))
            except (UnicodeDecodeError, UnicodeError):
                continue
        return []

    def scan_file(self, path: str | Path, max_bytes: int = 2_000_000) -> list[SecretFinding]:
        with Path(path).open("rb") as handle:
            return self.scan_bytes(handle.read(max_bytes + 1)[:max_bytes])


EXCLUDED_DIRS = frozenset(_EXCLUDED_DIRS)
