"""Import POG „na żądanie” z analizy działki (issue 300, ADR-026).

Analiza działki w gminie, której nie ma w lokalnym stanie krajowym, a dla której Rejestr Urbanistyczny publikuje plan ogólny
(wykaz ``pog_ru_catalog`` albo wynik discovery RU), zleca import tej gminy w tle: zlecenie trafia do kolejki ``import_jobs``
(deduplikacja: jedno aktywne zlecenie na gminę), a odpowiedź analizy mówi o tym jawnie (ostrzeżenie ``POG_IMPORT_QUEUED`` /
``POG_IMPORT_PENDING`` / ``POG_IMPORT_FAILED_RECENTLY``). Następna analiza po zakończeniu importu używa danych lokalnych.

Zlecenie zapisuje osobna, krótka sesja — analiza nie zmienia swojej transakcji. Brak kolejki (np. baza bez migracji 037) nigdy nie
przerywa analizy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.session import SessionLocal
from app.modules.imports.infrastructure.job_queue import enqueue_commune_import, job_status_for_terc
from app.schemas.analyze import WarningMessage

logger = logging.getLogger(__name__)

RETRY_AFTER_FAILURE: Final[timedelta] = timedelta(hours=6)


@dataclass(frozen=True)
class CommuneImportRequest:
    terc: str
    job_id: int | None
    job_status: str
    created: bool
    reason: str

    def warning(self) -> WarningMessage:
        if self.job_status == "failed":
            return WarningMessage(
                code="POG_IMPORT_FAILED_RECENTLY",
                message=(
                    f"Rejestr Urbanistyczny publikuje plan ogólny gminy {self.terc}, ale ostatni import lokalny nie powiódł się "
                    f"(zlecenie #{self.job_id}); kolejna próba nastąpi automatycznie. Wynik nie jest analizą powierzchniową stref."
                ),
                severity="warning",
                source_name="pog",
            )
        if self.created:
            code, sentence = "POG_IMPORT_QUEUED", "Import tej gminy został zlecony w tle"
        else:
            code, sentence = "POG_IMPORT_PENDING", "Import tej gminy jest już w toku"
        return WarningMessage(
            code=code,
            message=(
                f"Plan ogólny gminy {self.terc} jest opublikowany w Rejestrze Urbanistycznym, ale nie ma go jeszcze w lokalnym stanie "
                f"danych. {sentence} (zlecenie #{self.job_id}, stan: {self.job_status}); kolejna analiza użyje danych lokalnych "
                "i pokaże strefy. Brak lokalnych danych nie oznacza braku planu."
            ),
            severity="info",
            source_name="pog",
        )


def commune_code(teryt: str | None) -> str | None:
    code = (teryt or "").strip()[:6]
    return code if len(code) == 6 and code.isdigit() else None


def commune_is_imported(db: Session, terc: str) -> bool:
    return bool(
        db.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM pog_registry_entries e JOIN planning_acts pa ON pa.id = e.planning_act_id "
                "WHERE e.state_to_id IS NULL AND pa.kind = 'pog' AND pa.teryt LIKE :prefix)"
            ),
            {"prefix": f"{terc}%"},
        ).scalar_one()
    )


def catalog_has_commune(db: Session, terc: str) -> bool:
    return bool(
        db.execute(
            text("SELECT EXISTS (SELECT 1 FROM pog_ru_catalog WHERE is_current AND terc LIKE :prefix)"),
            {"prefix": f"{terc}%"},
        ).scalar_one()
    )


def request_commune_import(
    db: Session | None, teryt: str | None, *, discovery_found_act: bool
) -> CommuneImportRequest | None:
    """Zleca import gminy, jeśli RU ma jej plan, a lokalnego stanu brak; ``None`` — nic do zlecenia."""
    if db is None or not settings.pog_on_demand_import_enabled:
        return None
    terc = commune_code(teryt)
    if terc is None:
        return None
    try:
        if commune_is_imported(db, terc):
            return None
        if not (discovery_found_act or catalog_has_commune(db, terc)):
            return None
        with SessionLocal() as queue_session:
            previous = job_status_for_terc(queue_session, terc)
            if previous and previous["status"] == "failed" and previous["finished_at"]:
                finished = previous["finished_at"]
                if datetime.now(timezone.utc) - finished < RETRY_AFTER_FAILURE:
                    return CommuneImportRequest(terc, previous["job_id"], "failed", False, "recent_failure")
            result = enqueue_commune_import(
                queue_session,
                terc,
                requested_by="analysis",
                max_attempts=settings.pog_import_job_max_attempts,
            )
            queue_session.commit()
        return CommuneImportRequest(terc, result.job_id, result.status, result.created, "catalog_or_discovery")
    except Exception as exc:  # noqa: BLE001 - brak kolejki nie może przerwać analizy
        logger.warning("pog_on_demand_enqueue_failed error_type=%s", type(exc).__name__)
        return None
