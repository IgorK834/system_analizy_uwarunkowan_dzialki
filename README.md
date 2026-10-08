# System analizy uwarunkowań przestrzennych działki

Aplikacja do automatycznej analizy potencjału inwestycyjnego działki na podstawie danych GIS, planów miejscowych (MPZP), planów ogólnych gmin (POG), obszarów uzupełnienia zabudowy (OUZ), uzbrojenia terenu, ryzyka powodziowego oraz form ochrony przyrody.

## Uruchomienie

Domyślną ścieżką uruchomienia projektu jest **Docker Compose**. Cały system (backend, frontend, baza PostgreSQL/PostGIS) startuje w kontenerach — nie wymaga lokalnej instalacji PostgreSQL ani innych zależności systemowych na macOS.

### Pierwsze uruchomienie

```bash
cp .env.example .env
docker compose up --build
```

Tylko baza danych (bez backendu):

```bash
cp .env.example .env
docker compose up -d db
```

Tylko backend z bazą:

```bash
docker compose up -d db backend
curl http://localhost:8000/health
```

Po uruchomieniu całego zestawu frontend jest dostępny pod adresem
`http://localhost:3000`, a API pod `http://localhost:8000`.

### Warstwy podglądowe WMS

Nakładki MPZP, POG i uzbrojenia terenu KIUT nie odpytują usług Geoportalu
bezpośrednio z przeglądarki. Frontend pobiera bezpieczny rejestr z
`GET /api/v1/map/preview-sources`, a kafle z
`GET /api/v1/map/tiles/{mpzp|pog|kiut}/{z}/{x}/{y}.png`. Backend weryfikuje PNG
i zapisuje je w named volume `map_tile_cache`.

Każde źródło ma osobny timeout, limit współbieżności i okres ważności w
`backend/app/core/wms_preview_sources.json`. MPZP i POG są świeże przez 24
godziny, KIUT przez 6 godzin; podczas przejściowej awarii może zostać podany
starszy kafel. Nagłówek `X-Tile-Cache` rozróżnia odpowiedzi `MISS`, `HIT` i
`STALE`.

Warstwy służą wyłącznie do podglądu. Brak obiektów nie potwierdza braku planu
ani sieci, a KIUT nie jest używany do wyznaczania odległości do przyłączy.
Źródła, ograniczenia i informacje o buforowaniu są stale widoczne w panelu
warstw oraz w nocie strony.

`GET /api/v1/map/coverage/kiut?lon=&lat=` odpytuje wskaźnikową warstwę WMS
`gesut` i zwraca `covered`, `not_covered` albo `unknown`. Timeout i błąd usługi
zawsze dają `unknown`, nigdy `not_covered`. Ten sam wynik jest zapisywany jako
`utilities_preview` w snapshotcie analizy i trafia do panelu oraz raportu PDF;
nie zawiera odległości ani liczby sieci.

### Wektorowa mapa POG z lokalnego wydania (BK-401–403)

Mapa analityczna planu ogólnego nie korzysta z WMS: backend wystawia kafle
Mapbox Vector Tile z aktywnego, wersjonowanego wydania POG w PostGIS.

- `GET /api/v1/map/pog/releases/active` — metadane aktywnego wydania
  (`release_id`, SHA-256 artefaktu, zasięg, liczba aktów wg statusu, wersja i
  SHA stylu) oraz `tile_url_template` **przypięty do `release_id`**; 404, gdy
  lokalnego wydania nie ma (to nie jest „brak planu”).
- `GET /api/v1/map/pog/releases/{release_id}` — metadane konkretnego, także
  historycznego wydania (odtworzenie stanu mapy).
- `GET /api/v1/map/pog/releases/{release_id}/{z}/{x}/{y}.mvt?edition=all|binding|project`
  — warstwy `zones`, `ouz`, `downtown`, `social_infrastructure_standard`,
  `act_boundary`; pusty kafel to `200` z pustym protobufem, błędne z/x/y lub
  edycja `422`, nieznane wydanie `404`, przekroczony limit obiektów/bajtów
  `413`; `ETag` + `If-None-Match` → `304`, nagłówki `X-Tile-Cache`,
  `X-Pog-Release`, `X-Pog-Edition`, `X-Pog-Tile-Schema`, `X-Pog-Tile-Features`.

Parametry stref w kaflu liczy ta sama funkcja domenowa co analiza działki, więc
kliknięta cecha ma te same wartości co wynik analizy tej samej geometrii. Limity
i cache ustawiają zmienne `POG_TILE_*` (`backend/app/core/settings.py`).

Kolory, progi, jednostki, etykiety 13 ustawowych stref i wzory OUZ/OZS/OSDIS są
w jednym pliku `shared/pog-presentation.json`, czytanym przez frontend
(`lib/pogZones.ts`, `lib/pogThemes.ts`) i backend (`app/core/pog_presentation.py`,
raport PDF). Oba obrazy kopiują go z kontekstu budowania `shared`
(`additional_contexts` w `docker-compose.yml`). Decyzje: `docs/adr/ADR-007-pog-vector-tiles-and-shared-presentation.md`.

### Inspektor obiektu, agregaty stref i stan warstwy POG (BK-404–406)

