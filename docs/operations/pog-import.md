# Runbook: import POG z Rejestru Urbanistycznego

Dwa polecenia dla źródła `pog_app`:

* **`pog-national`** — import dla całego kraju (albo wybranych gmin) z plików GML całych aktów, krajowy rejestr aktywnych wersji, harmonogram i import na żądanie
  ([ADR-026](../adr/ADR-026-krajowy-stan-pog-rejestr-wersji-aktow.md); część A poniżej);
* **`pog`** — import jednej gminy z WFS RU, równoległy, z postępem i wznawianiem (AU-106, [ADR-024](../adr/ADR-024-import-pog-rownolegly-wznawianie-postep.md)); kanał zapasowy (część B).

Kontrakt usług RU: [ru_contracts.md](../data_sources/ru_contracts.md). Dane wynikowe: lokalny, wersjonowany **stan krajowy POG** (strefy, OUZ, obszary śródmiejskie, granice aktów) używany przez warstwę mapy,
inspektor, pokrycie i analizę powierzchniową. Odbiór i pomiary: [pog-national-verification.md](../evaluation/pog-national-verification.md).

# Część A. Import krajowy (`pog-national`)

## A1. Zasilenie całego kraju

```bash
# 1) podgląd planu (wykaz RU, nic nie jest pobierane ani zapisywane)
docker compose exec backend python -m app.modules.imports pog-national --plan-only
# 2) pierwsze zasilenie: wszystkie akty, 3 równolegle (limit grzeczności wobec RU), postęp z ETA na stderr
docker compose exec backend python -m app.modules.imports pog-national --concurrency 3 > pog-national.json
# 3) stan: rejestr, wykaz RU, ostatni przebieg, kolejka
docker compose exec backend python -m app.modules.imports pog-status
```

Pomiar 2026-10-13 (żywy RU, współbieżność 3): **435 aktów w 23,2 min, 3,0 GB pobrane, 440 żądań do RU (0 × HTTP 429)**; po przebiegu baza **3,96 GB** i wolumen artefaktów **3,0 GB** — zaplanuj ok. **7 GB** (plus cache kafli
do 5 GB, `MAP_TILE_CACHE_MAX_BYTES`, i miejsce na kopie zapasowe). Pierwszy przebieg można przerwać (Ctrl-C/SIGTERM) i uruchomić ponownie tym samym poleceniem: akty z tokenem w rejestrze są pomijane.

| Opcja | Znaczenie |
|---|---|
| `--terc 246101` (powtarzalna albo po przecinku) | Tylko wskazane gminy (6 cyfr, kod 7-cyfrowy jest skracany); filtr działa przed `--limit` i ogranicza także wycofania. |
| `--limit N` | Najwyżej N aktów do pobrania (próbka). |
| `--full` | Pobiera wszystkie akty, nie tylko nowe i zmienione (tokeny w rejestrze są ignorowane). |
| `--concurrency N` | Równoległe akty 1–6 (domyślnie `POG_NATIONAL_CONCURRENCY` = 3). Żądania do RU i tak są rozstawiane (`RU_PUBLISHED_MIN_INTERVAL_SECONDS` = 1,2 s). |
| `--dry-run` / `--plan-only` | Pobiera i waliduje bez publikacji / tylko wykaz i plan. |
| `--no-withdraw`, `--allow-mass-withdrawal` | Nie dezaktywuj aktów wycofanych z RU / zezwól na wycofanie więcej niż max(5, 10%) aktów z tokenem RU naraz. |
| `--no-csw`, `--allow-regression`, `--progress-interval S` | Bez metadanych CSW (szybciej) / pomiń odrzucenie przy spadku liczby obiektów aktu o ponad 50% / odstęp postępu. |

**Wyjście:** stderr — linia postępu co ≤ 10 s (`[import-krajowy] import 892 s, akty 272/435 (błędy 1), pobrano 1786.2 MB, w toku: Główczyce, …, ETA 8 min 54 s`); stdout — **jedna linia JSON**
(`status`, `plan`, `counts`, `failure_codes`, `failures`, `catalog`, `withdrawal`, `timing`, `registry_state_before/after`, `import_run_id`, `exit_code`, `resume`). **Kody wyjścia:** `0` — komplet;
`1` — błąd (wyjątek, wykaz niedostępny); `2` — błąd użycia; **`3` — część aktów nie zaimportowana albo przebieg przerwany** — uruchom to samo polecenie ponownie.

