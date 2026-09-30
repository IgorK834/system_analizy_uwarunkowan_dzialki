"""Testy deterministycznych map raportu (BK-503, ADR-010).

Mapy są zamrażane w snapshocie (EPSG:2180, kadr, tryb, style, font, wydania)
i renderowane lokalnie. Testy sprawdzają: czystą domenę (kadr, podziałka, hash
semantyczny, klasy tematu), budowę snapshotu z danych analizy, brak wywołań
sieciowych podczas renderu, niezmienność mapy starego snapshotu po zmianie
upstream/konfiguracji, podkład wyłącznie z zapisanego artefaktu oraz
porównania referencyjne map projektu, wielu stref, ryzyka i wartości null.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import socket
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from shapely.geometry import box

from app.core import report_config
from app.core.pog_presentation import load_pog_presentation, parse_pog_presentation
from app.modules.reporting.domain.map_snapshot import (
    REPORT_MAP_SNAPSHOT_SCHEMA,
    MapSnapshotError,
    canonical_json,
    choose_scale_bar,
    drawable_feature_count,
    fit_frame,
    round_coordinates,
    semantic_hash,
    theme_class_for,
    to_pixel,
    validate_snapshot,
    verify_semantic_hash,
)
from app.services import report_map, report_map_basemap, report_map_snapshot
from app.services.report_map import (
    ensure_valid_snapshot,
    format_length_m,
    png_to_data_uri,
    render_report_maps,
)
from app.services.report_map_snapshot import build_report_map_snapshot
from tests.report_map_reference import (
    CHANNEL_TOLERANCE,
    FROZEN_AT,
    MAPS_DIR,
    MAX_DIFF_RATIO,
    REFERENCE_CASES,
    REFERENCE_FILE,
    diff_ratio,
    load_fixture,
    reference_snapshot,
    render_case,
)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _snapshot(fixture: str = "multizone", theme: str = "zones", **overrides: Any) -> dict[str, Any]:
    response, parcel = load_fixture(fixture)
    if overrides:
        response = response.model_copy(update=overrides)
    return build_report_map_snapshot(response, parcel, pog_theme=theme, basemap_dir="", frozen_at=FROZEN_AT)


def _map(snapshot: dict[str, Any], map_id: str) -> dict[str, Any]:
    return next(item for item in snapshot["maps"] if item["id"] == map_id)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Każda próba połączenia sieciowego podczas renderu kończy test błędem."""
    attempts: list[Any] = []

    def refuse(self: socket.socket, address: Any) -> None:  # noqa: ARG001
        attempts.append(address)
        raise AssertionError(f"Render mapy próbował połączyć się z {address!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: refuse(None, a))  # type: ignore[arg-type]
    return attempts


# --- Czysta domena --------------------------------------------------------------


def test_fit_frame_keeps_metric_aspect_and_margin() -> None:
    frame = fit_frame((0.0, 0.0, 60.0, 40.0), width_px=900, height_px=600, margin_ratio=0.1, min_span_m=10)
    span_x = frame["max_x"] - frame["min_x"]
    span_y = frame["max_y"] - frame["min_y"]
    assert span_x / span_y == pytest.approx(1.5)
    assert frame["meters_per_pixel"] == pytest.approx(span_x / 900)
    assert frame["min_x"] <= -6.0 and frame["max_x"] >= 66.0
    assert frame["min_y"] <= -4.0 and frame["max_y"] >= 44.0
    # Działka wąska i wysoka dopełniana w poziomie.
    tall = fit_frame((0.0, 0.0, 10.0, 100.0), width_px=900, height_px=600, margin_ratio=0.1, min_span_m=10)
    assert (tall["max_x"] - tall["min_x"]) / (tall["max_y"] - tall["min_y"]) == pytest.approx(1.5)
    # Działka „punktowa” dostaje minimalną rozpiętość, nie nieskończoną skalę.
    point = fit_frame((5.0, 5.0, 5.0, 5.0), width_px=900, height_px=600, margin_ratio=0.0, min_span_m=10)
    assert point["max_y"] - point["min_y"] == pytest.approx(10.0)


@pytest.mark.parametrize(
    "bounds,size",
    [((0, 0, float("nan"), 1), (900, 600)), ((5, 0, 1, 1), (900, 600)), ((0, 0, 1, 1), (0, 600))],
)
def test_fit_frame_rejects_invalid_input(bounds: tuple[float, ...], size: tuple[int, int]) -> None:
    with pytest.raises(MapSnapshotError):
        fit_frame(bounds, width_px=size[0], height_px=size[1], margin_ratio=0.1, min_span_m=10)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "mpp,max_px,expected",
    [(0.1, 225, 20.0), (0.08, 225, 10.0), (1.0, 225, 200.0), (0.013, 225, 2.0), (3.3, 225, 500.0)],
)
def test_scale_bar_uses_1_2_5_series(mpp: float, max_px: float, expected: float) -> None:
    bar = choose_scale_bar(mpp, max_px)
    assert bar["length_m"] == expected
    assert bar["length_px"] <= max_px
    assert bar["length_px"] == pytest.approx(expected / mpp, abs=1e-3)
    with pytest.raises(MapSnapshotError):
        choose_scale_bar(0, 100)


