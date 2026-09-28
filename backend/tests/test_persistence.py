from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
from shapely.geometry import box
from sqlalchemy import delete, func, select, text

from app.db.session import SessionLocal
from app.models.analysis import Analysis
from app.models.infrastructure import Infrastructure
from app.models.mpzp_parameter import MpzpParameter
from app.models.mpzp_zone import MpzpZone
from app.models.parcel import Parcel
from app.models.pog_data import PogData
from app.models.risk import Risk
from app.models.source_record import SourceRecord
from app.schemas.analyze import (
    AnalyzeResponse,
    GeometryMetrics,
    InfrastructureResult,
    MpzpZoneResult,
    ParcelGeometryResponse,
    PogActResult,
    PogAreaResult,
    PogProfileResult,
    PogResult,
    PogZoneResult,
    RiskResult,
    UtilitiesPreviewResult,
    WarningMessage,
)
from app.schemas.source import SourceMetadata
from app.services.context import ContextResult, ContextSectionResult
from app.services.persistence import build_analyze_response_from_analysis, save_analysis
from app.services.report import (
    _build_report_context,
    _render_report_html,
    generate_analysis_report_pdf,
)

pytestmark = pytest.mark.integration

_PARCEL_PREFIX = "PERSISTENCE_TEST_"
_ANALYZED_AT = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)


def _cleanup() -> None:
    with SessionLocal() as db:
        parcel_ids = select(Parcel.id).where(
            Parcel.parcel_identifier.like(f"{_PARCEL_PREFIX}%")
        )
        analysis_ids = select(Analysis.id).where(Analysis.parcel_id.in_(parcel_ids))
        zone_ids = select(MpzpZone.id).where(MpzpZone.analysis_id.in_(analysis_ids))

        db.execute(
            delete(MpzpParameter).where(MpzpParameter.mpzp_zone_id.in_(zone_ids))
        )
        db.execute(
            delete(SourceRecord).where(SourceRecord.analysis_id.in_(analysis_ids))
        )
        db.execute(delete(Risk).where(Risk.analysis_id.in_(analysis_ids)))
        db.execute(
            delete(Infrastructure).where(Infrastructure.analysis_id.in_(analysis_ids))
        )
        db.execute(delete(PogData).where(PogData.analysis_id.in_(analysis_ids)))
        db.execute(delete(MpzpZone).where(MpzpZone.analysis_id.in_(analysis_ids)))
        db.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))
        db.execute(delete(Parcel).where(Parcel.id.in_(parcel_ids)))
        db.commit()


@pytest.fixture(autouse=True)
def cleanup_persistence_rows():
    _cleanup()
    yield
    _cleanup()


def _source(
    name: str,
    url: str | None,
    *,
    confidence: float = 0.9,
    manual_review_required: bool = False,
    response_status: int | None = 200,
) -> SourceMetadata:
    return SourceMetadata(
        source_name=name,
        source_url=url,
        fetched_at=_ANALYZED_AT,
        response_status=response_status,
        confidence=confidence,
        manual_review_required=manual_review_required,
    )


