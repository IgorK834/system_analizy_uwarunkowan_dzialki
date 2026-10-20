# Odbiór AU-112, AU-113 i AU-201 (Task 22.12, 22.13, 23.1)

Stan kodu i końcowa weryfikacja: 19.10.2026. Odpowiedzi źródłowe i pomiar
pełnych paczek zamrożono 17.10.2026. Baza git: `fffdafe7e03464ffc39506054955307bce836db5`,
zmiany w drzewie roboczym. Zachowano równoległą pracę AU-202–204 nad drogami,
budynkami i pokryciem terenu; migracja 040 następuje po jej migracji 039.

## Kryteria i dowody

| Zadanie | Wynik | Dowód |
|---|---|---|
| AU-112: wynik dokładny BK-602 w ≥ 3 gminach | **13 działek / 3 gminy**: Bielsko-Biała 7, Legnica 5, Sopot 1; dwa identyczne przebiegi | [raport](results/au112-113/centroid/report.md), [JSON](results/au112-113/centroid/summary.json), [determinizm](results/au112-113/centroid/determinism.json) |
| AU-112: legacy CRS i `NULL` | parser produkcyjny przyjmuje oryginalny legacy GML RU; `NULL` już odrzucał obecny adapter KIMPZP — potwierdzono jawną regresją na Piszu | `test_real_ru_legacy_srs_is_parsed_without_a_freezing_tool_rewrite`, `test_real_pisz_null_is_never_a_zone_symbol` |
| AU-113: ≥ 5/6 zapytań z audytu | **6/6**, ponadto **20/20** wariantów na UUG i przez endpoint API przy pustym indeksie; powtórzono 19.10 | [API 19.10](results/au112-113/addresses-e2e-2026-10-19.json), [API 17.10](results/au112-113/addresses-e2e.json); surowe odpowiedzi: `backend/tests/fixtures/uug/au113/manifest.json`; `test_au113_address_fallback.py` |
| AU-113: wskaźnik ograniczenia | `/addresses/status` zwraca `ready=false`; komunikat widoczny również nad otwartą listą wyników | [UI](results/au112-113/addresses-ui.png), `SearchPanel.test.tsx`, `test_empty_active_release_is_not_ready` |
| AU-201: wersjonowany import czterech grup | pełne paczki 1261, 0262, 2461 → drogi, budynki, pokrycie terenu i linie energetyczne; ponowienie tego samego hash daje te same wydania i liczności | [pomiary importów](results/au112-113/bdot-full-imports.json), [ADR-029](../adr/ADR-029-bdot10k-import-czterech-warstw.md) |
| AU-201: potwierdzony kontrakt i geometria | `bdot10k: contract_confirmed`; trzy rzeczywiste klasy SULN z hashami; długości z GML policzone niezależnie od GDAL zgadzają się z PostGIS | `test_bdot_powerlines_import.py`, `backend/tests/fixtures/source_contracts/bdot10k_base/` |
| AU-201: pokrycie i kolejka | liczności czterech grup w `/coverage` i UI; POST → deduplikowane zlecenie → worker → publikacja; rzeczywiste zlecenie Legnicy zakończone `succeeded`, `created=0` | [log workera](results/au112-113/bdot-queue-live.txt), [UI](results/au112-113/bdot-coverage-ui.png), `test_bdot_county_jobs.py`, `pog-sync --once --no-schedule` |
| AU-201: API, snapshot, PDF, audyt | działka Krakowa: droga, budynki i pokrycie `available`; podpisane pobranie PDF i ZIP: HTTP 200; manifest **11/11** zgodny, powtórzono 19.10 | [wynik API bez tokenu](results/au112-113/live-artifacts-2026-10-19/result.json), [PDF](results/au112-113/live-artifacts-2026-10-19/report.pdf), [ZIP](results/au112-113/live-artifacts-2026-10-19/audit.zip), [skróty](results/au112-113/live-artifacts-2026-10-19/summary.json) |
| Błędy źródła | timeout, zmiana schematu, uszkodzona geometria lub współrzędne nie dają pozornego sukcesu ani surowego 500 | regresje UUG, importów, kolejki i sekcji BDOT10k |

## BK-602: zakres pomiaru

Przetestowano wszystkie 30 działek pierwotnego korpusu. Dla 18 zachowano
potwierdzone puste odpowiedzi WFS (to brak cech w zapytaniu, nie dowód braku
planu). Dla 12 są rzeczywiste strefy. Sopot jest osobnym rozszerzeniem
`centroid_corpus`, z rzeczywistą geometrią ULDK `226401_1.0002.2/66`;
pierwotny korpus i jego historyczna prawda nie zostały zmienione.

Centroid pomija strefę w **5/13** przypadków, a strefa centroidu jest inna niż
dominująca w **1/13**. W Legnicy (`real-013`) centroid leży poza działką
i pomija 100% jej powierzchni. Wynik obejmuje geometrię, nie ocenę mocy
prawnej aktu ani parametrów urbanistycznych. Nadal nie redystrybuujemy
wektora MPZP Krakowa.

Zapytania używają URN w bbox (północ,wschód) oraz krótkiego `srsName=EPSG:2180`
dla odpowiedzi (wschód,północ). Zachowano surowe strony z SHA-256;
Sopot wymaga dwóch stron, bo RU ogranicza je do 10 cech. `--resume` ponawia
tylko błędy. Testy i porównanie działają offline na zamrożonych danych.