def test_length_is_formatted_in_polish() -> None:
    assert format_length_m(20.0) == "20 m"
    assert format_length_m(0.5) == "0,5 m"
    assert format_length_m(1000.0) == "1 000 m"


def test_theme_class_keeps_null_distinct_from_zero_and_uses_left_closed_intervals() -> None:
    classes = [{"min": 0, "max": 0.3, "label": "a"}, {"min": 0.3, "max": None, "label": "b"}]
    assert theme_class_for(None, classes) is None
    assert theme_class_for(float("nan"), classes) is None
    assert theme_class_for(0.0, classes)["label"] == "a"  # type: ignore[index]
    assert theme_class_for(0.3, classes)["label"] == "b"  # type: ignore[index]
    assert theme_class_for(-1.0, classes) is None


def test_round_coordinates_is_stable_and_rejects_garbage() -> None:
    assert round_coordinates([[1.004, -0.001], [2.0, 3.00999]]) == [[1.0, 0.0], [2.0, 3.01]]
    assert str(round_coordinates(-0.001)) == "0.0"
    with pytest.raises(MapSnapshotError):
        round_coordinates("x")
    with pytest.raises(MapSnapshotError):
        round_coordinates(float("inf"))


def test_semantic_hash_ignores_freeze_metadata_but_not_content() -> None:
    snapshot = _snapshot()
    later = copy.deepcopy(snapshot)
    later["frozen_at"] = "2030-01-01T00:00:00+00:00"
    assert semantic_hash(later) == snapshot["semantic_sha256"]
    assert verify_semantic_hash(snapshot)
    changed = copy.deepcopy(snapshot)
    changed["maps"][0]["layers"][-1]["features"][0]["geometry"]["coordinates"][0][0][0] += 0.01
    assert semantic_hash(changed) != snapshot["semantic_sha256"]
    assert not verify_semantic_hash(changed)
    assert canonical_json({"b": 1, "a": "ł"}) == '{"a":"ł","b":1}'


def test_validate_snapshot_rejects_foreign_schema_and_crs() -> None:
    snapshot = _snapshot()
    validate_snapshot(snapshot)
    assert ensure_valid_snapshot(snapshot)
    for mutate in (
        lambda s: s.update(schema="other/1"),
        lambda s: s["frame"].update(crs="EPSG:4326"),
        lambda s: s["frame"].pop("meters_per_pixel"),
        lambda s: s.update(maps=None),
    ):
        broken = copy.deepcopy(snapshot)
        mutate(broken)
        with pytest.raises(MapSnapshotError):
            validate_snapshot(broken)
        assert not ensure_valid_snapshot(broken)


# --- Budowa snapshotu -------------------------------------------------------------


