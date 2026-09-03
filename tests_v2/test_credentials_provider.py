from __future__ import annotations

from pathlib import Path

from papercraft.application.api import DesktopApplication
from papercraft.config import AppSettings, ProviderPolicy
from papercraft.infrastructure.gemini import GeminiAuthenticationError, GeminiUnavailableError
from papercraft.infrastructure.gemini.secrets import CredentialSecretStore


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, value: str) -> None:
        self.values[(service, username)] = value

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class CheckGateway:
    def __init__(self, outcome: Exception | None = None) -> None:
        self.outcome = outcome

    def health_check(self, *, fail_fast: bool = False) -> None:
        assert fail_fast is True
        if self.outcome is not None:
            raise self.outcome


def _application(tmp_path: Path, store: CredentialSecretStore, outcome: Exception | None = None) -> DesktopApplication:
    return DesktopApplication(
        AppSettings(projects_root=tmp_path / "projects"),
        credential_store=store,
        gateway_factory=lambda _settings, _store: CheckGateway(outcome),  # type: ignore[arg-type]
    )


def test_key_lifecycle_verifies_before_persisting_and_survives_new_application(tmp_path: Path) -> None:
    keyring = FakeKeyring()
    store = CredentialSecretStore(keyring_module=keyring)
    application = _application(tmp_path, store)

    status = application.configure_gemini("  unit-key  ")
    assert status.state == "valid"
    assert status.verified is True
    assert keyring.values[(store.service_name, store.username)] == "unit-key"

    restarted = _application(tmp_path, store)
    assert restarted.credential_status().state == "unverified"
    assert restarted.verify_gemini().state == "valid"
    restarted.delete_gemini_key()
    assert restarted.credential_status().state == "missing"


def test_invalid_replacement_does_not_destroy_existing_key(tmp_path: Path) -> None:
    keyring = FakeKeyring()
    store = CredentialSecretStore(keyring_module=keyring)
    store.set_api_key("known-good")
    application = _application(tmp_path, store, GeminiAuthenticationError("raw secret"))

    status = application.configure_gemini("rejected-key")
    assert status.state == "invalid"
    assert store.get_api_key() == "known-good"
    assert "rejected-key" not in status.safe_message


def test_unavailable_check_keeps_key_without_leaking_it(tmp_path: Path) -> None:
    keyring = FakeKeyring()
    store = CredentialSecretStore(keyring_module=keyring)
    application = _application(tmp_path, store, GeminiUnavailableError("Bearer hidden-key"))

    status = application.configure_gemini("network-key")
    assert status.state == "unverified"
    assert store.get_api_key() == "network-key"
    assert "network-key" not in status.safe_message
    check = application.verify_gemini()
    assert check.state == "unavailable"
    assert check.retryable is True


def test_environment_key_is_not_an_accepted_credential(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "environment-secret")
    store = CredentialSecretStore(keyring_module=FakeKeyring())
    assert store.get_api_key() is None
    assert _application(tmp_path, store).credential_status().state == "missing"


def test_capability_policy_keeps_fallbacks_within_their_capability(tmp_path: Path) -> None:
    settings = AppSettings(
        projects_root=tmp_path,
        provider_policy=ProviderPolicy(structured_fallback="structured-backup", image_fallback="image-backup"),
    )
    from papercraft.config import ModelCapabilityRegistry

    registry = ModelCapabilityRegistry(settings.model_policy, settings.provider_policy)
    assert registry.candidates("requirements") == ("gemini-3.5-flash-lite", "structured-backup")
    assert registry.candidates("image") == ("gemini-3.1-flash-image", "image-backup")