def _rich_response() -> AnalyzeResponse:
    uldk = _source("ULDK", "https://uldk.example.test")
    return AnalyzeResponse(
        status="complete",
        analyzed_at=_ANALYZED_AT,
        parcel=ParcelGeometryResponse(
            parcel_identifier=f"{_PARCEL_PREFIX}RICH",
            geometry_geojson={"type": "MultiPolygon", "coordinates": []},
            metrics=GeometryMetrics(
                area_sqm=10000.0,
                area_ha=1.0,
                perimeter_m=400.0,
                is_valid=True,
                geometry_repaired=False,
            ),
            source=uldk,
        ),
        mpzp_zones=[
            MpzpZoneResult(
                zone_symbol="MN.1",
                primary_use="zabudowa mieszkaniowa jednorodzinna",
                supplementary_use="usługi nieuciążliwe",
                max_building_height_m=9.0,
                max_floors=2,
                min_biologically_active_pct=40.0,
                max_floor_area_ratio=0.8,
                min_floor_area_ratio=0.01,
                max_building_coverage_pct=30.0,
                intersection_area_sqm=10000.0,
                intersection_pct=100.0,
                is_dominant=True,
                source=_source(
                    "MPZP_BIP",
                    "https://bip.example.test/plan.pdf",
                    confidence=0.7,
                    manual_review_required=True,
                ),
            )
        ],
        pog=PogResult(
            status="adopted",
            planning_zone="SJ",
            zone_type="SJ",
            in_ouz=True,
            area_ratio=0.75,
            in_downtown_area=True,
            uchwala_nr="X/42/2026",
            uchwala_date=date(2026, 2, 10),
            manual_review_required=True,
            conflict_with_mpzp=False,
            raw_attributes={
                "zone_type": "SJ",
                "source_layer": "StrefaPlanistyczna",
            },
            ouz_intersection_area_sqm=2500.0,
            ouz_intersection_pct=25.0,
            touches_ouz_boundary=False,
            source=_source("POG", "https://pog.example.test"),
        ),
        infrastructure=[
            InfrastructureResult(
                network_type="water",
                buffer_m=4.0,
                zone_area_sqm=320.0,
                rule_source="konfiguracja testowa",
                rule_confidence=0.65,
                rule_note="Bufor techniczny wymaga uzgodnienia z gestorem.",
                affects_buildable_area=True,
                network_geometry_geojson={
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": []},
                    "properties": {"layer": "network"},
                },
                protection_zone_geojson={
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": []},
                    "properties": {"layer": "protection_zone"},
                },
                source=_source(
                    "KIUT",
                    "https://kiut.example.test",
                    confidence=0.75,
                    manual_review_required=True,
                ),
            )
        ],
        utilities_preview=UtilitiesPreviewResult(
            coverage_status="covered",
            county_name="powiat krakowski",
            layer_available=True,
            note=(
                "Powiat publikuje dane GESUT; podgląd nie służy do obliczania "
                "odległości."
            ),
            source=_source(
                "KIUT (GUGiK)",
                "https://integracja.example.test/kiut",
            ),
        ),
        risks=[
            RiskResult(
                risk_type="flood_zone",
                description="Część działki leży w strefie zagrożenia powodziowego.",
                geometry_geojson={
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": []},
                    "properties": {"layer": "risk"},
                },
                source=_source("ISOK", "https://isok.example.test"),
            )
        ],
        buildable_area_sqm=6000.0,
        warnings=[
            WarningMessage(
                code="ISOK_WARNING",
                message="Wynik ISOK wymaga sprawdzenia.",
                severity="warning",
                source_name="ISOK",
            )
        ],
        sources=[
            uldk,
            _source(
                "KIMPZP",
                "https://kimpzp.example.test",
                response_status=None,
            ),
        ],
    )


def _context_result() -> ContextResult:
    return ContextResult(
        kiut=ContextSectionResult(
            section="kiut",
            status="available",
            source_metadata=_source(
                "KIUT",
                "https://kiut.example.test",
                confidence=0.75,
                manual_review_required=True,
            ),
            warnings=["Niepełne atrybuty sieci."],
        ),
        isok=ContextSectionResult(
            section="isok",
            status="unavailable",
            warnings=["Usługa ISOK jest niedostępna."],
        ),
        gdos=ContextSectionResult(
            section="gdos",
            status="error",
            warnings=["Błąd sekcji GDOŚ."],
        ),
        nmt=ContextSectionResult(section="nmt", status="available"),
    )


def _minimal_response() -> AnalyzeResponse:
    return AnalyzeResponse(
        status="partial",
        analyzed_at=_ANALYZED_AT,
        parcel=None,
        mpzp_zones=[],
        pog=None,
        infrastructure=[],
        risks=[],
        buildable_area_sqm=None,
        warnings=[],
        sources=[],
    )