## A2. Kody przyczyn nieudanych aktów

| `failure_codes` | Znaczenie | Co dalej |
|---|---|---|
| `qa_rejected` | Kontrola jakości odrzuciła dane źródła (np. udział procentowy > 100% w jednej strefie); komunikat wskazuje obiekt i wartość | Wada po stronie wydawcy aktu — zgłosić; ponowienie nic nie zmieni. Gmina zostaje „plan w RU — jeszcze niezaimportowany” z opisem przyczyny w pokryciu. |
| `rate_limited` | RU ograniczyło liczbę żądań mimo odczekań | Ponowić później; zmniejszyć `--concurrency` / zwiększyć `RU_PUBLISHED_MIN_INTERVAL_SECONDS`. |
| `not_xml`, `too_large`, `transport` | Odpowiedź nie jest GML / plik > `RU_PUBLISHED_MAX_GML_BYTES` (160 MB) / źródło niedostępne | Ponowić; przy `too_large` podnieść limit świadomie. |
| `gml_terc_mismatch`, `gml_no_act`, `gml_multiple_acts` | Plik nie jest dokładnie jednym aktem gminy z wykazu | Wada pliku RU — zgłosić. |

Przypadek z żywego przebiegu: akt 3438 (Bolków, TERC 020502) — `PL.ZIPPZP.11945/020502-POG/1POG-51SU: Parametr procentowy POG musi należeć do 0–100 (wartość: 150).` Pierwszy przebieg próbny odrzucił też
Jejkowice kodem `not_xml`, bo plik RU zaczyna się od BOM UTF-8; poprawione (BOM jest dozwolony przed deklaracją XML), objęte testem.

## A3. Harmonogram i import na żądanie

```bash
docker compose --profile sync up -d pog-sync        # usługa: kolejka zleceń + import przyrostowy co POG_SYNC_INTERVAL_SECONDS (domyślnie 24 h)
docker compose exec backend python -m app.modules.imports pog-sync --once   # jeden cykl ręcznie
docker compose exec backend python -m app.modules.imports pog-enqueue --terc 246101   # zlecenie importu gminy
```

Cykl działa pod blokadą doradczą `pog-sync`: druga instancja (albo ręczne `--once` obok usługi) kończy się statusem `locked` zamiast dublować pobieranie. Zlecenia z kolejki (`import_jobs`) mają deduplikację per gmina,
dzierżawę, trzy próby z odroczeniem; wada danych źródła (`qa_rejected`, `gml_*`) kończy zlecenie od razu. Analiza działki w gminie, której RU ma plan, a lokalnego stanu brak, zleca import i mówi o tym w odpowiedzi
(`POG_IMPORT_QUEUED` / `POG_IMPORT_PENDING` / `POG_IMPORT_FAILED_RECENTLY`). Stan: `GET /health/imports` (rejestr, wykaz RU, ostatni przebieg krajowy, opóźnienie, kolejka, `stale` po przekroczeniu dwóch interwałów).

## A4. Operacje na rejestrze

```bash
python -m app.modules.imports pog-withdraw --act-id PL.ZIPPZP.10169/241203-POG/1POG [--reason withdrawn|not_current]   # dezaktywacja (historia zostaje, nowy stan „withdrawal”)
python -m app.modules.imports pog-rollback --act-id … [--to-version-id N]                                              # cofnięcie do wcześniejszej wersji (nowy stan „rollback”)
python -m app.modules.imports pog-map-rebuild                                                                          # tabele mapy dla wersji, którym ich brakuje (po migracji 037)
```

Każda operacja drukuje jedną linię JSON. Po migracji `037_pog_registry` istniejące dane POG są w pierwszym stanie (`migration`), a mapa jest niepełna (`map_ready = false`, komunikat w panelu), dopóki nie wykona się
`pog-map-rebuild` (usługa `pog-sync` robi to sama na początku cyklu).

## A5. Pomiary i kontrola

```bash
python3 backend/scripts/measure_pog_tiles.py --base-url http://127.0.0.1:8000 --zooms 6,11,14 --samples 250   # czasy i rozmiary kafli (limit API: 1200/min)
curl -s localhost:8000/api/v1/coverage/pog | jq .summary                                                      # gminy: w RU / zaimportowane / czekają
curl -s localhost:8000/api/v1/map/pog/state | jq '{state_id, act_count, map_ready, acts_by_legal_status}'
```