- **Kliknięcie mapy nie uruchamia analizy.** Otwiera inspektor punktu, który
  natychmiast pokazuje z kafli wszystkie obiekty POG w punkcie (nakładające się
  strefy, OUZ/OZS/OSDIS — bez duplikatów z sąsiednich kafli): symbol, cztery
  parametry (`null` ≠ 0), profile, akt/status z plakietką projektu i wydanie.
  Pełną analizę działki uruchamia dopiero przycisk „Analizuj działkę w tym
  punkcie”. `Escape` zamyka panel i przywraca focus.
- `GET /api/v1/map/pog/releases/{release_id}/features/{feature_id}` — szczegóły
  obiektu spoza kafla (pełna etykieta, nazwy profili, uchwała i urzędowy kod
  statusu aktu); `feature_id` to atrybut z kafla (z ukośnikami, kodowany jako
  `%2F`) albo `planning_feature:<id>`; ETag/304, `404`/`409`/`422`.
- `GET /api/v1/map/pog/releases/{release_id}/summary?act_id=…` albo
  `?teryt=…&edition=binding|project` — struktura powierzchniowa stref aktu lub
  gminy (`area_sqkm`, `share_pct`, `zone_count`, `is_complete`, jawny mianownik,
  luka, nakładanie) policzona w EPSG:2180 **przy imporcie** (migracja `024`),
  bez obliczeń przestrzennych w HTTP; ETag/304. Brak granicy aktu daje
  `share_pct = null`, nie 100%. Wydanie opublikowane przed migracją `024`
  uzupełnia ponowny import jego artefaktu.
- Metadane wydania mają `coverage_areas[]` (zasięg i kompletność każdego aktu).
  Panel mapy pokazuje stan warstwy `loading | available | partial | no_coverage
  | error | stale` (niezależny od statusu prawnego), datę danych, przycisk
  „Ponów wczytanie warstwy” (zachowuje dotychczasowe dane) i stałą plakietkę
  „projekt / dane niewiążące”. Projekt ma też własny wzór na mapie
  (`style_version` `2026.09.29-1` w `shared/pog-presentation.json`).

Decyzje: `docs/adr/ADR-009-pog-inspector-area-summaries-layer-state.md`.

### Raport PDF v2 i deterministyczne mapy (BK-501–503)

`GET /report/{analysis_id}?access_token=…` (bez zmian kontraktu: `application/pdf`,
`Content-Disposition: attachment; filename="raport_analizy_{id}.pdf"`, `404`
dla nieistniejącej analizy, `500` bez szczegółów WeasyPrint) buduje raport
**wyłącznie** z zapisanego snapshotu analizy i zamrożonego snapshotu map — nie
uruchamia analizy i nie pobiera żadnych źródeł ani WMS.

- Dziesięć sekcji w stałej kolejności: identyfikacja i geometria, podsumowanie,
  MPZP, POG + OUZ/OZS/OSDIS, środowisko, teren, infrastruktura/transport,
  jakość i kompletność, źródła/provenance, ograniczenia; załącznik A to
  mapowanie pól `AnalyzeResponse` (`docs/report/field-mapping.md`). Każda wartość
  jest oznaczona jako fakt źródłowy, wynik obliczenia, przybliżenie albo dane
  ręczne; `null` = „nie określono” (≠ 0), puste sekcje podają powód; brak
  scoringu i średnich parametrów stref.
- Pełne tabele: strefy MPZP (pole m², udział %, akt/wydanie), parametry z
  jednostkami i odsyłaczami `[E#]` do evidence uchwały (strona, segment, SHA-256
  dokumentu `[D#]`), sprzeczności z listą kandydatów; strefy POG, parametry i
  profile (wiersz na strefę), status aktu, osobne tabele OUZ, OZS i OSDIS.
- Mapy (działka, MPZP, POG w trybie tematycznym, ISOK/GDOŚ) są zamrażane przy
  zapisie analizy w `analyses.report_map_snapshot` (migracja `025`): geometrie
  EPSG:2180, kadr, podziałka, kolejność warstw, style z wersją, font, wydania i
  daty danych oraz hash semantyczny. Render lokalny (Pillow) na neutralnym tle
  albo na zapisanym artefakcie podkładu z SHA-256.
- Konfiguracja: `REPORT_MAP_POG_THEME` (`zones` | `intensity` |
  `building_coverage` | `height` | `biologically_active`, zamrażany z analizą),
  `REPORT_MAP_BASEMAP_ARTIFACT_DIR` (katalog `*.png` + `*.json` z `sha256`,
  `bbox` EPSG:2180, licencją i `allowed_for_report`; puste = neutralne tło).
  Usunięto `REPORT_MAP_BASEMAP_ENABLED`, `REPORT_MAP_WMS_*`,
  `REPORT_MAP_KIMPZP_OVERLAY_ENABLED`, `REPORT_MAP_KIUT_OVERLAY_ENABLED`.
- Szablon: `backend/app/templates/report.html`. Fixtures i mapy referencyjne:
  `backend/tests/fixtures/reports/` (odświeżenie:
  `python -m tests.fixtures.reports.build_fixtures --maps` w kontenerze).

Decyzje: `docs/adr/ADR-010-report-v2-and-deterministic-maps.md`; odbiór:
`docs/evaluation/results/bk-501-503-verification.md`.

### Macierz kompletności i świeżości oraz pakiet audytowy (BK-504, BK-505)

