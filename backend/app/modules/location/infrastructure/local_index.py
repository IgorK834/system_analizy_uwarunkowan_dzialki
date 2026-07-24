"""Adapter prefiksowego wyszukiwania w lokalnym indeksie PostgreSQL/PostGIS."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import SessionLocal
from app.modules.location.application.ports import (
    AddressSearchProvider,
    AddressSearchProviderError,
)
from app.modules.location.domain.models import (
    AddressParts,
    AddressQuery,
    GeoPoint,
    RawAddressCandidate,
    ResultType,
)
from app.modules.location.domain.normalization import fold_text, tokenize

logger = logging.getLogger(__name__)

SOURCE_ID = "prg_address_dictionary"


class AddressIndexNotReady(AddressSearchProviderError):
    """Brak opublikowanego wydania lokalnego indeksu."""


@dataclass(frozen=True)
class AddressIndexStatus:
    ready: bool
    release_id: int | None = None
    version_label: str | None = None
    published_at: str | None = None
    entry_count: int = 0
    scopes: tuple[str, ...] = ()


class PostgresAddressSearchProvider(AddressSearchProvider):
    """Wyszukuje tokeny prefiksowo, a wynik biasuje bieżącym widokiem mapy."""

    def __init__(
        self, session_factory: sessionmaker[Session] = SessionLocal
    ) -> None:
        self._session_factory = session_factory

    async def search(self, query: AddressQuery) -> list[RawAddressCandidate]:
        return await asyncio.to_thread(self._search_sync, query)

    async def status(self) -> AddressIndexStatus:
        return await asyncio.to_thread(self._status_sync)

    def _status_sync(self) -> AddressIndexStatus:
        try:
            with self._session_factory() as db:
                row = db.execute(
                    text(
                        """
                        SELECT dr.id, dr.version_label, dr.published_at,
                               count(ase.id) AS entry_count,
                               max(ir.checkpoint::text) AS checkpoint
                        FROM data_sources ds
                        JOIN data_releases dr
                          ON dr.data_source_id=ds.id AND dr.is_active
                        LEFT JOIN address_search_entries ase
                          ON ase.data_release_id=dr.id
                        LEFT JOIN import_runs ir
                          ON ir.data_release_id=dr.id AND ir.status='succeeded'
                        WHERE ds.source_id=:source_id
                        GROUP BY dr.id
                        """
                    ),
                    {"source_id": SOURCE_ID},
                ).mappings().one_or_none()
        except SQLAlchemyError as exc:
            raise AddressSearchProviderError(
                "Nie udało się sprawdzić lokalnego indeksu adresowego."
            ) from exc
        if row is None:
            return AddressIndexStatus(ready=False)
        checkpoint_raw = row["checkpoint"]
        try:
            checkpoint = (
                json.loads(checkpoint_raw)
                if isinstance(checkpoint_raw, str)
                else checkpoint_raw or {}
            )
        except (TypeError, json.JSONDecodeError):
            checkpoint = {}
        return AddressIndexStatus(
            ready=True,
            release_id=int(row["id"]),
            version_label=str(row["version_label"]),
            published_at=row["published_at"].isoformat(),
            entry_count=int(row["entry_count"]),
            scopes=tuple(checkpoint.get("scopes", ())),
        )

    def _search_sync(self, query: AddressQuery) -> list[RawAddressCandidate]:
        normalized_query = " ".join(fold_text(query.q).split())
        tokens = tokenize(normalized_query)
        if not tokens:
            return []

        params: dict[str, object] = {
            "source_id": SOURCE_ID,
            "normalized_query": normalized_query,
            "prefix": f"{normalized_query}%",
            "candidate_limit": min(100, max(query.limit * 6, 30)),
        }
        token_conditions: list[str] = []
        for index, token in enumerate(tokens):
            key = f"token_{index}"
            params[key] = f"%{token}%"
            token_conditions.append(f"ase.normalized_label LIKE :{key}")

        type_condition = ""
        if query.type_filter:
            selected = [
                item.value
                for item in query.type_filter
                if item
                in {ResultType.CITY, ResultType.STREET, ResultType.HOUSE_NUMBER}
            ]
            if not selected:
                return []
            params["types"] = selected
            type_condition = "AND ase.result_type = ANY(:types)"

        spatial_score = "0"
        if query.bias is not None:
            params.update(bias_lon=query.bias.lon, bias_lat=query.bias.lat)
            spatial_score = """
                (1.0 / (1.0 + ST_Distance(
                    ase.geometry,
                    ST_Transform(
                        ST_SetSRID(ST_MakePoint(:bias_lon, :bias_lat), 4326),
                        2180
                    )
                ) / 10000.0))
            """

        bbox_score = "0"
        if query.bbox is not None:
            params.update(
                bbox_min_lon=query.bbox.min_lon,
                bbox_min_lat=query.bbox.min_lat,
                bbox_max_lon=query.bbox.max_lon,
                bbox_max_lat=query.bbox.max_lat,
            )
            bbox_score = """
                CASE WHEN ST_Intersects(
                    ase.geometry,
                    ST_Transform(
                        ST_MakeEnvelope(
                            :bbox_min_lon, :bbox_min_lat,
                            :bbox_max_lon, :bbox_max_lat, 4326
                        ),
                        2180
                    )
                ) THEN 1 ELSE 0 END
            """

        query_has_number = any(token.isdigit() for token in tokens)
        if query_has_number:
            type_score = (
                "CASE ase.result_type WHEN 'house_number' THEN 3 "
                "WHEN 'street' THEN 1 ELSE 0 END"
            )
        else:
            type_score = (
                "CASE ase.result_type WHEN 'city' THEN 3 "
                "WHEN 'street' THEN 2 WHEN 'house_number' THEN 1 ELSE 0 END"
            )

        statement = text(
            f"""
            SELECT
                ase.source_object_id, ase.label, ase.country, ase.voivodeship,
                ase.county, ase.municipality, ase.city, ase.street,
                ase.house_number, ase.result_type,
                ST_X(ST_Transform(ase.geometry, 4326)) AS lon,
                ST_Y(ST_Transform(ase.geometry, 4326)) AS lat,
                (
                    {type_score}
                    + CASE WHEN ase.normalized_label LIKE :prefix THEN 2 ELSE 0 END
                    + similarity(ase.normalized_label, :normalized_query)
                    + ({spatial_score})
                    + ({bbox_score})
                ) AS search_score
            FROM address_search_entries ase
            JOIN data_releases dr
              ON dr.id=ase.data_release_id AND dr.is_active
            JOIN data_sources ds
              ON ds.id=dr.data_source_id AND ds.source_id=:source_id
            WHERE {' AND '.join(token_conditions)}
              {type_condition}
            ORDER BY search_score DESC, ase.source_object_id ASC
            LIMIT :candidate_limit
            """
        )
        try:
            with self._session_factory() as db:
                has_release = db.execute(
                    text(
                        """
                        SELECT EXISTS(
                            SELECT 1 FROM data_sources ds
                            JOIN data_releases dr ON dr.data_source_id=ds.id
                            WHERE ds.source_id=:source_id AND dr.is_active
                        )
                        """
                    ),
                    {"source_id": SOURCE_ID},
                ).scalar_one()
                if not has_release:
                    raise AddressIndexNotReady(
                        "Lokalny indeks adresowy nie ma aktywnego wydania."
                    )
                rows = db.execute(statement, params).mappings().all()
        except AddressIndexNotReady:
            raise
        except SQLAlchemyError as exc:
            logger.exception("Błąd zapytania lokalnego indeksu adresowego")
            raise AddressSearchProviderError(
                "Lokalny indeks adresowy jest niedostępny."
            ) from exc

        return [
            RawAddressCandidate(
                label=str(row["label"]),
                point=GeoPoint(lon=float(row["lon"]), lat=float(row["lat"])),
                parts=AddressParts(
                    country=row["country"],
                    voivodeship=row["voivodeship"],
                    county=row["county"],
                    municipality=row["municipality"],
                    city=row["city"],
                    street=row["street"],
                    house_number=row["house_number"],
                ),
                result_type=ResultType(row["result_type"]),
                # Lokalny rekord pochodzi z opublikowanego, kontrolowanego
                # wydania. Niewielka różnica per typ stabilizuje ranking
                # podczas wpisywania miasta → ulicy → numeru.
                provider_confidence={
                    ResultType.CITY: 1.0,
                    ResultType.STREET: 0.98,
                    ResultType.HOUSE_NUMBER: 0.96,
                }[ResultType(row["result_type"])],
                source_identifier=str(row["source_object_id"]),
                source_id=SOURCE_ID,
            )
            for row in rows
        ]


class LocalFirstAddressSearchProvider(AddressSearchProvider):
    """Używa indeksu lokalnego, a UUG tylko jako kontrolowanego fallbacku."""

    def __init__(
        self,
        local: AddressSearchProvider,
        fallback: AddressSearchProvider | None,
    ) -> None:
        self._local = local
        self._fallback = fallback

    async def search(self, query: AddressQuery) -> list[RawAddressCandidate]:
        try:
            local_results = await self._local.search(query)
        except AddressIndexNotReady:
            if self._fallback is None:
                raise
            return await self._fallback.search(query)
        except AddressSearchProviderError:
            if self._fallback is None:
                raise
            logger.warning(
                "Lokalny indeks adresowy jest niedostępny; używam fallbacku UUG."
            )
            return await self._fallback.search(query)

        if local_results or self._fallback is None:
            return local_results
        try:
            return await self._fallback.search(query)
        except AddressSearchProviderError:
            # Gotowy indeks poprawnie odpowiedział "brak wyników". Awaria
            # opcjonalnego fallbacku nie może zmienić tego w błąd 503.
            return []
