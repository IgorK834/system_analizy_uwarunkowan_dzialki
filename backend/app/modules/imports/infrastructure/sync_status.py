"""Stan importu krajowego POG dla operatora i monitoringu (ADR-026): rejestr, ostatni przebieg, wykaz RU, kolejka.

Odczyt wyłącznie z bazy (``import_runs``, ``pog_registry_*``, ``pog_ru_catalog``, ``import_jobs``) — nie odpytuje RU.
Opóźnienie (``lag_seconds``) to czas od ostatniego udanego przebiegu krajowego; ``stale`` zapala się po przekroczeniu
dwukrotności interwału harmonogramu.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.imports.infrastructure.job_queue import queue_stats
from app.modules.imports.infrastructure.national_runtime import NATIONAL_IMPORTER_VERSION
from app.modules.imports.infrastructure.pog_map_tables import versions_missing_map_rows


def pog_sync_status(session: Session, *, interval_seconds: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    runs = session.execute(
        text(
            """
            SELECT id, status, started_at, finished_at, stats
            FROM import_runs WHERE importer_version = :version ORDER BY id DESC LIMIT 5
            """
        ),
        {"version": NATIONAL_IMPORTER_VERSION},
    ).mappings().all()
    last_success = session.execute(
        text(
            """
            SELECT id, finished_at, stats FROM import_runs
            WHERE importer_version = :version AND status = 'succeeded' ORDER BY id DESC LIMIT 1
            """
        ),
        {"version": NATIONAL_IMPORTER_VERSION},
    ).mappings().first()
    registry = session.execute(
        text(
            """
            SELECT (SELECT count(*) FROM pog_registry_entries WHERE state_to_id IS NULL) AS active_acts,
                   (SELECT max(id) FROM pog_registry_states) AS state_id,
                   (SELECT count(*) FROM pog_registry_states) AS states
            """
        )
    ).mappings().one()
    catalog = session.execute(
        text(
            """
            SELECT count(*) FILTER (WHERE is_current) AS current_rows,
                   count(*) AS all_rows, max(last_seen_at) AS last_seen_at,
                   count(DISTINCT terc) FILTER (WHERE is_current) AS communes
            FROM pog_ru_catalog
            """
        )
    ).mappings().one()
    not_imported = session.execute(
        text(
            """
            SELECT count(DISTINCT left(c.terc, 6)) FROM pog_ru_catalog c
            WHERE c.is_current AND NOT EXISTS (
                SELECT 1 FROM pog_registry_entries e JOIN planning_acts pa ON pa.id = e.planning_act_id
                WHERE e.state_to_id IS NULL AND left(pa.teryt, 6) = left(c.terc, 6))
            """
        )
    ).scalar_one()
    imported_communes = session.execute(
        text(
            """
            SELECT count(DISTINCT left(pa.teryt, 6)) FROM pog_registry_entries e
            JOIN planning_acts pa ON pa.id = e.planning_act_id WHERE e.state_to_id IS NULL AND pa.teryt IS NOT NULL
            """
        )
    ).scalar_one()
    finished = last_success["finished_at"] if last_success else None
    lag = (now - finished).total_seconds() if finished else None
    return {
        "checked_at": now.isoformat(),
        "registry": {
            "state_id": registry["state_id"],
            "active_acts": int(registry["active_acts"]),
            "states": int(registry["states"]),
            "imported_communes": int(imported_communes),
        },
        "ru_catalog": {
            "current_acts": int(catalog["current_rows"] or 0),
            "communes_with_act": int(catalog["communes"] or 0),
            "communes_not_imported": int(not_imported),
            "last_seen_at": catalog["last_seen_at"].isoformat() if catalog["last_seen_at"] else None,
        },
        "last_national_run": (
            {
                "id": runs[0]["id"],
                "status": runs[0]["status"],
                "started_at": runs[0]["started_at"].isoformat() if runs[0]["started_at"] else None,
                "finished_at": runs[0]["finished_at"].isoformat() if runs[0]["finished_at"] else None,
                "counts": (runs[0]["stats"] or {}).get("counts") if isinstance(runs[0]["stats"], dict) else None,
                "failure_codes": (runs[0]["stats"] or {}).get("failure_codes") if isinstance(runs[0]["stats"], dict) else None,
            }
            if runs
            else None
        ),
        "last_success_at": finished.isoformat() if finished else None,
        "lag_seconds": round(lag) if lag is not None else None,
        "stale": lag is None or lag > 2 * interval_seconds,
        "queue": queue_stats(session),
        "versions_without_map_rows": len(versions_missing_map_rows(session)),
    }
