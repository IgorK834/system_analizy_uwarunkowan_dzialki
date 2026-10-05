"""Polecenia utrzymaniowe modułu planowania.

``python -m app.modules.planning purge-llm-cache`` — usuwa z ``mpzp_llm_extractions`` zapisy starsze niż
``MPZP_LLM_CACHE_RETENTION_DAYS`` (PV3-13). Zapisy poza retencją i tak nie są serwowane jako trafienie;
polecenie zwalnia miejsce i ogranicza przechowywanie odpowiedzi modelu do okresu retencji.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence


def main(argv: Sequence[str] | None = None, *, purge: Callable[[], int] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.modules.planning", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("purge-llm-cache", help="usuń zapisy cache modelu poza okresem retencji")
    args = parser.parse_args(argv)
    if args.command == "purge-llm-cache":  # pragma: no branch - jedyne polecenie
        if purge is None:
            from app.modules.planning.composition import purge_llm_extraction_cache

            purge = purge_llm_extraction_cache
        removed = purge()
        print(f"mpzp_llm_extractions: usunięto {removed} zapisów poza okresem retencji")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
