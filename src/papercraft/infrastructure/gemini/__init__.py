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
    GeminiRequestCancelled,
    GeminiSafetyError,
    GeminiStructuredOutputError,
    GeminiUnavailableError,
    GroundedResult,
    ProviderRequestCoordinator,
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
    "GeminiRequestCancelled",
    "GeminiSafetyError",
    "GeminiStructuredOutputError",
    "GeminiUnavailableError",
    "GroundedResult",
    "ProviderRequestCoordinator",
    "RemoteFile",
    "UsageRecord",
]
