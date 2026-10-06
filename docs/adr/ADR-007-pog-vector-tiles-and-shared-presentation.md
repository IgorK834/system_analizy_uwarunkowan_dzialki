# ADR-007: Wektorowe kafle POG z wersjonowanego wydania i jeden artefakt prezentacji

- Status: Zaakceptowany
- Data: 2026-09-28
- Zakres: BK-401 (Task 5.1), BK-402 (Task 5.2), BK-403 (Task 5.3)
- Powiązane: ADR-001 (modularny monolit), ADR-002 (status prawny i pokrycie POG),
  ADR-003 (provenance aktu POG)

## Kontekst

Do BK-401 mapa pokazywała POG wyłącznie jako rastrowy podgląd WMS
(`/api/v1/map/tiles/pog/...png`). Analiza działki czytała natomiast wektory z
lokalnego, wersjonowanego wydania w PostGIS (`data_releases`,
`planning_act_versions`, `planning_features`, `plan_boundaries`). Użytkownik nie
mógł więc porównać kolorów mapy z liczbami wyniku, a kolory mapy, legendy i
raportu PDF były definiowane w trzech miejscach (TypeScript, Python, szablon).

## Decyzje

### 1. Kafle MVT przypięte do `release_id` (BK-401)

- Endpoint `GET /api/v1/map/pog/releases/{release_id}/{z}/{x}/{y}.mvt` obok
  (a nie zamiast) endpointu PNG. Metadane `GET /api/v1/map/pog/releases/active`
  i `/{release_id}` zwracają `tile_url_template` z konkretnym `release_id`.
  Frontend pobiera metadane raz na sesję mapy i nie podmienia źródła, gdy w tle
  aktywowane zostanie nowe wydanie — cała sesja pokazuje jeden stan danych.
  Historyczne wydanie pozostaje adresowalne (odtworzenie mapy dla audytu).
- Potok SQL: `ST_TileEnvelope` (3857, z marginesem bufora i segmentacją krawędzi)
  → `ST_Transform` koperty do 2180 → selekcja indeksem GiST na kanonicznych
  geometriach 2180 → `ST_ClipByBox2D` → `ST_Transform` do 3857 →
  `ST_AsMVTGeom(extent=4096, buffer=64)` → `ST_AsMVT` osobno dla każdej warstwy;
  warstwy są sklejane w jeden protobuf.
- Pięć warstw logicznych: `zones`, `ouz`, `downtown`,
  `social_infrastructure_standard`, `act_boundary`. Atrybuty są „chude”:
  `feature_id`, `feature_version`, `symbol`, `label` (≤ 200 znaków),
  `legal_status`, `teryt`, `act_id`, `data_release_id`; dla stref dodatkowo
  `zone_code`, cztery parametry BK-105 pod dokładnymi nazwami kontraktu,
  `parameters_informational` i kody profili (≤ 256 znaków). Surowe
  `raw_attributes`/XML nie są publikowane. Identyfikator cechy MVT to klucz
  `planning_features.id` (feature-state), stabilny identyfikator APP jest w
  `feature_id`.
- **Zgodność z analizą z konstrukcji.** Typ strefy, symbol, etykieta i cztery
  parametry liczy jedna czysta funkcja `planning.domain.pog_features`
  (`release_feature_attributes` + `pog_feature_presentation`) używana zarówno
  przez `pog_analyzer`/orkiestrator, jak i przez adapter kafli. Adapter pobiera z
  bazy tylko wąski podzbiór surowych kluczy (`RAW_ATTRIBUTE_KEYS`); test pilnuje,
  że podzbiór daje identyczny wynik jak pełny rekord. Z tego powodu kafel ma dwa
  zapytania (kandydaci → atrybuty w Pythonie → `ST_AsMVT` z `jsonb_to_recordset`)
  zamiast drugiej, równoległej implementacji ekstrakcji w SQL.
- `NULL` nie jest kodowany w MVT: brak wartości parametru = brak klucza, nigdy 0.
- Walidacja: `0 ≤ z ≤ 18` (konfigurowalne `POG_TILE_MIN_ZOOM/MAX_ZOOM`),
  `0 ≤ x, y < 2^z`, znana edycja → inaczej `422`; nieznane wydanie `404`; pusty
  kafel `200` z pustym (poprawnym) protobufem.
- Edycja (`all` | `binding` | `project`) filtruje po kanonicznym statusie aktu
  (BK-106); `project` obejmuje `project` i `in_progress`.
- Cache: kafel jest deterministyczną funkcją
  `(schemat atrybutów pog-mvt/1, release_id, edition, z, x, y)` — wydanie nie
  zmienia się po publikacji, a styl jest stosowany po stronie klienta. Klucz
  cache i ETag zawierają te składniki (ETag dodatkowo skrót treści). LRU w
  pamięci procesu ograniczony bajtami (`POG_TILE_CACHE_MAX_BYTES`), `304` dla
  `If-None-Match`, `Cache-Control: public, max-age=POG_TILE_BROWSER_TTL_SECONDS`.
  Zmiana znaczenia atrybutów wymaga podbicia `POG_TILE_SCHEMA_VERSION`.