**Macierz jakości sekcji.** Dla każdej z 10 sekcji analizy (także pustej)
`AnalyzeResponse.section_quality` niesie: status według kontraktu źródła
(`available`, `partial`, `no_coverage`, `unavailable`, `error`, `unknown`,
`out_of_scope`, `awaiting_input` — brak pokrycia i błąd są odrębne), `source_id`
z katalogu albo kod powodu jego braku, `fetched_at` (czas pobrania, nie wejścia
aktu w życie), wydanie i wersję danych, flagę `manual_review_required`,
świeżość (`fresh` | `stale` | `unknown`), `policy_version` i `reason_codes`, a do
tego legendę i `matrix_sha256`. Ocenę wystawia `save_analysis` **raz**, z punktem
odniesienia `analyzed_at`, i zapisuje w `analyses.section_quality` (migracja
`026`); cache, API, PDF i eksport tylko ją czytają — historyczny stan nie zmienia
się z upływem czasu ani po zmianie polityki.

- **Świeżość zależy od źródła.** Reguła wieku (`freshness_policy`: `max_age_days`,
  `basis`, `rationale`) jest opcjonalnym polem wpisu w
  `docs/data_sources/catalog.yaml`. Źródło bez reguły ma świeżość `unknown` —
  **nie ma globalnego TTL**, a źródło z `expected_update_interval: "unknown"`
  nie może mieć reguły (walidator katalogu). Obecnie reguły (7 dni,
  `project_decision`) mają wyłącznie usługi na żywo `isok`, `gdos`, `nmt`,
  `nmt_wcs`. Czas z przyszłości, bez strefy albo brak czasu daje `unknown`.
- **Wiek na dzień eksportu to osobne ostrzeżenie** (tabela 8.3 PDF, blok „na dziś”
  w UI); nie zmienia zapisanej oceny ani `matrix_sha256`. Analizy sprzed migracji
  nie są uzupełniane — odczyt odtwarza macierz z `origin=reconstructed` (kod
  `LEGACY_QUALITY_RECONSTRUCTED`) i jej nie zapisuje.
- Zmiana kontraktu wyniku: `RESULT_CONTRACT_VERSION` ma `+quality-v1.0` (wyniki
  z cache sprzed zmiany nie są serwowane). Adaptery ULDK, KIMPZP, KIUT WMS i POG
  podają teraz `source_id` z katalogu.

**Pakiet audytowy.** `GET /report/{analysis_id}/audit.zip?access_token=…`
(dostęp i limit zapytań jak dla raportu PDF; `404` brak analizy, `413` limit
rozmiaru, `500` bez szczegółów) strumieniuje ZIP zbudowany wyłącznie z zapisanego
snapshotu: `analysis.json`, `sources.json`, `parcel.geojson`, dozwolone
`layers/*.geojson`, `README.md` (CRS, data analizy, znaczenie statusów) i
`manifest.json` (`schema_version`, nazwa, bajty i SHA-256 każdego pliku poza
manifestem, pominięte artefakty). Hash paczki jest poza archiwum:
nagłówek `X-Audit-Package-SHA256`. GeoJSON jest w EPSG:4326, obliczenia w
EPSG:2180 (opisane osobno). Wpisy są posortowane, mają stały znacznik czasu i
bezpieczne nazwy; w plikach nie ma czasu eksportu, więc ten sam snapshot w tej
samej wersji eksportera (`audit-exporter/1.1.0`) daje identyczne bajty.

- **Redystrybucja steruje dołączaniem.** `redistribution` w katalogu
  (`allowed` | `derived_only` | `forbidden` | `unconfirmed`, domyślnie
  `unconfirmed`): warstwa pochodna trafia do pakietu, gdy każde jej źródło ma
  `allowed` albo `derived_only`; surowe atrybuty (np. `pog.raw_attributes`) —
  tylko przy `allowed`. Zakaz (także źródło nieznane) zostawia w manifeście
  wyłącznie referencję, SHA-256 i powód; rejestr źródeł nadal opisuje źródło.
  Wartości w katalogu to ostrożna interpretacja pola `license` i wymagają
  potwierdzenia właściciela danych.
- **Limity** (`AUDIT_EXPORT_MAX_FILES` = 200, `AUDIT_EXPORT_MAX_FILE_BYTES` = 32 MiB,
  `AUDIT_EXPORT_MAX_TOTAL_BYTES` = 64 MiB) — przekroczenie kończy się `413`, nie
  okrojonym pakietem.
- **Weryfikacja offline** (tylko biblioteka standardowa Pythona 3; zmiana jednego
  bajtu jest wykrywana z nazwą pliku):

```bash
python3 backend/scripts/verify_audit_package.py analiza_123_pakiet_audytowy.zip \
  --package-sha256 <wartość nagłówka X-Audit-Package-SHA256>
```

- UI: `ResultPanel` pokazuje macierz z legendą (status ma tekst i znak, nie tylko
  kolor), `ReportDownloadButton` — drugi przycisk „Pobierz pakiet audytowy (ZIP)”
  z sumą SHA-256 paczki, porównywaną z sumą pobranych bajtów.

Decyzje: `docs/adr/ADR-011-section-quality-matrix-and-audit-package.md`; odbiór:
`docs/evaluation/results/bk-504-505-verification.md`.

### Lokalny indeks podpowiedzi adresowych

