"""Versioned academic-work profiles."""

from .models import ProfileRegistry, WorkProfile, default_profile_registry
from .plugin import ProfilePlugin, WorkProfilePlugin

__all__ = ["ProfilePlugin", "ProfileRegistry", "WorkProfile", "WorkProfilePlugin", "default_profile_registry"]
