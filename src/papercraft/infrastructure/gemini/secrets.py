from __future__ import annotations

import os
from typing import Any, Protocol


class SecretStore(Protocol):
    def get_api_key(self) -> str | None: ...

    def set_api_key(self, value: str) -> None: ...

    def delete_api_key(self) -> None: ...


class CredentialSecretStore:
    """Store the Gemini key in Windows Credential Manager via ``keyring``.

    Environment lookup is a development fallback only.  The value is never
    persisted in a PaperCraft project database or log.
    """

    service_name = "PaperCraftAI"
    username = "gemini-api-key"

    def _keyring(self) -> Any | None:
        try:
            import keyring
        except ImportError:
            return None
        return keyring

    def get_api_key(self) -> str | None:
        keyring = self._keyring()
        if keyring is not None:
            try:
                value = keyring.get_password(self.service_name, self.username)
                if value and value.strip():
                    return str(value).strip()
            except Exception:
                # A broken desktop keyring must not hide a valid development
                # environment configuration.
                pass
        value = os.getenv("GEMINI_API_KEY", "").strip()
        return value or None

    def set_api_key(self, value: str) -> None:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Gemini API key cannot be empty")
        keyring = self._keyring()
        if keyring is None:
            raise RuntimeError("Install the 'desktop' dependency group to store credentials")
        keyring.set_password(self.service_name, self.username, cleaned)

    def delete_api_key(self) -> None:
        keyring = self._keyring()
        if keyring is None:
            return
        try:
            keyring.delete_password(self.service_name, self.username)
        except Exception:
            return
