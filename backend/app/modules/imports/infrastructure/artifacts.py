"""Lokalny, content-addressed magazyn oryginałów importu."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class LocalArtifactStore:
    """Zapisuje artefakty pod ``source_id/content_hash/nazwa`` atomowo."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)

    def save(
        self,
        *,
        source_id: str,
        content_hash: str,
        filename: str,
        content: bytes,
    ) -> str:
        safe_source = _SAFE_NAME.sub("_", source_id)
        safe_name = _SAFE_NAME.sub("_", Path(filename).name) or "artifact.bin"
        target_dir = self._root / safe_source / content_hash
        target = target_dir / safe_name
        target_dir.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return str(target)

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".import-", dir=target_dir
        )
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, target)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return str(target)
