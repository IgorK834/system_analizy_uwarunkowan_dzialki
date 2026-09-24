"""Katalog źródeł danych — jedyne źródło prawdy dla kontraktów urzędowych.

Moduł wczytuje i waliduje ``docs/data_sources/catalog.yaml``, w którym opisany
jest kontrakt każdego źródła danych (właściciel, status, licencja, CRS, zakres
TERYT itd.). Katalog pełni dwie role:

1. **Governance** — jednoznacznie rozdziela źródła potwierdzone (produkcyjne) od
   badawczych, wymagających umowy i placeholderów. Żadne niepotwierdzone źródło
   nie może być przedstawione jako produkcyjne.
2. **Guard uruchomienia** — ``ensure_source_runnable`` odrzuca próbę użycia
   adaptera dla źródła, którego nie wolno uruchamiać w produkcyjnym
   orchestratorze (status research/placeholder/contract_required/no_redistribution
   albo ``production_ready=false``).

Uwaga o dostępności pliku w kontenerze: obraz backendu kopiuje kod aplikacji i
skrypty, ale katalog ``docs/`` jest dostarczany przez montowanie repozytorium.
Dlatego loader lokalizuje plik względem katalogu repozytorium (zmienna
``REPO_ROOT`` albo przeszukanie katalogów nadrzędnych), a katalog NIE jest
wymagany w ścieżce obsługi zwykłego żądania HTTP. Guard i loader są używane
przez testy oraz narzędzia importu/orchestracji, które mają dostęp do repo.
"""

from __future__ import annotations

import os
from datetime import date
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


# CRS dozwolone jako źródłowe. Lista obejmuje układy realnie spotykane w polskich
# danych przestrzennych; nieznany kod jest odrzucany, aby nie ukrywać błędu
# konfiguracji źródła. Kanoniczny układ obliczeniowy systemu to EPSG:2180.
ALLOWED_SOURCE_CRS: frozenset[str] = frozenset(
    {
        "EPSG:2180",  # PUWG 1992 — układ kanoniczny
        "EPSG:4326",  # WGS84
        "EPSG:3857",  # Web Mercator (tylko wizualizacja)
        "EPSG:4258",  # ETRS89
        "EPSG:2176",  # PUWG 2000 strefa 5
        "EPSG:2177",  # PUWG 2000 strefa 6
        "EPSG:2178",  # PUWG 2000 strefa 7
        "EPSG:2179",  # PUWG 2000 strefa 8
    }
)

# Kanoniczny układ docelowy — wszystkie geometrie systemu są sprowadzane do 2180.
CANONICAL_TARGET_CRS: str = "EPSG:2180"

# Domyślna, względna ścieżka katalogu w repozytorium.
_CATALOG_RELATIVE_PATH = Path("docs") / "data_sources" / "catalog.yaml"


class SourceStatus(str, Enum):
    """Status kontraktu źródła w katalogu."""

    PRODUCTION = "production"
    RESEARCH = "research"
    CONTRACT_REQUIRED = "contract_required"
    NO_REDISTRIBUTION = "no_redistribution"
    PLACEHOLDER = "placeholder"


class AccessType(str, Enum):
    """Sposób dostępu do danych źródła."""

    REST = "rest"
    WMS = "wms"
    WMTS = "wmts"
    WFS = "wfs"
    CSW = "csw"
    APP_GML = "app_gml"
    FILE = "file"
    SOAP = "soap"


class MpzpDataClassification(str, Enum):
    """Zakres danych MPZP potwierdzony kontraktem źródłowym."""

    VECTOR_ZONES = "vector_zones"
    ACT_BOUNDARY_DOCUMENT = "act_boundary_document"
    RASTER = "raster"
    UNKNOWN = "unknown"