def test_save_analysis_persists_full_response_in_one_transaction() -> None:
    geometry = box(500000, 200000, 500100, 200100)
    with SessionLocal() as db:
        analysis = save_analysis(
            _rich_response(),
            f"{_PARCEL_PREFIX}RICH",
            geometry,
            db,
            context_result=_context_result(),
        )
        analysis_id = analysis.id

    with SessionLocal() as db:
        saved = db.get(Analysis, analysis_id)
        assert saved is not None
        assert saved.status == "complete"
        assert saved.analyzed_at == _ANALYZED_AT
        assert saved.buildable_area_sqm == 6000.0
        assert saved.warnings[0]["code"] == "ISOK_WARNING"

        assert (
            db.scalar(
                select(func.count())
                .select_from(MpzpZone)
                .where(MpzpZone.analysis_id == analysis_id)
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(MpzpParameter)
                .join(MpzpZone)
                .where(MpzpZone.analysis_id == analysis_id)
            )
            == 6
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(PogData)
                .where(PogData.analysis_id == analysis_id)
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(Infrastructure)
                .where(Infrastructure.analysis_id == analysis_id)
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(Risk)
                .where(Risk.analysis_id == analysis_id)
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(SourceRecord)
                .where(SourceRecord.analysis_id == analysis_id)
            )
            == 10
        )

        height = db.scalar(
            select(MpzpParameter)
            .join(MpzpZone)
            .where(
                MpzpZone.analysis_id == analysis_id,
                MpzpParameter.parameter_name == "max_building_height_m",
            )
        )
        assert height.normalized_value == "9.0"
        assert height.unit == "m"
        assert height.manual_review_required is True

        unavailable = db.scalar(
            select(SourceRecord).where(
                SourceRecord.analysis_id == analysis_id,
                SourceRecord.source_name == "isok",
            )
        )
        assert unavailable.response_status == "unavailable"
        assert unavailable.warnings == ["Usługa ISOK jest niedostępna."]
        assert unavailable.checksum is None

        saved_pog = db.scalar(select(PogData).where(PogData.analysis_id == analysis_id))
        assert saved_pog.zone_type == "SJ"
        assert saved_pog.in_ouz is True
        assert saved_pog.area_ratio == pytest.approx(0.75)
        assert saved_pog.in_downtown_area is True
        assert saved_pog.uchwala_nr == "X/42/2026"
        assert saved_pog.uchwala_date == date(2026, 2, 10)
        assert saved_pog.manual_review_required is True
        # Wejście legacy (boolean) zapisuje się wyłącznie jako evidence (BK-205).
        assert saved_pog.compatibility_assessment["status"] == "unknown"
        assert saved_pog.compatibility_assessment["legacy_evidence"][
            "conflict_with_mpzp"
        ] is False
        assert saved_pog.raw_attributes == {
            "zone_type": "SJ",
            "source_layer": "StrefaPlanistyczna",
        }

        saved_infrastructure = db.scalar(
            select(Infrastructure).where(Infrastructure.analysis_id == analysis_id)
        )
        assert saved_infrastructure.zone_area_sqm == pytest.approx(320.0)
        assert saved_infrastructure.rule_source == "konfiguracja testowa"
        assert saved_infrastructure.rule_confidence == pytest.approx(0.65)
        assert saved_infrastructure.affects_buildable_area is True
        assert saved_infrastructure.network_geometry_geojson["properties"]["layer"] == "network"
        assert saved_infrastructure.protection_zone_geojson["properties"]["layer"] == "protection_zone"

        saved_risk = db.scalar(select(Risk).where(Risk.analysis_id == analysis_id))
        assert saved_risk.geometry_geojson["properties"]["layer"] == "risk"

        cached_response = build_analyze_response_from_analysis(saved, db)
        assert cached_response.pog is not None
        assert cached_response.pog.zone_type == "SJ"
        assert cached_response.pog.area_ratio == pytest.approx(0.75)
        assert cached_response.pog.in_ouz is True
        assert cached_response.pog.in_downtown_area is True
        assert cached_response.pog.compatibility_assessment is not None
        assert cached_response.pog.compatibility_assessment.reason_code == (
            "LEGACY_BOOLEAN_ONLY"
        )
        assert cached_response.pog.raw_attributes == saved_pog.raw_attributes
        assert cached_response.infrastructure[0].zone_area_sqm == pytest.approx(320.0)
        assert cached_response.infrastructure[0].rule_source == "konfiguracja testowa"
        assert cached_response.infrastructure[0].affects_buildable_area is True
        assert cached_response.infrastructure[0].network_geometry_geojson == saved_infrastructure.network_geometry_geojson
        assert cached_response.infrastructure[0].protection_zone_geojson == saved_infrastructure.protection_zone_geojson
        assert cached_response.risks[0].geometry_geojson == saved_risk.geometry_geojson
        assert cached_response.utilities_preview is not None
        assert cached_response.utilities_preview.coverage_status == "covered"
        assert cached_response.utilities_preview.county_name == "powiat krakowski"
        assert cached_response.utilities_preview.layer_available is True