Stan 2026-10-13 po imporcie krajowym: stan #464, 464 aktywne akty w 461 gminach (462 gminy mają bieżący akt w RU; Bolków czeka na poprawkę danych), 518 338 obiektów; statusy prawne aktów
(`binding` 327, `in_progress` 112, `project` 25). Kafle: zimne p95 niepustych kafli z6 13 ms, z11 57 ms, z14 30 ms; największy kafel 199 KB.

# Część B. Import jednej gminy z WFS (zapas)

## B1. Polecenie

```bash
docker compose exec backend python -m app.modules.imports pog --source pog_app --act-id 246101-POG --teryt 246101
```

| Opcja | Znaczenie |
|---|---|
| `--dry-run` | Pobiera i waliduje komplet danych, **nic nie publikuje** (kod wyjścia 0, status `dry_run_only`). Pobrane strony zostają na dysku, więc następny przebieg bez `--dry-run` nie pobiera ich ponownie. |
| `--concurrency N` | Równoległe żądania stron WFS, 1–8 (domyślnie `IMPORT_WFS_CONCURRENCY` = 4). RU zwraca strony po 10 obiektów, więc to jedyna dźwignia czasu — patrz §B5 o granicy skuteczności. |
| `--fresh` | Porzuca zapisane strony i checkpoint poprzedniego przebiegu (wiersz dostaje status `abandoned`) i pobiera wszystko od zera. |
| `--no-resume` | Nie zapisuje stron do wznawiania (przerwany import trzeba będzie powtórzyć od początku). |
| `--progress-interval S` | Odstęp komunikatów postępu na stderr (domyślnie `IMPORT_PROGRESS_INTERVAL_SECONDS` = 10 s). |

## B2. Co widzi operator

- **stderr** — linia postępu co ≤ 10 s, także gdy żadna strona nie dotarła (żywotność procesu jest informacją):

  ```
  [import] download 20 s, app-pog:StrefaPlanistyczna 90/1168, strony 9/117, razem 322/1400 obiektów, ETA 1 min 07 s
  [import] download 110 s, app-pog:StrefaPlanistyczna 1010/1168, strony 101/117, razem 1210/1400 obiektów, ETA 0 min 13 s
  [import] done 123 s, typy gotowe: 6/6, razem 1400/1400 obiektów, ETA 0 s
  ```

  ETA liczy się z tempa pobierania z sieci (strony wznowione z dysku nie zawyżają tempa); „n/d” oznacza brak danych — nie zgadujemy liczby obiektów typów, których jeszcze nie odpytano.
- **stdout** — dokładnie jedna linia JSON, także przy błędzie i przerwaniu: `status`, `stats`, `warnings`, `import_run_id`, `data_release_id`, `timing` (czas całkowity i faz
  `download`/`parse`/`metadata`/`validate`/`publish`), `progress` (per typ: `matched`, `fetched`, strony, strony z magazynu, czas), `exit_code` oraz — gdy import można wznowić — `resume`.
- **Kod wyjścia:** `0` — import zakończony (także `--dry-run`); `1` — błąd (odrzucone QA, nieoczekiwany wyjątek; `status` = `rejected` / `failed`); `2` — błąd użycia;
  **`3` — import niepełny** (`status` = `interrupted` po SIGINT/SIGTERM albo `incomplete`, gdy pobieranie nie zostało ukończone, np. awaria usługi). Kod 3 znaczy: **uruchom to samo polecenie ponownie**.

## B3. Wznawianie

Pobrane strony są zapisywane bajt w bajt (SHA-256 każdej strony) w `$IMPORT_ARTIFACT_STORAGE_DIR/resume/<klucz>/<typ>/`, a stan w `import_runs.checkpoint`
(wiersz `importer_version = pog-download-v1`; osobna transakcja niż publikacja). Klucz wznowienia pochodzi z źródła, TERYT, `--act-id`, adresu WFS, typów i filtra — inny import nie
wznowi cudzego stanu.