## Adresy

Normalizacja zachowuje treść prezentowaną użytkownikowi; zmienia wyłącznie
wariant żądania UUG. Obsługuje kolejność, przecinek, odstępy, NFC, `ul.` i `al.`.
Nazwy gmin z zamrożonego PRG pomagają przywrócić polskie znaki miejscowości;
nie jest to import punktów adresowych ani pełny słownik wszystkich wsi.
Dla nieznanej nazwy jest najwyżej sześć wariantów. Numer nie jest usuwany.
Awaria statusu indeksu nie jest prezentowana jako pewny brak importu.

Sonda odtwarzalna:

```bash
python backend/scripts/freeze_uug_address_cases.py --output <katalog-odpowiedzi>
```

## BDOT10k: zakres i wdrożenie

Dotychczasowy import dróg i budynków był już wykonany. AU-201 uzupełnia go
o `OT_SULN_L`, integrację kolejki i jawne liczności pokrycia, zachowując import
12 klas PT z AU-204. Migracja **040**, EPSG:2180, GiST, FK do wydania;
`bdot10k-import/3` i etykiety `-v3`. Nieznany rodzaj linii odrzuca publikację;
brak klasy w starszym wycinku oznacza `null`, a nie potwierdzone 0.
Sam import SULN nie wprowadza analizy prawnych stref sieci ani napięcia w kV.
Sekcje API/UI/PDF/audytu dróg, budynków i pokrycia terenu pozostają zachowane.

```bash
python -m app.modules.imports bdot10k --powiat 1261 --package <pelna-paczka.zip>
python -m app.modules.imports pog-sync --no-schedule
```

Pełne wydania testowano w osobnej bazie, bez modyfikowania istniejącej bazy
użytkownika. Mini-paczki kontraktowe nie służą do publikowania pokrycia.
Pomiary rozmiaru i czasu trzech powiatów oraz zasady odtwarzania i rollbacku
są w ADR-029. Nie zmieniano migracji historycznych.

Kontrola eksportu 19.10 obejmowała render wszystkich 38 stron PDF i szczegółową
inspekcję mapy oraz tabel BDOT10k (strony 15–16, 19 i mapowanie pól na 37).
Nie stwierdzono nakładania ani ucięcia treści. ZIP przeszedł
`verify_audit_package.py`; pliki JSON eksportu nie zawierają `access_token`.

## Weryfikacja automatyczna i live smoke

Końcowe logi i sumy kontrolne testowanego snapshotu są w
`docs/evaluation/results/au112-113/`. Źródła są montowane tylko do odczytu;
kopia repozytorium nie zawiera `.env`. W testach obsługa kolejki POG ma
domyślny stan jak w CI; w odizolowanym API live wyłączono automatyczny import
POG, aby smoke nie uruchamiał importów kraju.

```bash
pytest -m "not docker_cli" --cov=app --cov-report=term-missing --cov-fail-under=80
ruff check .
python scripts/check_mypy_baseline.py
```

Frontend: obraz `--target test`, `npm run lint`, `npm run typecheck`,
`npm run test:coverage`, `npm run build`: **683 testy, 59 plików, 97,99% instrukcji,
98,98% linii**. Powtórzono po ostatnich zmianach pokrycia terenu;
[log](results/au112-113/frontend-final-2026-10-19.txt).
Nowy formularz jest w `coverage.include`.

Pełny backend: **5351 passed, 3 deselected**, pokrycie **95,33%**
([log](results/au112-113/backend-final-2026-10-19.txt)). Moduły objęte zadaniem:
`pog_fetch` 84%, adapter UUG 88%, normalizacja 97%, nowe przetwarzanie linii
100%, worker powiatu 96%, adapter kolejki 94%, router pokrycia 95%.
Ruff: bez zgłoszeń; mypy: brak nowych zgłoszeń (17 w bazie / 17 bieżących).
Skan sekretów: 0 trafień. `git diff --check`: bez błędów.

Pełny przebieg używał niezmiennego snapshotu. W jego trakcie drugi czat
zmienił sześć plików pokrycia terenu i testów; zachowano je. Drugi manifest
zawiera ich listę oraz hashe końcowego kodu:
[snapshot końcowy](results/au112-113/tested-final-snapshot-sha256.json).
Frontend, lint i typy ponownie zweryfikowano w całości na tym kodzie.
Dodatkowa weryfikacja backendu na końcowym snapshotcie: **81 passed**
([log](results/au112-113/backend-delta-2026-10-19.txt)); obejmuje regresje
pokrycia terenu, API–snapshot–PDF–ZIP, jakość i kompletność, import linii,
kolejkę powiatów oraz granice architektury.

Live smoke z 17.10 i ponowienie 19.10: **30/30 HTTP 200, 0 × 5xx, 0 brakujących odpowiedzi**.
Trzy powiaty pokrywają 16 działek — ich trzy sekcje BDOT10k mają `available`;
14 pozostałych ma `no_coverage`. Całość analizy pozostaje `partial`, zgodnie
z brakami innych źródeł; kompletność nadal uwzględnia także nieznane ustalenia
wewnątrz sekcji, np. odległość od lasu. Ponowienie 19.10 używało
`--force-refresh` (bez cache). Po ostatnich zmianach powtórzono cały korpus:
[raport końcowy](results/au112-113/live-smoke-final-2026-10-19.md),
[wyniki końcowe](results/au112-113/live-smoke-final-2026-10-19.json).