def test_pog_v2_three_zone_roundtrip_db_api_and_report_html() -> None:
    source = SourceMetadata(
        source_id="pog_app",
        source_version="pog-a",
        artifact_sha256="a" * 64,
        data_release_id=1,
        act_version="20260924T100000",
        source_name="POG_APP_LOCAL_POSTGIS",
        source_url=None,
        fetched_at=_ANALYZED_AT,
        response_status=None,
        confidence=1.0,
        manual_review_required=False,
    )
    profile = PogProfileResult(
        code="KPT-MPZP-MN",
        label="teren zabudowy mieszkaniowej jednorodzinnej",
        dictionary_source="https://www.gov.pl/ontology/KPT",
    )
    zones = [
        PogZoneResult(
            id="sj", symbol="SJ", type="SJ", label="strefa wielofunkcyjna",
            area_sqm=620, area_pct=62,
            max_overground_floor_area_ratio=0, max_building_height_m=10,
            max_building_coverage_pct=40, min_biologically_active_pct=30,
            primary_profile=[profile], source=source,
        ),
        PogZoneResult(
            id="su", symbol="SU", type="SU", label="strefa usługowa",
            area_sqm=280, area_pct=28,
            max_overground_floor_area_ratio=None, max_building_height_m=0,
            max_building_coverage_pct=None, min_biologically_active_pct=5,
            source=source,
        ),
        PogZoneResult(
            id="sn", symbol="SN", type="SN", label="strefa zieleni",
            area_sqm=100, area_pct=10, source=source,
        ),
    ]
    pog = PogResult(
        schema_version="2.0", legal_status="adopted", coverage_status="complete",
        act=PogActResult(id="pog-1", version="v1", title="POG testowy"),
        zones=zones, dominant_zone_id="sj",
        ouz=[PogAreaResult(id="ouz-1", symbol="OUZ", area_sqm=500, area_pct=50, source=source)],
        downtown_areas=[PogAreaResult(id="ozs-1", symbol="OZS", area_sqm=100, area_pct=10, source=source)],
        social_infrastructure_standard_areas=[PogAreaResult(id="osdis-1", symbol="OSD", area_sqm=1000, area_pct=100, source=source)],
        status="adopted", planning_zone="SJ", zone_type="SJ", in_ouz=True,
        area_ratio=0.62, in_downtown_area=True, manual_review_required=False,
        ouz_intersection_area_sqm=500, ouz_intersection_pct=50,
        touches_ouz_boundary=False, source=source,
    )
    response = _rich_response().model_copy(update={"pog": pog})
    identifier = f"{_PARCEL_PREFIX}POG_V2"
    with SessionLocal() as db:
        saved = save_analysis(response, identifier, box(0, 0, 40, 25), db)
        rebuilt = build_analyze_response_from_analysis(saved, db)
        assert rebuilt.pog is not None
        assert [(z.symbol, z.area_sqm, z.area_pct) for z in rebuilt.pog.zones] == [
            ("SJ", 620.0, 62.0), ("SU", 280.0, 28.0), ("SN", 100.0, 10.0)
        ]
        assert rebuilt.pog.zones[0].max_overground_floor_area_ratio == 0
        assert rebuilt.pog.zones[1].max_overground_floor_area_ratio is None
        assert rebuilt.pog.social_infrastructure_standard_areas[0].symbol == "OSD"
        # Snapshot 2.0 z przypiętym wydaniem (SHA + wersja aktu) zachowuje
        # potwierdzenie źródłowe, więc alias adopted przechodzi w binding.
        assert rebuilt.pog.legal_status == "binding"
        assert rebuilt.pog.coverage_status == "available"
        assert rebuilt.pog.legal_status_evidence is not None
        assert "data_release:1" in (rebuilt.pog.legal_status_evidence.reference or "")
        saved_row = db.scalar(select(PogData).where(PogData.analysis_id == saved.id))
        assert saved_row.legal_status == "binding"
        assert saved_row.coverage_status == "available"
        html = _render_report_html(_build_report_context(rebuilt, None, None))
        assert all(symbol in html for symbol in ("SJ", "SU", "SN", "OSD"))
        assert "620" in html and "280" in html and "100" in html
        with patch(
            "app.services.report._render_map_data_uri",
            return_value=(None, None, False),
        ):
            pdf = generate_analysis_report_pdf(saved.id, db)
        assert pdf.startswith(b"%PDF")