def test_snapshot_freezes_geometry_frame_style_mode_and_releases() -> None:
    snapshot = _snapshot()
    assert snapshot["schema"] == REPORT_MAP_SNAPSHOT_SCHEMA
    config = snapshot["render_config"]
    assert config["config_version"] == report_config.REPORT_MAP_CONFIG_VERSION
    assert config["crs"] == "EPSG:2180" and config["pog_theme"] == "zones"
    assert config["font"]["family"] == "DejaVu Sans"
    assert config["pog"]["style_version"] == load_pog_presentation().style_version
    assert config["pog"]["theme"]["id"] == "zones"
    assert set(config["pog"]["legal_statuses"]) >= {"binding", "project"}
    assert config["basemap"] == {"mode": "neutral"}

    frame = snapshot["frame"]
    assert frame["crs"] == "EPSG:2180"
    assert frame["min_x"] < 566000 and frame["max_x"] > 566060
    assert frame["scale_bar"]["length_m"] == 10.0

    assert [item["id"] for item in snapshot["maps"]] == ["parcel", "mpzp", "pog", "environment"]
    parcel_layer = _map(snapshot, "parcel")["layers"][-1]
    assert parcel_layer["id"] == "parcel"
    ring = parcel_layer["features"][0]["geometry"]["coordinates"][0]
    assert {tuple(point) for point in ring} == {
        (566000.0, 244000.0), (566060.0, 244000.0), (566060.0, 244040.0), (566000.0, 244040.0)
    }
    # Obrys działki jest zawsze na wierzchu każdej mapy.
    assert all(item["layers"][-1]["id"] == "parcel" for item in snapshot["maps"] if item["layers"])
    pog = _map(snapshot, "pog")
    assert [layer["id"] for layer in pog["layers"]] == [
        "pog_zone:zone:SW", "pog_zone:zone:SJ", "pog_zone:zone:SU",
        "pog_overlay:ouz", "pog_overlay:downtown", "pog_overlay:social_infrastructure_standard", "parcel",
    ]
    assert pog["data_release_ids"] == [12]
    assert pog["data_dates"] == ["2026-09-20T09:29:00+00:00"]
    assert _map(snapshot, "mpzp")["data_release_ids"] == [7]
    assert snapshot["data"]["data_release_ids"] == [7, 12]
    assert drawable_feature_count(pog["layers"]) == 7


def test_snapshot_clips_large_features_to_frame_and_rounds_to_centimetres() -> None:
    snapshot = _snapshot()
    frame = snapshot["frame"]
    nature = next(layer for layer in _map(snapshot, "environment")["layers"] if layer["id"] == "nature")
    xs = [point[0] for point in nature["features"][0]["geometry"]["coordinates"][0]]
    ys = [point[1] for point in nature["features"][0]["geometry"]["coordinates"][0]]
    assert min(xs) >= frame["min_x"] - 0.01 and max(xs) <= frame["max_x"] + 0.01
    assert min(ys) >= frame["min_y"] - 0.01 and max(ys) <= frame["max_y"] + 0.01
    assert all(round(value, 2) == value for value in xs + ys)


def test_numeric_theme_uses_class_colour_and_null_style_not_zero() -> None:
    snapshot = _snapshot(theme="height")
    pog = _map(snapshot, "pog")
    null_layer = next(layer for layer in pog["layers"] if layer["id"] == "pog_zone:null")
    assert [feature["id"] for feature in null_layer["features"]] == ["PL.ZIPOG.1261.POG/strefa/3SU"]
    assert null_layer["style"]["pattern"] == "diagonal-hatch"
    assert null_layer["features"][0]["value"] is None
    class_labels = {feature["class_label"] for layer in pog["layers"] for feature in layer["features"]
                    if layer["id"].startswith("pog_zone:class")}
    assert len(class_labels) == 2
    assert "Tryb tematyczny: Maksymalna wysokość zabudowy [m]" == pog["mode_label"]
    assert any(entry["kind"] == "note" for entry in pog["legend"])
    # Zero w temacie liczbowym jest klasą, nie „brakiem wartości”.
    zero = _snapshot(theme="biologically_active")
    zero_pog = _map(zero, "pog")
    assert not any(layer["id"] == "pog_zone:null" for layer in zero_pog["layers"])


