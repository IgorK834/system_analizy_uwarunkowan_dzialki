"""Parser argumentów CLI niezależny od infrastruktury i bazy."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from typing import Sequence


@dataclass(frozen=True)
class CliCommand:
    name: str
    source_id: str
    dry_run: bool
    input_path: str | None = None
    resources: tuple[tuple[str, str], ...] = ()
    act_identifier: str | None = None
    resolution_number: str | None = None
    resolution_date: date | None = None
    teryt: str | None = None
    legal_status: str | None = None
    name_label: str | None = None
    boundary_path: str | None = None
    control_points_path: str | None = None
    page: int = 0
    act_version_id: int | None = None
    transform_method: str | None = None
    nodata: float | None = None
    # AU-106: równoległe pobieranie stron WFS, wznawianie po przerwaniu i odstęp komunikatów postępu.
    concurrency: int | None = None
    fresh: bool = False
    no_resume: bool = False
    progress_interval: float | None = None
    # Task 23.1: import paczki powiatowej BDOT10k.
    powiat: str | None = None
    package: str | None = None
    # ADR-026: import krajowy POG z GML RU, harmonogram, kolejka, wycofanie i cofnięcie wersji aktu.
    terc: tuple[str, ...] = ()
    full: bool = False
    plan_only: bool = False
    limit: int | None = None
    no_withdraw: bool = False
    allow_mass_withdrawal: bool = False
    no_csw: bool = False
    allow_regression: bool = False
    reason: str | None = None
    to_version_id: int | None = None
    once: bool = False
    interval: int | None = None
    no_schedule: bool = False


def _concurrency(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("równoległość musi być liczbą całkowitą") from exc
    if not 1 <= parsed <= 8:
        raise argparse.ArgumentTypeError("równoległość musi mieścić się w zakresie 1–8")
    return parsed


def _national_concurrency(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("równoległość musi być liczbą całkowitą") from exc
    if not 1 <= parsed <= 6:
        raise argparse.ArgumentTypeError("równoległość importu krajowego musi mieścić się w zakresie 1–6")
    return parsed


def _positive_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("odstęp musi być liczbą") from exc
    if not 0 < parsed <= 3600:
        raise argparse.ArgumentTypeError("odstęp musi mieścić się w przedziale (0, 3600] s")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> CliCommand:
    parser = argparse.ArgumentParser(description="Wersjonowane importy GIS")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parcels = subparsers.add_parser("parcels", help="Import działek")
    parcels.add_argument("--source", required=True)
    parcels.add_argument("--dry-run", action="store_true")
    parcels.add_argument("--input")

    mpzp = subparsers.add_parser("mpzp", help="Import MPZP")
    mpzp.add_argument("--source", required=True)
    mpzp.add_argument("--dry-run", action="store_true")
    mpzp.add_argument(
        "--resource",
        action="append",
        default=[],
        metavar="ROLE=PATH",
        help="Lokalny fixture, np. boundaries=plan.gml",
    )
    mpzp.add_argument(
        "--act-id",
        help="Identyfikator aktu wymagany dla źródła wyłącznie rastrowego",
    )
    mpzp.add_argument(
        "--resolution-number",
        help="Numer uchwały wymagany dla źródła wyłącznie rastrowego",
    )
    mpzp.add_argument(
        "--resolution-date",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Data uchwały wymagana dla źródła wyłącznie rastrowego",
    )
    mpzp.add_argument(
        "--teryt",
        help="TERYT wymagany, gdy katalog nie określa pojedynczego zasięgu",
    )

    pog = subparsers.add_parser("pog", help="Import Planu Ogólnego Gminy (POG/APP)")
    pog.add_argument("--source", required=True)
    pog.add_argument("--dry-run", action="store_true")
    pog.add_argument(
        "--resource",
        action="append",
        default=[],
        metavar="FEATURE_TYPE=PATH",
        help=(
            "Warstwa POG, np. planning_zone=strefy.gml, ouz=ouz.gml, "
            "downtown_area=srodmiescie.gml, "
            "social_infrastructure_standard=standardy.gml"
        ),
    )
    pog.add_argument(
        "--boundary",
        help="Opcjonalna warstwa granicy aktu (aktPlanowaniaPrzestrzennego)",
    )
    pog.add_argument("--act-id", required=True, help="Identyfikator aktu POG")
    pog.add_argument("--resolution-number", help="Numer uchwały POG")
    pog.add_argument(
        "--resolution-date",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Data uchwały POG",
    )
    pog.add_argument(
        "--legal-status",
        default=None,
        help=(
            "Urzędowy kod statusu aktu z Rejestru Urbanistycznego/INSPIRE "
            "(legalForce, adoption, elaboration, obsolete). Brak kodu daje "
            "status unknown; binding wymaga kodu urzędowego."
        ),
    )
    pog.add_argument("--name", dest="name_label", help="Nazwa aktu POG")
    pog.add_argument(
        "--teryt",
        help="TERYT gminy aktu POG",
    )
    pog.add_argument(
        "--concurrency",
        type=_concurrency,
        metavar="N",
        help=(
            "Liczba równoległych żądań stron WFS (1–8; domyślnie IMPORT_WFS_CONCURRENCY=4). Rejestr Urbanistyczny "
            "zwraca strony po 10 obiektów, więc równoległość jest główną dźwignią czasu importu; wartość to limit "
            "grzeczności wobec usługi."
        ),
    )
    pog.add_argument(
        "--fresh",
        action="store_true",
        help="Porzuć zapisane strony i checkpoint poprzedniego, przerwanego przebiegu i pobierz wszystko od zera.",
    )
    pog.add_argument(
        "--no-resume",
        action="store_true",
        help="Nie zapisuj stron do wznawiania (przerwany import trzeba będzie powtórzyć od początku).",
    )
    pog.add_argument(
        "--progress-interval",
        type=_positive_seconds,
        metavar="SEKUNDY",
        help="Odstęp komunikatów postępu na stderr (domyślnie IMPORT_PROGRESS_INTERVAL_SECONDS=10).",
    )

    bdot10k = subparsers.add_parser(
        "bdot10k",
        help="Import paczki powiatowej BDOT10k (osie jezdni, obrysy budynków, zasięg powiatu)",
    )
    bdot10k.add_argument("--source", default="bdot10k")
    bdot10k.add_argument("--dry-run", action="store_true")
    bdot10k.add_argument("--powiat", required=True, help="Czterocyfrowy TERYT powiatu, np. 1261")
    bdot10k.add_argument(
        "--package",
        help=(
            "Plik ZIP paczki albo adres https z hosta dozwolonego w katalogu. Bez tej opcji paczka jest pobierana "
            "z adresu zapisanego w katalogu źródeł (opendata.geoportal.gov.pl)."
        ),
    )

    national = subparsers.add_parser(
        "pog-national",
        help="Import krajowy POG: wykaz RU → GML aktów → rejestr aktywnych wersji (przyrostowo albo w całości)",
    )
    national.add_argument("--source", default="pog_app")
    national.add_argument("--dry-run", action="store_true", help="Pobiera i waliduje, nie publikuje.")
    national.add_argument("--plan-only", action="store_true", help="Tylko wykaz i plan (bez pobierania aktów).")
    national.add_argument("--full", action="store_true", help="Pobiera wszystkie akty, nie tylko nowe i zmienione.")
    national.add_argument(
        "--terc", action="append", default=[], metavar="TERC",
        help="Ogranicza do gmin (6-cyfrowy kod; można powtarzać albo podać po przecinku).",
    )
    national.add_argument("--limit", type=int, metavar="N", help="Najwyżej N aktów do pobrania (próbka).")
    national.add_argument("--concurrency", type=_national_concurrency, metavar="N", help="Równoległe akty (1–6).")
    national.add_argument("--no-withdraw", action="store_true", help="Nie dezaktywuj aktów wycofanych z RU.")
    national.add_argument(
        "--allow-mass-withdrawal", action="store_true", help="Zezwól na dezaktywację > 10% aktywnych aktów naraz."
    )
    national.add_argument("--no-csw", action="store_true", help="Nie dołączaj metadanych CSW (szybciej).")
    national.add_argument(
        "--allow-regression", action="store_true",
        help="Pomiń odrzucenie importu przy spadku liczby obiektów aktu o ponad 50%.",
    )
    national.add_argument("--progress-interval", type=_positive_seconds, metavar="SEKUNDY")

    withdraw = subparsers.add_parser("pog-withdraw", help="Dezaktywuje akt POG (historia zostaje)")
    withdraw.add_argument("--source", default="pog_app")
    withdraw.add_argument("--act-id", required=True, help="Identyfikator aktu (przestrzeń nazw/lokalnyId)")
    withdraw.add_argument("--reason", choices=("withdrawn", "not_current"), default="withdrawn")

    rollback = subparsers.add_parser("pog-rollback", help="Cofa akt POG do wcześniejszej wersji")
    rollback.add_argument("--source", default="pog_app")
    rollback.add_argument("--act-id", required=True)
    rollback.add_argument("--to-version-id", type=int, help="Domyślnie najnowsza starsza wersja")

    rebuild = subparsers.add_parser(
        "pog-map-rebuild", help="Uzupełnia tabele mapy POG dla aktywnych wersji, którym ich brakuje"
    )
    rebuild.add_argument("--source", default="pog_app")

    enqueue = subparsers.add_parser("pog-enqueue", help="Zleca import gminy do kolejki (import na żądanie)")
    enqueue.add_argument("--source", default="pog_app")
    enqueue.add_argument("--terc", action="append", required=True, metavar="TERC")

    sync = subparsers.add_parser(
        "pog-sync", help="Usługa harmonogramu: kolejka zleceń + cykliczny import przyrostowy (blokada przed dublami)"
    )
    sync.add_argument("--source", default="pog_app")
    sync.add_argument("--once", action="store_true", help="Jeden przebieg (kolejka + import przyrostowy) i koniec.")
    sync.add_argument("--interval", type=int, metavar="SEKUNDY", help="Odstęp przebiegów (domyślnie POG_SYNC_INTERVAL_SECONDS).")
    sync.add_argument("--no-csw", action="store_true")
    sync.add_argument("--no-schedule", action="store_true", help="Tylko kolejka zleceń (POG/BDOT10k), bez cyklicznego importu kraju.")

    status = subparsers.add_parser("pog-status", help="Stan importu krajowego POG (JSON)")
    status.add_argument("--source", default="pog_app")

    raster = subparsers.add_parser(
        "raster", help="Georeferencja rysunku planu do COG"
    )
    raster.add_argument("--source", required=True)
    raster.add_argument("--dry-run", action="store_true")
    raster.add_argument(
        "--input", required=True, help="Oryginalny PDF lub GeoTIFF/obraz"
    )
    raster.add_argument(
        "--control-points",
        required=True,
        metavar="JSON",
        help="Plik JSON z listą {pixel_col,pixel_row,map_x,map_y}",
    )
    raster.add_argument(
        "--page", type=int, default=0, help="Numer strony PDF (od 0)"
    )
    raster.add_argument(
        "--act-version-id",
        type=int,
        help="Opcjonalne powiązanie z wersją aktu planistycznego",
    )
    raster.add_argument(
        "--transform",
        choices=("gcp_affine", "gcp_tps"),
        default="gcp_affine",
        help="Metoda transformacji GDAL",
    )
    raster.add_argument("--nodata", type=float, help="Wartość NODATA (opcjonalna)")

    namespace = parser.parse_args(argv)
    resources: list[tuple[str, str]] = []
    for value in getattr(namespace, "resource", []):
        if "=" not in value:
            parser.error("--resource wymaga zapisu ROLE=PATH")
        role, path = value.split("=", maxsplit=1)
        resources.append((role, path))
    return CliCommand(
        name=namespace.command,
        source_id=namespace.source,
        dry_run=bool(getattr(namespace, "dry_run", False)),
        input_path=getattr(namespace, "input", None),
        resources=tuple(resources),
        act_identifier=getattr(namespace, "act_id", None),
        resolution_number=getattr(namespace, "resolution_number", None),
        resolution_date=getattr(namespace, "resolution_date", None),
        teryt=getattr(namespace, "teryt", None),
        legal_status=getattr(namespace, "legal_status", None),
        name_label=getattr(namespace, "name_label", None),
        boundary_path=getattr(namespace, "boundary", None),
        control_points_path=getattr(namespace, "control_points", None),
        page=getattr(namespace, "page", 0) or 0,
        act_version_id=getattr(namespace, "act_version_id", None),
        transform_method=getattr(namespace, "transform", None),
        nodata=getattr(namespace, "nodata", None),
        concurrency=getattr(namespace, "concurrency", None),
        fresh=bool(getattr(namespace, "fresh", False)),
        no_resume=bool(getattr(namespace, "no_resume", False)),
        progress_interval=getattr(namespace, "progress_interval", None),
        powiat=getattr(namespace, "powiat", None),
        package=getattr(namespace, "package", None),
        terc=_split_terc(getattr(namespace, "terc", None)),
        full=bool(getattr(namespace, "full", False)),
        plan_only=bool(getattr(namespace, "plan_only", False)),
        limit=getattr(namespace, "limit", None),
        no_withdraw=bool(getattr(namespace, "no_withdraw", False)),
        allow_mass_withdrawal=bool(getattr(namespace, "allow_mass_withdrawal", False)),
        no_csw=bool(getattr(namespace, "no_csw", False)),
        allow_regression=bool(getattr(namespace, "allow_regression", False)),
        reason=getattr(namespace, "reason", None),
        to_version_id=getattr(namespace, "to_version_id", None),
        once=bool(getattr(namespace, "once", False)),
        interval=getattr(namespace, "interval", None),
        no_schedule=bool(getattr(namespace, "no_schedule", False)),
    )


def _split_terc(values: Sequence[str] | str | None) -> tuple[str, ...]:
    """TERC z opcji ``--terc`` (powtarzanej albo po przecinku); 6 cyfr (kod 7-cyfrowy jest skracany)."""
    if not values:
        return ()
    items = [values] if isinstance(values, str) else list(values)
    codes = {
        part.strip()[:6]
        for item in items
        for part in str(item).split(",")
        if part.strip()
    }
    for code in codes:
        if not (code.isdigit() and len(code) == 6):
            raise SystemExit(f"--terc: niepoprawny kod gminy {code!r} (oczekiwano 6 cyfr).")
    return tuple(sorted(codes))