def test_legacy_not_available_pog_status_is_unknown_and_review_flag_persisted() -> None:
    response = _minimal_response().model_copy(
        update={
            "pog": PogResult(
                status="not_available",
                planning_zone=None,
                in_ouz=False,
                manual_review_required=True,
                touches_ouz_boundary=False,
                source=None,
            ),
            "warnings": [
                WarningMessage(
                    code="POG_TRANSITIONAL_STATUS",
                    message="Brak uchwalonego POG wymaga ręcznej weryfikacji.",
                    severity="warning",
                    source_name="pog",
                )
            ],
        }
    )
    identifier = f"{_PARCEL_PREFIX}POG_MISSING"

    with SessionLocal() as db:
        analysis = save_analysis(
            response,
            identifier,
            box(500000, 200000, 500010, 200010),
            db,
        )
        analysis_id = analysis.id

    with SessionLocal() as db:
        saved = db.scalar(select(PogData).where(PogData.analysis_id == analysis_id))
        analysis = db.get(Analysis, analysis_id)

        # Wartość sprzed BK-106 oznaczała brak danych, nie brak aktu.
        assert saved.status == "unknown"
        assert saved.legal_status == "unknown"
        assert saved.coverage_status == "unknown"
        assert saved.in_ouz is False
        assert saved.manual_review_required is True
        assert analysis.warnings[0]["code"] == "POG_TRANSITIONAL_STATUS"

        cached_response = build_analyze_response_from_analysis(analysis, db)
        assert cached_response.pog is not None
        assert cached_response.pog.status == "unknown"
        assert cached_response.pog.legal_status == "unknown"
        assert cached_response.pog.manual_review_required is True
        assert cached_response.warnings[0].code == "POG_TRANSITIONAL_STATUS"


def test_persistence_preserves_ouz_threshold_decision_for_tiny_sliver() -> None:
    response = _minimal_response().model_copy(
        update={
            "pog": PogResult(
                status="adopted",
                planning_zone="SJ",
                in_ouz=False,
                ouz_intersection_area_sqm=0.5,
                ouz_intersection_pct=0.005,
                touches_ouz_boundary=True,
                manual_review_required=True,
                source=_source("POG", "https://pog.example.test"),
            )
        }
    )
    identifier = f"{_PARCEL_PREFIX}OUZ_SLIVER"

    with SessionLocal() as db:
        analysis = save_analysis(
            response,
            identifier,
            box(500000, 200000, 500100, 200100),
            db,
        )
        analysis_id = analysis.id

    with SessionLocal() as db:
        saved = db.scalar(select(PogData).where(PogData.analysis_id == analysis_id))
        assert saved is not None
        assert saved.ouz_intersection_area_sqm == pytest.approx(0.5)
        assert saved.in_ouz is False


def test_save_analysis_reuses_parcel_but_keeps_analysis_history() -> None:
    identifier = f"{_PARCEL_PREFIX}HISTORY"
    geometry = box(500000, 200000, 500010, 200010)
    with SessionLocal() as db:
        first = save_analysis(_minimal_response(), identifier, geometry, db)
        first_id = first.id
        second = save_analysis(_minimal_response(), identifier, geometry, db)
        second_id = second.id

    assert first_id != second_id
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Parcel)
                .where(Parcel.parcel_identifier == identifier)
            )
            == 1
        )
        parcel = db.scalar(select(Parcel).where(Parcel.parcel_identifier == identifier))
        assert (
            db.scalar(
                select(func.count())
                .select_from(Analysis)
                .where(Analysis.parcel_id == parcel.id)
            )
            == 2
        )


def test_save_analysis_stores_parcel_geometry_with_srid_2180() -> None:
    identifier = f"{_PARCEL_PREFIX}SRID"
    with SessionLocal() as db:
        save_analysis(
            _minimal_response(),
            identifier,
            box(500000, 200000, 500010, 200010),
            db,
        )
        srid = db.execute(
            text(
                "SELECT ST_SRID(geometry) FROM parcels "
                "WHERE parcel_identifier = :identifier"
            ),
            {"identifier": identifier},
        ).scalar_one()

    assert srid == 2180


def _table_counts() -> dict[str, int]:
    models = (
        Parcel,
        Analysis,
        MpzpZone,
        MpzpParameter,
        PogData,
        Infrastructure,
        Risk,
        SourceRecord,
    )
    with SessionLocal() as db:
        return {
            model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in models
        }


def test_save_analysis_rolls_back_every_table_when_risk_insert_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _table_counts()
    with SessionLocal() as db:
        original_add = db.add

        def failing_add(instance, *args, **kwargs):
            if isinstance(instance, Risk):
                raise RuntimeError("kontrolowany błąd zapisu ryzyka")
            return original_add(instance, *args, **kwargs)

        monkeypatch.setattr(db, "add", failing_add)
        with pytest.raises(RuntimeError, match="kontrolowany błąd"):
            save_analysis(
                _rich_response(),
                f"{_PARCEL_PREFIX}ROLLBACK",
                box(500000, 200000, 500100, 200100),
                db,
            )

    assert _table_counts() == before