class SourceResource(BaseModel):
    """Pojedynczy, uporządkowany zasób składający się na kontrakt źródła.

    Kolejność elementów ``resources`` jest kolejnością prób odczytu. Dzięki
    temu adapter nie zgaduje, czy ma najpierw użyć WFS, GML czy pliku.
    """

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1)
    access_type: AccessType
    url: str = Field(min_length=1)
    type_name: str | None = None
    type_names: list[str] = Field(default_factory=list)
    layer: str | None = None
    layers: list[str] = Field(default_factory=list)
    protocol_version: str | None = None
    namespace_uri: str | None = None
    source_crs: str
    supported_crs: list[str] = Field(default_factory=list)
    inherited_crs: list[str] = Field(default_factory=list)
    count_default: int | None = Field(default=None, gt=0)
    output_formats: list[str] = Field(default_factory=list)
    field_mapping: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_resource(self) -> SourceResource:
        if self.source_crs not in ALLOWED_SOURCE_CRS:
            raise ValueError(
                f"Niedozwolony lub nieznany CRS {self.source_crs!r} zasobu "
                f"{self.role!r}."
            )
        for crs in (*self.supported_crs, *self.inherited_crs):
            if crs != "CRS:84" and crs not in ALLOWED_SOURCE_CRS:
                raise ValueError(
                    f"Niedozwolony lub nieznany CRS {crs!r} zasobu {self.role!r}."
                )
        if self.access_type is AccessType.WFS and not (
            self.type_name or self.type_names
        ):
            raise ValueError(
                f"Zasób WFS {self.role!r} musi deklarować type_name lub type_names."
            )
        return self

    @property
    def declared_type_names(self) -> tuple[str, ...]:
        values = [*self.type_names]
        if self.type_name:
            values.append(self.type_name)
        return tuple(dict.fromkeys(values))


# Statusy, których adapter NIE może uruchomić w produkcyjnym orchestratorze,
# niezależnie od pozostałych pól.
_NON_RUNNABLE_STATUSES: frozenset[SourceStatus] = frozenset(
    {
        SourceStatus.RESEARCH,
        SourceStatus.PLACEHOLDER,
        SourceStatus.CONTRACT_REQUIRED,
        SourceStatus.NO_REDISTRIBUTION,
    }
)


# --- Wyjątki domenowe --------------------------------------------------------


class CatalogError(Exception):
    """Bazowy wyjątek katalogu źródeł danych."""


class CatalogFileError(CatalogError):
    """Nie udało się zlokalizować lub odczytać pliku katalogu."""


class CatalogValidationError(CatalogError):
    """Zawartość katalogu nie spełnia kontraktu (walidacja Pydantic/struktury)."""


class DuplicateSourceIdError(CatalogValidationError):
    """W katalogu wystąpił zduplikowany ``source_id``."""


class SourceNotFoundError(CatalogError):
    """W katalogu nie ma źródła o podanym ``source_id``."""


class SourceNotRunnableError(CatalogError):
    """Źródła nie wolno uruchomić w produkcyjnym orchestratorze."""


class MpzpSourceNotUsableError(SourceNotRunnableError):
    """Źródło nie dostarcza potwierdzonego rodzaju danych MPZP."""


# --- Modele Pydantic ---------------------------------------------------------


