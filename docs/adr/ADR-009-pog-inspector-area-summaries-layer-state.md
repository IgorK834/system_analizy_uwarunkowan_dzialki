# ADR-009: Inspektor obiektu POG, agregaty stref wydania i stan warstwy mapy

- Status: Zaakceptowany
- Data: 2026-09-29
- Zakres: BK-404 (Task 5.4), BK-405 (Task 5.5), BK-406 (Task 5.6)
- Powiązane: ADR-001 (modularny monolit), ADR-002 (status prawny ≠ pokrycie),
  ADR-007 (kafle MVT przypięte do wydania), ADR-008 (wydanie = pełny snapshot)

## Kontekst

Po BK-401–403 mapa POG pokazywała wektorowe kafle z przypiętego wydania, ale:

- każde kliknięcie mapy od razu wysyłało `POST /analyze` (kosztowna analiza
  działki), a panel pokazywał wyłącznie jedną strefę z kafla — bez OUZ/OZS/OSDIS,
  bez nazw profili i danych aktu;
- nie istniały trwałe agregaty powierzchni stref aktu/gminy, więc każda
  odpowiedź musiałaby liczyć `ST_Intersection`/`ST_Area` przy HTTP;
- stan warstwy był opisany jednym zdaniem zależnym od odpowiedzi metadanych;
  awaria kafla, pusty widok poza zasięgiem i nieaktualne metadane wyglądały
  tak samo jak „mapa bez obiektów”, a projekt różnił się od aktu wiążącego
  wyłącznie kryciem i obrysem.

## Decyzje

### 1. Inspektor obiektu bez analizy (BK-404)

- Kliknięcie mapy wykonuje `queryRenderedFeatures` **wyłącznie** na warstwach
  wypełnień BK-401 (`pog-zones-fill`, `pog-ouz-pattern`, `pog-downtown-pattern`,
  `pog-social-infrastructure-standard-pattern`). Cechy są deduplikowane kluczem
  `warstwa:data_release_id:id MVT` (identyfikator MVT = klucz wiersza wydania,
  zapasowo `feature_id`), bo ta sama cecha przecina kilka kafli. Pokazywane są
  wszystkie trafienia: nakładające się strefy oraz OUZ/OZS/OSDIS.
- Panel pokazuje natychmiast dane z kafla: symbol i strefę, cztery parametry
  (`null` = „brak wartości w danych”, nigdy 0), kody profili, akt i
  `legal_status` z plakietką projektu, wydanie `#release_id` + etykietę.
- Szczegóły, których kafel nie mieści, pobiera wersjonowany
  `GET /api/v1/map/pog/releases/{release_id}/features/{feature_id}` (ścieżka
  `{feature_id:path}`, bo identyfikator APP zawiera `/`; akceptowany też zapis
  `planning_feature:<pk>` z kafla). Zwraca pełną etykietę, nazwy profili ze
  słownika, dane aktu (nazwa, uchwała, okres obowiązywania, urzędowy kod statusu
  `legal_status_code`) i wydania. Atrybuty kanoniczne liczy ta sama funkcja co
  kafel i analiza (`candidate_presentation`). ETag z treści + `Cache-Control`;
  `404` (obiekt spoza wydania), `409` (niejednoznaczny identyfikator), `422`.
- Pełną analizę uruchamia **wyłącznie** przycisk „Analizuj działkę w tym
  punkcie” — dotychczasowy flow `{method: "map", lon, lat}`; przycisk jest
  zablokowany podczas trwającej analizy, a kliknięcia w panelu nie propagują.
- Brak trafień jest interpretowany w kontekście stanu warstwy i pokrycia
  (`inspectorEmptyMessage`): ładowanie, warstwa niedostępna (brak wydania,
  awaria, warstwy nieodpytane), punkt poza zasięgiem wydania, kafle z błędem,
  aktywny filtr edycji, i dopiero wtedy „brak obiektu w wydaniu #N” — zawsze z
  zastrzeżeniem, że to nie jest urzędowe potwierdzenie braku planu.
- Dostępność: panel `region` z nagłówkiem otrzymującym focus po każdym
  kliknięciu, `Escape` zamyka i przywraca focus, statusy w `aria-live`.

### 2. Agregaty powierzchni stref przy imporcie (BK-405)

- Migracja `024_pog_area_summaries` (po `023`): `pog_area_summaries` (jeden
  wiersz na wersję aktu w wydaniu albo na gminę + edycję `binding`/`project`)
  i `pog_area_summary_zones` (pole m²/km², udział, liczność na typ strefy).
- Liczone w EPSG:2180 w `SqlAlchemyImportRepository.publish_pog` **po**
  zapisaniu aktów i **przed** aktywacją wydania, w tej samej transakcji
  (`imports/infrastructure/pog_aggregates.py`). Błąd obliczeń wycofuje
  publikację; aktywne wydanie i jego agregaty przełączają się atomowo, a
  przypięte starsze wydanie zachowuje własne agregaty.
- Typ strefy wyznacza `planning.domain.pog_features` (ta sama funkcja co
  analiza i kafle — dozwolony import domeny innego modułu z infrastruktury).
- `area_sqkm = m²/1e6`, `share_pct = 100·pole/pole(granicy)`. Mianownik to
  wyłącznie granica aktu ze źródła; brak granicy → `denominator_area_sqm =
  NULL`, `share_pct = NULL`, `is_complete = false` (`no_boundary`) — nigdy 100%.
  CHECK `ck_pog_area_summaries_null_denominator` wymusza to w bazie.
