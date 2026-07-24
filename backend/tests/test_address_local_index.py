"""Integracja lokalnego indeksu autocomplete z PostgreSQL/PostGIS."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
import io
from uuid import uuid4
import zipfile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import sessionmaker

from app.db.session import engine
from app.modules.location.domain.models import AddressQuery, GeoPoint, ResultType
from app.modules.location.infrastructure.local_index import (
    AddressIndexNotReady,
    LocalFirstAddressSearchProvider,
    PostgresAddressSearchProvider,
)
from app.modules.location.application.ports import AddressSearchProviderError
from app.modules.location.infrastructure.dictionary_import import AddressIndexImporter

pytestmark = pytest.mark.integration


@pytest.fixture
def indexed_provider() -> Iterator[PostgresAddressSearchProvider]:
    command.upgrade(Config("alembic.ini"), "head")
    connection: Connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection)
    suffix = uuid4().hex[:10]
    try:
        connection.execute(
            text(
                "UPDATE data_releases SET is_active=false WHERE data_source_id IN "
                "(SELECT id FROM data_sources WHERE source_id='prg_address_dictionary')"
            )
        )
        source_id = connection.execute(
            text(
                """
                INSERT INTO data_sources (
                    source_id, owner, status, access_type, license, attribution
                ) VALUES (
                    'prg_address_dictionary', 'GUGiK', 'production', 'soap',
                    'publiczna usługa GUGiK', 'GUGiK'
                )
                ON CONFLICT (source_id) DO UPDATE SET owner=EXCLUDED.owner
                RETURNING id
                """
            )
        ).scalar_one()
        release_id = connection.execute(
            text(
                """
                INSERT INTO data_releases (
                    data_source_id, version_label, published_at, is_active
                ) VALUES (:source_id, :version, :published_at, true)
                RETURNING id
                """
            ),
            {
                "source_id": source_id,
                "version": f"test-{suffix}",
                "published_at": datetime.now(timezone.utc),
            },
        ).scalar_one()
        connection.execute(
            text(
                """
                INSERT INTO address_search_entries (
                    data_release_id, source_object_id, result_type, label,
                    normalized_label, country, voivodeship, municipality, city,
                    street, house_number, teryt, geometry
                ) VALUES
                (:release, 'city:warszawa', 'city', 'Warszawa',
                 'warszawa mazowieckie', 'Polska', 'mazowieckie', 'Warszawa',
                 'Warszawa', NULL, NULL, '1465011',
                 ST_SetSRID(ST_MakePoint(637400, 486900), 2180)),
                (:release, 'street:marszalkowska', 'street',
                 'Warszawa, Marszałkowska', 'warszawa marszalkowska mazowieckie',
                 'Polska', 'mazowieckie', 'Warszawa', 'Warszawa',
                 'Marszałkowska', NULL, '1465011',
                 ST_SetSRID(ST_MakePoint(637400, 486900), 2180)),
                (:release, 'address:odysei-51', 'house_number',
                 'Gdańsk, Odysei 51', 'gdansk odysei 51 pomorskie',
                 'Polska', 'pomorskie', 'Gdańsk', 'Gdańsk', 'Odysei', '51',
                 '2261011', ST_SetSRID(ST_MakePoint(463000, 728000), 2180))
                """
            ),
            {"release": release_id},
        )
        yield PostgresAddressSearchProvider(factory)
    finally:
        transaction.rollback()
        connection.close()


def test_migration_creates_trigram_and_spatial_indexes() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    indexes = {
        item["name"]
        for item in inspect(engine).get_indexes("address_search_entries")
    }
    assert "ix_address_search_entries_normalized_trgm" in indexes
    assert "ix_address_search_entries_geometry_gist" in indexes
    with engine.connect() as connection:
        srid = connection.execute(
            text(
                "SELECT srid FROM geometry_columns "
                "WHERE f_table_name='address_search_entries' "
                "AND f_geometry_column='geometry'"
            )
        ).scalar_one()
    assert srid == 2180


def test_prefix_returns_city_then_street(indexed_provider) -> None:
    results = indexed_provider._search_sync(AddressQuery(q="Wars"))
    assert results[0].label == "Warszawa"
    assert {item.result_type for item in results} >= {
        ResultType.CITY,
        ResultType.STREET,
    }
    assert results[0].source_id == "prg_address_dictionary"


def test_tokens_find_house_without_city_in_input(indexed_provider) -> None:
    results = indexed_provider._search_sync(AddressQuery(q="Odysei 51"))
    assert [item.label for item in results] == ["Gdańsk, Odysei 51"]
    assert results[0].result_type is ResultType.HOUSE_NUMBER


def test_bias_and_type_filter_are_applied(indexed_provider) -> None:
    results = indexed_provider._search_sync(
        AddressQuery(
            q="Wars",
            bias=GeoPoint(lon=21.01, lat=52.23),
            type_filter=frozenset({ResultType.STREET}),
        )
    )
    assert [item.result_type for item in results] == [ResultType.STREET]


def test_bbox_score_and_unsupported_type_filter(indexed_provider) -> None:
    from app.modules.location.domain.models import BBox

    results = indexed_provider._search_sync(
        AddressQuery(
            q="Wars",
            bbox=BBox(20.9, 52.1, 21.2, 52.4),
        )
    )
    assert results[0].label == "Warszawa"
    assert (
        indexed_provider._search_sync(
            AddressQuery(
                q="Wars",
                type_filter=frozenset({ResultType.COUNTRY}),
            )
        )
        == []
    )
    assert indexed_provider._search_sync(AddressQuery(q="---")) == []


def test_status_describes_active_release(indexed_provider) -> None:
    status = indexed_provider._status_sync()
    assert status.ready is True
    assert status.entry_count == 3
    assert status.version_label and status.version_label.startswith("test-")
    assert status.scopes == ()


def test_parent_suggestions_are_rebuilt_with_aggregated_geometry(
    indexed_provider,
) -> None:
    factory = indexed_provider._session_factory
    with factory.begin() as db:
        release_id = db.execute(
            text(
                "SELECT id FROM data_releases WHERE is_active AND data_source_id IN "
                "(SELECT id FROM data_sources "
                "WHERE source_id='prg_address_dictionary')"
            )
        ).scalar_one()
        AddressIndexImporter._rebuild_parent_entries(db, release_id)
        rows = db.execute(
            text(
                "SELECT result_type, count(*) FROM address_search_entries "
                "WHERE data_release_id=:release GROUP BY result_type"
            ),
            {"release": release_id},
        ).all()
    counts = dict(rows)
    assert counts["city"] == 1
    assert counts["street"] == 1
    assert counts["house_number"] == 1


def test_import_maps_gugik_northing_easting_to_postgis_axis_order(
    indexed_provider,
) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "adres.xml",
            """
            <lista-adresow><adres>
              <pktPrgIIPPn>PL.PZGIK.200</pktPrgIIPPn>
              <pktPrgIIPId>axis-check</pktPrgIIPId>
              <pktNumer>1</pktNumer><pktStatus>istniejacy</pktStatus>
              <pktX>725793.11</pktX><pktY>379900.32</pktY>
              <miejscNazwa>Dębnica Kaszubska</miejscNazwa>
              <gmIdTeryt>2212032</gmIdTeryt>
            </adres></lista-adresow>
            """,
        )

    factory = indexed_provider._session_factory
    with factory.begin() as db:
        release_id = db.execute(
            text(
                "SELECT id FROM data_releases WHERE is_active AND data_source_id IN "
                "(SELECT id FROM data_sources "
                "WHERE source_id='prg_address_dictionary')"
            )
        ).scalar_one()
        counters = {"upserted": 0, "deleted": 0}
        AddressIndexImporter()._apply_package(
            db, release_id, None, output.getvalue(), counters
        )
        lon, lat = db.execute(
            text(
                """
                SELECT ST_X(ST_Transform(geometry, 4326)),
                       ST_Y(ST_Transform(geometry, 4326))
                FROM address_search_entries
                WHERE data_release_id=:release
                  AND source_object_id='PL.PZGIK.200:axis-check'
                """
            ),
            {"release": release_id},
        ).one()
    assert lon == pytest.approx(17.16, abs=0.1)
    assert lat == pytest.approx(54.37, abs=0.1)


def test_missing_active_release_is_explicit() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(
            text(
                "UPDATE data_releases SET is_active=false WHERE data_source_id IN "
                "(SELECT id FROM data_sources WHERE source_id='prg_address_dictionary')"
            )
        )
        provider = PostgresAddressSearchProvider(sessionmaker(bind=connection))
        with pytest.raises(AddressIndexNotReady):
            provider._search_sync(AddressQuery(q="Wars"))
    finally:
        transaction.rollback()
        connection.close()


class _Provider:
    def __init__(
        self,
        results=None,
        error: Exception | None = None,
    ) -> None:
        self.results = results or []
        self.error = error
        self.calls = 0

    async def search(self, query):
        self.calls += 1
        if self.error:
            raise self.error
        return self.results


async def test_local_first_returns_local_without_calling_fallback() -> None:
    local = _Provider(results=["local"])
    fallback = _Provider(results=["fallback"])
    provider = LocalFirstAddressSearchProvider(local, fallback)

    assert await provider.search(AddressQuery(q="Wars")) == ["local"]
    assert fallback.calls == 0


async def test_local_first_uses_fallback_when_index_not_ready() -> None:
    local = _Provider(error=AddressIndexNotReady("not ready"))
    fallback = _Provider(results=["fallback"])
    provider = LocalFirstAddressSearchProvider(local, fallback)

    assert await provider.search(AddressQuery(q="Wars")) == ["fallback"]


async def test_local_first_uses_fallback_on_local_failure() -> None:
    local = _Provider(error=AddressSearchProviderError("db"))
    fallback = _Provider(results=["fallback"])
    provider = LocalFirstAddressSearchProvider(local, fallback)

    assert await provider.search(AddressQuery(q="Wars")) == ["fallback"]


async def test_local_first_keeps_empty_result_when_optional_fallback_fails() -> None:
    local = _Provider(results=[])
    fallback = _Provider(error=AddressSearchProviderError("uug"))
    provider = LocalFirstAddressSearchProvider(local, fallback)

    assert await provider.search(AddressQuery(q="Wars")) == []


async def test_local_first_without_fallback_propagates_not_ready() -> None:
    provider = LocalFirstAddressSearchProvider(
        _Provider(error=AddressIndexNotReady("not ready")),
        None,
    )
    with pytest.raises(AddressIndexNotReady):
        await provider.search(AddressQuery(q="Wars"))
