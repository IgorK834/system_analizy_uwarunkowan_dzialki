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
        default="not_available",
        help="Status prawny: project/in_progress/adopted/not_available",
    )
    pog.add_argument("--name", dest="name_label", help="Nazwa aktu POG")
    pog.add_argument(
        "--teryt",
        help="TERYT gminy aktu POG",
    )

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
        dry_run=namespace.dry_run,
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
    )
