"""Paczka audytowa: akty MPZP, warstwa ``layers/mpzp_acts.geojson`` i stan ustalenia MPZP (AU-101, AU-103).

Akty i stany pochodzą z rzeczywistych, zamrożonych odpowiedzi KIMPZP i RU; paczka jest budowana z ``AnalyzeResponse`` bez
bazy (adapter ``build_audit_input`` + ``build_audit_package``).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.modules.reporting.application.audit_export import build_audit_package
from app.services import mpzp_acts as service
from app.shared.mpzp_status_text import MPZP_STATUS_LABELS, MPZP_STATUS_MISSING_MESSAGE, MPZP_STATUS_VALUES
from tests.report_map_reference import load_fixture
from tests.ru_mpzp_fixtures import BIELSKO, GORA_KALWARIA
from tests.test_audit_export import LIMITS, _catalog, _input
from tests.test_mpzp_acts_service import _flow, _status


def _update(slug: str, html: str):  # noqa: ANN202
    _, _, resolution = asyncio.run(_flow(slug, html))

    def apply(response):  # noqa: ANN001, ANN202
        return response.model_copy(
            update={
                "mpzp_zones": [],
                "mpzp_acts": service.acts_to_contract(resolution),
                "mpzp_status": _status(resolution),
                "sources": [
                    *response.sources,
                    *[source for source in resolution.sources.values()],
                ],
            }
        )

    return apply


def _package(slug: str, html: str, **redistribution: str):  # noqa: ANN202
    return build_audit_package(_input(catalog=_catalog(**redistribution), response_update=_update(slug, html)), LIMITS)


def test_ru_act_layer_is_included_because_ru_allows_redistribution() -> None:
    package = _package(BIELSKO, "bielsko_biala_246101_1.0056.155_3.html")
    layer = json.loads(package.by_name("layers/mpzp_acts.geojson").data)
    assert layer["type"] == "FeatureCollection" and layer["name"] == "mpzp_acts"
    assert layer["metadata"]["source_ids"] == ["mpzp_ru"]
    assert layer["metadata"]["computation_crs"] == "EPSG:2180"
    parts = {feature["properties"]["part"] for feature in layer["features"]}
    assert parts == {"intersection", "boundary_window"}
    properties = layer["features"][0]["properties"]
    assert properties["resolution_number"] == "LIII/749/1998"
    assert properties["share_pct"] == pytest.approx(11.84, abs=0.02)
    assert properties["area_sqm"] == pytest.approx(167.44, abs=0.05)
    assert properties["sources"] == ["mpzp_ru"]
    assert {feature["properties"]["layer"] for feature in layer["features"]} == {
        "mpzp_act_intersection",
        "mpzp_act_boundary",
    }
    manifest = json.loads(package.by_name("manifest.json").data)
    assert not any(item["name"] == "layers/mpzp_acts.geojson" for item in manifest.get("omitted", []))


def test_analysis_json_has_acts_and_state_without_embedded_geometry() -> None:
    package = _package(BIELSKO, "bielsko_biala_246101_1.0056.155_3.html")
    result = json.loads(package.by_name("analysis.json").data)["result"]
    assert result["mpzp_status"]["status"] == "available"
    assert result["mpzp_status"]["reason_code"] == "MPZP_ACT_FOUND"
    assert result["mpzp_status"]["precedence_rule_version"] == "mpzp-act-precedence/1.0"
    assert {c["channel"]: c["status"] for c in result["mpzp_status"]["channels"]} == {
        "kimpzp": "unavailable",
        "mpzp_ru": "available",
    }
    (act,) = result["mpzp_acts"]
    assert act["resolution_number"] == "LIII/749/1998" and act["intersection_pct"] == pytest.approx(11.84, abs=0.02)
    # Geometria jest w plikach GeoJSON, a nie w analysis.json (zasada paczki).
    assert act["intersection_geojson"] == {"moved_to_geojson_files": True}
    assert act["boundary_geojson"] == {"moved_to_geojson_files": True}


def test_layer_is_omitted_with_a_reason_when_ru_forbids_redistribution() -> None:
    package = _package(BIELSKO, "bielsko_biala_246101_1.0056.155_3.html", mpzp_ru="forbidden")
    assert "layers/mpzp_acts.geojson" not in {file.name for file in package.files}
    omitted = {item.name: item for item in package.omitted}
    assert omitted["layers/mpzp_acts.geojson"].reason == "redistribution_forbidden"
    assert omitted["layers/mpzp_acts.geojson"].source_ids == ("mpzp_ru",)
    # Akt i stan pozostają w analysis.json — zakaz dotyczy geometrii źródła, nie wyniku.
    result = json.loads(package.by_name("analysis.json").data)["result"]
    assert result["mpzp_acts"][0]["resolution_number"] == "LIII/749/1998"


def test_kimpzp_only_acts_have_no_geometry_layer_and_null_share() -> None:
    package = _package(GORA_KALWARIA, "gora_kalwaria_141801_4.0701.23_8.html")
    assert "layers/mpzp_acts.geojson" not in {file.name for file in package.files}
    result = json.loads(package.by_name("analysis.json").data)["result"]
    assert [act["resolution_number"] for act in result["mpzp_acts"]] == ["IV/30/2024", "576/XLVII/2010"]
    assert all(act["intersection_pct"] is None and act["intersection_area_sqm"] is None for act in result["mpzp_acts"])
    assert result["mpzp_status"]["status"] == "ambiguous"
    assert result["mpzp_status"]["rejected_acts"][0]["reason"] == "no_overlap"


def test_readme_lists_the_six_states_and_the_state_of_this_analysis() -> None:
    package = _package(GORA_KALWARIA, "gora_kalwaria_141801_4.0701.23_8.html")
    readme = package.by_name("README.md").data.decode("utf-8")
    assert "## Stan ustalenia MPZP i akty" in readme
    for key in MPZP_STATUS_VALUES:
        assert f"`{key}` — {MPZP_STATUS_LABELS[key]}" in readme
    assert "Stan w tej analizie: `ambiguous`" in readme and "`MPZP_ACTS_UNRESOLVED`" in readme
    assert "layers/mpzp_acts.geojson" in readme
    assert "nie zero" in readme


def test_snapshot_without_state_is_labelled_in_the_readme() -> None:
    def legacy(response):  # noqa: ANN001, ANN202
        return response.model_copy(update={"mpzp_status": None, "mpzp_acts": []})

    package = build_audit_package(_input(response_update=legacy), LIMITS)
    readme = package.by_name("README.md").data.decode("utf-8")
    assert MPZP_STATUS_MISSING_MESSAGE in readme
    result = json.loads(package.by_name("analysis.json").data)["result"]
    assert result["mpzp_status"] is None and result["mpzp_acts"] == []
    assert load_fixture("multizone")[0].mpzp_status is None
