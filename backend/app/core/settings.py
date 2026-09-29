from pathlib import Path
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
    # Podkład miniatury raportu: OSM WMS (działa bez autoryzacji). ORTO/TOPO
    # Geoportalu zwracają 401 — nie używać jako domyślne. Nakładka KIMPZP
    # (``report_map_kimpzp_overlay_enabled``) odwzorowuje widok MPZP z UI.
    # Nakładka KIUT jest tym samym świadomym wyjątkiem UX co KIMPZP: rastrowy
    # GetMap w chwili generowania PDF, nie geometria ze snapshotu analizy.
    report_map_basemap_enabled: bool = True
    report_map_wms_base_url: str = "https://ows.terrestris.de/osm/service?"
    report_map_wms_layers: str = "OSM-WMS"
    report_map_kimpzp_overlay_enabled: bool = True
    report_map_kiut_overlay_enabled: bool = True
    report_map_wms_timeout_seconds: float = 8.0
    report_map_wms_max_response_bytes: int = 8 * 1024 * 1024
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
