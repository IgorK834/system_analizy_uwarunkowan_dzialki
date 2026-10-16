# Odbiór: droga i zabudowa sąsiednia z BDOT10k (#226, #227, #307, #308, #309)

Data: 13 października 2026 r. Baza kodu: `fcf6a9b` z **niezacommitowanymi** zmianami (AU-104–106 oraz ta praca; nic nie zacommitowano ani nie wypchnięto).
Decyzje i pozycje do potwierdzenia: [ADR-025](../adr/ADR-025-bdot10k-droga-i-zabudowa-sasiednia.md). Kontrakty źródła: [road_contracts.md](../data_sources/road_contracts.md),
[building_contracts.md](../data_sources/building_contracts.md). Odbiór zadań pokrewnych (parser, testy Docker): [pv3-closure-checks-2026-10-13.md](pv3-closure-checks-2026-10-13.md).

## Wynik w jednym akapicie

Issues #226, #227, #307, #308 i #309 były zamknięte (lub otwarte) przy kodzie, który miał model i przypadek użycia, ale **nie miał importu, infrastruktury ani integracji**. Teraz droga i zabudowa sąsiednia przechodzą całą ścieżkę:
paczka powiatowa BDOT10k → artefakt z SHA-256 → wydanie `data_releases` (jeden powiat = jedno aktywne wydanie) → PostGIS → adapter → orkiestrator → `AnalyzeResponse` → cache → macierz jakości i kompletność → UI → PDF → pakiet audytowy.
Na żywym stosie (osobny projekt Compose, sześć powiatów korpusu zaimportowanych poleceniem CLI) **30 z 30 działek korpusu dało `road_context` i `neighbour_buildings` ze statusem `available`, bez ani jednego 5xx**, a odległość drogi i liczba budynków na działce
zgadzają się z pomiarem niezależnym (30/30). Dwa kryteria nie są spełnione literalnie: **EGiB/KIEG nie jest użytecznym kanałem** (budynki pochodzą z BDOT10k — #226 wprost zakładał EGiB WFS), a **porównanie „w QGIS” wykonano w GDAL** (QGIS niedostępny).
Kryterium #309 „11 budynków dla `141801_4.0701.23/8`” **nie zostało sprawdzone** (powiat 1418 poza korpusem — nie pobrano).

## Kryteria akceptacji

### #226 (Task 4.4) — zabudowa sąsiednia

| Kryterium | Stan | Dowód |
|---|---|---|
| Kontrolne geometrie po obu stronach granicy bufora: liczności, odległości, pola, bez podwójnego liczenia | spełnione | `tests/test_building_context.py`: `test_geometries_on_both_sides_of_the_buffer_boundary_are_counted_exactly` (49,9 m i 50,1 m), `…an_id_present_twice_is_counted_once_and_overlapping_footprints_are_not_double_counted`, obrysy zachodzące i przylegające |
| ≥ 1 działka korpusu z niezależnym pomiarem „QGIS” wszystkich trzech buforów | **spełnione innym narzędziem** | 30/30 działek zgodnych z pomiarem **GDAL `ogr2ogr` (nie QGIS)**: `docs/evaluation/results/bdot10k/gdal-cross-check.md`; wartości oczekiwane liczone osobno Shapely z `gml:posList` |
| Brak pokrycia i awaria ≠ „brak budynków”; eksport bez danych osobowych | spełnione | statusy rozłączne (`no_coverage`/`unavailable`/`no_match`), `test_building_context.py` (`…failures_and_missing_coverage_are_disjoint…`, `…too_many_candidates…`), `test_bdot_context_e2e.py`; nie importujemy nazw, uwag ani `identyfikatorEGiB` (`test_reader_never_exposes_free_text_or_the_egib_identifier`); warstwa w ZIP ma tylko `building_id`, odległość, relację, kondygnacje i funkcję |
| ≥ 80% pokrycia zmienionego modułu; progi CI bez obniżenia | spełnione | backend 95,29% ogółem, zmienione moduły ≥ 87% (zob. „Dowody”); frontend 97,8% |
| Polecenia, wynik scenariusza końcowego, artefakty; zmiany kontraktu/konfiguracji/migracji opisane | spełnione | ten dokument, ADR-025, migracja `036_bdot10k_context`, README (import BDOT10k), `.env.example`, `docker-compose.yml` |
| **Tytuł: „z oficjalnego EGiB WFS”** | **niespełnione — kanał nie istnieje** | KIEG odpowiada na żądanie WFS dokumentem WMS, warstwa `budynki` jest niepytalna, regulamin zabrania automatycznego pobierania: `building_contracts.md` §1, fixture `kieg_wfs_request_returns_wms.txt`; źródłem jest `OT_BUBD_A` (85–97% obiektów ma geometrię z EGiB) |

### #227 (Task 4.5) — droga

| Kryterium | Stan | Dowód |
|---|---|---|
| Znane linie dają poprawną odległość i relację w EPSG:2180; test realny porównany w „QGIS” | **spełnione innym narzędziem** | `test_road_context.py::test_distance_relation_and_attributes_match_the_independent_measurement` (30 działek na PostGIS), GDAL 30/30 (odległość ≤ 0,011 m), żywe API 30/30 (poniżej) |
| UI/PDF mają zdanie o braku potwierdzenia prawnego dostępu; brak pola `legal_public_road_access` wyprowadzanego z geometrii | spełnione | UI `[data-testid=road-note]`, PDF 30 s. (tekst sprawdzony w wygenerowanym PDF), ZIP (`analysis.json`, `layers/road_context.geojson`); brak pola w schemacie, szablonie, odpowiedzi API, ZIP i UI: `test_road_context.py`, `test_report_bdot.py`, `test_bdot_context_e2e.py`, `RoadContextCard.test.tsx` |
| Pusty ograniczony zasięg, `no_coverage` i timeout rozróżnione, z metadanymi wyszukiwania | spełnione | `test_road_context.py`: `…empty_bounded_radius_is_no_match…`, `…no_coverage_distinguishes…`, `…timeout_and_database_errors_are_unavailable…`; wynik niesie `search_radius_m`, listę sprawdzonych promieni, limit kandydatów, `search_area_within_coverage` |
| Pokrycie ≥ 80%, polecenia, dokumentacja | spełnione | j.w. |

### #307 (Task 23.1) — import wydania powiatowego

| Kryterium | Stan | Dowód |
|---|---|---|
| Import powiatu z korpusu odtwarzalny: ten sam hash → ta sama liczba obiektów per klasa | spełnione | `test_bdot10k_import.py::test_the_same_package_hash_gives_the_same_release_and_the_same_object_counts`; sześć importów pełnych paczek na żywym stosie (liczności: `road_contracts.md` §3) |
| Wpis `bdot10k` z `contract_confirmed`; test kontraktowy na zamrożonym wycinku; pokrycie powiatu w `/api/v1/coverage` | spełnione (z uwagą o nazwie skryptu) | `catalog.yaml` (`production`, `contract_confirmed: true`); zamrożone mini-paczki `backend/tests/fixtures/bdot10k/` budowane `build_bdot10k_fixtures.py` — kryterium nazywa starszy `build_bdot10k_road_fixture.py` (jednopowiatowy, Kraków), który **zostaje w repozytorium**, ale testy używają nowego; `GET /api/v1/coverage/bdot10k?lon&lat` (`test_bdot_coverage_api.py`) |
| Wpis w katalogu z zamrożonymi odpowiedziami albo `contract_required` z uzasadnieniem | spełnione | `bdot10k_wfs_*.xml`, `kieg_wfs_request_returns_wms.txt` (`kieg_wms`: `research` z uzasadnieniem) |
| Sekcja widoczna w API, UI, PDF i pakiecie audytowym; macierz jakości z właściwym `source_id`; failure injection kończy się `unavailable`/`unknown`, nigdy „brak ograniczenia” | spełnione | `test_bdot_context_e2e.py` (API → PostGIS → ponowny odczyt → cache → PDF → ZIP), `test_section_quality.py` (`source_id=bdot10k`), failure injection: timeout/błąd bazy/niepotwierdzony katalog/wyjątek (`test_road_context.py`, `test_building_context.py`); pusta odpowiedź → `no_match`/`no_coverage`; „zmiana schematu” dotyczy paczki: brak klasy albo inny układ współrzędnych odrzuca import bez publikacji (`test_package_without_a_required_class_is_rejected_by_name`, `…another_coordinate_system_is_not_guessed`) |
| Dokumentacja: `current_state.md` i ADR | spełnione | `docs/current_state.md` (sekcja BDOT10k), ADR-025 |
| ≥ 80% pokrycia modułu; moduł frontendu w `coverage.include` | spełnione | patrz tabela pokrycia; `frontend/vitest.config.ts` (`lib/bdot.ts`, `RoadContextCard`, `BuildingsCard`, `BdotSourceLine`) |

### #308 (Task 23.2) — droga i dostęp

| Kryterium | Stan | Dowód |
|---|---|---|
| ≥ 6 działek: odległość i relacja zgodne z wartościami niezależnymi (tolerancja 0,05 m); `transport` zmienia status z `out_of_scope` na `available`/`no_coverage` | spełnione (30 działek, GDAL zamiast QGIS) | PostGIS 30/30 (`test_road_context.py`), żywe API 30/30 (`live-vs-independent.md`), GDAL 30/30; `completeness/1.1`: transport w zakresie (`test_completeness.py`); live: `available` 30/30, bez wydania `no_coverage` 6/6 |
| Test architektoniczny: brak pola prawnego dostępu w schemacie wyniku i szablonie PDF | spełnione | `test_road_context.py` (schemat JSON), `test_report_bdot.py::test_template_and_context_do_not_define_a_legal_road_access_field` (szablon i kontekst PDF) |
| Dokumentacja i ADR | spełnione | j.w. |
| Pokrycie ≥ 80% | spełnione | j.w. |

### #309 (Task 23.3) — zabudowa sąsiednia

| Kryterium | Stan | Dowód |
|---|---|---|
| `141801_4.0701.23/8`: liczba i odległości budynków w 50 m zgodne z BDOT10k; OnGeo: 11 budynków (sanity check); EPSG:2180 | **niespełnione — niesprawdzone** | działki nie ma w korpusie referencyjnym, a powiat piaseczyński (1418) nie jest jednym z zaimportowanych (zgodnie z zasadą pobierania tylko powiatów korpusu nie pobrano jego paczki). Obliczenia w EPSG:2180 i zgodność z pomiarem niezależnym dla 30 działek korpusu: spełnione. Propozycja: zgoda właściciela na import powiatu 1418 (paczka z `opendata.geoportal.gov.pl`) i jedno porównanie |
| Wpis w katalogu z `contract_confirmed` i zamrożonymi odpowiedziami albo `contract_required` | spełnione | `bdot10k` (`contract_confirmed`), `kieg_wms` (`research`), fixtures kontraktowe |
| Widoczność w API, UI, PDF, ZIP; macierz jakości (`buildings`, `source_id=bdot10k`); failure injection | spełnione | j.w.; sekcja `buildings` w `QUALITY_SECTIONS` (11 sekcji) |
| Dokumentacja i ADR | spełnione | j.w. |
| Pokrycie ≥ 80% | spełnione | j.w. |

## Dowody: polecenia i wyniki

**Backend w kontenerze jak w CI** (kopia drzewa bez `/.env`, `/backend/.env`, `/.git`; osobny projekt Compose, `down -v` po przebiegu):

```bash
docker compose -p <proj> --env-file /dev/null --profile test -f docker-compose.yml -f <override-bez-portow.yml> run --rm \
  -v $PWD/backend/app:/app/app -v $PWD/backend/tests:/app/tests -v $PWD/backend/alembic:/app/alembic -v $PWD/backend/scripts:/app/scripts \
  -v $PWD/backend/pyproject.toml:/app/pyproject.toml -v $PWD/backend/mypy-baseline.txt:/app/mypy-baseline.txt -v <kopia>:/repo:ro -e REPO_ROOT=/repo \
  backend-test pytest -m 'not docker_cli' --cov=app --cov-fail-under=80
```

Wynik: ****5011 passed**, 0 failed, 3 deselected (`docker_cli`; osobno `3 passed`, zob. pv3-closure-checks), pokrycie **95,29%** (próg 80%)**. Lint i typy (jak w CI): `ruff check .` — czysto; `python scripts/check_mypy_baseline.py` — „brak nowych zgłoszeń (17 znanych w bazie, 17 bieżących)”.
Frontend (`docker build --build-context shared=./shared --target test …`, potem `npm run lint && npm run typecheck && npm run test:coverage && npm run build && npm audit --omit=dev --audit-level=high`): **54 pliki / **621 testów** zielone, pokrycie 97,81%, `eslint`/`tsc`/`next build` zielone, `npm audit` 0 podatności**.

Pokrycie zmienionych i nowych modułów backendu (pełny przebieg; kolumna `Cover`): `bdot_ports` 100%, `roads` 100%, `domain/bdot` 100%, `domain/buildings` 100%, `domain/roads` 100%, `application/buildings` 98%, `bdot_repository` 94%, `bdot10k_import` 89%, `imports/domain/bdot10k` 98%, `bdot10k/reader` 94%, `bdot10k/repository` 99%, `bdot10k/geometry` 98%, `bdot10k/download` 95%, `imports/api/cli` 99%, `imports/composition` 93%, `imports/__main__` 87%, `services/bdot_context` 98%, `schemas/bdot_context` 98%, `routers/coverage` 100%, `section_quality` 99%, `completeness` 98%, `persistence` 98%, `report` 98%, `audit_package` 100%, `analysis_orchestrator` 90%, `models/bdot10k` 100%. Opakowanie CLI `imports/__main__.py` miało w pierwszym przebiegu 71% (obejmuje też starsze polecenia `parcels`/`raster`/`mpzp`); po dodaniu `tests/test_imports_cli_bdot10k.py` ma 87%, a niepokryte linie to starsze gałęzie, których ta praca nie zmieniała.

**Niezależny pomiar (GDAL, nie QGIS):**

```bash
docker run --rm --entrypoint python -v $PWD/backend:/work:ro -v $PWD/docs/evaluation/results/bdot10k:/out -w /work dzialki-backend-test \
  scripts/verify_bdot10k_with_gdal.py --output /out/gdal-cross-check
```

→ `docs/evaluation/results/bdot10k/gdal-cross-check.md`: **30 z 30 zgodnych** (droga: id, relacja, odległość ≤ 0,011 m; budynki: liczba na działce i w trzech buforach, pole części w pierścieniu ≤ 0,05 m², minimalna odległość).

## Live smoke (30 działek korpusu, żywe usługi, osobny projekt Compose)

Stos: `bdotlive` (obraz backendu zbudowany z drzewa roboczego, PostGIS 16 z czystą bazą, migracje do `036`), backend `127.0.0.1:18000`, frontend zbudowany z `NEXT_PUBLIC_API_BASE_URL=http://localhost:18000` na `127.0.0.1:13000`.
Import: `python -m app.modules.imports bdot10k --powiat <TERYT> --package <ZIP>` dla sześciu powiatów; **wszystkie `succeeded`, 0 odrzuconych, 0 naprawionych geometrii**.

```bash
python3 -I backend/scripts/live_smoke_corpus.py --base-url http://127.0.0.1:18000 --concurrency 3 \
  --output docs/evaluation/results/live-smoke/2026-10-13.md --json-output docs/evaluation/results/live-smoke/2026-10-13.json
python3 -I backend/scripts/compare_live_smoke_bdot.py docs/evaluation/results/live-smoke/2026-10-13.json --output docs/evaluation/results/bdot10k/live-vs-independent
```

| Miara | 2026-10-12 (przed) | 2026-10-13 (po) |
|---|---|---|
| odpowiedzi HTTP | 200 × 30, 5xx = 0 | 200 × 30, **5xx = 0** |
| `road_context.status` | — (sekcji nie było) | `available` **30** |
| `neighbour_buildings.status` | — | `available` **30** |
| zakres analizy (`completeness.total`) | 8 sekcji | **10 sekcji** (transport i zabudowa w zakresie) |
| sprawdzone sekcje | 2 z 8: 1; 3 z 8: 17; 4 z 8: 10; 5 z 8: 2 | 4 z 10: 5; 5 z 10: 13; 6 z 10: 11; 7 z 10: 1 |
| `complete` | 0 / 30 | 0 / 30 (blokuje POG, ADR-022) |
| brakujące: `pog`, `pog_overlays`, `mpzp_pog_relation` | 30 / 30 / 30 | 30 / 30 / 30 |
| brakujące: `transport`, `buildings` | (poza zakresem) | **0 / 0** |
| brakujące: `terrain`, `flood` | 20, 0 | 10, 14 — zmienność żywych usług (NMT/ISOK: `NMT_RELIEF_WARNING` 19→9, `ISOK_WARNING` 6→18), nie zmiana kodu |
| p50 / p95 czasu odpowiedzi | 22,6 s / 50,5 s | 12,2 s / 45,3 s |

Działki korpusu: relacja drogi `disjoint` 16, `intersects` 14; kategorie zarządzania: gminna 18, powiatowa 5, **wewnętrzna 5**, krajowa 2 (osie `wewnętrzna` są w wyniku widoczne i nie oznaczają „dostępu do drogi publicznej”); odległość min./mediana/maks. 0,0 / 3,1 / 30,2 m; budynki na działce: 19 z 30 działek.
**Żywe API vs pomiar niezależny: 30 z 30 zgodnych** (odległość ≤ 0,05 m i liczba budynków na działce — `docs/evaluation/results/bdot10k/live-vs-independent.md`). Wyniki bez wydań i poza pokryciem:

| Przypadek | Wynik | Plik |
|---|---|---|
| 6 działek korpusu **przed** importem (brak jakiegokolwiek wydania) | droga i zabudowa `no_coverage` (`NO_ACTIVE_RELEASE`) 6/6; kompletność 3–4 z 10, `transport` i `buildings` na liście braków | `results/bdot10k/live-no-release.{json,md}` |
| `141801_4.0701.23/8` przy 6 aktywnych wydaniach (poza pokryciem) | `no_coverage` (`OUTSIDE_COVERAGE`), „3 z 10 sekcji sprawdzono; brak: … droga i dostęp, zabudowa sąsiednia …” | `results/bdot10k/live-outside-coverage.json` |

## Weryfikacja w przeglądarce (wbudowana przeglądarka, build produkcyjny frontendu)

| Widok | Działka | Obserwacja |
|---|---|---|
| 1440×900 | `126105_9.0001.49/2` (Kraków, z wydaniem) | karty „Droga i dostęp do drogi” (30,21 m, „oś jezdni leży poza działką”, gminna, droga dojazdowa, 602384K / Floriańska, promień 50 m z maksimum 500 m) i „Zabudowa sąsiednia” (3 budynki na działce, pokrycie 47,8%, bufory 29/59/163); zdanie o braku potwierdzenia prawnego dostępu; wydanie `bdot10k-1261-1355c5a51538`, stan danych 27.05.2026; wiersze macierzy jakości „sprawdzono”, wydanie #5, świeżość „aktualne wg reguły (365 dni)”; przełączniki „Najbliższa droga” i „Budynki w sąsiedztwie” działają (budynki i oś jezdni znikają i wracają na mapie) |
| 1440×900 | `141801_4.0701.23/8` (poza pokryciem) | „brak pokrycia danymi BDOT10k (działka poza zaimportowanymi powiatami)”, nota „Brak danych nie oznacza braku drogi/budynków”, zdanie o dostępie nadal obecne; wiersze jakości „brak pokrycia źródła”, wydanie —, świeżość „nieustalona” |
| 375×812 | `126105_9.0001.49/2` | brak poziomego przewijania strony (`scrollWidth` 375), karta czytelna, tabele buforów i listy w przewijanym regionie z `role="region"`, `aria-label` i fokusem klawiatury |
| PDF (pobrany z `POST /analyze/{id}/links`) | Kraków, z wydaniem | 30 stron; tabele 7.3 (oś jezdni), 7.4 (budynki na działce i bufory), 7.5 (najbliższe budynki); zdanie o dostępie; etykieta wydania |
| PDF | poza pokryciem | 29 stron; „status źródła: brak pokrycia źródła”, „Brak danych nie oznacza braku drogi/budynków”, zdanie o dostępie |
| ZIP + `verify_audit_package.py` | Kraków | `OK: 8 plików zgodnych z manifest.json`; `audit-exporter/1.5.0`; `layers/road_context.geojson` (oś, odcinek), `layers/neighbour_buildings.geojson` (30 obiektów, bez nazw), `sources.json` z `bdot10k` (`derived_only`, wydanie #5); brak `legal_public_road_access` |
| ZIP + weryfikacja | poza pokryciem | `OK: 7 plików`; w `analysis.json` obie sekcje `no_coverage` / `OUTSIDE_COVERAGE`, brak warstw BDOT10k |

Konsola przeglądarki: jedyne błędy to `GET /api/v1/map/pog/releases/active → 404` (brak wydania POG — udokumentowane zachowanie, zob. README) i anulowane kafle map; żadne nie dotyczy sekcji BDOT10k. Widok wrócił do rozmiaru pulpitu.

**Poprawki znalezione podczas weryfikacji na żywym stosie:** (1) wiersz macierzy jakości dla `no_coverage` pokazywał „Pobrano <chwila próby>” i świeżość „aktualne”, choć niczego nie odczytano — czas pobrania jest teraz pusty, a świeżość „nieustalona”
(`services/bdot_context.py`, testy: `test_road_context.py`, `test_audit_e2e.py`); (2) etykieta statusu `no_coverage` powtarzała przyczynę („…poza zaimportowanymi powiatami… (działka poza zaimportowanymi powiatami)”) — skrócona.

## Zmiany kontraktu, konfiguracji i migracji

* **Migracja `036_bdot10k_context`** (head; `035_completeness` bez zmian): `analyses.result_contract_version` 128 → 256 znaków (ciąg kontraktu ma 97 znaków; test ma zapas), `data_releases.scope_key` + nowy unikalny indeks aktywnego wydania `(data_source_id, COALESCE(scope_key,''))`,
  tabele `bdot_releases`, `bdot_roads`, `bdot_buildings` (GiST, CHECK-i), kolumny JSONB `analyses.road_context` i `analyses.neighbour_buildings` (nullable); pełny downgrade (`test_migration_036.py`). Polityka kolumn tekstowych: `tests/column_length_policy.py`.
* **Wersje:** `ROAD_RESULT_SCHEMA_VERSION` 1.0 i `BUILDINGS_RESULT_SCHEMA_VERSION` 1.0 w `RESULT_CONTRACT_VERSION` (cache: jednorazowe chybienie dla zapisanych analiz), `QUALITY_RESULT_SCHEMA_VERSION` 1.1, `COMPLETENESS_RESULT_SCHEMA_VERSION` 1.1 / reguła `completeness/1.1`,
  `AUDIT_EXPORTER_VERSION` `audit-exporter/1.5.0`, `REPORT_LAYOUT_VERSION` `report-v2/2026.10.13-1`, `quality_policy_version` (wyliczany z katalogu — zmienił się wraz z regułą świeżości `bdot10k`), `docs/report/field-mapping.md` odtworzony skryptem w obrazie backendu.
* **Konfiguracja:** `ROAD_CONTEXT_ENABLED`, `BUILDING_CONTEXT_ENABLED`, `BDOT10K_MAX_PACKAGE_BYTES`, `BDOT10K_DOWNLOAD_TIMEOUT_SECONDS` (`.env.example`, `docker-compose.yml`); wyłączenie funkcji daje jawne `unavailable`/`FEATURE_DISABLED`.
* **Katalog źródeł:** `bdot10k` (nowe), `kieg_wms` (nowe, `research`); `egib` bez zmian (`no_redistribution`).
* **API:** `AnalyzeResponse.road_context`, `neighbour_buildings` (opcjonalne — starsze zapisy dają `unknown`, nie „brak”), `GET /api/v1/coverage/bdot10k`.
* **Fixtures frontendu** (`frontend/test/bdotFixtures.ts`) są generowane z backendu (`scripts/export_bdot_fixtures.py`); test pilnuje zgodności (`test_bdot_fixture_tools.py`).

## Odstępstwa i decyzje właściciela

Zob. ADR-025, tabela D1–D8 („Proponowany”). Skrót: redystrybucja `derived_only` w pakiecie audytowym; reguła świeżości liczona od importu (stan danych wyświetlany osobno) — wariant od stanu danych oznaczyłby powiaty piski i warszawski jako przestarzałe; próg 0,01 m² „na działce”
(w korpusie 3 obrysy łącznie 0,153 m² na działce `real-005-126105-9-0001-580-4`); lista 100 budynków w API, 25 w PDF; zakres wdrożenia (które powiaty importować); brak interpretacji prawnej kategorii „droga wewnętrzna”.

## Czego nie zrobiono

* Porównania `141801_4.0701.23/8` z OnGeo (11 budynków) — działka spoza korpusu, powiat 1418 nie został pobrany.
* Pomiaru w QGIS (GDAL zamiast QGIS).
* Mapy PDF z osią drogi i budynkami (tabele i opis spełniają kryteria; dodanie warstw do mapy statycznej zmieni semantyczne skróty fixtures map).
* Harmonogramu odświeżania paczek (import ręczny, ponowny import = nowe wydanie).
* Usunięcia starego `backend/scripts/build_bdot10k_road_fixture.py` i `backend/tests/fixtures/roads/bdot10k_1261/` (zatwierdzone wcześniej, nieużywane przez testy; kandydaci do usunięcia po decyzji).