Autocomplete korzysta z lokalnego PostgreSQL/PostGIS zasilanego oficjalnymi,
pełnymi i przyrostowymi paczkami słowników PRG Adresy / EMUiA GUGiK. Pierwszy
pełny import jest zadaniem utrzymaniowym i może pobierać duży wolumen danych:

```bash
docker compose --profile maintenance run --rm address-index-sync
```

Podczas developmentu można ograniczyć import do województwa, powiatu albo
gminy (TERYT ma odpowiednio 2, 4 albo 7 cyfr):

```bash
docker compose --profile maintenance run --rm address-index-sync \
  python -m app.modules.location.infrastructure.dictionary_import \
  sync --mode full --scope 14
```

Kolejne aktualizacje używają checkpointu `verId` poprzedniego wydania:

```bash
docker compose --profile maintenance run --rm address-index-sync \
  python -m app.modules.location.infrastructure.dictionary_import \
  sync --mode incremental --scope 14
```

Gotowość można sprawdzić przez
`GET /api/v1/search/addresses/status` albo polecenie `status`. Nowe wydanie jest
publikowane atomowo po kontroli jakości; w czasie importu API nadal czyta
poprzednie. Dopóki nie istnieje pierwsze wydanie, działa ograniczony fallback
UUG, który nie zapewnia pełnych sugestii dla krótkich prefiksów.

### Testy, jakość i łańcuch dostaw (AU-010)

**Backend.** `docker compose up` buduje obraz produkcyjny (`runtime`): zależności z `backend/requirements.lock`
(dokładne wersje i skróty SHA-256, instalacja z `--require-hashes`), bez `pytest`, `tests/` i `scripts/`.
Narzędzia testowe (`pytest`, `respx`, `pytest-cov`, `ruff`, `mypy`, `pip-audit`) są w `backend/requirements-dev.txt`
i trafiają wyłącznie do obrazu `test` (usługa `backend-test`, profil `test`):

```bash
docker compose --profile test run --rm backend-test pytest -m 'not docker_cli' --cov=app --cov-fail-under=80
docker compose --profile test run --rm --no-deps --entrypoint sh backend-test -c \
  'ruff check . && python scripts/check_mypy_baseline.py'
docker compose --profile test run --rm --no-deps --entrypoint sh backend-test -c \
  'pip-audit --require-hashes --disable-pip -r requirements.lock && pip-audit --require-hashes --disable-pip -r requirements-dev.txt'
```

Zależności zmienia się w `requirements.in` / `requirements-dev.in`, a lock generuje (kontener `python:3.13-slim`, `pip-tools >= 7`):
`pip-compile --generate-hashes --allow-unsafe --strip-extras --no-header --annotation-style=line requirements.in -o requirements.lock`
(polecenie dla pliku dev jest w nagłówku `requirements-dev.txt`). `mypy` obejmuje `app/modules/*`; znane zgłoszenia są w
`backend/mypy-baseline.txt`, a bramka odrzuca tylko NOWE (`python scripts/check_mypy_baseline.py --update` po świadomej zmianie).
Obrazy bazowe (`python`, `node`, `postgis`) są przypięte digestem; aktualizacje proponuje Dependabot (`.github/dependabot.yml`).

**Frontend** (Vitest, React Testing Library, ESLint, `npm audit`) — bez lokalnego Node.js, w obrazie testowym:

```bash
docker build --build-context shared=./shared --target test -t dzialki-frontend-test ./frontend
docker run --rm -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 dzialki-frontend-test \
  sh -c 'npm audit --omit=dev --audit-level=high && npm run lint && npm run typecheck && npm run test:coverage && npm run build'
```

CI (`.github/workflows/ci.yml`) uruchamia te same bramki w trzech zadaniach: backend (build `runtime` i `test`, kontrola, że obraz
produkcyjny nie zawiera narzędzi testowych, `ruff`, `mypy`, `pip-audit`, testy z `--cov-fail-under=80`), powtarzalność buildu (dwa
buildy bez cache dają identyczną listę pakietów) i frontend (`npm audit --omit=dev --audit-level=high`, ESLint, `tsc`, testy, build).

### Punkt odniesienia BK-001

[Stan projektu na commicie `8418bdd`](docs/current_state.md) rozdziela funkcje
działające od planowanych i od niezacommitowanych zmian katalogu źródeł.
[Wyniki i ograniczenia pomiaru](docs/evaluation/results/baseline/README.md)
zawierają polecenia, logi, kody wyjścia oraz raporty coverage. Izolowany
pomiar dokładnego commita można odtworzyć poleceniem:

```bash
./scripts/capture_baseline.sh
```

Skrypt korzysta z `git archive`, osobnego projektu Compose i przykładowej
konfiguracji `.env.example`; nie kopiuje lokalnego `.env` ani zmian roboczych.

### Rzeczywisty korpus referencyjny BK-002

[Opis doboru, źródeł i odtwarzania offline](docs/evaluation/corpus.md) dokumentuje
30 rzeczywistych działek z oczekiwanymi wynikami domenowymi. Korpus jest
oddzielony od syntetycznych fixtures i można go sprawdzić bez sieci:

```bash
cd backend
pytest tests/test_reference_corpus.py -q
```

### Ground truth i ewaluacja BK-003/BK-004

