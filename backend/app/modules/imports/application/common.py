"""Wspólne kontrakty przypadków użycia importu."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


class ImportSourceNotRunnable(RuntimeError):
    """Źródło bez potwierdzonego kontraktu próbowało publikować dane."""


@dataclass(frozen=True)
class ImportRelease:
    source_id: str
    version_label: str
    published_at: datetime
    publication_allowed: bool
    dry_run: bool = False
    teryt_scope: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceBatch:
    content: bytes
    filename: str
    media_type: str


@dataclass(frozen=True)
class ImportOutcome:
    status: str
    stats: Mapping[str, int | float | str]
    warnings: tuple[str, ...] = ()
    import_run_id: int | None = None
    data_release_id: int | None = None


@dataclass
class MutableImportStats:
    input: int = 0
    rejected: int = 0
    repaired: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    extra: dict[str, int | float | str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, int | float | str]:
        return {
            "input": self.input,
            "rejected": self.rejected,
            "repaired": self.repaired,
            "new": self.new,
            "changed": self.changed,
            "unchanged": self.unchanged,
            **self.extra,
        }


def artifact_sha256(content: bytes) -> str:
    """Liczy identyfikator niezmiennego oryginału wspólny dla obu potoków."""
    return hashlib.sha256(content).hexdigest()