def test_project_act_is_marked_by_pattern_dash_and_note() -> None:
    snapshot = _snapshot("project")
    pog = _map(snapshot, "pog")
    zone_layers = [layer for layer in pog["layers"] if layer["id"].startswith("pog_zone:")]
    assert zone_layers and all(layer["style"]["pattern"] == "horizontal-lines" for layer in zone_layers)
    assert all(layer["style"]["line_dash"] == [2, 2] for layer in zone_layers)
    assert pog["legal_status"] == "project"
    assert any("projekt / dane niewiążące" in note for note in pog["notes"])
    binding = _map(_snapshot(), "pog")
    assert binding["notes"] == []


def test_empty_maps_have_explicit_reasons() -> None:
    response, parcel = load_fixture("multizone")
    manual_zone = response.mpzp_zones[0].model_copy(
        update={"intersection_geojson": None, "assignment_method": "manual_user_input"}
    )
    emptied = response.model_copy(
        update={"mpzp_zones": [manual_zone], "pog": None, "risks": [], "infrastructure": []}
    )
    snapshot = build_report_map_snapshot(emptied, parcel, basemap_dir="", frozen_at=FROZEN_AT)
    mpzp = _map(snapshot, "mpzp")
    assert mpzp["status"] == "empty" and "MN.1" in mpzp["empty_reason"]
    assert "nie oznacza braku planu" in mpzp["empty_reason"]
    pog = _map(snapshot, "pog")
    assert pog["status"] == "empty" and "brak wyniku nie oznacza braku planu" in pog["empty_reason"]
    environment = _map(snapshot, "environment")
    assert environment["status"] == "empty"
    assert "powódź — available" in environment["empty_reason"]
    no_mpzp = build_report_map_snapshot(
        emptied.model_copy(update={"mpzp_zones": []}), parcel, basemap_dir="", frozen_at=FROZEN_AT
    )
    assert "nie sprawdzono stref MPZP" in _map(no_mpzp, "mpzp")["empty_reason"]
    # Strefy bez geometrii obok narysowanych są wymienione w notatce.
    mixed = build_report_map_snapshot(
        response.model_copy(update={"mpzp_zones": [response.mpzp_zones[1], manual_zone]}),
        parcel, basemap_dir="", frozen_at=FROZEN_AT,
    )
    assert _map(mixed, "mpzp")["notes"] == ["Bez geometrii (nienarysowane): MN.1."]


def test_pog_without_geometry_or_style_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    response, parcel = load_fixture("multizone")
    assert response.pog is not None
    no_geometry = response.pog.model_copy(
        update={
            "zones": [zone.model_copy(update={"geometry_geojson": None}) for zone in response.pog.zones],
            "ouz": [], "downtown_areas": [], "social_infrastructure_standard_areas": [],
        }
    )
    snapshot = build_report_map_snapshot(
        response.model_copy(update={"pog": no_geometry}), parcel, basemap_dir="", frozen_at=FROZEN_AT
    )
    assert "Brak geometrii nie oznacza braku planu" in _map(snapshot, "pog")["empty_reason"]
    monkeypatch.setattr(report_map_snapshot, "report_map_render_config",
                        lambda pog, theme, basemap: {**report_config.report_map_render_config(None, theme, basemap)})
    no_style = build_report_map_snapshot(response, parcel, basemap_dir="", frozen_at=FROZEN_AT)
    assert _map(no_style, "pog")["empty_reason"] == "Brak zapisanego stylu POG — mapy nie narysowano."


