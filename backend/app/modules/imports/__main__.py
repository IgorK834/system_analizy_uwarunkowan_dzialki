"""Punkt wejścia ``python -m app.modules.imports``."""

from __future__ import annotations

import json
from typing import Sequence

from app.db.session import SessionLocal
from app.modules.imports.api.cli import parse_args
from app.modules.imports.composition import run_mpzp_command, run_parcels_command


def main(argv: Sequence[str] | None = None) -> int:
    command = parse_args(argv)
    with SessionLocal() as session:
        if command.name == "parcels":
            outcome = run_parcels_command(
                session,
                source_id=command.source_id,
                dry_run=command.dry_run,
                input_path=command.input_path,
            )
        else:
            outcome = run_mpzp_command(
                session,
                source_id=command.source_id,
                dry_run=command.dry_run,
                local_resources=command.resources,
                act_identifier=command.act_identifier,
                resolution_number=command.resolution_number,
                resolution_date=command.resolution_date,
                raster_teryt=command.teryt,
            )
    print(
        json.dumps(
            {
                "status": outcome.status,
                "stats": dict(outcome.stats),
                "warnings": outcome.warnings,
                "import_run_id": outcome.import_run_id,
                "data_release_id": outcome.data_release_id,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
