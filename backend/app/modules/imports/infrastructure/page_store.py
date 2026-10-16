"""Plikowy magazyn stron WFS dla wznawiania długich importów (AU-106, ADR-024).

Import gminy z Rejestru Urbanistycznego to setki stron po 10 obiektów, więc przerwanie w połowie nie może
oznaczać pobierania od zera. Magazyn przechowuje bajt w bajt każdą pobraną stronę wraz z jej skrótem SHA-256:

* strona odczytana z magazynu jest sprawdzana skrótem — uszkodzony albo niepełny plik jest traktowany jak brak
  strony i pobierany ponownie;
* magazyn jest związany z **tokenem zapytania** (typ, filtr, ``numberMatched``, identyfikatory pierwszej strony).
  Zmiana któregokolwiek składnika oznacza, że usługa zmieniła stan od poprzedniego przebiegu; zapisane strony
  są wtedy odrzucane w całości, żeby artefakt nie zmieszał dwóch stanów rejestru;
* zapisy są atomowe (plik tymczasowy + ``os.replace``), więc przerwanie procesu nie zostawia strony w połowie.

Magazyn nie jest źródłem prawdy o wydaniu: po udanej publikacji katalog jest usuwany (``clear``).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Final

_META_NAME: Final[str] = "meta.json"


class FilePageStore:
    """Magazyn stron jednego zapytania WFS w katalogu ``directory`` (bezpieczny wątkowo)."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._lock = threading.Lock()
        self._meta: dict[str, object] = {"token": None, "pages": {}}

    @property
    def directory(self) -> Path:
        return self._directory

    # --- protokół WfsPageStore ---------------------------------------------------------------------------

    def prepare(self, token: str) -> None:
        """Przyjmuje token zapytania; zapisane strony z innym tokenem są usuwane."""
        with self._lock:
            self._directory.mkdir(parents=True, exist_ok=True)
            stored = self._read_meta()
            if stored.get("token") == token:
                self._meta = stored
                return
            for child in self._directory.iterdir():
                if child.is_file():
                    child.unlink(missing_ok=True)
            self._meta = {"token": token, "pages": {}}
            self._write_meta()

    def load(self, start_index: int) -> bytes | None:
        with self._lock:
            entry = self._pages().get(str(start_index))
            path = self._page_path(start_index)
        if not isinstance(entry, dict) or not path.is_file():
            return None
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != entry.get("sha256") or len(content) != entry.get("size"):
            return None
        return content

    def save(self, start_index: int, content: bytes) -> None:
        path = self._page_path(start_index)
        temporary = path.with_suffix(".tmp")
        with self._lock:
            self._directory.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(content)
            os.replace(temporary, path)
            self._pages()[str(start_index)] = {
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
            self._write_meta()

    # --- pomocnicze -------------------------------------------------------------------------------------

    def stored_pages(self) -> int:
        """Liczba stron zapisanych w magazynie (do raportu wznowienia)."""
        with self._lock:
            return len(self._pages())

    def clear(self) -> None:
        with self._lock:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._meta = {"token": None, "pages": {}}

    def _pages(self) -> dict[str, object]:
        pages = self._meta.setdefault("pages", {})
        assert isinstance(pages, dict)
        return pages

    def _page_path(self, start_index: int) -> Path:
        return self._directory / f"page-{start_index:010d}.xml"

    def _read_meta(self) -> dict[str, object]:
        path = self._directory / _META_NAME
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"token": None, "pages": {}}
        return data if isinstance(data, dict) and isinstance(data.get("pages"), dict) else {"token": None, "pages": {}}

    def _write_meta(self) -> None:
        path = self._directory / _META_NAME
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._meta, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
