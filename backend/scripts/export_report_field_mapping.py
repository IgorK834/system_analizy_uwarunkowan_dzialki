"""Eksport tabeli mapowania pól API → raport do ``docs/report/field-mapping.md``.

Uruchomienie z katalogu głównego repozytorium::

    python backend/scripts/export_report_field_mapping.py

albo w kontenerze backendu z zamontowanym repozytorium (``REPO_ROOT=/repo``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.report_fields import render_field_mapping_markdown  # noqa: E402


def main() -> None:
    root = Path(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[2])
    target = root / "docs" / "report" / "field-mapping.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_field_mapping_markdown(), "utf-8")
    print(f"Zapisano {target}")


if __name__ == "__main__":
    main()