def test_unknown_zone_code_uses_unknown_style_and_invalid_geometry_is_skipped() -> None:
    response, parcel = load_fixture("multizone")
    assert response.pog is not None
    zones = list(response.pog.zones)
    zones[0] = zones[0].model_copy(update={"type": "XX"})
    zones[1] = zones[1].model_copy(update={"geometry_geojson": {"type": "Polygon", "coordinates": "zły"}})
    snapshot = build_report_map_snapshot(
        response.model_copy(update={"pog": response.pog.model_copy(update={"zones": zones})}),
        parcel, basemap_dir="", frozen_at=FROZEN_AT,
    )
    layers = {layer["id"]: layer for layer in _map(snapshot, "pog")["layers"]}
    assert layers["pog_zone:unknown"]["style"]["pattern"] == "cross-hatch"
    assert "pog_zone:zone:SJ" not in layers


def test_snapshot_requires_parcel_geometry() -> None:
    response, _ = load_fixture("multizone")
    with pytest.raises(ValueError):
        build_report_map_snapshot(response, box(0, 0, 1, 1).difference(box(0, 0, 1, 1)))


def test_infrastructure_buffers_are_drawn_as_approximation() -> None:
    response, parcel = load_fixture("multizone")
    from app.schemas.analyze import InfrastructureResult
    from app.schemas.source import SourceMetadata

    parcel_feature = response.parcel.geometry_geojson  # type: ignore[union-attr]
    item = InfrastructureResult(
        network_type="woda", buffer_m=3.0, zone_area_sqm=12.0,
        network_geometry_geojson={"type": "LineString", "coordinates": parcel_feature["geometry"]["coordinates"][0][:2]},
        protection_zone_geojson=parcel_feature,
        source=SourceMetadata(source_name="KIUT", confidence=0.4, manual_review_required=True),
    )
    snapshot = build_report_map_snapshot(
        response.model_copy(update={"infrastructure": [item]}), parcel, basemap_dir="", frozen_at=FROZEN_AT
    )
    parcel_map = _map(snapshot, "parcel")
    kinds = {layer["id"]: layer["kind"] for layer in parcel_map["layers"]}
    assert kinds == {"protection_zone": "approximation", "network": "approximation",
                     "buildable_area": "approximation", "parcel": "source"}
    assert {entry["kind"] for entry in parcel_map["legend"]} >= {"approximation"}
    png = render_report_maps(snapshot, basemap_dir="").by_id("parcel")
    assert png is not None and png.png_bytes.startswith(_PNG_SIGNATURE)  # type: ignore[union-attr]


# --- Render ---------------------------------------------------------------------


def test_render_returns_png_per_map_with_legend_and_provenance(no_network: list[Any]) -> None:
    maps = render_report_maps(_snapshot(), basemap_dir="")
    assert [item.id for item in maps.maps] == ["parcel", "mpzp", "pog", "environment"]
    for item in maps.maps:
        assert item.png_bytes is not None and item.png_bytes.startswith(_PNG_SIGNATURE)
        image = Image.open(io.BytesIO(item.png_bytes))
        assert image.size == (report_config.MAP_IMAGE_WIDTH, report_config.MAP_IMAGE_HEIGHT)
        assert item.data_uri.startswith("data:image/png;base64,")  # type: ignore[union-attr]
        assert item.png_sha256 == hashlib.sha256(item.png_bytes).hexdigest()
    assert maps.integrity_ok and maps.from_snapshot
    assert maps.semantic_sha256 == maps.stored_semantic_sha256
    assert maps.environment["font_family"] == "DejaVu Sans"
    assert maps.basemap == {"mode": "neutral", "used": False}
    pog = maps.by_id("pog")
    assert pog is not None
    labels = [entry["label"] for entry in pog.legend]
    assert labels[0].startswith("SW — ") and any(label.startswith("OUZ — ") for label in labels)
    patterns = {entry["pattern_label"] for entry in pog.legend}
    assert {"ukośne kreskowanie", "wypełnienie z kropek"} <= patterns
    assert maps.by_id("missing") is None
    assert no_network == []


def test_render_is_byte_identical_for_identical_snapshot(no_network: list[Any]) -> None:
    first = render_report_maps(_snapshot(), basemap_dir="")
    second = render_report_maps(_snapshot(), basemap_dir="")
    assert [item.png_sha256 for item in first.maps] == [item.png_sha256 for item in second.maps]
    assert first.semantic_sha256 == second.semantic_sha256


