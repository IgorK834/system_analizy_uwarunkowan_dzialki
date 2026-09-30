from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator
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
    # BK-306: dawny placeholder WFS KIUT/GESUT (odpowiadał HTTP 401) usunięto.
    # Adres wektorowego źródła sieci jest wyłącznie kontraktem katalogu
    # (source_id ``kiut_gesut``, zasób ``networks``); bez potwierdzonego
    # kontraktu guard nie wysyła żadnego żądania.
    # BK-305: kontekst drogowy z lokalnego wydania BDOT10k (PostGIS). Promienie
    # rosnącego wyszukiwania kandydatów są jawną konfiguracją; ostatni jest
    # maksymalnym zasięgiem. Brak kandydata w limicie NIE dowodzi braku drogi.
    road_context_enabled: bool = True
    road_context_search_radii_m: tuple[float, ...] = (25.0, 50.0, 100.0, 250.0, 500.0)
    road_context_candidate_limit: int = 25
    road_context_statement_timeout_ms: int = 3_000
    road_context_timeout_seconds: float = 10.0
    # Usługa pobierania INSPIRE Map Zagrożenia i Ryzyka Powodziowego (MZP/MRP)
    # prowadzona przez PGW Wody Polskie. Kontrakt (WFS 2.0.0, typeNames
    # nz-core:HazardArea, wymagane srsName w formie URN) potwierdzono realnym
    # GetCapabilities i GetFeature 2026-07-30 — patrz app/services/isok.py oraz
    # tests/fixtures/source_contracts/isok_getcapabilities.xml.
    # Poprzedni adres wms.isok.gov.pl był placeholderem i jego nazwa domenowa
    # nie rozwiązuje się już w DNS.
    isok_wfs_base_url: str = (
        "https://wody.isok.gov.pl/wss/INSPIRE/INSPIRE_NZ_HY_MZPMRP_WFS"
    )
    # WMS jest wyłącznie referencyjnym linkiem do ręcznej weryfikacji wizualnej.
    # Analiza rastra WMS NIE jest zaimplementowana — fetch_flood_risks korzysta
    # tylko z WFS. Zobacz docstring fetch_flood_risks w app/services/isok.py.
    isok_wms_fallback_url: str = (
        "https://wody.isok.gov.pl/wss/INSPIRE/INSPIRE_NZ_HY_MZPMRP_WMS"
    )
    # Usługa pobierania WFS Generalnej Dyrekcji Ochrony Środowiska (formy ochrony
    # przyrody). Kontrakt (WFS 2.0.0, nazwy warstw GDOS:*, EPSG:2180, brak opłat
    # i ograniczeń dostępu) potwierdzono realnym GetCapabilities,
    # DescribeFeatureType i GetFeature 2026-07-30 — patrz app/services/gdos.py
    # oraz tests/fixtures/source_contracts/gdos_getcapabilities.xml.
    gdos_wfs_base_url: str = "https://sdi.gdos.gov.pl/wfs"
    # Usługa NMT GUGiK (rzeźba terenu). Zapytania REST GET bez autoryzacji;
    # współrzędne w PUWG92 (EPSG:2180). Kontrakt GetMinMaxByPolygon potwierdzono
    # realnym zapytaniem 2026-07-30 — patrz app/services/nmt.py oraz
    # tests/fixtures/source_contracts/nmt_getminmaxbypolygon.txt.
    nmt_base_url: str = "https://services.gugik.gov.pl/nmt/"
    # Pochodne rastra NMT (BK-302): spadek, ekspozycja, profil. Endpoint WCS i
    # identyfikator pokrycia są kontraktem katalogu (source_id ``nmt_wcs``);
    # tutaj są wyłącznie limity bezpieczeństwa jednego zapytania. 1 mln pikseli
    # przy natywnym pikselu 1 m to ok. 100 ha okna (działka + bufor kernela).
    terrain_relief_enabled: bool = True
    terrain_raster_max_pixels: int = 1_000_000
    terrain_raster_max_bytes: int = 8 * 1024 * 1024
    terrain_raster_timeout_seconds: float = 20.0
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
    # Konfiguracja prezentacyjnych źródeł WMS jest współdzielona przez proxy
    # kafelków i endpoint metadanych dla frontendu. Alternatywny plik pozwala
    # nadpisać cały rejestr bez wystawiania dowolnego URL-a w publicznym API.
    wms_preview_sources_path: str = str(
        Path(__file__).with_name("wms_preview_sources.json")
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
    # Wektorowe kafle POG (BK-401) z lokalnego, wersjonowanego wydania PostGIS.
    # URL kafla jest przypięty do ``release_id``, więc przeglądarka może je
    # trzymać długo; limity chronią bazę przed kaflem całego kraju.
    pog_tile_source_id: str = "pog_app"
    pog_tile_min_zoom: int = 0
    pog_tile_max_zoom: int = 18
    pog_tile_max_features: int = 20_000
    pog_tile_max_bytes: int = 4 * 1024 * 1024
    pog_tile_cache_max_bytes: int = 64 * 1024 * 1024
    pog_tile_browser_ttl_seconds: int = 3_600
    pog_tile_statement_timeout_ms: int = 5_000
    # Wspólny artefakt prezentacji POG (BK-403): paleta, progi, etykiety. Pusta
    # wartość = wykrycie ``/app/shared`` w obrazie albo ``<repo>/shared`` lokalnie.
    pog_presentation_path: str = ""
    # Mapy raportu PDF (BK-503, ADR-010) są renderowane lokalnie z zamrożonego
    # snapshotu — generowanie PDF nigdy nie pobiera WMS/OSM/KIUT. Tryb tematyczny
    # mapy POG jest zamrażany przy zapisie analizy (jeden z pięciu tematów
    # ``shared/pog-presentation.json``). Podkład jest opcjonalnym, zapisanym
    # artefaktem PNG z metadanymi i SHA-256 w podanym katalogu (bez sieci);
    # pusta wartość = neutralne tło.
    report_map_pog_theme: Literal[
        "zones", "intensity", "building_coverage", "height", "biologically_active"
    ] = "zones"
    report_map_basemap_artifact_dir: str = ""
    # Pakiet audytowy analizy (BK-505, ADR-011): jawne limity liczby plików i
    # rozmiaru (po dekompresji). Przekroczenie kończy się odpowiedzią 413, a nie
    # okrojonym pakietem.
    audit_export_max_files: int = Field(default=200, ge=1)
    audit_export_max_file_bytes: int = Field(default=32 * 1024 * 1024, ge=1024)
    audit_export_max_total_bytes: int = Field(default=64 * 1024 * 1024, ge=1024)
    # Endpointy Rejestru Urbanistycznego są wersjonowanym kontraktem katalogu
    # docs/data_sources/catalog.yaml. Nie dublujemy ich w zmiennych runtime.
    # Oficjalne słowniki off-line GUGiK zasilają lokalny indeks autocomplete.
    # Synchronizacja jest osobnym zadaniem utrzymaniowym; API nie pobiera paczek
    # w ścieżce żądania użytkownika.
    address_dictionary_soap_url: str = (
        "https://mapy.geoportal.gov.pl/wss/service/SLNOFF/guest/slowniki-offline"
    )
    address_index_teryt_scopes: str = "02,04,06,08,10,12,14,16,18,20,22,24,26,28,30,32"
    address_index_connect_timeout_seconds: float = 5.0
    address_index_read_timeout_seconds: float = 60.0
    address_index_max_package_bytes: int = 512 * 1024 * 1024
    address_index_max_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024
    address_index_import_batch_size: int = 5_000
    address_index_uug_fallback_enabled: bool = True
    # Oryginały importów są deduplikowane po SHA-256 i przechowywane lokalnie.
    # S3/MinIO pozostaje poza zakresem, dopóki projekt nie ma object storage.
    # Podpis cache obejmuje kontrakt odpowiedzi i aktywne wydania danych, ale nie
    # dane z usług na żywo (ISOK, GDOŚ, NMT). Dlatego wynik ``complete`` ma
    # ograniczony TTL, a nie wieczny: po tym czasie analiza pobiera je ponownie.
    analysis_cache_max_age_days: int = Field(default=7, ge=1)
    # Sekret HMAC tokenów dostępu do raportów/dokumentów analiz. Ustaw stałą,
    # losową wartość (np. ``openssl rand -hex 32``); pusta = losowy klucz procesu.
    access_token_secret: str = ""
    # Klucze operatorów endpointów administracyjnych: ``operator:klucz,...``.
    # Puste = endpointy administracyjne wyłączone.
    admin_api_keys: str = ""
    # Limity zapytań na klienta w oknie 60 s dla kosztownych endpointów (każda
    # analiza odpytuje zewnętrzne usługi GIS, raport uruchamia WeasyPrint).
    # Limiter jest in-process: przy N workerach efektywny limit to N × wartość.
    rate_limit_enabled: bool = True
    rate_limit_analyze_per_minute: int = Field(default=20, ge=1)
    rate_limit_refresh_per_minute: int = Field(default=5, ge=1)
    rate_limit_report_per_minute: int = Field(default=30, ge=1)
    rate_limit_coverage_per_minute: int = Field(default=60, ge=1)
    # Za reverse proxy ``request.client`` to adres proxy. Włącz tylko, gdy przed
    # aplikacją stoi zaufany proxy dopisujący ``X-Forwarded-For`` — używany jest
    # ostatni wpis (dodany przez ten proxy), bo wcześniejsze poda sam klient.
    rate_limit_trust_forwarded_for: bool = False
    import_artifact_storage_dir: str = "/tmp/dzialki-import-artifacts"
    import_area_tolerance_ratio: float = 0.02
    import_overlap_tolerance_sqm: float = 0.01
    import_topology_tolerance_m: float = 0.05
    import_topology_area_tolerance_sqm: float = 0.01

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