| `import_runs.status` | Znaczenie | Co dalej |
|---|---|---|
| `running` | Pobieranie trwa albo proces zginął bez sprzątania (SIGKILL, restart kontenera) | Uruchom ponownie — wznowi |
| `interrupted` | Przerwane sygnałem; strony zachowane | Uruchom ponownie |
| `failed` | Błąd (np. awaria RU); strony zachowane | Uruchom ponownie po naprawie przyczyny |
| `fetched` | Komplet stron pobrany, nic nie opublikowano (np. `--dry-run`) | Uruchom bez `--dry-run` — pobierze tylko pierwszą stronę każdego typu |
| `succeeded` | Wydanie opublikowane; strony usunięte | — |
| `abandoned` | Porzucone przez `--fresh` | — |

Bezpieczeństwo wznowienia: pierwsza strona każdego typu jest **zawsze pobierana z sieci** i porównywana z magazynem (`numberMatched`, identyfikatory). Zmiana którejkolwiek
wartości oznacza, że rejestr zmienił stan od poprzedniego przebiegu — zapisane strony tego typu są odrzucane w całości, więc artefakt nie miesza dwóch stanów. Uszkodzona albo
niepełna strona (inny skrót) jest pobierana ponownie. Artefakt źródłowy nie niesie atrybutów zmiennych w czasie (`timeStamp`, `next`), więc **przerwany i wznowiony import daje te same
bajty, ten sam skrót artefaktu i to samo wydanie** co import nieprzerwany (test: `tests/test_pog_import_resume.py`).

Wznowienie na innym hoście bez wolumenu `import_artifacts` nie ma stron — checkpoint w bazie niczego wtedy nie odtworzy i import zaczyna od zera (bez błędu).

## B4. Pomiar czasu (2026-10-12, żywy RU, `--dry-run`)

Czas ścienny polecenia (z uruchomieniem kontenera ok. 2–3 s), przebiegi starego kodu (`fcf6a9b`: pobieranie sekwencyjne po 10 obiektów) i nowego **naprzemiennie**; dane:
[`import-timing.json`](../evaluation/results/au-104-106/import-timing.json).

| Gmina (TERYT) | Cechy APP (obiektów w RU) | Stan wyjściowy | Po zmianie (4 równoległe) | Stosunek |
|---|---:|---:|---:|---:|
| Bielsko-Biała (246101) | 1397 (1400) | **359 s** (6,0 min; dwa przebiegi: 359 s i 359 s) | **187 s** i **123 s** (dwa przebiegi) | 52% i **34%** |
| Legnica (026201) | 983 (986) | 213 s | 105 s | 49% |
| Sopot (226401) | 785 (788) | 205 s | 68 s | 33% |

Kryterium AU-106 „czas importu Bielska-Białej ≤ 60% stanu wyjściowego” jest spełnione w obu przebiegach nowego kodu (52% i 34%); rozrzut wynika z usługi RU. W Bielsku-Białej 96% czasu to
pobieranie 117 stron stref (po 10 obiektów, ok. 2,8 s sekwencyjnie), parsowanie 0,4 s, walidacja z naprawą geometrii w PostGIS 2,3 s. W Legnicy 25 s z 105 s zajmuje pobranie metadanych
CSW (`metadata`) — to osobny, sekwencyjny krok po pobraniu stron.

## B5. Granice skuteczności

- RU **ignoruje `count`** powyżej 10 (sprawdzone dla 5, 10, 50, 100, 1000), więc „większy `count` strony” nie jest dostępny; klient wysyła `count=100` i liczy `startIndex` z faktycznej liczby obiektów.
- Zwiększenie równoległości z 4 do 8 nie skróciło czasu Bielska-Białej (123 s w obu przypadkach); powyżej 8 usługa kolejkuje żądania. Domyślne 4 to limit grzeczności.
- Klient ma limity bezpieczeństwa: 500 stron i 10 000 obiektów na typ. Przy 10 obiektach na stronę RU oznacza to maks. 5 000 obiektów typu — import bardzo dużej gminy zakończy się
  błędem limitu (`status` = `incomplete`), a nie cichym obcięciem.

## B6. Diagnostyka

```bash
# ostatni przebieg pobierania danej gminy
docker compose exec db psql -U app -d dzialki -c "select id, status, started_at, finished_at, checkpoint->'progress'->>'objects_fetched' as fetched, checkpoint->>'resumes' as resumes from import_runs where importer_version='pog-download-v1' order by id desc limit 5;"
# strony zapisane do wznawiania
docker compose exec backend sh -c 'du -sh "$IMPORT_ARTIFACT_STORAGE_DIR"/resume/* 2>/dev/null'
```