def test_tampered_snapshot_is_rendered_with_integrity_warning() -> None:
    snapshot = _snapshot()
    snapshot["maps"][2]["layers"][0]["style"]["fill"] = "#000000"
    maps = render_report_maps(snapshot, basemap_dir="")
    assert not maps.integrity_ok
    assert any("zmodyfikowany" in warning for warning in maps.warnings)
    # Odtworzona (nie zapisana) specyfikacja nie jest oceniana jako naruszona.
    rebuilt = render_report_maps(snapshot, from_snapshot=False, basemap_dir="")
    assert not any("zmodyfikowany" in warning for warning in rebuilt.warnings)


def test_projection_maps_frame_corners_to_canvas() -> None:
    frame = _snapshot()["frame"]
    assert to_pixel(frame, frame["min_x"], frame["max_y"]) == pytest.approx((0.0, 0.0))
    assert to_pixel(frame, frame["max_x"], frame["min_y"]) == pytest.approx((900.0, 600.0), abs=0.05)


def test_parcel_outline_colour_is_drawn_at_parcel_edge() -> None:
    snapshot = _snapshot()
    png = render_report_maps(snapshot, basemap_dir="").by_id("mpzp").png_bytes  # type: ignore[union-attr]
    image = Image.open(io.BytesIO(png)).convert("RGB")
    x, y = to_pixel(snapshot["frame"], 566030.0, 244040.0)
    assert image.getpixel((round(x), round(y))) == report_config.PARCEL_LAYER_STYLE.line_rgb
    background = image.getpixel((890, 300))
    assert background == report_config.MAP_BACKGROUND_RGB


def test_renderer_modules_have_no_http_client() -> None:
    for module in (report_map, report_map_basemap, report_map_snapshot):
        source = Path(module.__file__).read_text("utf-8")  # type: ignore[arg-type]
        assert "httpx" not in source and "urllib" not in source and "requests" not in source


def test_missing_font_falls_back_to_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    report_map._load_fonts.cache_clear()
    monkeypatch.setattr(report_map, "REPORT_MAP_FONT_DIRS", ("/nonexistent",))
    try:
        maps = render_report_maps(_snapshot(), basemap_dir="")
        assert maps.environment["font_sha256"] is None
        assert "wbudowany" in maps.environment["font_file"]
        assert maps.by_id("pog").png_bytes is not None  # type: ignore[union-attr]
    finally:
        report_map._load_fonts.cache_clear()


def test_png_to_data_uri_roundtrip() -> None:
    uri = png_to_data_uri(_PNG_SIGNATURE + b"x")
    assert uri.startswith("data:image/png;base64,")


# --- Niezmienność po zmianie upstream i konfiguracji --------------------------------


