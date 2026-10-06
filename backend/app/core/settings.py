from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator
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
    # Ekstrakcja parametrów MPZP modelem językowym (PV3-10, ADR-012). WYŁĄCZONA domyślnie:
    # bez ``MPZP_LLM_ENABLED=true`` adapter nie jest tworzony i aplikacja nie otwiera żadnego
    # połączenia z dostawcą modelu. Wynik modelu jest wyłącznie kandydatem do ręcznej weryfikacji.
    # Do modelu trafia tylko tekst publicznego aktu planistycznego i symbole stref — nigdy
    # identyfikator działki, analizy ani użytkownika. Adres dostawcy jest stałą adaptera (nie
    # konfiguracją), żeby klucz nie mógł zostać skierowany pod obcy host.
    mpzp_llm_enabled: bool = False
    # ``gemini`` — Gemini Developer API (REST); ``fake`` — odtwarzanie zapisanych odpowiedzi
    # z ``mpzp_llm_replay_dir`` (bez sieci i bez klucza).
    mpzp_llm_provider: Literal["gemini", "fake"] = "gemini"
    # Identyfikator modelu z ADR-012; wartość nie jest zgadywana — potwierdza ją ``models.list``.
    mpzp_llm_model: str = Field(default="gemini-3.8-flash", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    # Klucz projektu płatnego (warstwa bezpłatna jest wykluczona w ADR-012). Wyłącznie ze zmiennej
    # środowiskowej ``GEMINI_API_KEY``; ``SecretStr`` ukrywa go w ``repr`` i zrzutach ustawień.
    gemini_api_key: SecretStr | None = None
    mpzp_llm_replay_dir: str = ""
    mpzp_llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    mpzp_llm_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    mpzp_llm_max_output_tokens: int = Field(default=8192, ge=256, le=65_536)
    # Temperatura jest stała: ekstrakcja ma być jak najbardziej powtarzalna (ADR-012).
    mpzp_llm_temperature: float = Field(default=0.0, ge=0.0, le=0.0)
    mpzp_llm_thinking_level: Literal["low", "medium", "high"] = "low"
    mpzp_llm_max_request_bytes: int = Field(default=200_000, ge=1_024)
    mpzp_llm_max_response_bytes: int = Field(default=1_048_576, ge=1_024)
    mpzp_llm_max_retries: int = Field(default=2, ge=0, le=5)
    mpzp_llm_retry_base_delay_seconds: float = Field(default=1.0, ge=0, le=30)
    mpzp_llm_retry_max_delay_seconds: float = Field(default=20.0, ge=0, le=120)
    mpzp_llm_max_retry_after_seconds: float = Field(default=30.0, ge=0, le=300)
    mpzp_llm_breaker_failure_threshold: int = Field(default=5, ge=1, le=100)
    mpzp_llm_breaker_cooldown_seconds: float = Field(default=60.0, ge=0, le=3_600)
    # Podział dużych bloków strefy (PV3-11): limit znaków tekstu jednego żądania, nakładka
    # kontekstu między częściami i górny limit liczby żądań na blok.
    mpzp_llm_block_char_limit: int = Field(default=6_000, ge=1_000, le=60_000)
    mpzp_llm_chunk_overlap_chars: int = Field(default=400, ge=0, le=5_000)
    mpzp_llm_max_chunks_per_block: int = Field(default=6, ge=1, le=50)
    # Tryb parsera MPZP (PV3-14): ``legacy`` (domyślny, zachowanie sprzed PV3-14), ``v3`` (rdzeń
    # deterministyczny na blokach stref), ``hybrid_shadow`` (odpowiedź = ``v3``; model liczony i
    # porównywany w tle, bez wpływu na odpowiedź i zapis), ``hybrid`` (``v3`` + zweryfikowani kandydaci
    # modelu ze statusem ``ai_candidate``). Domyślny tryb zmienia się dopiero po bramce jakości z
    # Task 20.17 (ADR-012). Tryby z modelem wymagają też ``MPZP_LLM_ENABLED=true``.
    mpzp_parser_mode: Literal["legacy", "v3", "hybrid_shadow", "hybrid"] = "legacy"
    # Budżet jednej analizy (ADR-012, progi wejściowe Task 20.15): żądanie ponad budżet nie jest
    # wysyłane, a wynik pozostaje deterministyczny z ostrzeżeniem ``MPZP_LLM_UNAVAILABLE``.
    mpzp_llm_max_requests_per_analysis: int = Field(default=6, ge=0, le=100)
    mpzp_llm_max_input_tokens_per_analysis: int = Field(default=12_000, ge=0, le=1_000_000)
    # Próg pewności zakresu bloku (bramka G7 i wybór par do modelu w trybie ``hybrid``).
    mpzp_llm_scope_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    # Cache i provenance wywołań (PV3-13): zapis wyłącznie wyjścia modelu i skrótów wejścia w
    # ``mpzp_llm_extractions``; zapisy starsze niż retencja nie są trafieniem i są usuwane poleceniem
    # ``python -m app.modules.planning purge-llm-cache``.
    mpzp_llm_cache_enabled: bool = True
    mpzp_llm_cache_retention_days: int = Field(default=180, ge=1, le=3_650)
    # Cena za 1 mln tokenów do szacowania kosztu (ADR-012: cena od 2027-01-01, nie promocyjna).
    mpzp_llm_price_input_usd_per_mtok: float = Field(default=1.50, ge=0)
    mpzp_llm_price_output_usd_per_mtok: float = Field(default=7.50, ge=0)
    # Limity i bezpieczna degradacja (PV3-15, progi z ADR-012, limity doby/miesiąca zaakceptowane przez właściciela 2026-10-05).
    # Na żądanie: tokeny wejścia (ADR-012: 4000); na dokument: tokeny wejścia; czas: ile ścieżka modelu może
    # dodać do analizy (od pierwszego żądania) i termin całej analizy (od jej startu, propagowany).
    mpzp_llm_max_input_tokens_per_request: int = Field(default=4_000, ge=1, le=1_000_000)
    mpzp_llm_max_input_tokens_per_document: int = Field(default=12_000, ge=0, le=1_000_000)
    mpzp_llm_time_budget_seconds: float = Field(default=30.0, gt=0, le=600)
    mpzp_llm_analysis_deadline_seconds: float = Field(default=90.0, gt=0, le=3_600)
    # Twarde limity między analizami (rejestr ``mpzp_llm_usage``, doba i miesiąc w UTC): tokeny wejścia +
    # wyjścia i koszt szacowany wg ceny od 2027. Pusta wartość = brak limitu (nie 0).
    mpzp_llm_daily_token_limit: int | None = Field(default=2_000_000, ge=0)
    mpzp_llm_daily_cost_limit_usd: float | None = Field(default=5.0, ge=0)
    mpzp_llm_monthly_token_limit: int | None = Field(default=40_000_000, ge=0)
    mpzp_llm_monthly_cost_limit_usd: float | None = Field(default=100.0, ge=0)
    # Współbieżność i częstotliwość w JEDNYM procesie (przy N procesach efektywnie N × wartość).
    mpzp_llm_max_concurrency: int = Field(default=4, ge=1, le=64)
    mpzp_llm_concurrency_wait_seconds: float = Field(default=2.0, ge=0, le=60)
    mpzp_llm_max_requests_per_minute: int = Field(default=60, ge=1, le=10_000)
    # Kill switch (PV3-16): istnienie tego pliku natychmiast wyłącza ścieżkę modelu (bez restartu i bez
    # wdrożenia kodu); pusta wartość = brak przełącznika plikowego. ``MPZP_LLM_ENABLED=false`` też wyłącza.
    mpzp_llm_kill_switch_file: str = "/var/lib/dzialki/llm-disabled"
    # Monitoring i przypięcie wersji (PV3-19, ADR-012 aneks PV3-18–20). ``mpzp_llm_prompt_version`` to
    # deklaracja operatora: musi odpowiadać wersji promptu w kodzie i przypięciu (``model_pin.json``);
    # rozbieżność wyłącza ścieżkę modelu (``pin_mismatch``), dopóki ``mpzp_llm_enforce_pin`` jest włączone.
    mpzp_llm_prompt_version: str = Field(default="mpzp-extraction/1", pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,59}$")
    mpzp_llm_enforce_pin: bool = True
    # Progi alarmów (propozycja, nieskalibrowana na ruchu): odsetki w oknie czasowym i od minimalnej próby;
    # próg kosztu dobowego jest poniżej twardego limitu (pusta wartość = bez alarmu kosztu).
    mpzp_llm_alarm_window_seconds: float = Field(default=3_600.0, gt=0, le=86_400)
    mpzp_llm_alarm_min_candidates: int = Field(default=20, ge=1)
    mpzp_llm_alarm_min_analyses: int = Field(default=10, ge=1)
    mpzp_llm_alarm_rejection_rate: float = Field(default=0.30, ge=0.0, le=1.0)
    mpzp_llm_alarm_degradation_rate: float = Field(default=0.20, ge=0.0, le=1.0)
    mpzp_llm_alarm_daily_cost_usd: float | None = Field(default=4.0, ge=0)
    # Kontrola dryfu (``scripts/check_llm_drift.py``): próg odsetka rozbieżnych wartości, plik ze stanem
    # ostatniej kontroli i wiek, po którym brak kontroli jest ostrzeżeniem.
    mpzp_llm_drift_alarm_rate: float = Field(default=0.20, ge=0.0, le=1.0)
    mpzp_llm_drift_state_file: str = "/var/lib/dzialki/llm-drift.json"
    mpzp_llm_drift_max_age_days: int = Field(default=14, ge=1, le=365)
    # Zużycie dobowe w odpowiedzi zdrowia jest czytane z rejestru z tym buforem (sekundy).
    mpzp_llm_health_ledger_ttl_seconds: float = Field(default=30.0, ge=0, le=3_600)
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

    @field_validator(
        "mpzp_llm_daily_token_limit",
        "mpzp_llm_daily_cost_limit_usd",
        "mpzp_llm_monthly_token_limit",
        "mpzp_llm_monthly_cost_limit_usd",
        "mpzp_llm_alarm_daily_cost_usd",
        mode="before",
    )
    @classmethod
    def empty_limit_means_unlimited(cls, value: Any) -> Any:
        # ``MPZP_LLM_DAILY_TOKEN_LIMIT=`` (pusty) znaczy „bez limitu”, a nie 0.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_backend_cors_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            # W .env.example trzymamy prosty zapis tekstowy, żeby konfiguracja Compose
            # była czytelna także bez znajomości składni JSON dla list.
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()
