# ADR-011: Macierz kompletności i świeżości sekcji oraz pakiet audytowy analizy

- Status: Zaakceptowany
- Data: 2026-09-29
- Zakres: BK-504 (Task 6.4), BK-505 (Task 6.5)
- Powiązane: ADR-001 (modularny monolit), ADR-002 (status prawny ≠ pokrycie),
  ADR-006 (teren), ADR-010 (raport v2, snapshot map)

## Kontekst

`SourceMetadata`, `SourceRecord`, ostrzeżenia i `cache_valid_until` istniały, ale
nie tworzyły trwałej macierzy jakości sekcji:

- ocena „sprawdzono / częściowo / niedostępne” była liczona w chwili renderu PDF
  (`_domain_assessments`), więc zależała od kodu raportu, a nie od zapisanej
  analizy; API i UI jej nie miały;
- **brak pokrycia** (NMT poza zasięgiem, powiat bez GESUT) i **błąd źródła**
  były zlane w jeden status „źródło niedostępne”;
- nie istniało pojęcie świeżości: stary pomiar wyglądał jak nowy;
- TTL cache (`ANALYSIS_CACHE_MAX_AGE_DAYS`) dotyczy ponownego użycia wyniku, a nie
  ważności danych źródła — a jedna data ważności dla wszystkich źródeł byłaby
  fałszem (właściciele danych nie publikują częstotliwości aktualizacji);
- brakowało eksportu, który pozwala niezależnie sprawdzić wynik i pochodzenie
  danych bez ponownego odpytywania źródeł.

## Decyzje

### 1. `SectionQuality` i trwała macierz (BK-504)

Dla każdej z 10 sekcji analizy (`parcel`, `mpzp`, `pog`, `pog_overlays`, `flood`,
`nature`, `terrain`, `utilities`, `transport`, `mpzp_pog_relation`) — także pustej
— powstaje wiersz: `section`, `status`, `source_id`, `fetched_at`,
`data_release_id`, `source_version`, `manual_review_required`, `freshness`,
`policy_version`, `reason_codes`. Wiersze tworzą `SectionQualityMatrix`
(`schemas/source.py`), którą `save_analysis` zapisuje w `analyses.section_quality`
(JSONB, migracja `026`) razem z `matrix_sha256`.

- **Status wynika z kontraktu źródła**, nie ze świeżości: `available`, `partial`,
  `no_coverage`, `unavailable`, `error`, `unknown`, `out_of_scope`,
  `awaiting_input`. Brak pokrycia i błąd są odrębne; `manual_review_required` jest
  flagą ortogonalną (sekcja może być `available` i wymagać weryfikacji). Mapowanie
  (`services/section_quality.py`): `RiskSectionResult.status` i
  `TerrainResult.status` przechodzą 1:1; POG z `coverage_status`/`data_availability`;
  KIUT `covered` → `partial` (podgląd nie jest geometrią sieci, BK-306),
  `not_covered` → `no_coverage`; transport → `out_of_scope` (BK-305).
- **Źródło**: `source_id` z `SourceMetadata` (adaptery ULDK, KIMPZP, KIUT WMS i POG
  niosą go od BK-504), a dla zapisów sprzed tej zmiany — jawna, mała tabela nazw.
  Sekcja bez źródła ma zawsze kod powodu (`NO_SOURCE_CONTRACT`,
  `MPZP_NOT_DETERMINED`, `DERIVED_SECTION`…); źródło spoza katalogu daje
  `SOURCE_NOT_IN_CATALOG`, a nierozpoznane — `SOURCE_ID_UNRESOLVED`.
- **`fetched_at` to czas pobrania danych**, nie wejścia aktu w życie; daty aktów są
  w wynikach POG/MPZP osobno.

### 2. Świeżość: reguła per źródło i jawny punkt odniesienia

`freshness` ∈ {`fresh`, `stale`, `unknown`} liczy `evaluate_freshness`
(`shared/data_quality.py`) z trzech wejść: `fetched_at`, **punktu odniesienia**
(`analyzed_at` — chwila analizy) i **reguły źródła** z katalogu
(`freshness_policy: {max_age_days, basis, rationale}`).

- Brak reguły ⇒ `unknown` (`FRESHNESS_NO_POLICY`), ale zmierzony wiek jest podany.
  **Nie istnieje globalny TTL.** Źródło z `expected_update_interval: "unknown"`
  nie może mieć reguły — walidator katalogu odrzuca wymyślony TTL; `basis:
  source_declared_interval` wymaga zadeklarowanej częstotliwości.