def test_snapshot_a_is_unchanged_after_release_b_and_config_change(
    monkeypatch: pytest.MonkeyPatch, no_network: list[Any]
) -> None:
    """Snapshot A: te same granice, wartości, legenda i skala po publikacji B."""
    stored = json.loads(json.dumps(_snapshot()))  # jak JSONB w bazie
    before = render_report_maps(stored, basemap_dir="")

    # „Publikacja B”: inna geometria i parametry stref dla tej samej działki…
    response, parcel = load_fixture("multizone")
    assert response.pog is not None
    moved = [
        zone.model_copy(update={"max_building_height_m": 99.0, "geometry_geojson": response.parcel.geometry_geojson})  # type: ignore[union-attr]
        for zone in response.pog.zones
    ]
    snapshot_b = build_report_map_snapshot(
        response.model_copy(update={"pog": response.pog.model_copy(update={"zones": moved[:1]})}),
        parcel, basemap_dir="", frozen_at=FROZEN_AT,
    )
    assert snapshot_b["semantic_sha256"] != stored["semantic_sha256"]

    # …oraz zmiana bieżącej palety POG i wersji konfiguracji renderowania.
    loaded = load_pog_presentation()
    changed = json.loads(Path(loaded.path).read_text("utf-8"))
    changed["style_version"] = "2099.01.01-1"
    changed["zones"][0]["fill"] = "#000000"
    monkeypatch.setattr(report_config, "load_pog_presentation",
                        lambda: parse_pog_presentation(json.dumps(changed).encode()))
    monkeypatch.setattr(report_config, "REPORT_MAP_CONFIG_VERSION", "report-map/2099.01.01-1")
    monkeypatch.setitem(report_config.REPORT_MAP_LAYER_STYLES, "parcel", report_config.NATURE_LAYER_STYLE)

    after = render_report_maps(stored, basemap_dir="")
    assert [item.png_sha256 for item in after.maps] == [item.png_sha256 for item in before.maps]
    assert [item.legend for item in after.maps] == [item.legend for item in before.maps]
    assert after.frame == before.frame and after.frame["scale_bar"] == before.frame["scale_bar"]
    assert after.semantic_sha256 == before.semantic_sha256 == stored["semantic_sha256"]
    assert after.config_version == stored["render_config"]["config_version"] != "report-map/2099.01.01-1"
    # Nowy snapshot po zmianie konfiguracji ma nową wersję i nowy hash.
    fresh = _snapshot()
    assert fresh["render_config"]["config_version"] == "report-map/2099.01.01-1"
    assert fresh["semantic_sha256"] != stored["semantic_sha256"]
    assert no_network == []


# --- Podkład z zapisanego artefaktu --------------------------------------------------


def _write_basemap(directory: Path, frame: dict[str, float], *, allowed: bool = True,
                   sha: str | None = None) -> str:
    image = Image.new("RGB", (400, 300), (10, 120, 200))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    content = buffer.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    (directory / "orto.png").write_bytes(content)
    (directory / "orto.json").write_text(json.dumps({
        "file": "orto.png", "sha256": sha or digest, "crs": "EPSG:2180",
        "bbox": [frame["min_x"] - 50, frame["min_y"] - 50, frame["max_x"] + 50, frame["max_y"] + 50],
        "license": "CC BY 4.0 (fixture)", "attribution": "fixture", "allowed_for_report": allowed,
    }), "utf-8")
    (directory / "zly.json").write_text("{nie json", "utf-8")
    return digest


def test_stored_basemap_artifact_is_used_only_with_matching_sha(tmp_path: Path, no_network: list[Any]) -> None:
    frame = _snapshot()["frame"]
    digest = _write_basemap(tmp_path, frame)
    response, parcel = load_fixture("multizone")
    snapshot = build_report_map_snapshot(response, parcel, basemap_dir=str(tmp_path), frozen_at=FROZEN_AT)
    reference = snapshot["render_config"]["basemap"]
    assert reference["mode"] == "artifact" and reference["sha256"] == digest
    assert reference["license"] == "CC BY 4.0 (fixture)"

    maps = render_report_maps(snapshot, basemap_dir=str(tmp_path))
    assert maps.basemap["used"] is True and maps.warnings == []
    image = Image.open(io.BytesIO(maps.by_id("mpzp").png_bytes)).convert("RGB")  # type: ignore[union-attr]
    assert image.getpixel((890, 300)) == (10, 120, 200)

    # Podmieniona treść pliku (inny SHA) → neutralne tło + adnotacja, bez błędu.
    Image.new("RGB", (400, 300), (0, 0, 0)).save(tmp_path / "orto.png")
    tampered = render_report_maps(snapshot, basemap_dir=str(tmp_path))
    assert tampered.basemap["used"] is False
    assert tampered.warnings == [report_map_basemap.BASEMAP_UNAVAILABLE_NOTE]
    missing = render_report_maps(snapshot, basemap_dir="")
    assert missing.warnings == [report_map_basemap.BASEMAP_UNAVAILABLE_NOTE]
    elsewhere = render_report_maps(snapshot, basemap_dir=str(tmp_path / "brak"))
    assert elsewhere.basemap["used"] is False
    assert no_network == []


