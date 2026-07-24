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
    )