- Czas z przyszłości poza tolerancją 5 min (`FRESHNESS_FUTURE_TIME`), czas bez
  strefy (`FRESHNESS_INVALID_TIME`) i brak czasu (`FRESHNESS_NO_FETCH_TIME`) dają
  `unknown`, nigdy `fresh`. Wiek równy limitowi jest jeszcze świeży.
- Reguły w katalogu (2026-09-29) mają tylko `isok`, `gdos`, `nmt`, `nmt_wcs`:
  7 dni, `basis: project_decision` — to **decyzja projektowa, nie deklaracja
  właściciela danych** (zgodna z domyślnym limitem wieku zamrożonego wyniku usług
  na żywo w cache analiz). Pozostałe źródła (ULDK, KIMPZP, POG/APP, MPZP RU, KIUT)
  mają świeżość `unknown`, dopóki właściciel nie opublikuje częstotliwości albo
  projekt nie podejmie decyzji z uzasadnieniem.
- `policy_version` = `quality-policy/1+<12 znaków SHA-256 reguł całego katalogu>`;
  każda zmiana reguły zmienia wersję, więc zapisana ocena wskazuje, wedle jakiej
  polityki ją wystawiono.

### 3. Historyczna ocena vs wiek na dzień eksportu

Ocena jest wystawiana **raz, przy zapisie**, z punktem odniesienia `analyzed_at`,
i tylko czytana przez cache, API, raport i eksport. Upływ czasu, zmiana polityki
w katalogu ani chwila eksportu jej nie zmieniają.

- Wiek na dzień eksportu to **osobne ostrzeżenie** (tabela 8.3 raportu, blok w UI
  „na dziś”): liczone z `fetched_at` i reguły *zapisanej z oceną*, tylko dla
  źródeł z regułą; nie wchodzi do zapisu ani do `matrix_sha256`.
- `matrix_sha256` = SHA-256 kanonicznego JSON (sekcje, wersja polityki, punkt
  odniesienia, znormalizowane do UTC). `origin` i legenda nie wchodzą do hasha.
  Raport pokazuje sumę i ostrzega, gdy zapis jest niezgodny z treścią.
- Wznowienie MPZP zmienia treść i `analyzed_at` analizy, więc macierz jest
  wystawiana ponownie w tej samej transakcji (jak `report_map_snapshot`).
- Zapis sprzed migracji `026` nie jest uzupełniany (ocena wstecz użyłaby bieżącej
  polityki i udawała stan z chwili analizy). Odczyt odtwarza macierz z
  `origin=reconstructed` i kodem `LEGACY_QUALITY_RECONSTRUCTED`; nie zapisuje jej.
- Kontrakt wyniku (`RESULT_CONTRACT_VERSION`) dostał `+quality-v1.0`, więc
  wyniki z cache sprzed zmiany nie są serwowane jako trafienia.

### 4. Ta sama macierz w API, UI i PDF

`AnalyzeResponse.section_quality` (wraz z wyliczaną legendą statusów, świeżości i
kodów powodów) jest jedynym źródłem dla:

- UI (`SectionQualityMatrix.tsx`): tabela ze statusem (tekst + znak, kolor nie
  jest jedynym nośnikiem), źródłem albo powodem braku, czasem pobrania, wydaniem,
  flagą weryfikacji, świeżością i powodami; legenda z API; jawna informacja, gdy
  odpowiedź nie ma macierzy albo ocena jest odtworzona;
- PDF: podsumowanie (sekcja 2) i macierz (tabela 8.1) korzystają z tych samych
  wierszy; tabela 8.2 to legenda, 8.3 — wiek na dzień eksportu, 8.4 — ostrzeżenia
  analizy; suma kontrolna macierzy i wersja polityki są w sekcji 9.

### 5. Pakiet audytowy (BK-505)

`GET /report/{analysis_id}/audit.zip?access_token=…` — dostęp i limit zapytań jak
dla raportu PDF (`require_analysis_token`, `rate_limit`), `404` dla nieistniejącej
analizy, `413` po przekroczeniu limitów (`AUDIT_EXPORT_MAX_FILES`,
`…_MAX_FILE_BYTES`, `…_MAX_TOTAL_BYTES`), odpowiedź `StreamingResponse`.

Moduły (ADR-001): `reporting/domain/audit_package.py` (bezpieczne nazwy, limity,
manifest — czysta biblioteka standardowa), `reporting/application/audit_export.py`
(składanie treści i reguła redystrybucji), `reporting/infrastructure/archive.py`
(deterministyczny ZIP i strumień), adapter `services/audit_package.py` (snapshot →
dane wejściowe; warstwy GeoJSON z listą źródeł, referencje `SourceArtifact`).

