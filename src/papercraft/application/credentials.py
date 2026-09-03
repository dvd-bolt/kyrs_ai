"""Credential Manager backed Gemini lifecycle service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from papercraft.config import AppSettings
from papercraft.infrastructure.gemini import (
    CredentialStoreUnavailableError,
    GeminiAuthenticationError,
    GeminiGateway,
    GeminiUnavailableError,
    SecretStore,
)

from .api.contracts import CredentialStatus, ProviderCheck


class _CandidateSecretStore:
    """One-use in-memory key for validation before Credential Manager write."""

    def __init__(self, value: str) -> None:
        self._value = value

    def get_api_key(self) -> str:
        return self._value

    def set_api_key(self, value: str) -> None:
        self._value = value

    def delete_api_key(self) -> None:
        self._value = ""


GatewayFactory = Callable[[AppSettings, SecretStore], GeminiGateway]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class GeminiCredentialService:
    """Keeps credential checks safe, ephemeral, and separate from project data."""

    def __init__(
        self,
        settings: AppSettings,
        store: SecretStore,
        *,
        gateway_factory: GatewayFactory | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._gateway_factory = gateway_factory or (lambda settings, store: GeminiGateway(settings, secret_store=store))
        self._last_check: ProviderCheck | None = None

    def status(self) -> CredentialStatus:
        try:
            configured = self._store.get_api_key() is not None
        except CredentialStoreUnavailableError:
            return CredentialStatus(
                configured=False,
                verified=False,
                state="missing",
                safe_message="Windows Credential Manager is unavailable.",
            )
        if not configured:
            return CredentialStatus(
                configured=False,
                verified=False,
                state="missing",
                safe_message="Gemini key is not configured.",
            )
        if self._last_check is None:
            return CredentialStatus(
                configured=True,
                verified=False,
                state="unverified",
                safe_message="Gemini key has not been verified in this session.",
            )
        return CredentialStatus(
            configured=True,
            verified=self._last_check.ok,
            state="valid" if self._last_check.ok else "invalid" if not self._last_check.retryable else "unverified",
            last_checked_at=self._last_check.checked_at,
            safe_message=self._last_check.safe_message,
        )

    def configure(self, api_key: str) -> CredentialStatus:
        candidate = api_key.strip()
        if not candidate:
            raise ValueError("Gemini API key cannot be empty")
        check = self._check(_CandidateSecretStore(candidate))
        # An explicit authentication rejection never replaces a known-good
        # stored key. A network/quota failure is inconclusive, so retain the
        # new key and let the durable worker retry later.
        if check.state != "invalid":
            self._store.set_api_key(candidate)
        self._last_check = check
        return self.status()

    def verify(self) -> ProviderCheck:
        if self._store.get_api_key() is None:
            raise ValueError("Gemini key is not configured")
        self._last_check = self._check(self._store)
        return self._last_check

    def delete(self) -> None:
        self._store.delete_api_key()
        self._last_check = None

    def _check(self, store: SecretStore) -> ProviderCheck:
        checked_at = _now()
        try:
            self._gateway_factory(self._settings, store).health_check(fail_fast=True)
        except GeminiAuthenticationError:
            return ProviderCheck(
                ok=False, state="invalid", checked_at=checked_at, retryable=False,
                safe_message="Gemini rejected the API key.",
            )
        except (GeminiUnavailableError, CredentialStoreUnavailableError):
            return ProviderCheck(
                ok=False, state="unavailable", checked_at=checked_at, retryable=True,
                safe_message="Gemini is temporarily unavailable; the key was kept for retry.",
            )
        return ProviderCheck(
            ok=True, state="valid", checked_at=checked_at, retryable=False,
            safe_message="Gemini key verified.",
        )


__all__ = ["GatewayFactory", "GeminiCredentialService"]
