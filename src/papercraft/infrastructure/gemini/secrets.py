from __future__ import annotations

from typing import Any, Protocol


class SecretStore(Protocol):
    def get_api_key(self) -> str | None: ...

    def set_api_key(self, value: str) -> None: ...

    def delete_api_key(self) -> None: ...


class CredentialStoreUnavailableError(RuntimeError):
    """Windows Credential Manager cannot safely service a credential request."""


class CredentialSecretStore:
    """Store the Gemini key in Windows Credential Manager via ``keyring``.

    This is intentionally fail-closed.  An environment variable is not an
    acceptable fallback because it leaks into child processes and diagnostic
    surfaces.  The value is never persisted in a PaperCraft project database,
    settings file, log, command line, or worker event.
    """

    service_name = "PaperCraftAI"
    username = "gemini-api-key"

    def __init__(self, *, keyring_module: Any | None = None) -> None:
        self._keyring_module = keyring_module

    def _keyring(self) -> Any | None:
        if self._keyring_module is not None:
            return self._keyring_module
        try:
            import keyring
        except ImportError:
            return None
        return keyring

    def get_api_key(self) -> str | None:
        keyring = self._keyring()
        if keyring is None:
            raise CredentialStoreUnavailableError("Windows Credential Manager is unavailable")
        try:
            value = keyring.get_password(self.service_name, self.username)
        except Exception as exc:
            raise CredentialStoreUnavailableError("Windows Credential Manager is unavailable") from exc
        return str(value).strip() if value and str(value).strip() else None

    def set_api_key(self, value: str) -> None:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Gemini API key cannot be empty")
        keyring = self._keyring()
        if keyring is None:
            raise CredentialStoreUnavailableError("Windows Credential Manager is unavailable")
        try:
            keyring.set_password(self.service_name, self.username, cleaned)
        except Exception as exc:
            raise CredentialStoreUnavailableError("Windows Credential Manager is unavailable") from exc

    def delete_api_key(self) -> None:
        keyring = self._keyring()
        if keyring is None:
            raise CredentialStoreUnavailableError("Windows Credential Manager is unavailable")
        try:
            keyring.delete_password(self.service_name, self.username)
        except Exception as exc:
            # Deleting a missing entry is idempotent on the native backend;
            # other errors must not masquerade as successful deletion.
            if type(exc).__name__ != "PasswordDeleteError":
                raise CredentialStoreUnavailableError("Windows Credential Manager is unavailable") from exc