- **Zawartość**: `analysis.json` (wynik bez sekretu `access_token`, bez rejestru
  źródeł i z geometriami zastąpionymi znacznikiem; macierz jakości w całości),
  `sources.json` (rejestr źródeł z katalogiem, licencją, artefaktem, wydaniem i
  decyzją redystrybucji), `parcel.geojson`, `layers/*.geojson`, `README.md`
  (CRS, data analizy, znaczenie statusów i świeżości, macierz, pominięcia,
  weryfikacja) i `manifest.json`.
- **CRS**: obliczenia w EPSG:2180 (geometria działki w `analysis.json`,
  `computation.parcel_geometry_epsg2180`); GeoJSON wyłącznie w EPSG:4326
  (RFC 7946) do prezentacji i wymiany — opisane osobno w README, manifeście i
  metadanych każdej warstwy.
- **Manifest**: `schema_version`, `exporter_version`, dla każdego pliku poza sobą
  nazwa, bajty i SHA-256 (posortowane), `omitted_artifacts`. Hash całej paczki jest
  **poza archiwum** — nagłówek `X-Audit-Package-SHA256` (klient porównuje go z
  sumą pobranych bajtów). `backend/scripts/verify_audit_package.py` (tylko
  biblioteka standardowa) weryfikuje pakiet offline; zmiana jednego bajtu jest
  wykrywana z nazwą pliku.
- **Determinizm**: wpisy w kolejności nazw, stały znacznik czasu 1980-01-01, stała
  kompresja i atrybuty; brak czasu eksportu w jakimkolwiek pliku; z rejestru
  wydań nie eksportujemy pól zmiennych w czasie (`is_active`). Ten sam snapshot w
  tej samej wersji eksportera daje identyczne bajty; wersję podbijamy przy każdej
  zmianie treści (`audit-exporter/1.0.0`).
- **Redystrybucja** — `redistribution` w katalogu (`allowed`, `derived_only`,
  `forbidden`, `unconfirmed`; domyślnie `unconfirmed`; `no_redistribution` ⇒
  `forbidden`; `allowed`/`derived_only` tylko dla źródeł `production`).
  Warstwa pochodna trafia do pakietu, gdy **każde** jej źródło ma `allowed` lub
  `derived_only`; surowe atrybuty (np. `pog.raw_attributes`) — tylko przy
  `allowed`. Źródło nieznane lub `unconfirmed` traktujemy jak zakaz. Pominięty
  artefakt zostawia w manifeście referencję, SHA-256 i rozmiar kanonicznej treści,
  źródła i powód; rejestr źródeł nadal opisuje źródło (adres, czas, hash).
  Wartości w katalogu są ostrożną interpretacją pola `license` i wymagają
  potwierdzenia właściciela danych.

## Alternatywy odrzucone

- **Jeden TTL dla wszystkich źródeł** — arbitralny i fałszywy; niezgodny z
  zasadą „reguła zależy od źródła”.
- **Wyprowadzanie TTL z tekstu `expected_update_interval`** — wartości katalogu
  to `not_published`/`unknown`; parsowanie tekstu udawałoby deklarację właściciela.
- **Przeliczanie oceny przy każdym odczycie/eksporcie** — ocena zmieniałaby się z
  czasem i polityką, a historyczny PDF nie byłby odtwarzalny.
- **Uzupełnianie starych analiz w migracji** — użyłoby bieżącej polityki i
  udawało ocenę z chwili analizy (jak w ADR-010 dla map).
- **Hash paczki wewnątrz archiwum** — niemożliwy (paczka zawierałaby własny hash);
  hash poza archiwum jest jedynym spójnym rozwiązaniem.
- **Surowe artefakty źródeł w ZIP** — poza zakresem: pakiet ma umożliwić weryfikację
  wyniku, a nie mirrorować dane, których redystrybucja nie jest potwierdzona.

## Konsekwencje

- Użytkownik odróżnia stary pomiar, brak pokrycia i błąd źródła; zbiorczy status
  analizy nie jest gwarancją kompletności raportu.
- Nowe pole `AnalyzeResponse` nadal wymaga decyzji w tabeli mapowania raportu;
  `section_quality.*` ma wpisy (`docs/report/field-mapping.md`).
- Nowy adapter musi podawać `SourceMetadata.source_id` z katalogu; bez niego
  sekcja ma `SOURCE_ID_UNRESOLVED`, świeżość `unknown`, a jej warstwy nie trafią do
  pakietu (`unconfirmed`).
- Dodanie reguły świeżości albo zmiana `redistribution` to zmiana katalogu (i
  `quality_policy_version` dla reguł), opisywana w uzasadnieniu wpisu.
- Rozmiar wiersza `analyses` rośnie o kilka kB (10 sekcji).
