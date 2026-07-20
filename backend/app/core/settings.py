from typing import Annotated, Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://app:app@db:5432/dzialki"
    backend_cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://frontend:3000",
    ]
    app_title: str = "System analizy działki"
    app_version: str = "1.0.0"
    # Domyślne techniczne odsunięcie od granicy działki w metrach. To NIE jest linia
    # zabudowy z MPZP — to przybliżenie wynikające z przepisów o minimalnej odległości
    # od granicy, które może zostać nadpisane przez konkretne ustalenia planistyczne.
    default_technical_setback_m: float = 4.0
    # Adres WFS KIUT/GESUT jest placeholderem opartym o publicznie znaną domenę GUGiK.
    # Rzeczywisty kontrakt zapytania (typename, wersja WFS, przestrzenie nazw) zostanie
    # doprecyzowany, gdy będzie dostępna pełna dokumentacja usługi.
    kiut_wfs_base_url: str = (
        "https://mapy.geoportal.gov.pl/wss/service/PZGIK/KIUT/WFS/GESUT"
    )
    # Adres WFS ISOK (mapy zagrożenia i ryzyka powodziowego) jest placeholderem
    # opartym o publicznie znaną domenę ISOK/GUGiK. Rzeczywisty kontrakt zapytania
    # (typename, wersja WFS, nazwy warstw dla Q1%/Q10%/Q0,2%) zostanie doprecyzowany,
    # gdy będzie dostępna pełna dokumentacja usługi.
    isok_wfs_base_url: str = "https://wms.isok.gov.pl/isap/services/PZGIK/ISOK/WFS"
    # WMS jest wyłącznie referencyjnym linkiem do ręcznej weryfikacji wizualnej.
    # Analiza rastra WMS NIE jest zaimplementowana — fetch_flood_risks korzysta
    # tylko z WFS. Zobacz docstring fetch_flood_risks w app/services/isok.py.
    isok_wms_fallback_url: str = "https://wms.isok.gov.pl/isap/services/PZGIK/ISOK/WMS"
    # Adres WFS GDOŚ (formy ochrony przyrody) jest placeholderem opartym o publicznie
    # znaną domenę Generalnej Dyrekcji Ochrony Środowiska. Rzeczywisty kontrakt
    # zapytania (typename, wersja WFS, nazwy warstw) zostanie doprecyzowany po
    # udostępnieniu pełnej dokumentacji usługi.
    gdos_wfs_base_url: str = "https://sdi.gdos.gov.pl/wfs"
    # Oficjalny endpoint prezentacyjny WMS Krajowej Integracji MPZP. Discovery
    # używa queryable warstwy ``plany_granice`` i kontraktu GetFeatureInfo
    # opublikowanego w bieżącym GetCapabilities usługi.
    kimpzp_wms_base_url: str = (
        "https://mapy.geoportal.gov.pl/wss/ext/"
        "KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego"
    )
    # Warstwy używane przez serwerowy proxy kafelków. Frontend nie przekazuje
    # nazw warstw ani adresu upstreamu, dzięki czemu endpoint nie staje się
    # otwartym proxy SSRF.
    kimpzp_wms_layers: str = (
        "plany_granice,raster,wektor-str,wektor-lzb,wektor-lin,"
        "wektor-pow,wektor-pkt,granice"
    )
    map_tile_cache_dir: str = "/tmp/dzialki-map-tile-cache"
    map_tile_cache_ttl_seconds: int = 86_400
    map_tile_stale_ttl_seconds: int = 604_800
    map_tile_browser_ttl_seconds: int = 3_600
    map_tile_cache_max_bytes: int = 5 * 1024 * 1024 * 1024
    map_tile_upstream_connect_timeout_seconds: float = 2.0
    map_tile_upstream_read_timeout_seconds: float = 8.0
    map_tile_upstream_max_concurrency: int = 8
    map_tile_min_zoom: int = 11
    map_tile_max_zoom: int = 18
    # Rejestr Urbanistyczny jest opcjonalnym, eksperymentalnym kanałem discovery.
    # W lipcu 2026 publiczny kontrakt API ani adres usługi nie są potwierdzone,
    # dlatego brak wartości jest bezpiecznym ustawieniem domyślnym. Adresy WMS/BIP
    # POG przekazuje się per gmina do discover_pog, a nie przez globalny endpoint.
    rejestr_urbanistyczny_base_url: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_backend_cors_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            # W .env.example trzymamy prosty zapis tekstowy, żeby konfiguracja Compose
            # była czytelna także bez znajomości składni JSON dla list.
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()
