#!/usr/bin/env python3
"""Generuje ``frontend/test/completenessFixtures.ts`` z backendowego silnika kompletności (AU-104).

Fixtures UI nie są pisane ręcznie: powstają z ``build_completeness`` na zamrożonej odpowiedzi raportu
(``tests/fixtures/reports/multizone.json``) w pięciu przypadkach — kompletna z wektorem, kompletna z aktu i dokumentu,
częściowa (POG bez pełnego pokrycia), kompletna z ``no_match`` (relacja MPZP–POG poza zakresem) i wstrzymana do
podania symbolu strefy. Uruchomienie z katalogu ``backend`` w obrazie testowym::

    python scripts/export_completeness_fixtures.py > ../frontend/test/completenessFixtures.ts
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas.analyze import MpzpStatus  # noqa: E402
from app.services.completeness import build_completeness  # noqa: E402
from app.services.pog_scenarios import assess_mpzp_pog_compatibility  # noqa: E402
from tests.report_map_reference import load_fixture  # noqa: E402


def _base():  # noqa: ANN202
    response, _ = load_fixture("multizone")
    # Zamrożona odpowiedź ma celowo sprzeczne parametry (do testów raportu); sprzeczność uniemożliwia ``complete``.
    response = response.model_copy(
        update={
            "mpzp_zones": [
                zone.model_copy(
                    update={
                        "parameters": [
                            parameter.model_copy(update={"conflict_group_id": None}) for parameter in zone.parameters
                        ]
                    }
                )
                for zone in response.mpzp_zones
            ]
        }
    )
    assessment, _warnings = assess_mpzp_pog_compatibility(
        response.mpzp_zones, response.pog, as_of=datetime(2026, 10, 1, tzinfo=timezone.utc)
    )
    assessment = assessment.model_copy(update={"status": "compatible", "reason_code": "PAIRS_EVALUATED"})
    pog = response.pog.model_copy(update={"compatibility_assessment": assessment})
    return response.model_copy(update={"pog": pog, "section_quality": None, "completeness": None})


def _cases() -> dict[str, object]:
    base = _base()
    candidates = [zone.model_copy(update={"assignment_method": "document_candidate"}) for zone in base.mpzp_zones]
    no_match_status = MpzpStatus(
        status="no_match",
        reason_code="MPZP_NO_ACT_AT_PARCEL",
        label="Nie znaleziono planu",
        message="Odpytane kanały odpowiedziały i nie wskazały obowiązującego planu.",
        precedence_rule_version="mpzp-act-precedence/1.0",
    )
    unknown_relation = base.pog.compatibility_assessment.model_copy(
        update={"status": "unknown", "reason_code": "MPZP_ZONES_MISSING"}
    )
    return {
        "complete_vector": base,
        # Strefa z aktu i dokumentu nie ma geometrii: ocena relacji MPZP–POG jest nierozstrzygalna (jak w analizie na żywo).
        "complete_document": base.model_copy(
            update={
                "mpzp_zones": candidates,
                "pog": base.pog.model_copy(
                    update={
                        "compatibility_assessment": base.pog.compatibility_assessment.model_copy(
                            update={"status": "unknown", "reason_code": "PAIRS_EVALUATED"}
                        )
                    }
                ),
            }
        ),
        "partial_pog": base.model_copy(update={"pog": base.pog.model_copy(update={"coverage_status": "partial"})}),
        "complete_no_match": base.model_copy(
            update={
                "mpzp_zones": [],
                "mpzp_status": no_match_status,
                "pog": base.pog.model_copy(update={"compatibility_assessment": unknown_relation}),
            }
        ),
        "waiting": base.model_copy(update={"manual_zone_required": True, "mpzp_zones": []}),
    }


def main() -> None:
    payload = {name: build_completeness(response).model_dump(mode="json") for name, response in _cases().items()}
    print(
        '/**\n'
        ' * Kompletność analizy wygenerowana przez backend (`build_completeness`, reguła `completeness/1.0`) z zamrożonej\n'
        ' * odpowiedzi raportu `multizone` — po jednym przypadku na istotną sytuację (AU-104). Nie edytuj ręcznie:\n'
        ' * `python scripts/export_completeness_fixtures.py > ../frontend/test/completenessFixtures.ts`.\n'
        ' */\n'
        'import type { Completeness } from "@/lib/types";\n'
    )
    print("export const COMPLETENESS_CASES: Record<string, Completeness> = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";")


if __name__ == "__main__":
    main()