[Protokół niezależnego ground truth](docs/evaluation/ground_truth_protocol.md)
opisuje źródła, CRS, kolejność transformacji, tolerancje i drugą sesję dla 20%
próby. [Harness ewaluacyjny](docs/evaluation/harness.md) generuje komplet
raportów offline jednym poleceniem:

```bash
python3 backend/scripts/evaluate_reference_corpus.py \
  --corpus backend/tests/fixtures/reference_corpus/manifest.json \
  --output-dir docs/evaluation/results/reference-corpus \
  --mode offline \
  --fail-on-regression
```

### Badania ilościowe BK-601–BK-603

Trzy badania działają offline na zamrożonych danych i zapisują komplet artefaktów
(JSON, CSV, Markdown, SVG, zamrożony manifest z hashami). Opis, wyniki, ograniczenia i
mapowanie na kryteria akceptacji: [odbiór BK-601–603](docs/evaluation/bk-601-603-verification.md).

```bash
# BK-601: poprawność na korpusie referencyjnym (pola, statusy, rejestr błędów, determinizm)
python3 backend/scripts/evaluate_reference_corpus.py --study --repeat 3

# BK-602: centroid vs pełne przecięcie (kontrole, granice z korpusu, symulacja)
python3 backend/scripts/compare_centroid_intersection.py \
  --output-dir docs/evaluation/results/centroid

# BK-603 / PV3-03: parser MPZP na anotowanym korpusie (korpus: backend/tests/fixtures/mpzp_evaluation);
# wyniki silnika w docs/evaluation/results/parser/<silnik>/, porównanie wielu silników w .../comparison/
python3 backend/scripts/evaluate_mpzp_parser.py --mode offline \
  --output-dir docs/evaluation/results/parser
```

Parser MPZP v3 (wariant C: rdzeń deterministyczny + ekstrakcja modelem językowym z weryfikacją
cytatu) jest przygotowywany w Epicu 20. Gotowe są: ewaluator wielosilnikowy z metryką `source_consistent`
(PV3-03), protokół i narzędzia nowego zbioru końcowego (PV3-02) oraz narzędzie spike’u modelu i
[ADR-012](docs/adr/ADR-012-mpzp-llm-extraction.md) (PV3-01, pomiar na żywo wykonany 2026-10-02; decyzja właściciela: GO, 2026-10-05).
Stan i polecenia: [odbiór PV3-01–03](docs/evaluation/pv3-01-03-verification.md).
Fundament parsera v3 (PV3-04–06): wspólna reguła symbolu strefy (do 40 znaków, jeden plik przypadków dla
API i UI), drzewo struktury dokumentu z blokami stref i resolver zakresu strefy, dostępny w ewaluatorze
jako silnik `v3` (`--engine legacy v3`); produkcja nadal używa trybu `legacy`. Stan, wyniki i ograniczenia:
[odbiór PV3-04–06](docs/evaluation/pv3-04-06-verification.md).
Silnik wartości liczbowych (PV3-07–09): jeden deterministyczny silnik oparty o leksykon
(`app/modules/planning/domain/quantity_*.py`, wersja parsera `mpzp-parser/3.0-det`) współdzielony przez
aplikację i ewaluator; warunki wartości (`conditional` ≠ `conflict`, API/PDF/paczka audytowa/UI pokazują
warunek) oraz skalibrowana pewność z artefaktem `backend/app/core/mpzp_confidence_calibration.json`
(pasma low/medium/high z zmierzonymi błędami, raport niezawodności w ewaluatorze). Rekalibracja po każdej
zmianie silnika: `python3 backend/scripts/calibrate_mpzp_confidence.py` (`--check` sprawdza zgodność).
Stan, liczby i ograniczenia: [odbiór PV3-07–09](docs/evaluation/pv3-07-09-verification.md),
[ADR-013](docs/adr/ADR-013-mpzp-quantity-engine-conditions-calibration.md).
Ścieżka modelu językowego (PV3-10/11): port `StructuredExtractionProvider` (`planning/application`), adapter
Gemini przez REST (`planning/infrastructure/llm`, wyłączony domyślnie: `MPZP_LLM_ENABLED=false`, klucz tylko
z `GEMINI_API_KEY`), kontrakt wyjścia z wersjonowaną instrukcją i schematem oraz usługa ekstrakcji bloków
stref; wynik modelu to wyłącznie kandydat do ręcznej weryfikacji, a w CI nie ma internetu ani klucza
(odtwarzanie złotych odpowiedzi). Stan i ograniczenia: [odbiór PV3-10/11](docs/evaluation/pv3-10-11-verification.md).
Tryb parsera (PV3-12–14): `MPZP_PARSER_MODE=legacy` (domyślny, bez zmian zachowania), `v3` (rdzeń
deterministyczny na blokach stref), `hybrid_shadow` (odpowiedź = `v3`, model liczony w tle do porównań) albo
`hybrid` (`v3` + kandydaci modelu po bramkach deterministycznych G1–G8, zawsze `ai_candidate`); tryby z modelem
wymagają `MPZP_LLM_ENABLED=true`, a niedostępny model daje wynik deterministyczny z ostrzeżeniem
`MPZP_LLM_UNAVAILABLE`. Odpowiedzi modelu są cache'owane w `mpzp_llm_extractions` (bez treści żądania); zapisy
poza retencją usuwa `docker compose exec backend python -m app.modules.planning purge-llm-cache`. Stan i
ograniczenia: [odbiór PV3-12–14](docs/evaluation/pv3-12-14-verification.md).
Limity, dane i bramka (PV3-15–17): twarde limity doby/miesiąca (`MPZP_LLM_DAILY_*`, `MPZP_LLM_MONTHLY_*`),
współbieżność, częstotliwość i budżet czasu; dostawca modelu jest **przetwarzającym** publiczny tekst aktu, nie
źródłem danych (do modelu nie trafiają identyfikator działki ani dane użytkownika); kill switch bez wdrożenia:
`docker compose exec backend touch /var/lib/dzialki/llm-disabled`. Skanowanie sekretów:
`python3 backend/scripts/secret_scan.py .`. Szczegóły: [ADR-014](docs/adr/ADR-014-llm-data-handling.md),
[odbiór PV3-15–17](docs/evaluation/pv3-15-17-verification.md).

