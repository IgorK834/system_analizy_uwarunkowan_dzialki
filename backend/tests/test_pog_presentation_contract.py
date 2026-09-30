"""Kontrakt wspólnego artefaktu prezentacji POG (BK-403).

Sprawdza, że ``shared/pog-presentation.json``:
- zawiera pełną listę stref ze zamrożonego urzędowego słownika
  RodzajStrefyPlanistycznejKod (kod, etykieta, kolejność, źródło i data),
- ma ciągłe progi z jawnym domknięciem, jednostki zgodne z importem APP i styl
  braku wartości odrębny od zera,
- zasila backendowy adapter, legendę i mapę raportu tymi samymi wartościami,
- trafia do snapshotu analizy tak, że stary raport rysuje się zapisanym stylem,
- jest kopiowany do obu obrazów Docker z jednego kontekstu ``shared``.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml
from PIL import Image, ImageDraw

from app.core.planning_compatibility import PogPlanningZoneType
from app.core.pog_presentation import (
    PogPresentationError,
    load_pog_presentation,
    parse_pog_presentation,
    presentation_path_candidates,
    style_snapshot,
)
from app.core.report_config import (
    ReportPogStyle,
    pog_overlay_layer_style,
    pog_zone_layer_style,
    report_pog_style,
)
from app.modules.planning.domain.pog_features import POG_PARAMETER_NAMES, POG_ZONE_CODES
from app.schemas.analyze import (
    PogAreaResult,
    PogPresentationStyle,
    PogResult,
    PogZoneResult,
)
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform

from app.services import report_map
from app.services.report_map_snapshot import build_report_map_snapshot
from app.services.pog_analyzer import with_presentation_style
from tests.repo_structure import find_repo_root

_TO_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)

FIXTURES = Path(__file__).parent / "fixtures" / "pog_presentation"
RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
SKOS = "{http://www.w3.org/2004/02/skos/core#}"
DCT = "{http://purl.org/dc/terms/}"
EXPECTED_UNITS = {
    "max_overground_floor_area_ratio": "1",
    "max_building_height_m": "m",
    "max_building_coverage_pct": "%",
    "min_biologically_active_pct": "%",
}


def _repo_file(relative: str) -> Path:
    return find_repo_root() / relative


def _shared_bytes() -> bytes:
    return _repo_file("shared/pog-presentation.json").read_bytes()


def _official_zones() -> list[dict[str, object]]:
    root = ET.parse(FIXTURES / "codelist_rodzaj_strefy_planistycznej.xml").getroot()
    zones = []
    for description in root.findall(f"{RDF}Description"):
        alt = description.find(f"{SKOS}altLabel")
        if alt is None:
            continue
        definition = description.findtext(f"{SKOS}definition", "")
        point = re.search(r"art\. 13c ust\. 2 pkt (\d+)", definition)
        assert point, definition
        zones.append({
            "code": alt.text,
            "codelist_id": description.findtext(f"{DCT}identifier"),
            "label": " ".join((description.findtext(f"{SKOS}prefLabel") or "").split()),
            "order": int(point.group(1)),
        })
    return sorted(zones, key=lambda item: int(item["order"]))  # type: ignore[arg-type]


# --- Strefy względem urzędowego słownika -----------------------------------------


def test_zone_list_matches_frozen_official_codelist() -> None:
    presentation = load_pog_presentation().presentation
    ours = [
        {"code": z.code, "codelist_id": z.codelist_id, "label": z.label, "order": z.order}
        for z in sorted(presentation.zones, key=lambda item: item.order)
    ]
    assert ours == _official_zones()
    assert len(ours) == 13


def test_codelist_provenance_is_recorded_and_matches_fixture() -> None:
    presentation = load_pog_presentation().presentation
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["codelist_rodzaj_strefy_planistycznej.xml"]
    fixture_sha = hashlib.sha256(
        (FIXTURES / "codelist_rodzaj_strefy_planistycznej.xml").read_bytes()
    ).hexdigest()
    assert fixture_sha == entry["sha256"] == presentation.zone_dictionary.sha256
    assert presentation.zone_dictionary.url == entry["url"]
    assert presentation.zone_dictionary.verified_at == "2026-09-28"
    assert "art. 13c ust. 2" in presentation.zone_dictionary.legal_basis


def test_zone_codes_match_domain_and_compatibility_enum() -> None:
    codes = {zone.code for zone in load_pog_presentation().presentation.zones}
    assert codes == set(POG_ZONE_CODES)
    assert codes == {item.value for item in PogPlanningZoneType} - {"unknown"}


def test_unknown_and_null_styles_are_labelled_and_patterned() -> None:
    presentation = load_pog_presentation().presentation
    assert presentation.unknown_zone.code == "unknown" and presentation.unknown_zone.pattern
    assert presentation.null_style.pattern and "nie jest wartość 0" in presentation.null_style.description
    assert presentation.null_style.pattern != presentation.unknown_zone.pattern
    all_fills = {zone.fill for zone in presentation.zones}
    assert len(all_fills) == 13
    assert presentation.null_style.fill not in all_fills


def test_project_style_differs_by_colour_pattern_and_text() -> None:
    """BK-406: projekt ≠ akt wiążący — krycie, wzór, obrys i stały tekst plakietki."""
    presentation = load_pog_presentation().presentation
    styles = {item.status: item for item in presentation.legal_statuses}
    binding, project = styles["binding"], styles["project"]
    assert binding.pattern is None and binding.badge is None
    assert project.badge == styles["in_progress"].badge == "projekt / dane niewiążące"
    assert project.pattern and project.fill_opacity < binding.fill_opacity
    assert project.line_dasharray and not binding.line_dasharray
    # Wzór projektu nie może udawać nakładki OUZ/OZS/OSDIS ani „brak wartości”.
    taken = {presentation.null_style.pattern, presentation.unknown_zone.pattern} | {
        overlay.pattern for overlay in presentation.overlays
    }
    assert project.pattern not in taken
    for status in styles.values():
        assert "brak planu" not in (status.description + (status.badge or "")).lower()

    raw = json.loads(_shared_bytes())
    project_index = next(
        index for index, item in enumerate(raw["legal_statuses"]) if item["status"] == "project"
    )
    raw["legal_statuses"][project_index]["pattern"] = None
    with pytest.raises(PogPresentationError, match="wymaga wzoru i plakietki"):
        parse_pog_presentation(json.dumps(raw).encode("utf-8"))
    raw["legal_statuses"][project_index]["pattern"] = "horizontal-lines"
    binding_index = next(
        index for index, item in enumerate(raw["legal_statuses"]) if item["status"] == "binding"
    )
    raw["legal_statuses"][binding_index]["badge"] = "projekt / dane niewiążące"
    with pytest.raises(PogPresentationError, match="nie może mieć wzoru ani plakietki"):
        parse_pog_presentation(json.dumps(raw).encode("utf-8"))


# --- Tematy, progi i jednostki -----------------------------------------------------


def test_numeric_themes_use_exact_bk105_fields_and_units() -> None:
    presentation = load_pog_presentation().presentation
    numeric = {t.property: t for t in presentation.themes if t.kind == "numeric"}
    assert set(numeric) == set(POG_PARAMETER_NAMES)
    for name, theme in numeric.items():
        assert theme.unit == EXPECTED_UNITS[name]
        assert theme.interval_closure == "left" and theme.direction == "ascending"
        assert theme.scale_description and theme.unit_label
    assert numeric["max_overground_floor_area_ratio"].unit_label == "wartość bezwymiarowa"
    zones_theme = presentation.theme("zones")
    assert (zones_theme.kind, zones_theme.property) == ("categorical", "zone_code")


@pytest.mark.parametrize(
    "theme_id", ["intensity", "building_coverage", "height", "biologically_active"]
)
def test_every_threshold_null_and_zero_have_distinct_classes(theme_id: str) -> None:
    theme = load_pog_presentation().presentation.theme(theme_id)
    assert theme.class_for(None) is None
    assert theme.class_for(float("nan")) is None
    assert theme.class_for(0.0) == theme.classes[0]
    for index, item in enumerate(theme.classes):
        assert theme.class_for(item.min) == item, (theme_id, item.min)
        if index:
            assert theme.class_for(item.min - 1e-9) == theme.classes[index - 1]
        assert _formatted(item.min) in item.label, item.label
        if theme.unit in {"m", "%"}:
            assert item.label.endswith(f"{theme.unit}" if theme.unit == "%" else " m")
    assert len({item.color for item in theme.classes}) == len(theme.classes)
    assert theme.breaks == tuple(item.min for item in theme.classes[1:])


def _formatted(value: float) -> str:
    """Polski zapis dolnej granicy klasy (przecinek dziesiętny)."""
    return str(int(value)) if value == int(value) else f"{value:g}".replace(".", ",")


def test_invalid_presentation_is_rejected() -> None:
    data = json.loads(_shared_bytes())
    broken = copy.deepcopy(data)
    broken["themes"][1]["classes"][2]["min"] = 0.7  # luka między klasami
    with pytest.raises(PogPresentationError):
        parse_pog_presentation(json.dumps(broken).encode())
    duplicated = copy.deepcopy(data)
    duplicated["zones"][1]["code"] = "SW"
    with pytest.raises(PogPresentationError):
        parse_pog_presentation(json.dumps(duplicated).encode())
    bad_color = copy.deepcopy(data)
    bad_color["zones"][0]["fill"] = "red"
    with pytest.raises(PogPresentationError):
        parse_pog_presentation(json.dumps(bad_color).encode())
    with pytest.raises(PogPresentationError):
        parse_pog_presentation(b"{not json")


# --- Jeden config zasila legendę i mapę raportu ----------------------------------


def test_report_layer_styles_and_legend_come_from_the_same_config() -> None:
    loaded = load_pog_presentation()
    style = ReportPogStyle(style=style_snapshot(loaded), from_snapshot=True)
    for zone in loaded.presentation.zones:
        layer = pog_zone_layer_style(zone.code, style)
        assert "#%02x%02x%02x" % layer.fill_rgb == zone.fill  # type: ignore[str-format]
        assert "#%02x%02x%02x" % layer.line_rgb == zone.outline
        assert zone.label in layer.label
    unknown = pog_zone_layer_style("XX", style)
    assert unknown.pattern == loaded.presentation.unknown_zone.pattern
    for overlay_id in ("ouz", "downtown", "social_infrastructure_standard"):
        overlay = loaded.presentation.overlay(overlay_id)
        layer = pog_overlay_layer_style(overlay_id, style)
        assert layer.pattern == overlay.pattern and layer.fill_rgb is None
        assert layer.line_dash == overlay.line_dasharray
    patterns = [loaded.presentation.overlay(i).pattern for i in ("ouz", "downtown", "social_infrastructure_standard")]
    assert len(set(patterns)) == 3, "OUZ/OZS/OSDIS muszą różnić się wzorem, nie tylko barwą"


def test_changing_one_config_value_changes_report_style() -> None:
    data = json.loads(_shared_bytes())
    data["zones"][3]["fill"] = "#010203"  # SU
    changed = parse_pog_presentation(json.dumps(data).encode())
    style = ReportPogStyle(style=style_snapshot(changed), from_snapshot=True)
    assert pog_zone_layer_style("SU", style).fill_rgb == (1, 2, 3)
    assert changed.sha256 != load_pog_presentation().sha256


def _zone_square(code: str) -> PogZoneResult:
    lon, lat = 18.54, 54.45
    ring = [[lon, lat], [lon + 0.001, lat], [lon + 0.001, lat + 0.001], [lon, lat + 0.001], [lon, lat]]
    return PogZoneResult(
        id=f"z-{code}", symbol=code, type=code, area_sqm=10.0, area_pct=100.0,
        geometry_geojson={"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring]}},
    )


class _Response:
    def __init__(self, pog: PogResult | None) -> None:
        self.pog = pog
        self.parcel = None
        self.infrastructure = []
        self.risks = []
        self.utilities_preview = None


def _pog_map(pog: PogResult) -> tuple[dict, report_map.RenderedMap]:
    """Mapa POG raportu (BK-503) dla działki równej pierwszej strefie."""
    square = pog.zones[0].geometry_geojson["geometry"]  # type: ignore[index]
    parcel = transform(_TO_2180.transform, shape(square))
    snapshot = build_report_map_snapshot(_Response(pog), parcel, basemap_dir="")
    rendered = report_map.render_report_maps(snapshot, basemap_dir="").by_id("pog")
    assert rendered is not None
    return snapshot, rendered


def test_old_report_uses_saved_style_version() -> None:
    saved = style_snapshot()
    saved["style_version"] = "2020.01.01-1"
    saved["zones"]["SU"]["fill"] = "#00ff00"
    old = PogResult(
        touches_ouz_boundary=False,
        zones=[_zone_square("SU")],
        presentation_style=PogPresentationStyle.model_validate(saved),
    )
    snapshot, rendered = _pog_map(old)
    image = Image.open(io.BytesIO(rendered.png_bytes or b"")).convert("RGB")
    probe = image.getpixel((image.width // 2 + 120, image.height // 2 + 90))
    assert probe[1] > 150 and probe[0] < 150, probe  # zielony ze snapshotu, nie bieżący SU
    assert snapshot["render_config"]["pog"]["style_version"] == "2020.01.01-1"
    assert snapshot["render_config"]["pog"]["style_from_analysis"] is True
    assert rendered.legend[0]["fill"] == "#00ff00"

    legacy = PogResult(touches_ouz_boundary=False, zones=[_zone_square("SU")])
    legacy_style = report_pog_style(legacy)
    assert legacy_style is not None and legacy_style.from_snapshot is False
    assert legacy_style.version == load_pog_presentation().style_version


def test_analysis_result_carries_style_version_and_hash() -> None:
    pog = with_presentation_style(PogResult(touches_ouz_boundary=False))
    assert pog is not None and pog.presentation_style is not None
    loaded = load_pog_presentation()
    assert pog.presentation_style.style_version == loaded.style_version
    assert pog.presentation_style.style_sha256 == loaded.sha256
    restored = PogResult.model_validate(pog.model_dump(mode="json"))
    assert restored.presentation_style == pog.presentation_style
    assert with_presentation_style(None) is None


# --- Oba obrazy Docker z jednego artefaktu ---------------------------------------


def test_backend_loads_the_same_artifact_as_the_repository() -> None:
    """W kontenerze CI (REPO_ROOT) obraz ma tę samą wersję co repozytorium."""
    loaded = load_pog_presentation()
    assert loaded.sha256 == hashlib.sha256(_shared_bytes()).hexdigest()
    assert any(Path(loaded.path) == candidate for candidate in presentation_path_candidates())


def test_both_images_copy_presentation_from_shared_build_context() -> None:
    root = find_repo_root()
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    for service in ("backend", "frontend", "address-index-sync"):
        assert compose["services"][service]["build"]["additional_contexts"] == {"shared": "./shared"}
    backend = (root / "backend" / "Dockerfile").read_text(encoding="utf-8")
    frontend = (root / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY --from=shared pog-presentation.json ./shared/pog-presentation.json" in backend
    assert "COPY --from=shared pog-presentation.json /workspace/shared/pog-presentation.json" in frontend
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "--build-context shared=./shared" in workflow


def test_frontend_adapters_import_the_shared_artifact() -> None:
    root = find_repo_root()
    for adapter in ("frontend/lib/pogZones.ts", "frontend/lib/pogThemes.ts"):
        source = (root / adapter).read_text(encoding="utf-8")
        assert "shared/pog-presentation.json" in source, adapter


def test_configured_path_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "custom.json"
    target.write_bytes(_shared_bytes())
    monkeypatch.setattr("app.core.pog_presentation.settings.pog_presentation_path", str(target))
    assert presentation_path_candidates() == [target]
    monkeypatch.setattr("app.core.pog_presentation.settings.pog_presentation_path", "")
    monkeypatch.setenv("POG_PRESENTATION_PATH", str(target))
    assert presentation_path_candidates() == [target]
    monkeypatch.delenv("POG_PRESENTATION_PATH")
    assert os.environ.get("POG_PRESENTATION_PATH") is None


def _area(identifier: str, lon: float, lat: float, size: float) -> PogAreaResult:
    outer = [[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size], [lon, lat]]
    inset = size / 4
    hole = [
        [lon + inset, lat + inset], [lon + 2 * inset, lat + inset],
        [lon + 2 * inset, lat + 2 * inset], [lon + inset, lat + 2 * inset], [lon + inset, lat + inset],
    ]
    return PogAreaResult(
        id=identifier, area_sqm=1.0, area_pct=10.0,
        geometry_geojson={"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [outer, hole]}},
    )


def test_report_map_distinguishes_overlays_by_pattern_and_dash() -> None:
    """OUZ/OZS/OSDIS i kod nierozpoznany mają wzór i obrys niezależny od barwy."""
    lon, lat = 18.54, 54.45
    pog = with_style(PogResult(
        touches_ouz_boundary=False,
        zones=[_zone_square("SU"), _zone_square("XX")],
        ouz=[_area("ouz", lon, lat, 0.0004)],
        downtown_areas=[_area("ozs", lon + 0.0005, lat, 0.0004)],
        social_infrastructure_standard_areas=[_area("osdis", lon, lat + 0.0005, 0.0004)],
    ))
    _, rendered = _pog_map(pog)
    image = Image.open(io.BytesIO(rendered.png_bytes or b"")).convert("RGB")
    colors = {image.getpixel((x, y)) for x in range(0, image.width, 3) for y in range(0, image.height, 3)}
    for overlay_id in ("ouz", "downtown", "social_infrastructure_standard"):
        outline = pog_overlay_layer_style(overlay_id, report_pog_style(pog)).line_rgb  # type: ignore[arg-type]
        assert any(
            sum(abs(a - b) for a, b in zip(color, outline)) < 60 for color in colors
        ), overlay_id
    assert [entry["pattern_label"] for entry in rendered.legend] == [
        "wypełnienie jednolite",
        "kratka ukośna",
        "ukośne kreskowanie",
        "wypełnienie z kropek",
        "kratka pionowo-pozioma",
        "wypełnienie jednolite",  # obrys działki
    ]
    assert [bool(entry["line_dash"]) for entry in rendered.legend[2:5]] == [True, True, True]


def with_style(pog: PogResult) -> PogResult:
    result = with_presentation_style(pog)
    assert result is not None
    return result


def test_dashed_outline_leaves_gaps() -> None:
    image = Image.new("RGB", (100, 10), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    report_map._draw_dashed(draw, [(0.0, 5.0), (100.0, 5.0)], (0, 0, 0, 255), 2, (4.0, 2.0, 1.0))
    row = [image.getpixel((x, 5)) for x in range(100)]
    assert (0, 0, 0) in row and (255, 255, 255) in row


def test_report_without_pog_or_style_draws_no_pog_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    pog = PogResult(touches_ouz_boundary=False, zones=[_zone_square("SU")])
    square = pog.zones[0].geometry_geojson["geometry"]  # type: ignore[index]
    parcel = transform(_TO_2180.transform, shape(square))
    without = build_report_map_snapshot(_Response(None), parcel, basemap_dir="")
    pog_map = next(item for item in without["maps"] if item["id"] == "pog")
    assert pog_map["status"] == "empty" and [layer["id"] for layer in pog_map["layers"]] == ["parcel"]
    monkeypatch.setattr("app.core.report_config.report_pog_style", lambda pog: None)
    no_style = build_report_map_snapshot(_Response(pog), parcel, basemap_dir="")
    assert next(item for item in no_style["maps"] if item["id"] == "pog")["empty_reason"] == (
        "Brak zapisanego stylu POG — mapy nie narysowano."
    )
