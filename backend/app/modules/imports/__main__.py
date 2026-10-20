"""Punkt wejścia ``python -m app.modules.imports``.

Kontrakt wyjścia (AU-106, ADR-024):

* **stderr** — komunikaty postępu importu POG co najwyżej co ``--progress-interval`` sekund (domyślnie 10) i
  ewentualny ślad błędu; **stdout** — dokładnie jedna linia JSON z wynikiem końcowym (także przy błędzie i
  przerwaniu, więc uruchomienie w tle nigdy nie kończy się „pustym logiem");
* kod wyjścia: ``0`` — import zakończony (także ``--dry-run``); ``1`` — błąd (odrzucone QA, wyjątek); ``2`` — błąd
  użycia (argparse); ``3`` — import **niepełny** (przerwany sygnałem albo pobieranie nie zostało ukończone, np. awaria
  usługi) — zapisane strony i checkpoint pozwalają wznowić import tym samym poleceniem.

ADR-026: ``pog-national`` (import krajowy z GML RU, kody wyjścia jak wyżej: 3 = część aktów nie zaimportowana albo przebieg
przerwany — ponów polecenie), ``pog-sync`` (usługa harmonogramu), ``pog-enqueue``, ``pog-withdraw``, ``pog-rollback``,
``pog-map-rebuild`` i ``pog-status`` — wszystkie drukują jedną linię JSON na stdout.
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import time
import traceback
from collections.abc import Sequence
from typing import Any

from app.core.data_sources import get_catalog
from app.core.settings import settings
from app.db.session import SessionLocal
from app.modules.imports.api.cli import CliCommand, parse_args
from app.modules.imports.application.progress import HeartbeatReporter, ImportProgress
from app.modules.imports.application.national_import import NationalImportOptions, NationalProgress
from app.modules.imports.composition import (
    Bdot10kDownloadError,
    Bdot10kReadError,
    Bdot10kValidationError,
    OgcError,
    PogImportOptions,
    run_bdot10k_command,
    run_mpzp_command,
    run_parcels_command,
    run_pog_command,
    run_pog_national_command,
    run_raster_command,
)
from app.modules.imports.domain.pog import PogValidationError
from app.modules.imports.infrastructure.run_checkpoint import ImportRunCheckpoints
from app.modules.imports.infrastructure.wfs import WfsFetchError, WfsFetchInterrupted

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INCOMPLETE = 3


def _run_pog(command: CliCommand) -> int:
    """Import POG z jawnym postępem, wznawianiem i kodami wyjścia (stderr: postęp, stdout: JSON)."""
    started = time.monotonic()
    progress = ImportProgress()
    cancel = threading.Event()
    options = PogImportOptions(
        concurrency=command.concurrency or settings.import_wfs_concurrency,
        resume=not command.no_resume,
        fresh=command.fresh,
        progress=progress,
        cancel=cancel,
    )
    reporter = HeartbeatReporter(
        progress,
        sys.stderr,
        interval=command.progress_interval or settings.import_progress_interval_seconds,
        on_tick=lambda: options.save_checkpoint() if options.save_checkpoint else None,
    )
    restore = _install_signal_handlers(cancel, reporter)
    payload: dict[str, Any]
    exit_code: int
    reporter.start()
    try:
        if not command.resources:  # import z usługi: checkpoint w ``import_runs``; pliki lokalne go nie potrzebują
            options.checkpoints = ImportRunCheckpoints(
                SessionLocal,
                get_catalog().get(command.source_id),
                artifact_dir=settings.import_artifact_storage_dir,
            )
        with SessionLocal() as session:
            outcome = run_pog_command(
                session,
                source_id=command.source_id,
                dry_run=command.dry_run,
                local_resources=command.resources,
                act_identifier=command.act_identifier,
                resolution_number=command.resolution_number,
                resolution_date=command.resolution_date,
                teryt=command.teryt,
                legal_status=command.legal_status,
                name_label=command.name_label,
                boundary_path=command.boundary_path,
                options=options,
            )
        payload = {
            "status": outcome.status,
            "stats": dict(outcome.stats),
            "warnings": outcome.warnings,
            "import_run_id": outcome.import_run_id,
            "data_release_id": outcome.data_release_id,
        }
        exit_code = EXIT_OK
    except (WfsFetchInterrupted, KeyboardInterrupt) as exc:
        payload = _failure("interrupted", exc, options)
        exit_code = EXIT_INCOMPLETE
    except (WfsFetchError, OgcError) as exc:
        payload = _failure("incomplete", exc, options)
        exit_code = EXIT_INCOMPLETE
    except PogValidationError as exc:
        payload = _failure("rejected", exc, options)
        exit_code = EXIT_FAILED
    except Exception as exc:  # noqa: BLE001 - każdy błąd ma skończyć się JSON-em i niezerowym kodem
        traceback.print_exc(file=sys.stderr)
        payload = _failure("failed", exc, options)
        exit_code = EXIT_FAILED
    finally:
        progress.set_phase("done")
        reporter.stop()
        restore()
    payload["timing"] = {
        "total_seconds": round(time.monotonic() - started, 1),
        "phases_seconds": progress.snapshot()["phases"],
    }
    payload["progress"] = progress.snapshot()
    payload["exit_code"] = exit_code
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return exit_code


def _failure(status: str, exc: BaseException, options: PogImportOptions) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "error": f"{type(exc).__name__}: {exc}",
        "stats": {},
        "warnings": [],
        "import_run_id": None,
        "data_release_id": None,
    }
    if status in {"interrupted", "incomplete"}:
        payload["resume"] = {
            "resume_key": options.resume_key,
            "hint": (
                "Pobrane strony zostały zachowane; uruchom to samo polecenie ponownie, aby wznowić "
                "(--fresh porzuca zapisany stan)."
            ),
        }
    return payload


def _install_signal_handlers(cancel: threading.Event, reporter: HeartbeatReporter):  # noqa: ANN202
    """SIGINT/SIGTERM przerywają pobieranie łagodnie (strony zostają w magazynie); drugi sygnał kończy od razu."""
    previous: dict[int, Any] = {}

    def handler(signum: int, _frame: object) -> None:
        if cancel.is_set():
            raise KeyboardInterrupt
        cancel.set()
        reporter.emit(
            "[import] otrzymano sygnał przerwania — kończę trwające żądania i zapisuję postęp "
            "(kolejny sygnał kończy natychmiast)"
        )

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, handler)
    except ValueError:  # nie główny wątek (np. testy) — bez obsługi sygnałów
        return lambda: None

    def restore() -> None:
        for signum, old in previous.items():
            signal.signal(signum, old)

    return restore


def _run_bdot10k(command: CliCommand) -> int:
    """Import paczki BDOT10k: stdout — jedna linia JSON; kod 0 — sukces, 1 — odrzucona/uszkodzona paczka lub błąd."""
    started = time.monotonic()
    payload: dict[str, Any]
    try:
        with SessionLocal() as session:
            outcome = run_bdot10k_command(
                session,
                source_id=command.source_id,
                dry_run=command.dry_run,
                powiat=command.powiat or "",
                package=command.package,
            )
        payload = {
            "status": outcome.status,
            "stats": dict(outcome.stats),
            "warnings": outcome.warnings,
            "import_run_id": outcome.import_run_id,
            "data_release_id": outcome.data_release_id,
        }
        exit_code = EXIT_OK
    except (Bdot10kValidationError, Bdot10kReadError, Bdot10kDownloadError) as exc:
        payload = {"status": "rejected", "error": f"{type(exc).__name__}: {exc}", "stats": {}, "warnings": []}
        exit_code = EXIT_FAILED
    except Exception as exc:  # noqa: BLE001 - każdy błąd ma skończyć się JSON-em i niezerowym kodem
        traceback.print_exc(file=sys.stderr)
        payload = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "stats": {}, "warnings": []}
        exit_code = EXIT_FAILED
    payload["timing"] = {"total_seconds": round(time.monotonic() - started, 1)}
    payload["exit_code"] = exit_code
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return exit_code


def _emit_json(payload: dict[str, Any], exit_code: int) -> int:
    payload["exit_code"] = exit_code
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return exit_code


def _run_national(command: CliCommand) -> int:
    """Import krajowy POG: stderr — postęp z ETA; stdout — jedna linia JSON; kody wyjścia jak w ADR-024."""
    progress = NationalProgress()
    cancel = threading.Event()
    options = NationalImportOptions(
        full=command.full,
        terc=command.terc,
        limit=command.limit,
        concurrency=command.concurrency or settings.pog_national_concurrency,
        memory_budget_bytes=settings.pog_national_memory_budget_bytes,
        dry_run=command.dry_run,
        plan_only=command.plan_only,
        withdraw=not command.no_withdraw,
        allow_mass_withdrawal=command.allow_mass_withdrawal,
        max_withdraw_fraction=settings.pog_national_max_withdraw_fraction,
    )
    reporter = HeartbeatReporter(
        progress,  # type: ignore[arg-type]
        sys.stderr,
        interval=command.progress_interval or settings.import_progress_interval_seconds,
    )
    restore = _install_signal_handlers(cancel, reporter)
    started = time.monotonic()
    reporter.start()
    try:
        result = run_pog_national_command(
            options,
            source_id=command.source_id,
            with_csw=not command.no_csw,
            allow_regression=command.allow_regression,
            progress=progress,
            cancel=cancel,
        )
        payload = result.as_payload()
        exit_code = result.exit_code
    except Exception as exc:  # noqa: BLE001 - każdy błąd ma skończyć się JSON-em i niezerowym kodem
        traceback.print_exc(file=sys.stderr)
        payload = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        exit_code = EXIT_FAILED
    finally:
        progress.set_phase("done")
        reporter.stop()
        restore()
    payload.setdefault("timing", {})["wall_seconds"] = round(time.monotonic() - started, 1)
    return _emit_json(payload, exit_code)


def _run_registry_operation(command: CliCommand) -> int:
    """pog-withdraw / pog-rollback / pog-map-rebuild / pog-enqueue / pog-status — krótkie operacje z wynikiem JSON."""
    from app.core.data_sources import get_catalog as _catalog
    from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
    from app.modules.imports.infrastructure.job_queue import enqueue_commune_import
    from app.modules.imports.infrastructure.pog_map_tables import rebuild_missing_map_tables
    from app.modules.imports.infrastructure.repository import SqlAlchemyImportRepository
    from app.modules.imports.infrastructure.sync_status import pog_sync_status
    from sqlalchemy import select

    from app.models.versioned import PlanningAct
    from app.modules.imports.infrastructure.pog_registry import active_entries  # noqa: F401

    source = _catalog().get(command.source_id)
    try:
        with SessionLocal() as session:
            repository = SqlAlchemyImportRepository(session, source, LocalArtifactStore(settings.import_artifact_storage_dir))
            if command.name == "pog-withdraw":
                act_id = session.execute(
                    select(PlanningAct.id).where(
                        PlanningAct.act_identifier == command.act_identifier, PlanningAct.kind == "pog"
                    )
                ).scalar_one_or_none()
                if act_id is None:
                    return _emit_json({"status": "rejected", "error": f"Nieznany akt POG: {command.act_identifier}"}, EXIT_FAILED)
                outcome = repository.withdraw_pog_acts([int(act_id)], reason=command.reason or "withdrawn")
                session.commit()
                return _emit_json({"status": "succeeded", **outcome}, EXIT_OK)
            if command.name == "pog-rollback":
                outcome = repository.rollback_pog_act(
                    command.act_identifier or "", to_version_id=command.to_version_id
                )
                session.commit()
                return _emit_json({"status": "succeeded", **outcome}, EXIT_OK)
            if command.name == "pog-map-rebuild":
                rebuilt = rebuild_missing_map_tables(session)
                return _emit_json(
                    {"status": "succeeded", "acts": rebuilt.acts, "zones": rebuilt.zones, "overlays": rebuilt.overlays},
                    EXIT_OK,
                )
            if command.name == "pog-enqueue":
                jobs = []
                for terc in command.terc:
                    queued = enqueue_commune_import(
                        session, terc, requested_by="operator", max_attempts=settings.pog_import_job_max_attempts
                    )
                    jobs.append({"terc": terc, "job_id": queued.job_id, "created": queued.created, "status": queued.status})
                session.commit()
                return _emit_json({"status": "queued", "jobs": jobs}, EXIT_OK)
            status = pog_sync_status(session, interval_seconds=settings.pog_sync_interval_seconds)
            return _emit_json({"status": "ok", **status}, EXIT_OK)
    except (LookupError, ValueError) as exc:
        return _emit_json({"status": "rejected", "error": f"{type(exc).__name__}: {exc}"}, EXIT_FAILED)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        return _emit_json({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, EXIT_FAILED)


def _run_sync(command: CliCommand) -> int:
    from app.modules.imports.api.sync_loop import run_sync_cycle, run_sync_forever

    stop = threading.Event()
    previous: dict[signal.Signals, Any] = {}

    def handler(_signum: int, _frame: object) -> None:
        stop.set()

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, handler)
    except ValueError:
        pass

    def say(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    if command.interval:
        settings.pog_sync_interval_seconds = command.interval
    try:
        if command.once:
            summary = run_sync_cycle(with_csw=not command.no_csw, cancel=stop, emit=say, schedule=not command.no_schedule)
            return _emit_json(summary, EXIT_OK if summary.get("status") in {"ok", "locked"} else EXIT_INCOMPLETE)
        say("[pog-sync] usługa uruchomiona (SIGTERM kończy po bieżącym zleceniu)")
        run_sync_forever(stop, with_csw=not command.no_csw, emit=say, schedule=not command.no_schedule)
        return _emit_json({"status": "stopped"}, EXIT_OK)
    finally:
        for signum, old in previous.items():
            signal.signal(signum, old)


def main(argv: Sequence[str] | None = None) -> int:
    command = parse_args(argv)
    if command.name == "pog":
        return _run_pog(command)
    if command.name == "bdot10k":
        return _run_bdot10k(command)
    if command.name == "pog-national":
        return _run_national(command)
    if command.name == "pog-sync":
        return _run_sync(command)
    if command.name in {"pog-withdraw", "pog-rollback", "pog-map-rebuild", "pog-enqueue", "pog-status"}:
        return _run_registry_operation(command)
    with SessionLocal() as session:
        if command.name == "parcels":
            outcome = run_parcels_command(
                session,
                source_id=command.source_id,
                dry_run=command.dry_run,
                input_path=command.input_path,
            )
        elif command.name == "raster":
            # Parser CLI wymaga obu ścieżek dla polecenia ``raster``.
            if command.input_path is None or command.control_points_path is None:
                raise SystemExit("raster wymaga --input i --control-points")
            outcome = run_raster_command(
                session,
                source_id=command.source_id,
                dry_run=command.dry_run,
                input_path=command.input_path,
                control_points_path=command.control_points_path,
                page=command.page,
                act_version_id=command.act_version_id,
                transform_method=command.transform_method or "gcp_affine",
                nodata=command.nodata,
            )
            print(
                json.dumps(
                    {
                        "status": outcome.status,
                        "raster_asset_id": outcome.raster_asset_id,
                        "rmse_m": outcome.rmse_m,
                        "review_status": outcome.review_status,
                        "qa_report": outcome.qa_report,
                        "warnings": outcome.warnings,
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
            return 0
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
