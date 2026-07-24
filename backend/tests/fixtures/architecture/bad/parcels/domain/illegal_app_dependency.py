"""FIXTURE (celowo błędny): domain sięga do app.core (spoza domain/shared)."""

from app.core.settings import settings  # noqa: F401
