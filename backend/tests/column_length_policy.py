"""Audyt długości kolumn tekstowych (AU-001): skąd pochodzi wartość i co się dzieje przy nadmiarze.

Każda kolumna ``String(n)`` w metadanych ORM musi być tu sklasyfikowana — nowa kolumna bez
wpisu przerywa ``tests/test_column_length_contract.py``, więc autor musi zdecydować, czy wartość
pochodzi z zewnątrz. Trzy kategorie:

- ``text`` — kolumna jest ``Text`` (bez limitu); dotyczy adresów i odnośników, których długość
  zależy od zapytania (np. adres NMT ``GetMinMaxByPolygon`` zawiera cały wielokąt działki);
- ``clipped`` — kolumna jest ``ClippedString(n)``: tekst z zewnątrz o rozsądnej długości (nazwy,
  numery uchwał, etykiety, nagłówki HTTP) przycinany przy zapisie z widocznym ``…``;
- ``strict`` — zwykły ``String(n)``: wartość nadaje lub waliduje aplikacja (skróty, statusy,
  wersje, klucze tożsamości) albo jest zapisywana wsadowo przez importer. Nadmiar jest błędem
  programisty i ma kończyć się jawnym błędem zapisu — cicha zmiana skrótu albo klucza byłaby
  gorsza niż błąd. ``save_analysis`` zamienia go na ``PersistenceError`` (HTTP 503).
"""

from __future__ import annotations

from typing import Final

TEXT: Final[str] = "text"
CLIPPED: Final[str] = "clipped"
STRICT: Final[str] = "strict"

_ENUM = "wartość z zamkniętego zbioru nadawana przez kod (CHECK/enum)"
_HASH = "skrót SHA-256/hex o stałej długości liczony przez aplikację"
_CONST = "stała lub wersja nadawana przez kod"
_URL = "adres/odnośnik z zewnątrz, długość zależy od zapytania (np. pełny wielokąt w URL)"
_LABEL = "etykieta/nazwa z zewnątrz; widoczne przycięcie lepsze niż utrata całej analizy"
_IMPORT = "zapis wsadowy importera; nadmiar przerywa import jawnie"
_KEY = "klucz tożsamości/unikalności — przycięcie zmieniłoby tożsamość rekordu"

