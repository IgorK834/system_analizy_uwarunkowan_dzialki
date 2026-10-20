"""Mapowanie wyniku pokrycia na API i bezpieczny odczyt historyczny."""

from pydantic import ValidationError
from shapely import from_wkb
from app.modules.analysis.application.landcover import LandCoverOutcome
from app.modules.analysis.domain.landcover import CLASS_LABELS
from app.schemas.land_cover import ForestBuffer, ForestContext, LandCoverResult, LandCoverShare, LandCoverZone
from app.services.bdot_context import bdot_source_metadata, release_info
from app.services.geojson import analysis_layer_geometry_to_geojson


def missing_land_cover(status="unknown", reason="SECTION_NOT_RECORDED") -> LandCoverResult:
    return LandCoverResult(
        status=status,
        reason_code=reason,
        forest=ForestContext(status=status, reason_code=reason),
        source=bdot_source_metadata(None, available=False, partial=False, manual_review=status == "unavailable")
        if status != "unknown"
        else None,
        warnings=["Pokrycia terenu nie ustalono. Brak danych nie oznacza braku lasu ani ograniczeń."],
    )


def land_cover_from_snapshot(raw):
    if raw is None:
        return missing_land_cover()
    try:
        return LandCoverResult.model_validate(raw)
    except ValidationError:
        return missing_land_cover("unavailable", "SNAPSHOT_SCHEMA_INVALID")


def build_land_cover(outcome: LandCoverOutcome) -> LandCoverResult:
    zones = {}
    features = []
    for zone in ("parcel", "surroundings"):
        rows = [r for r in outcome.measurements if r.zone == zone]
        if not rows:
            continue
        classes = [
            LandCoverShare(
                class_code=r.class_code,
                label=CLASS_LABELS[r.class_code],
                area_sqm=round(r.area_sqm, 4),
                share_pct=r.area_sqm / r.zone_area_sqm * 100,
            )
            for r in rows
        ]
        zones[zone] = LandCoverZone(
            area_sqm=rows[0].zone_area_sqm, classified_share_pct=sum(c.share_pct for c in classes), classes=classes
        )
        for row in rows:
            feature = analysis_layer_geometry_to_geojson(
                from_wkb(row.geometry_wkb),
                "land_cover",
                {"class_code": row.class_code, "label": CLASS_LABELS[row.class_code], "zone": zone},
            )
            if feature:
                features.append(feature)
    forest_status, forest_reason = outcome.forest_status, outcome.forest_reason_code
    if forest_status == "unknown" and forest_reason is None and outcome.status in {"no_coverage", "unavailable"}:
        forest_status, forest_reason = outcome.status, outcome.reason_code
    forest = ForestContext(
        status=forest_status,
        reason_code=forest_reason,
        search_radius_m=outcome.forest_search_radius_m,
    )
    if outcome.forest is not None:
        item = outcome.forest
        forest.distance_m, forest.forest_id = round(item.distance_m, 2), item.local_id
        forest.geometry_geojson = analysis_layer_geometry_to_geojson(
            from_wkb(item.geometry_wkb), "forest", {"forest_id": item.local_id}
        )
        forest.buffers = [
            ForestBuffer(
                radius_m=r,
                overlap_with_parcel_sqm=round(a, 4),
                geometry_geojson=analysis_layer_geometry_to_geojson(from_wkb(wkb), "forest_buffer", {"radius_m": r})
                if wkb
                else None,
            )
            for r, a, wkb in item.buffers
        ]
    incomplete_forest = outcome.status == "available" and outcome.forest_status not in {"available", "no_match"}
    warnings = (
        []
        if outcome.status == "available"
        else ["Niepełne lub niedostępne dane pokrycia terenu. Brak danych nie oznacza braku lasu ani ograniczeń."]
    )
    if incomplete_forest:
        warnings.append(
            "Udziały pokrycia policzono, lecz sąsiedztwa lasu nie ustalono w pełnym zasięgu. Brak danych nie oznacza braku lasu."
        )
    return LandCoverResult(
        status=outcome.status,
        reason_code=outcome.reason_code,
        search_area_within_coverage=outcome.search_area_within_coverage,
        release=release_info(outcome.release),
        source=bdot_source_metadata(
            outcome.release,
            available=outcome.status == "available",
            partial=outcome.status == "unknown" or incomplete_forest,
            manual_review=outcome.status != "available" or incomplete_forest,
        ),
        forest=forest,
        features_geojson=features,
        warnings=warnings,
        **zones,
    )