Oznaczenie, monitoring i obsługa (PV3-18–20): wartość z modelu językowego jest w UI, raporcie PDF i pakiecie audytowym
(`audit-exporter/1.2.0`, blok `model_provenance`) **zawsze oznaczona** — „odczyt automatyczny (model językowy),
zweryfikowany z cytatem — wymaga potwierdzenia” — z cytatem, stroną, warunkami, modelem, wersją instrukcji i skrótem
odpowiedzi; wartość deterministyczna nigdy nie jest tak oznaczana, a odczyt nie jest interpretacją prawną. Z odpowiedzi
modelu pakiet audytowy niesie tylko skrót i zweryfikowany cytat (cytat zgodnie z `redistribution` źródła). `GET /health`
raportuje komponent `llm` (`ok`/`degraded`/`disabled`, nigdy „failed”, bez wpływu na gotowość), a model i prompt są
przypięte (`model_pin.json`): zmiana bez ponownej ewaluacji `--live` i wpisu w ADR jest wykrywana. Runbook (włączanie,
wyłączanie, limity, alarmy, kontrola dryfu, rotacja klucza, zmiana modelu) z wynikiem próby:
[`docs/operations/mpzp-llm.md`](docs/operations/mpzp-llm.md). Stan i ograniczenia: [odbiór PV3-18–20](docs/evaluation/pv3-18-20-verification.md).

```bash
curl -s http://localhost:8000/health                                  # components.llm: ok | degraded | disabled
python3 backend/scripts/check_llm_pin.py check                        # przypięcie vs ustawienia, kod, Compose, .env.example, ADR
python3 backend/scripts/check_llm_pin.py check --require-evaluation   # bramka wdrożeniowa (dziś kod 1: brak ewaluacji --live)
python3 backend/scripts/rehearse_llm_runbook.py                       # próba runbooka offline (14 kroków)
# RĘCZNIE, poza CI — wysyłają publiczny tekst aktów do dostawcy, kosztują, wymagają zgody i GEMINI_API_KEY:
python3 backend/scripts/check_llm_drift.py --confirm-public-text     # dryf: bieżące wyjścia vs zamrożone odtworzenie
python3 backend/scripts/evaluate_mpzp_parser.py --engine v3 hybrid --live --model gemini-3.8-flash \
  --llm-replay <katalog odpowiedzi> --output-dir docs/evaluation/results/<bieg>   # ponowna ewaluacja pary (model, prompt)
```

**Ograniczenia (stan 2026-10-05):** ścieżka modelu jest wyłączona domyślnie, a decyzja o włączeniu produkcyjnym nie została
podjęta (bramka jakości `NOT_DECIDABLE`); przypięta para `gemini-3.8-flash` + `mpzp-extraction/1` **nie ma ponownej
ewaluacji na zbiorze złotym**; progi alarmów to propozycja bez kalibracji na ruchu; zbiór ewaluacyjny BK-603 (21 próbek, 9
gmin) ma anotacje asystenta AI bez przeglądu człowieka, a silnik v3 był na nim rozwijany — wyniki są rozwojowe i nie są
gwarancją jakości.

Geometrie stref POG dla dokładnej części BK-602 zamraża ręcznie (z siecią, poza CI)
`backend/scripts/freeze_pog_zone_layers.py`.

## Wymagania

- Docker
- Docker Compose

## Baza danych (PostgreSQL 16 + PostGIS 3.4)

Baza działa jako kontener `db` w sieci Docker. Backend łączy się z nią po hoście `db:5432` — **nie** przez `localhost`.

Dane są przechowywane w named volume `postgres_data`, a kafelki WMS w
`map_tile_cache`:

- `docker compose down` — zatrzymuje kontenery, **zachowuje** dane w wolumenie,
- `docker compose down -v` — zatrzymuje kontenery i **usuwa** wolumen wraz z danymi bazy.

### Debugowanie bazy (psql)

```bash
docker compose exec db psql -U app -d dzialki
```

Przykładowe zapytania kontrolne:

```sql
SELECT PostGIS_Version();
SELECT srid FROM spatial_ref_sys WHERE srid = 2180;
```

Port `5432` jest wystawiony wyłącznie na interfejsie loopback hosta (`127.0.0.1`)
w celach developerskich (klient DB, debug) — baza ma domyślne dane dostępowe i
nie jest widoczna w sieci. Kod aplikacji nie powinien używać `localhost:5432`
wewnątrz kontenera backendu.