def test_basemap_selection_requires_permission_and_full_frame_cover(tmp_path: Path) -> None:
    frame = _snapshot()["frame"]
    _write_basemap(tmp_path, frame, allowed=False)
    assert report_map_basemap.select_basemap_artifact(frame, str(tmp_path)) == {"mode": "neutral"}
    assert report_map_basemap.select_basemap_artifact(frame, str(tmp_path / "brak")) == {"mode": "neutral"}
    assert report_map_basemap.select_basemap_artifact(frame, "") == {"mode": "neutral"}
    _write_basemap(tmp_path, frame)
    shifted = {**frame, "max_x": frame["max_x"] + 500}
    assert report_map_basemap.select_basemap_artifact(shifted, str(tmp_path)) == {"mode": "neutral"}
    assert report_map_basemap.load_basemap_image({"mode": "neutral"}, frame, str(tmp_path)) == (None, None)
    (tmp_path / "orto.png").write_bytes(b"not png")
    digest = hashlib.sha256(b"not png").hexdigest()
    (tmp_path / "orto.json").write_text(json.dumps({
        "file": "orto.png", "sha256": digest, "crs": "EPSG:2180", "bbox": [0, 0, 1, 1],
        "allowed_for_report": True,
    }), "utf-8")
    assert report_map_basemap.load_basemap_image(
        {"mode": "artifact", "sha256": digest, "bbox": [0, 0, 1, 1]}, frame, str(tmp_path)
    ) == (None, report_map_basemap.BASEMAP_UNAVAILABLE_NOTE)
    (tmp_path / "escape.json").write_text(json.dumps({
        "file": "../outside.png", "sha256": "f" * 64, "crs": "EPSG:2180", "bbox": [0, 0, 1, 1],
        "allowed_for_report": True,
    }), "utf-8")
    assert report_map_basemap._find_artifact_bytes("f" * 64, str(tmp_path)) is None


# --- Porównania referencyjne (przypięte środowisko) ---------------------------------


REFERENCE = json.loads(REFERENCE_FILE.read_text("utf-8"))


@pytest.mark.parametrize("case", [item[0] for item in REFERENCE_CASES])
def test_reference_maps_match(case: str, no_network: list[Any]) -> None:
    png, snapshot, environment = render_case(case)
    expected = REFERENCE[case]
    # Znaczenie obrazu (dane + konfiguracja) musi być identyczne zawsze.
    assert snapshot["semantic_sha256"] == expected["semantic_sha256"], case
    reference_png = (MAPS_DIR / f"{case}.png").read_bytes()
    assert diff_ratio(png, reference_png) <= MAX_DIFF_RATIO, case
    # Bajtowa identyczność tylko w identycznym środowisku (Pillow + font).
    if environment == expected["environment"]:
        assert hashlib.sha256(png).hexdigest() == expected["png_sha256"], case
    assert no_network == []


def test_reference_cases_cover_project_multizone_risk_and_null() -> None:
    cases = {case: (fixture, theme, map_id) for case, fixture, theme, map_id in REFERENCE_CASES}
    assert cases["project_pog"][0] == "project"
    assert cases["multizone_pog"][2] == "pog" and cases["multizone_mpzp"][2] == "mpzp"
    assert cases["risk_environment"][2] == "environment"
    assert cases["null_height_pog"][1] == "height"
    assert CHANNEL_TOLERANCE > 0
    assert reference_snapshot("multizone", "zones")["semantic_sha256"] == REFERENCE["multizone_pog"]["semantic_sha256"]


def test_diff_ratio_detects_changed_and_resized_images() -> None:
    png, _, _ = render_case("parcel")
    assert diff_ratio(png, png) == 0.0
    other = Image.new("RGB", (900, 600), (0, 0, 0))
    buffer = io.BytesIO()
    other.save(buffer, format="PNG")
    assert diff_ratio(png, buffer.getvalue()) > 0.5
    small = io.BytesIO()
    Image.new("RGB", (10, 10)).save(small, format="PNG")
    assert diff_ratio(png, small.getvalue()) == 1.0
