"""Gemini provider boundary.

Only this package imports the Google SDK.  Application code talks to the
``GeminiPort`` protocol and can therefore use the deterministic fake in tests.
"""

from .fake import FakeGeminiGateway
from .gateway import (
    GeminiAuthenticationError,
    GeminiConfigurationError,
    GeminiGateway,
    GeminiGatewayError,
    GeminiSafetyError,
    GeminiUnavailableError,
    GroundedResult,
    RemoteFile,
    UsageRecord,
)
from .ports import GeminiPort
from .secrets import CredentialSecretStore

__all__ = [
    "CredentialSecretStore",
    "FakeGeminiGateway",
    "GeminiAuthenticationError",
    "GeminiConfigurationError",
    "GeminiGateway",
    "GeminiGatewayError",
    "GeminiPort",
    "GeminiSafetyError",
    "GeminiUnavailableError",
    "GroundedResult",
    "RemoteFile",
    "UsageRecord",
]