### Uprawnienia kontenera backendu i cache analiz

Backend działa jako użytkownik `app`, nie `root`. Nowe wolumeny `map_tile_cache`
i `import_artifacts` dziedziczą właściciela z obrazu. Wolumeny utworzone przez
wcześniejszy obraz są własnością `root` i wymagają jednorazowej naprawy:

```bash
docker compose run --rm --user root --entrypoint chown backend \
  -R app:app /var/cache/dzialki /var/lib/dzialki
```

### Dostęp, limity i status analizy

- **Raport PDF, dokument uchwały i wznowienie analizy** (`GET /report/{id}`,
  `GET /analyze/{id}/pending-document`, `POST /analyze/resume`) wymagają tokenu
  dostępu. Token (pole `access_token` odpowiedzi analizy) to HMAC identyfikatora
  analizy — samo zgadywanie kolejnych ID nie wystarcza. Raport i dokument przyjmują
  go w parametrze `access_token`; `POST /analyze/resume` w polu `access_token` body
  albo w nagłówku `X-Analysis-Token` (interfejs wysyła token z wyniku). Brak lub zły
  token to zawsze `403` o tej samej treści, niezależnie od tego, czy analiza
  istnieje; `404`/`409` pojawiają się dopiero po poprawnym tokenie. Ustaw stały
  `ACCESS_TOKEN_SECRET` (`openssl rand -hex 32`); bez niego tokeny ważą do restartu
  backendu.
- **Akceptacja/odrzucenie rastrów** (`POST /api/v1/raster-assets/{id}/accept|reject`)
  wymaga nagłówka `X-Admin-Key`. Klucze konfiguruje `ADMIN_API_KEYS`
  (`operator:klucz,...`); operator w audycie pochodzi z klucza. Bez kluczy
  endpointy są wyłączone.
- **Limity zapytań** (429 + `Retry-After`): jeden limiter i jeden klucz klienta dla
  wszystkich endpointów publicznych, okno 60 s, osobny licznik na politykę.
  Domyślne progi (żądań/min na klienta): `POST /analyze` 20, z `force_refresh=true`
  5, raport i dokument 30, pokrycie KIUT 60, **kafle WMS i MVT 1200**
  (`RATE_LIMIT_TILES_PER_MINUTE`), `/geocode/suggest` 60, `/api/v1/search/addresses`
  30, pozostałe odczyty (metadane map, wydania POG, dokumenty aktów, sondy zdrowia,
  endpointy z kluczem administracyjnym) 300 (`RATE_LIMIT_DATA_PER_MINUTE`). Test
  `tests/test_rate_limit_coverage.py` pilnuje, że każda trasa z OpenAPI ma limiter i
  zwraca 429 z `Retry-After`. Limiter działa w procesie, więc przy N workerach
  efektywny limit rośnie N razy.
  **Kiedy włączać `X-Forwarded-For`:** domyślnie nagłówek jest ignorowany, a klientem
  jest adres połączenia — `docker-compose.yml` publikuje backend na `0.0.0.0:8000`
  bez proxy, więc nagłówek podaje sam klient i dałoby się nim obejść limit (pomiar z
  audytu: limit 5/min, rotacja nagłówka → 50/50 żądań przeszło; teraz co najwyżej 5).
  Włączaj go tylko za własnym reverse proxy, które dopisuje adres klienta, i tylko
  razem: `RATE_LIMIT_TRUST_FORWARDED_FOR=true`,
  `RATE_LIMIT_TRUSTED_PROXIES=<adres/sieć proxy widziana przez backend>` (CSV, np.
  `172.18.0.0/16`) i `TRUSTED_PROXY_COUNT=<liczba proxy przed backendem>`.
  Klientem jest wpis `X-Forwarded-For` liczony od końca o `TRUSTED_PROXY_COUNT`
  (wcześniejsze wpisy podaje klient), a gdy adres połączenia nie jest na liście
  zaufanych — nagłówek jest ignorowany. Klienci IPv6 są liczeni po prefiksie /64.
- **Jedna analiza na działkę (single-flight):** równoległe `POST /analyze` dla tej
  samej działki dają jedną analizę — pierwsze żądanie liczy, pozostałe czekają i
  dostają jej wynik (to samo `analysis_id`), również z `force_refresh=true`.
  Między workerami działa sesyjna blokada doradcza PostgreSQL. Gdy trwająca analiza
  tej samej działki nie skończy się w `ANALYSIS_SINGLEFLIGHT_WAIT_SECONDS` (90 s),
  oczekujące żądanie dostaje `503 ANALYSIS_IN_PROGRESS` z `Retry-After` — ponowienie
  trafia już w cache. Kolejka (`analysis_singleflight_waiters`) i liczniki
  (`analysis_singleflight.leader|wait|takeover|timeout`) są w `GET /health/upstream`
  (`X-Admin-Key`); log ma wpisy `singleflight=leader|wait`. Wyłącznik:
  `ANALYSIS_SINGLEFLIGHT_ENABLED=false`. Decyzje:
  [ADR-017](docs/adr/ADR-017-resume-token-rate-limit-and-single-flight.md).