- Utrwalane: `denominator_area_sqm`, `missing_area_sqm` (luka), `overlap_area_sqm`
  (nakładanie stref), `outside_area_sqm` (strefy poza granicą), `share_sum_pct`,
  tolerancje, `is_complete` z listą przyczyn, `method_version =
  pog-aggregates/1`, `computed_at` — provenance także dla wyniku niepełnego.
  Tolerancja powierzchni: 0,05% mianownika (min. 1 m²); sumy udziałów: ±0,1 pp.
- Gmina: akty jednej edycji w kolejności priorytetu (najnowsza
  `version_started_at`), obszar pokryty przez akt o wyższym priorytecie nie jest
  liczony ponownie (`deduplicated_area_sqm`); mianownik = suma granic bez
  podwójnego liczenia i tylko gdy każdy akt ma granicę. Akty `unknown`/`superseded`
  mają wyłącznie agregat aktu (nie mieszamy statusów w agregacie gminy).
- `GET /api/v1/map/pog/releases/{release_id}/summary?act_id=…` albo
  `?teryt=…&edition=binding|project` czyta gotowe liczby (test przechwytuje SQL:
  brak `ST_Intersection`, `ST_Area`, `ST_Union`, `ST_Difference`), ETag/304.
  Wydanie sprzed migracji nie ma agregatów → `404` z wyjaśnieniem; ponowny import
  jego artefaktu trafia w to samo wydanie (ADR-008) i je uzupełnia.
- Frontend `PogAreaSummary`: wykres SVG i równoważna tabela budowane z jednej
  funkcji `summaryRows`, więc liczby są identyczne; luka jest jawnym wierszem,
  dane niepełne mają widoczne oznaczenie z przyczyną.

### 3. Stan warstwy i styl projektu (BK-406)

- `LayerState = loading | available | partial | no_coverage | error | stale`
  jest odrębny od `legal_status`. `derivePogLayerStatus` wyznacza go z
  metadanych wydania i zdarzeń źródła MapLibre (`sourcedataloading`,
  `sourcedata`, `error` z `sourceId`), nigdy z liczby pikseli ani pustego kafla:
  - `loading` — metadane lub kafle w toku;
  - `no_coverage` — brak lokalnego wydania albo okno mapy poza zasięgami aktów;
  - `error` — metadane niedostępne albo żaden kafel się nie wczytał;
  - `partial` — część kafli z błędem, limit kafla `413`, albo akt w widoku bez
    granicy / z niepełnym agregatem BK-405;
  - `stale` — ponowienie metadanych nie powiodło się (zachowane wydanie i data
    ostatniego potwierdzenia) albo przypięte wydanie nie jest już aktywne.
- Pokrycie ocenia się metadanymi obszaru: odpowiedź wydania ma
  `coverage_areas[]` (zasięg 4326 każdego aktu, `has_boundary`, `is_complete` z
  agregatu BK-405).
- Ponowienie („Ponów wczytanie warstwy”) pobiera metadane i odświeża kafle
  (`refreshTiles`) bez usuwania wyświetlonych danych; to samo wydanie zachowuje
  obiekt (źródło mapy nie jest odtwarzane), nowsze wydanie jest przełączane
  wyłącznie jawnie.
- Styl projektu: artefakt `shared/pog-presentation.json` (`style_version`
  `2026.09.29-1`) dostał w `legal_statuses` pola `pattern` i `badge`. Projekt i
  akt w toku: słabsze krycie + obrys przerywany + wzór `horizontal-lines`
  (osobna warstwa `pog-zones-status-pattern`) + stała plakietka „projekt / dane
  niewiążące”; akt wiążący — bez wzoru, z opisem „akt obowiązujący”. Oba
  adaptery (TS i Pydantic) odrzucają artefakt, w którym projekt nie ma wzoru i
  plakietki albo akt wiążący je ma.
- Plakietki zależą od aktów wydania, nie od stanu warstwy — pozostają widoczne
  przy awarii kafli i w stanie `stale`. Edycja (wszystkie / obowiązujące /
  projekty) to jawna grupa radiowa obsługiwana klawiaturą.
- Żaden komunikat nie używa frazy „brak planu” (brak urzędowego potwierdzenia
  w lokalnym wydaniu); dozwolone jest zastrzeżenie ADR-002 „nie oznacza braku
  planu”. Warstwy podglądowe WMS dostały ten sam słownik stanów.

## Alternatywy

- **Liczenie agregatów w HTTP z cache.** Pierwsze żądanie po wdrożeniu
  obciąża bazę, a cache może się rozjechać z aktywnym wydaniem. Odrzucone.
- **Osobny endpoint analizy „lekkiej” zamiast inspektora z kafla.** Dublowałby
  analizę i wymagał żądania przy każdym kliknięciu. Odrzucone — kafel ma już te
  same atrybuty (ADR-007), a szczegóły są pobierane leniwie.
- **Stan pokrycia z pustego kafla.** Pusty kafel nie odróżnia braku danych od
  braku obiektu i błędnie sugerowałby brak planu. Odrzucone.

## Konsekwencje

- `POST /analyze` wykonuje się tylko po świadomej decyzji użytkownika.
- Import wydania POG trwa dłużej o obliczenie agregatów (jedna transakcja);
  statystyki `import_runs.stats.area_summaries(_incomplete)` i ostrzeżenia
  `pog_aggregate_incomplete:<akt>:<przyczyny>` są w wyniku importu.
- Zmiana stylu (nowe pola) podbiła `style_version`; analizy sprzed zmiany
  rysują się zapisanym snapshotem stylu (ADR-007).