- Limity: `POG_TILE_MAX_FEATURES` i `POG_TILE_MAX_BYTES` → `413` z nagłówkiem
  `X-Pog-Tile-Limit` (kafel niepełny byłby mylący — brak strefy wyglądałby jak
  „brak planu”); `statement_timeout` transakcji kafla.
- Kod mieszka w module `planning` (domena `pog_tiles`/`pog_features`, aplikacja
  `pog_tiles`, infrastruktura `mvt.py`, kompozycja), a cienkie endpointy w
  istniejącym routerze `map_tiles.py` (granice ADR-001 zachowane).
- Brak nowej migracji: wszystkie potrzebne indeksy GiST i kolumny istnieją.

### 2. Pięć trybów tematycznych bez nowych żądań (BK-402)

- Tryby `zones | intensity | building_coverage | height | biologically_active`
  mapują się na `zone_code` i cztery pola BK-105.
- Źródło wektorowe jest dodawane raz; zmiana trybu to wyłącznie
  `setPaintProperty` (`fill-color`, wzór i krycie warstwy „brak wartości”).
  Brak wartości jest sprawdzany jawnie (`has` / `== null`) przed `to-number`,
  dopiero potem `step` po progach — `NULL` nigdy nie trafia do klasy z zerem.
- Filtr projekt/akt wiążący to `setFilter` na już pobranych kaflach; projekt ma
  słabsze krycie i obrys przerywany (osobna warstwa obrysu, bo `line-dasharray`
  nie jest sterowany danymi).
- Tryb i filtr są zapisywane w `localStorage` i odtwarzane po remoncie mapy.

### 3. Jeden, wersjonowany artefakt prezentacji (BK-403)

- `shared/pog-presentation.json` (`schema: pog-presentation/1`,
  `style_version`) zawiera: 13 stref z kodem, identyfikatorem słownika, polską
  etykietą, kolejnością i paletą; styl „strefa nierozpoznana” i „brak wartości”
  (kolor + wzór + opis); pięć tematów z jednostką, opisem kierunku skali,
  domknięciem przedziałów (`left`: [min, max)) i klasami; wzory/obrysy/etykiety
  OUZ, OZS, OSDIS i granicy aktu; style statusów prawnych.
- Lista stref jest zweryfikowana ze słownikiem urzędowym
  `RodzajStrefyPlanistycznejKod` (art. 13c ust. 2 upzp), pobranym 2026-09-28 i
  zamrożonym z SHA-256 w `backend/tests/fixtures/pog_presentation/`. Paleta jest
  autorska (nie kopiuje serwisu referencyjnego).
- Adaptery: `frontend/lib/pogZones.ts`, `frontend/lib/pogThemes.ts` (walidacja
  przy imporcie, wyrażenia MapLibre, legenda) oraz
  `backend/app/core/pog_presentation.py` (Pydantic, ta sama walidacja). Ten sam
  config zasila mapę, `PogLegend`, wykres udziałów stref i miniaturę raportu.
- OUZ/OZS/OSDIS są rozróżnione wzorem (ukośne kreski / kropki / kratka),
  obrysem (różne `dasharray`) i etykietą tekstową — nie tylko barwą.
- Budowanie: oba obrazy kopiują plik z kontekstu `shared`
  (`additional_contexts` w Compose, `--build-context shared=./shared` w CI).
  Next.js (Turbopack) rozwiązuje wyłącznie pliki pod swoim korzeniem, dlatego
  `turbopack.root`/`outputFileTracingRoot` wskazują katalog repozytorium, a
  serwer standalone leży w `.next/standalone/frontend`.
- Snapshot: `PogResult.presentation_style` zapisuje wersję, SHA-256 artefaktu i
  zamrożony podzbiór stylu (strefy, nierozpoznana, brak wartości, nakładki).
  Raport rysuje mapę i legendę stylem ze snapshotu; analiza sprzed BK-403 (brak
  pola) dostaje bieżący styl z jawną adnotacją w legendzie. `PogZoneResult` i
  `PogAreaResult` mają `geometry_geojson` (przecięcie z działką w 4326) wyłącznie
  do prezentacji. `POG_RESULT_SCHEMA_VERSION` 2.3 → 2.4 (unieważnia cache wyników).

## Konsekwencje

- Mapa działa na lokalnym wydaniu bez odpytywania RU; brak wydania jest
  komunikowany jako niedostępność warstwy, nie brak planu.
- Zmiana koloru, progu lub etykiety to edycja jednego pliku; testy kontraktowe w
  obu językach (`test_pog_presentation_contract.py`, `pogZones.test.ts`,
  `pogThemes.test.ts`, `PogLegend.test.tsx`) wykrywają rozjazd legendy i warstwy.
- `docker build` frontendu bez `--build-context shared=./shared` kończy się
  błędem — to celowe, bo obraz nie może powstać bez configu.
- Zależność BK-104: wydanie musi być kompletnym snapshotem aktów, bo kafle
  filtrują po `data_release_id`; zapewnia to ADR-008 (akty niezmienione są
  przenoszone do nowego wydania).
