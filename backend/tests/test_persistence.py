from datetime import datetime, timezone

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
    PogResult,
    RiskResult,
    WarningMessage,
)
from app.schemas.source import SourceMetadata
from app.services.context import ContextResult, ContextSectionResult
from app.services.persistence import save_analysis

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
            ouz_intersection_area_sqm=2500.0,
            ouz_intersection_pct=25.0,
            touches_ouz_boundary=False,
            source=_source("POG", "https://pog.example.test"),
        ),
        infrastructure=[
            InfrastructureResult(
                network_type="water",
                buffer_m=4.0,
                source=_source(
                    "KIUT",
                    "https://kiut.example.test",
                    confidence=0.75,
                    manual_review_required=True,
                ),
            )
        ],
        risks=[
            RiskResult(
                risk_type="flood_zone",
                description="Część działki leży w strefie zagrożenia powodziowego.",
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

        assert db.scalar(
            select(func.count()).select_from(MpzpZone).where(
                MpzpZone.analysis_id == analysis_id
            )
        ) == 1
        assert db.scalar(
            select(func.count()).select_from(MpzpParameter).join(MpzpZone).where(
                MpzpZone.analysis_id == analysis_id
            )
        ) == 6
        assert db.scalar(
            select(func.count()).select_from(PogData).where(
                PogData.analysis_id == analysis_id
            )
        ) == 1
        assert db.scalar(
            select(func.count()).select_from(Infrastructure).where(
                Infrastructure.analysis_id == analysis_id
            )
        ) == 1
        assert db.scalar(
            select(func.count()).select_from(Risk).where(
                Risk.analysis_id == analysis_id
            )
        ) == 1
        assert db.scalar(
            select(func.count()).select_from(SourceRecord).where(
                SourceRecord.analysis_id == analysis_id
            )
        ) == 8

        height = db.scalar(
            select(MpzpParameter).join(MpzpZone).where(
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
        assert db.scalar(
            select(func.count()).select_from(Parcel).where(
                Parcel.parcel_identifier == identifier
            )
        ) == 1
        parcel = db.scalar(
            select(Parcel).where(Parcel.parcel_identifier == identifier)
        )
        assert db.scalar(
            select(func.count()).select_from(Analysis).where(
                Analysis.parcel_id == parcel.id
            )
        ) == 2


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
