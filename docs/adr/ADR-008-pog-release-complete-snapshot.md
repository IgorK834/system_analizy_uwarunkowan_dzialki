# ADR-008: Wydanie POG jest kompletnym snapshotem wszystkich aktów paczki

- Status: Zaakceptowany
- Data: 2026-09-28
- Zakres: BK-104 (wersjonowany import POG), poprawka zgłoszona przy BK-401
- Powiązane: ADR-003 (łańcuch provenance aktu), ADR-007 (kafle MVT przypięte do wydania)

## Kontekst

`SqlAlchemyImportRepository.publish_pog` pomijał akt, którego treść
(`content_hash` snapshotu) była równa ostatniej aktywnej wersji z dowolnego
wydania. Nowe wydanie nie dostawało więc wiersza `planning_act_versions` dla
niezmienionych aktów, a mimo to było aktywowane. Wszyscy czytelnicy
(`load_pog_release_features`, `find_pog_acts_for_parcel`,
`find_pog_intersections`, kafle MVT) filtrują po `pav.data_release_id`, dlatego
po imporcie, w którym zmienił się tylko jeden akt — albo zmieniły się jedynie
bajty artefaktu (np. `timeStamp` odpowiedzi WFS) — aktywne wydanie traciło
pozostałe akty: analiza i mapa pokazywały brak danych tam, gdzie plan istnieje.
Kontrola regresji liczby cech (`previous_pog_feature_count`) porównywała też
nową paczkę z niepełnym wydaniem.

Odtworzenie na PostGIS: wydanie A = {obowiązujący akt Sopotu, projekt}, wydanie
B = {ten sam akt Sopotu, zmieniony projekt} → B zawierało tylko projekt.

## Decyzja

**Każde opublikowane wydanie zawiera własną wersję każdego aktu z paczki.**

Dla każdego aktu paczki w `publish_pog`:

| Sytuacja | Działanie | Statystyki |
|---|---|---|
| Wersja o tym samym `content_hash` już należy do **tego** wydania (ponowny import identycznego artefaktu) | nic nie dopisujemy | `unchanged` |
| Ostatnia aktywna wersja (inne wydanie) ma ten sam `content_hash` | nowa wersja w tym wydaniu z tym samym `content_hash`, zapisana z rekordu paczki (granica, cztery warstwy, dokumenty, metadane CSW); poprzednia wersja dostaje `valid_to` | `unchanged` + `carried_forward` |
| Treść zmieniona | nowa wersja; poprzednia dostaje `valid_to` | `changed` |
| Akt nowy | nowa wersja | `new` |

- Wersja przeniesiona jest zmaterializowana (kopia wierszy zapisana z tego
  samego, zwalidowanego rekordu paczki, więc identyczna z poprzednią). Dzięki
  temu czytelnicy i indeksy GiST pozostają bez zmian, a `release_id` odtwarza
  pełny stan analizy i mapy (ADR-007).
- Model temporalny jest zachowany: częściowy indeks unikalny
  `uq_planning_act_versions_active` pozostaje „najwyżej jedna aktywna wersja
  aktu w obrębie wydania”, a w rejestrze aktywna jest wersja z najnowszego
  wydania. Wiersze poprzedniego wydania nie są usuwane ani zmieniane poza
  `valid_to` (audyt, rollback przez ponowną aktywację).
- Idempotencja: identyczny artefakt trafia w to samo wydanie
  (`_release_row` po odcisku artefaktu i konfiguracji), a wszystkie jego akty są
  już w tym wydaniu — żadnych nowych wersji. Ponowny import artefaktu, którego
  wydanie powstało przed tą poprawką, uzupełnia brakujące akty w tym wydaniu.
- `import_runs.stats.carried_forward` jawnie liczy akty przeniesione bez zmian.

## Alternatywy

- **Tabela asocjacyjna wydanie ↔ wersja aktu** (bez kopiowania geometrii).
  Oszczędza miejsce, ale wymaga zmiany wszystkich zapytań analizy, pokrycia,
  provenance i kafli MVT oraz nowej migracji i indeksów; ryzyko rozjazdu
  czytelników jest większe niż koszt kopii dla wolumenu MVP (wybrane gminy).
  Odrzucone na tym etapie; do rozważenia przy imporcie krajowym.
- **Czytanie „ostatniej aktywnej wersji” zamiast filtra po wydaniu.** Łamie
  odtwarzalność historycznego `release_id` i przypięcie URL kafli. Odrzucone.

## Konsekwencje

- Każdy `release_id` jest kompletny i samowystarczalny; kafle MVT i analiza
  wydania nie zależą od historii importów.
- Koszt: geometrie niezmienionych aktów są przechowywane raz na wydanie.
- Brak migracji schematu. Wydania utworzone przed poprawką mogą być niepełne;
  naprawia je ponowny import ich artefaktu albo nowy import.
- Dowód: `test_new_release_is_complete_snapshot_including_unchanged_act` i
  `test_reimport_of_identical_artifact_adds_no_versions_and_repairs_nothing_twice`
  (`backend/tests/test_map_tiles.py`, PostGIS + HTTP).

## Aneks (2026-10-13, [ADR-026](ADR-026-krajowy-stan-pog-rejestr-wersji-aktow.md)): wydanie POG nie jest już stanem mapy

Decyzja tego ADR (wydanie = kompletny snapshot *wszystkich aktów paczki*, niezmienione akty kopiowane do nowego wydania) zakładała **jedno aktywne wydanie na źródło**. Przy imporcie
kilku gmin to założenie jest błędem: `_activate_release` wygaszał inne wydania, a `publish_pog` przenosił do nowego wydania tylko akty z importowanej paczki, więc import gminy B
odcinał gminę A od analizy, kafli i inspektora (odtworzone testem `test_pog_multi_commune.py`, zapis czerwonego przebiegu: `docs/evaluation/results/pog-national/regression-before-fix.txt`).

Dla źródła `pog_app` ta decyzja jest **zastąpiona przez ADR-026**:

- wydanie (`data_releases`) jest **rekordem audytowym jednego przebiegu/artefaktu** (kto, kiedy, jaki SHA, ile aktów) i dla `pog_app` zostaje nieaktywne (`is_active = false`) — nie wyznacza widocznych danych;
- widoczność wyznacza **stan krajowy**: zbiór aktywnych wersji aktów w temporalnym rejestrze `pog_registry_entries`; **nie ma kopii „przeniesionych” aktów** (każda wersja aktu jest zapisana raz, wpisem rejestru
  jest okres jej aktywności);
- dalej obowiązuje to, co było wartościowe w ADR-008: wersja aktu jest niezmienna i samowystarczalna, ponowny import identycznej treści jest idempotentny (żadnych nowych wersji — zmienia się najwyżej token RU),
  a zapisany stan można odtworzyć w dowolnej chwili — teraz po numerze stanu (`state_id`), a nie po `release_id`;
- dla MPZP, BDOT10k i parcel ADR-008 nie jest zmieniany (tam wydanie nadal bywa aktywne i kompletne).

Migracja `037_pog_registry` zamienia dotychczasowe aktywne wydanie POG w pierwszy stan krajowy (rodzaj `migration`) i przywraca akty ukryte przez dawny model (test danych `test_migration_037_data.py`).
