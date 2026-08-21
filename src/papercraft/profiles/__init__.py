"""Versioned academic-work profiles."""

from .models import ProfileRegistry, WorkProfile, default_profile_registry

__all__ = ["ProfileRegistry", "WorkProfile", "default_profile_registry"]