class DataSourceEntry(BaseModel):
    """Pojedynczy wpis katalogu opisujący kontrakt źródła danych."""

    # extra="forbid" chroni przed literówką w kluczu, która po cichu ukryłaby
    # wymagane pole (np. "licence" zamiast "license").
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=2, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    status: SourceStatus
    production_ready: bool
    contract_confirmed: bool = False
    access_type: AccessType
    capabilities_url: str | None = None
    file_url: str | None = None
    type_names: list[str] | None = None
    layers: list[str] | None = None
    protocol_version: str | None = None
    source_crs: str
    target_crs: str = CANONICAL_TARGET_CRS
    teryt_scope: list[str] = Field(min_length=1)
    license: str = Field(min_length=1)
    attribution: str = Field(min_length=1)
    expected_update_interval: str = Field(min_length=1)
    sla: str = Field(min_length=1)
    last_manual_verification: date | None = None
    notes: str | None = None
    field_mapping: dict[str, str] = Field(default_factory=dict)
    resources: list[SourceResource] = Field(default_factory=list)
    mpzp_classification: MpzpDataClassification | None = None

    @model_validator(mode="after")
    def _validate_contract(self) -> DataSourceEntry:
        # Układ docelowy musi być kanoniczny, inaczej geometrie źródła nie dałyby
        # się bezpiecznie połączyć z resztą danych systemu.
        if self.target_crs != CANONICAL_TARGET_CRS:
            raise ValueError(
                f"target_crs źródła {self.source_id!r} musi być "
                f"{CANONICAL_TARGET_CRS!r}, jest {self.target_crs!r}."
            )

        # Nieznany/niedozwolony CRS źródłowy jest błędem konfiguracji — nie
        # zgadujemy reprojekcji z układu, którego nie rozpoznajemy.
        for crs in (self.source_crs, self.target_crs):
            if crs not in ALLOWED_SOURCE_CRS:
                raise ValueError(
                    f"Niedozwolony lub nieznany CRS {crs!r} w źródle "
                    f"{self.source_id!r}. Dozwolone: {sorted(ALLOWED_SOURCE_CRS)}."
                )

        # Spójność statusu i gotowości produkcyjnej: tylko status `production`
        # może być produkcyjnie gotowy.
        if self.production_ready and self.status is not SourceStatus.PRODUCTION:
            raise ValueError(
                f"Źródło {self.source_id!r} ma production_ready=true, ale status "
                f"{self.status.value!r}. Gotowe może być tylko źródło 'production'."
            )
        if not self.production_ready and self.status is SourceStatus.PRODUCTION:
            raise ValueError(
                f"Źródło {self.source_id!r} ma status 'production', ale "
                "production_ready=false — status i gotowość muszą być spójne."
            )

        # Warstwy vs typeNames zależnie od sposobu dostępu — dotyczy wyłącznie
        # źródeł, dla których deklarujemy je jako potwierdzone (production_ready).
        if self.production_ready:
            self._validate_production_contract()

        self._validate_mpzp_contract()

        return self

    def _validate_mpzp_contract(self) -> None:
        """Nie pozwala nazwać WMS ani samej granicy wektorem stref planu."""
        if self.mpzp_classification is None:
            return

        zone_resources = [item for item in self.resources if item.role == "zones"]
        if self.mpzp_classification is MpzpDataClassification.VECTOR_ZONES:
            if self.access_type in (AccessType.WMS, AccessType.WMTS):
                raise ValueError(
                    f"Źródło MPZP {self.source_id!r}: WMS/WMTS nie może być "
                    "sklasyfikowany jako vector_zones."
                )
            if not zone_resources:
                raise ValueError(
                    f"Źródło MPZP {self.source_id!r}: vector_zones wymaga "
                    "jawnego zasobu o roli 'zones'."
                )
            invalid = [
                item.access_type.value
                for item in zone_resources
                if item.access_type
                not in (AccessType.WFS, AccessType.APP_GML, AccessType.FILE)
            ]
            if invalid:
                raise ValueError(
                    f"Źródło MPZP {self.source_id!r}: zasoby zones muszą być "
                    f"wektorowe, otrzymano {invalid}."
                )
        elif zone_resources:
            raise ValueError(
                f"Źródło MPZP {self.source_id!r}: klasyfikacja "
                f"{self.mpzp_classification.value!r} nie może deklarować zasobu "
                "'zones'."
            )

    def _validate_production_contract(self) -> None:
        """Twarde wymagania dla źródła oznaczonego jako produkcyjnie gotowe."""
        if not self.contract_confirmed:
            raise ValueError(
                f"Źródło {self.source_id!r} jest production_ready, ale "
                "contract_confirmed=false — potwierdź kontrakt przed produkcją."
            )
        if self.last_manual_verification is None:
            raise ValueError(
                f"Źródło {self.source_id!r} jest production_ready, ale nie ma "
                "daty ręcznej weryfikacji (last_manual_verification)."
            )
        if not self.capabilities_url and not self.file_url:
            raise ValueError(
                f"Źródło produkcyjne {self.source_id!r} musi mieć capabilities_url "
                "albo file_url."
            )
        if self.access_type in (AccessType.WMS, AccessType.WMTS) and not self.layers:
            raise ValueError(
                f"Źródło WMS/WMTS {self.source_id!r} musi deklarować listę warstw "
                "(layers)."
            )
        if (
            self.access_type in (AccessType.WFS, AccessType.APP_GML)
            and not self.type_names
        ):
            raise ValueError(
                f"Źródło WFS/APP {self.source_id!r} musi deklarować type_names."
            )

    @property
    def is_runnable(self) -> bool:
        """Czy źródło wolno uruchomić w produkcyjnym orchestratorze."""
        return (
            self.status is SourceStatus.PRODUCTION
            and self.production_ready
            and self.contract_confirmed
            and self.status not in _NON_RUNNABLE_STATUSES
        )