- **Błędy API i `X-Request-ID`**: każda odpowiedź ma nagłówek `X-Request-ID`
  (poprawny identyfikator z żądania jest zachowany, inaczej UUID4), a błąd ma
  ciało `ErrorResponse` (`error`, `detail`, `request_id`) — także `500
  INTERNAL_ERROR`, który ma nagłówki CORS i nie zawiera stack trace. Ten sam
  `request_id` jest w logu serwera (`[request_id]` w każdym wpisie), więc kod
  zgłoszenia z interfejsu wystarcza do znalezienia przyczyny. Kody domenowe:
  `PARCEL_NOT_FOUND` (404), `UPSTREAM_UNAVAILABLE` (503),
  `UPSTREAM_INVALID_RESPONSE` (502), `PERSISTENCE_FAILED` (503, zapis wyniku
  odrzucony przez bazę). Liczniki odpowiedzi ULDK: `GET /health/upstream`
  (`X-Admin-Key`). Decyzje: [ADR-015](docs/adr/ADR-015-api-errors-request-id-and-text-column-policy.md).
- **Status `complete`**: niedostępność ISOK/GDOŚ lub awaria KIUT obniża status do
  `partial`. Znana luka „brak potwierdzonego kontraktu KIUT/GESUT” jest tylko
  ostrzeżeniem i nie blokuje `complete`.

Wynik analizy `complete` jest serwowany z cache przez `ANALYSIS_CACHE_MAX_AGE_DAYS`
dni (domyślnie 7). Podpis cache obejmuje wersje danych z katalogu, ale nie dane
z usług na żywo (ISOK, GDOŚ, NMT), więc dłuższy TTL oznacza starsze dane o ryzyku.

### Reset bazy danych

```bash
docker compose down -v
docker compose up -d db
```

**Uwaga:** to usuwa wolumen `postgres_data`, czyli **wszystkie** dane w `dzialki`
(zapisane analizy). Nie rób tego bez potwierdzenia, jeśli baza deweloperska
zawiera dane, na których komuś zależy.

#### Baza utknęła w pośredniej rewizji / limit 1600 kolumn (BK-306)

Testy migracji/alembic (`backend/tests/test_alembic_integration.py`,
`test_migration_*.py`, `test_versioned_model.py`) wykonują `alembic
downgrade`/`upgrade`, żeby sprawdzić rollback. Od tej pory (BK-306) robią to
na jednorazowej bazie utworzonej z szablonu `template_postgis`
(`tests/conftest.py::_isolated_migration_database`), a nie na bazie
wskazanej przez `DATABASE_URL` — więc uruchamianie ich lokalnie nie powinno
już dotykać bazy aplikacji.

Jeśli mimo to trafisz na objawy sprzed tej zmiany — `alembic_version`
wskazuje starą rewizję (np. `014_utilities_preview` zamiast najnowszej) i/albo
zapytanie zwraca błąd w rodzaju `tables can have at most 1600 columns`
(PostgreSQL liczy do tego limitu też kolumny już usunięte — `attnum` nie jest
odzyskiwany po `ALTER TABLE ... DROP COLUMN`), to oznacza, że jakiś proces
(stara wersja testów, ręczny `alembic downgrade` na współdzielonej bazie)
zostawił bazę w złym stanie. Sprawdź stan:

```bash
docker compose exec db psql -U app -d dzialki -c "SELECT * FROM alembic_version;"
docker compose exec db psql -U app -d dzialki -c \
  "SELECT count(*) FILTER (WHERE attisdropped) AS dropped, max(attnum) AS max_attnum \
   FROM pg_attribute WHERE attrelid = 'pog_data'::regclass;"
```

Możliwe naprawy, od najmniej do najbardziej inwazyjnej — **zapytaj, zanim
wykonasz którąkolwiek na bazie z danymi, na których komuś zależy**:

1. **Dokończ migrację do head** (nie usuwa danych, jeśli limit kolumn nie
   został jeszcze przekroczony):
   ```bash
   docker compose exec backend alembic upgrade head
   ```
2. **Limit kolumn już przekroczony** (`upgrade head` sam rzuca
   `TooManyColumns`) — kolumn z usuniętymi (`attisdropped`) atrybutami nie da
   się „odzyskać” bez przepisania tabeli. Jedyne wyjście to migracja danych do
   nowej tabeli/bazy (`CREATE TABLE ... AS SELECT` z jawną listą żywych
   kolumn, albo `pg_dump --data-only` + `pg_restore` do świeżo zainicjowanej
   bazy na aktualnym `head`) — zrób to tylko po konsultacji z kimś, kto zna
   wagę danych w tej bazie.
3. **Baza jest tylko środowiskiem testowym bez ważnych danych** — najprościej
   zacząć od zera:
   ```bash
   docker compose down -v
   docker compose up -d db
   docker compose exec backend alembic upgrade head
   ```

## Struktura katalogów

```text
backend/    — API FastAPI, serwisy domenowe, modele, testy
frontend/   — aplikacja Next.js z mapą i panelem wyników
shared/     — artefakty wspólne dla obu obrazów (styl i legenda POG)
docs/       — dokumentacja techniczna
scripts/    — skrypty pomocnicze (migracje, import danych itp.)
```

## Uwaga prawna

Analiza ma charakter **wyłącznie informacyjny** i nie stanowi oficjalnego dokumentu urzędowego ani podstawy do decyzji administracyjnych. Przed podjęciem decyzji inwestycyjnej należy zweryfikować dane w urzędach i u uprawnionych specjalistów.