# tabela -> kolumna -> (kategoria, uzasadnienie)
COLUMN_POLICY: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "analyses": {
        "status": (STRICT, _ENUM),
        "result_contract_version": (STRICT, _CONST),
        "cache_signature": (STRICT, _HASH),
        "pending_uchwala_url": (TEXT, _URL),
        "pending_plan_id": (CLIPPED, "identyfikator planu z odkrywania MPZP (zewnętrzny)"),
        "resolved_zone_symbol": (STRICT, "symbol walidowany regułą kanoniczną (do 40 znaków)"),
    },
    "analysis_pending_documents": {
        "requested_url": (TEXT, _URL),
        "final_url": (TEXT, _URL),
        "media_type": (CLIPPED, "nagłówek Content-Type zdalnego serwera"),
        "filename": (CLIPPED, "nazwa pliku z nagłówka Content-Disposition zdalnego serwera"),
        "content_sha256": (STRICT, _HASH),
    },
    "parcels": {
        "parcel_identifier": (STRICT, _KEY),
        "teryt": (STRICT, "kod TERYT o stałej długości"),
    },
    "source_records": {
        "source_name": (CLIPPED, _LABEL),
        "source_url": (TEXT, _URL),
        "response_status": (STRICT, "kod HTTP albo etykieta statusu z kodu"),
        "checksum": (STRICT, _HASH),
        "source_id": (STRICT, "identyfikator z katalogu źródeł"),
        "source_version": (CLIPPED, _LABEL),
        "artifact_sha256": (STRICT, _HASH),
        "act_version": (CLIPPED, "wersja aktu z zewnętrznego źródła"),
    },
    "risk_records": {
        "risk_type": (STRICT, _ENUM),
        "section": (STRICT, _ENUM),
        "feature_id": (CLIPPED, "identyfikator obiektu z usługi zewnętrznej"),
        "severity": (STRICT, _ENUM),
        "probability_class": (CLIPPED, "klasa prawdopodobieństwa z usługi zewnętrznej"),
        "protection_type": (CLIPPED, _LABEL),
        "name": (CLIPPED, _LABEL),
        "source_url": (TEXT, _URL),
    },
    "pog_data": {
        "status": (STRICT, _ENUM),
        "legal_status": (STRICT, _ENUM),
        "coverage_status": (STRICT, _ENUM),
        "data_availability": (STRICT, _ENUM),
        "legacy_status": (STRICT, _ENUM),
        "planning_zone": (CLIPPED, _LABEL),
        "zone_type": (CLIPPED, _LABEL),
        "uchwala_nr": (CLIPPED, "numer uchwały z zewnętrznego źródła"),
        "source_url": (TEXT, _URL),
        "schema_version": (STRICT, _CONST),
    },
    "infrastructure_records": {
        "network_type": (STRICT, _ENUM),
        "rule_source": (CLIPPED, "opis źródła reguły z konfiguracji"),
        "source_url": (TEXT, _URL),
    },
    "mpzp_zones": {
        "zone_symbol": (CLIPPED, "symbol strefy z usługi zewnętrznej"),
        "primary_use": (CLIPPED, _LABEL),
        "source_url": (TEXT, _URL),
        "zone_identifier": (CLIPPED, "identyfikator wydzielenia z usługi zewnętrznej"),
        "act_identifier": (CLIPPED, "identyfikator aktu z usługi zewnętrznej"),
        "act_version": (CLIPPED, "wersja aktu z usługi zewnętrznej"),
        "assignment_method": (STRICT, _ENUM),
    },
    "mpzp_parameters": {
        "parameter_name": (CLIPPED, "nazwa parametru wyodrębniona z dokumentu"),
        "normalized_value": (CLIPPED, "wartość wyodrębniona z dokumentu"),
        "unit": (CLIPPED, "jednostka wyodrębniona z dokumentu"),
        "segment_id": (STRICT, "identyfikator segmentu nadawany przez parser"),
        "document_sha256": (STRICT, _HASH),
        "parser_version": (STRICT, _CONST),
        "extraction_method": (STRICT, _ENUM),
        "conflict_group_id": (STRICT, "skrót liczony przez parser (limit 300 znaków jest wpisany w kod)"),
        "value_kind": (STRICT, _ENUM),
        "review_status": (STRICT, _ENUM),
        "model_id": (STRICT, "identyfikator modelu z przypiętej konfiguracji"),
        "prompt_version": (STRICT, _CONST),
        "response_sha256": (STRICT, _HASH),
    },
    "mpzp_llm_extractions": {
        "cache_key": (STRICT, _HASH),
        "document_sha256": (STRICT, _HASH),
        "block_sha256": (STRICT, _HASH),
        "prompt_version": (STRICT, _CONST),
        "schema_version": (STRICT, _CONST),
        "model_id": (STRICT, "identyfikator modelu z przypiętej konfiguracji"),
        "params_hash": (STRICT, _HASH),
        "response_sha256": (STRICT, _HASH),
        "status": (STRICT, _ENUM),
        "error_code": (STRICT, "kod błędu ze stałego zbioru"),
    },
    "mpzp_llm_usage": {
        "model_id": (STRICT, "identyfikator modelu z przypiętej konfiguracji"),
        "status": (STRICT, _ENUM),
        "outcome": (STRICT, "kod wyniku ze stałego zbioru"),
    },
    # --- model wersjonowany: zapis wsadowy importerów ---------------------------------------
    "data_sources": {
        "source_id": (STRICT, "identyfikator z katalogu źródeł (klucz unikalny)"),
        "owner": (STRICT, "właściciel źródła z katalogu docs/data_sources"),
        "status": (STRICT, _ENUM),
        "access_type": (STRICT, _ENUM),
    },
    "source_artifacts": {
        "uri": (TEXT, _URL),
        "media_type": (CLIPPED, "nagłówek Content-Type zdalnego serwera"),
        "content_hash": (STRICT, _HASH),
        "etag": (CLIPPED, "nagłówek ETag zdalnego serwera"),
    },
    "data_releases": {
        "version_label": (CLIPPED, "etykieta wydania nadana przez importer z danych źródła"),
        "importer_version": (STRICT, _CONST),
    },
    "import_runs": {
        "status": (STRICT, _ENUM),
        "importer_version": (STRICT, _CONST),
    },
    "parcel_versions": {
        "content_hash": (STRICT, _HASH),
        "review_status": (STRICT, _ENUM),
    },
    "planning_acts": {
        # Identyfikator z URL-a dokumentu albo planu z zewnątrz jest ograniczany do 200 znaków przez
        # ``app.shared.act_identifier.bounded_act_identifier`` (skrót zamiast przycięcia klucza).
        "act_identifier": (STRICT, _KEY),
        "teryt": (STRICT, "kod TERYT o stałej długości"),
        "kind": (STRICT, _ENUM),
    },
    "planning_act_versions": {
        "legal_status": (STRICT, _ENUM),
        "legacy_legal_status": (STRICT, _ENUM),
        "publication_id": (CLIPPED, "gml:identifier wersji z publikacji APP"),
        "source_reference": (TEXT, _URL),
        "document_url": (TEXT, _URL),
        "object_version_id": (CLIPPED, "identyfikator wersji obiektu ze źródła"),
        "version_label": (CLIPPED, _LABEL),
        "resolution_number": (CLIPPED, "numer uchwały z zewnętrznego źródła"),
        "content_hash": (STRICT, _HASH),
        "review_status": (STRICT, _ENUM),
    },
    "planning_symbols": {
        "local_symbol": (STRICT, _KEY),
        "name": (CLIPPED, _LABEL),
        "normalized_category": (STRICT, "kategoria znormalizowana przez aplikację"),
    },
    "land_use_areas": {
        "symbol": (CLIPPED, "symbol strefy z zewnętrznego źródła"),
        "zone_identifier": (STRICT, _KEY),
    },
    "planning_features": {
        "feature_type": (STRICT, _ENUM),
        "feature_identifier": (CLIPPED, "identyfikator obiektu ze źródła"),
        "feature_version": (CLIPPED, "wersja obiektu ze źródła"),
        "act_reference": (TEXT, _URL),
        "source_reference": (TEXT, _URL),
        "symbol": (CLIPPED, "symbol z zewnętrznego źródła"),
    },
    "pog_formal_documents": {
        "document_identifier": (STRICT, _KEY),
        "document_version": (STRICT, _KEY),
        "act_reference": (TEXT, _URL),
        "link": (TEXT, _URL),
        "source_reference": (TEXT, _URL),
        "publication_id": (CLIPPED, "gml:identifier publikacji APP"),
        "identification_number": (CLIPPED, "numer identyfikacyjny dokumentu ze źródła"),
        "relation": (STRICT, _ENUM),
        "record_sha256": (STRICT, _HASH),
        "resolution_status": (STRICT, _ENUM),
    },
    "pog_act_metadata_records": {
        "record_id": (CLIPPED, "gmd:fileIdentifier rekordu CSW"),
        "resource_identifier": (TEXT, _URL),
        "metadata_url": (TEXT, _URL),
        "record_sha256": (STRICT, _HASH),
        "response_sha256": (STRICT, _HASH),
    },
    "pog_area_summaries": {
        "scope": (STRICT, _ENUM),
        "act_identifier": (STRICT, "kopia klucza aktu z bazy"),
        "act_version": (STRICT, "kopia wersji z bazy (już ograniczonej do 120 znaków)"),
        "teryt": (STRICT, "kod TERYT o stałej długości"),
        "edition": (STRICT, "edycja wydania nadana przez importer"),
        "legal_status": (STRICT, _ENUM),
        "denominator_source": (STRICT, _ENUM),
        "method_version": (STRICT, _CONST),
    },
    "pog_area_summary_zones": {
        "zone_code": (STRICT, "kod strefy ze słownika klasyfikacji POG"),
    },
    "raster_assets": {
        "transform_method": (STRICT, _ENUM),
        "review_status": (STRICT, _ENUM),
    },
    "source_documents": {
        "title": (CLIPPED, "tytuł dokumentu ze źródła"),
        "document_type": (STRICT, _ENUM),
    },
    "document_versions": {
        "media_type": (CLIPPED, "nagłówek Content-Type zdalnego serwera"),
        "extraction_method": (STRICT, _ENUM),
        "ocr_engine_version": (STRICT, "wersja silnika OCR odczytana przez aplikację"),
        "content_hash": (STRICT, _HASH),
        "review_status": (STRICT, _ENUM),
    },
    "legal_units": {
        "unit_type": (STRICT, _ENUM),
        "number": (CLIPPED, "numer jednostki redakcyjnej odczytany z dokumentu"),
    },
    "symbol_legal_units": {
        "relation_type": (STRICT, _ENUM),
        "review_status": (STRICT, _ENUM),
    },
    "manual_reviews": {
        "subject_type": (STRICT, _ENUM),
        "reviewer": (STRICT, "operator przypisany do klucza administracyjnego"),
        "decision": (STRICT, _ENUM),
        "review_status": (STRICT, _ENUM),
    },
    "planning_rules": {
        "code": (STRICT, "kod reguły ze słownika parsera"),
        "operator": (STRICT, _ENUM),
        "unit": (STRICT, "jednostka ze słownika parsera"),
        "parser_version": (STRICT, _CONST),
        "review_status": (STRICT, _ENUM),
        "conflict_group": (STRICT, "UUID"),
    },
    # --- tabela modułu location (poza app/models/): import słownika adresowego -----------------
    "address_search_entries": {
        "source_object_id": (STRICT, _IMPORT),
        "source_version": (STRICT, _IMPORT),
        "result_type": (STRICT, _ENUM),
        "country": (STRICT, _IMPORT),
        "voivodeship": (STRICT, _IMPORT),
        "county": (STRICT, _IMPORT),
        "municipality": (STRICT, _IMPORT),
        "city": (STRICT, _IMPORT),
        "street": (STRICT, _IMPORT),
        "house_number": (STRICT, _IMPORT),
        "postal_code": (STRICT, _IMPORT),
        "teryt": (STRICT, _IMPORT),
        "simc": (STRICT, _IMPORT),
        "ulic": (STRICT, _IMPORT),
    },
}