def ensure_mpzp_vector_zones_source(
    source: DataSourceEntry,
) -> tuple[SourceResource, ...]:
    """Zwraca potwierdzone wydzielenia MPZP albo przerywa przed odczytem.

    Status prawny publikacji jest nadal obsługiwany przez
    :func:`ensure_source_runnable`. Ten guard dotyczy semantyki danych i działa
    również w dry-run: publiczny WMS i wektor samej granicy aktu nie stają się
    przez tryb testowy geometrią wydzieleń planu.
    """
    if source.mpzp_classification is not MpzpDataClassification.VECTOR_ZONES:
        value = (
            source.mpzp_classification.value
            if source.mpzp_classification is not None
            else "unset"
        )
        raise MpzpSourceNotUsableError(
            f"Źródło {source.source_id!r} ma klasyfikację MPZP {value!r}; "
            "analiza stref wymaga 'vector_zones'."
        )
    zones = tuple(item for item in source.resources if item.role == "zones")
    if not zones:
        raise MpzpSourceNotUsableError(
            f"Źródło {source.source_id!r} nie ma potwierdzonego zasobu 'zones'."
        )
    return zones


class DataSourceCatalog(BaseModel):
    """Cały katalog źródeł danych."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1)
    sources: list[DataSourceEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> DataSourceCatalog:
        seen: set[str] = set()
        for entry in self.sources:
            if entry.source_id in seen:
                raise ValueError(
                    f"Zduplikowany source_id w katalogu: {entry.source_id!r}."
                )
            seen.add(entry.source_id)
        return self

    def get(self, source_id: str) -> DataSourceEntry:
        """Zwraca wpis źródła albo podnosi ``SourceNotFoundError``."""
        for entry in self.sources:
            if entry.source_id == source_id:
                return entry
        raise SourceNotFoundError(
            f"W katalogu nie ma źródła o source_id={source_id!r}."
        )

    def production_sources(self) -> list[DataSourceEntry]:
        """Zwraca wpisy oznaczone jako produkcyjnie gotowe."""
        return [entry for entry in self.sources if entry.production_ready]


# --- Loader ------------------------------------------------------------------


def _resolve_catalog_path() -> Path:
    """Lokalizuje ``docs/data_sources/catalog.yaml`` względem repozytorium.

    Kolejność: zmienna środowiskowa ``DATA_SOURCES_CATALOG_PATH`` (jawne
    wskazanie), następnie ``REPO_ROOT`` (używane też przez testy struktury),
    a na końcu przeszukanie katalogów nadrzędnych względem tego pliku i CWD.
    """
    explicit = os.environ.get("DATA_SOURCES_CATALOG_PATH")
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        raise CatalogFileError(
            f"DATA_SOURCES_CATALOG_PATH wskazuje na nieistniejący plik: {explicit!r}."
        )

    repo_root_env = os.environ.get("REPO_ROOT")
    candidates: list[Path] = []
    if repo_root_env:
        candidates.append(Path(repo_root_env) / _CATALOG_RELATIVE_PATH)

    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in (base, *base.parents):
            candidates.append(parent / _CATALOG_RELATIVE_PATH)

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise CatalogFileError(
        "Nie znaleziono pliku katalogu źródeł danych "
        f"({_CATALOG_RELATIVE_PATH}). Ustaw DATA_SOURCES_CATALOG_PATH albo "
        "REPO_ROOT."
    )


def load_catalog(path: str | os.PathLike[str] | None = None) -> DataSourceCatalog:
    """Wczytuje i waliduje katalog źródeł danych z pliku YAML.

    Przy błędnej strukturze podnosi ``CatalogValidationError`` (a przy duplikacie
    ``source_id`` jego podklasę ``DuplicateSourceIdError``), przy problemie z
    plikiem — ``CatalogFileError``.
    """
    catalog_path = Path(path) if path is not None else _resolve_catalog_path()
    if not catalog_path.is_file():
        raise CatalogFileError(f"Plik katalogu nie istnieje: {catalog_path}.")

    try:
        raw_text = catalog_path.read_text(encoding="utf-8")
        data: Any = yaml.safe_load(raw_text)
    except (OSError, yaml.YAMLError) as exc:
        raise CatalogFileError(
            f"Nie udało się odczytać katalogu {catalog_path}: {exc}."
        ) from exc

    return parse_catalog(data)


def parse_catalog(data: Any) -> DataSourceCatalog:
    """Waliduje surową strukturę (dict) katalogu i zwraca model.

    Wydzielone z ``load_catalog``, aby walidację dało się testować bez pliku.
    """
    if not isinstance(data, dict):
        raise CatalogValidationError(
            "Katalog musi być mapą z kluczami 'schema_version' i 'sources'."
        )
    try:
        return DataSourceCatalog.model_validate(data)
    except ValidationError as exc:
        # Duplikat source_id sygnalizujemy dedykowanym wyjątkiem, aby wywołujący
        # mógł go rozróżnić od innych błędów walidacji.
        if "Zduplikowany source_id" in str(exc):
            raise DuplicateSourceIdError(str(exc)) from exc
        raise CatalogValidationError(str(exc)) from exc


@lru_cache(maxsize=1)
def get_catalog() -> DataSourceCatalog:
    """Zwraca zwalidowany katalog z cache (jedno wczytanie na proces).

    Cache jest bezpieczny, ponieważ katalog jest artefaktem tylko do odczytu.
    W testach można go wyczyścić przez ``get_catalog.cache_clear()``.
    """
    return load_catalog()


# --- Guard uruchomienia adaptera ---------------------------------------------


def ensure_source_runnable(
    source_id: str, catalog: DataSourceCatalog | None = None
) -> DataSourceEntry:
    """Sprawdza, czy adapter źródła wolno uruchomić; zwraca wpis albo podnosi błąd.

    Odrzuca źródła research, placeholder, contract_required i no_redistribution
    oraz każde źródło, które nie jest jednocześnie ``production``,
    ``production_ready`` i ``contract_confirmed``. Dzięki temu produkcyjny
    orchestrator nie odpyta źródła bez potwierdzonego kontraktu i licencji.
    """
    active_catalog = catalog if catalog is not None else get_catalog()
    entry = active_catalog.get(source_id)
    if not entry.is_runnable:
        raise SourceNotRunnableError(
            f"Źródło {source_id!r} (status={entry.status.value}, "
            f"production_ready={entry.production_ready}, "
            f"contract_confirmed={entry.contract_confirmed}) nie może zostać "
            "uruchomione przez produkcyjny orchestrator."
        )
    return entry
