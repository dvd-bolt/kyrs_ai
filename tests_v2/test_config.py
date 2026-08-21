from pathlib import Path

import pytest
from pydantic import ValidationError

from papercraft.config import AppSettings, ModelPolicy, RetryPolicy


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PAPERCRAFT_PROJECTS_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "secret-value")
    settings = AppSettings.from_environment()
    assert settings.projects_root == tmp_path.resolve()
    assert settings.gemini_api_key is not None
    assert settings.gemini_api_key.get_secret_value() == "secret-value"


def test_model_and_retry_policies_are_validated() -> None:
    with pytest.raises(ValidationError):
        ModelPolicy(writer="not a valid model")
    with pytest.raises(ValidationError):
        RetryPolicy(max_attempts=0)
