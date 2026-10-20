# Odbiór AU-202–204 / Task 23.2–23.4

Implementacja: 2026-10-17; końcowy odbiór: 2026-10-19. Zmiany wyłącznie w drzewie roboczym; bez commitów. Współdzielony katalog zawiera też równoległe
prace AU-112/113/201 — niniejszy protokół opisuje drogę, zabudowę i pokrycie terenu, bez przypisywania pozostałych zmian.

## Zakres

Droga i zabudowa były już zintegrowane przed tym zadaniem (ADR-025, protokół z 2026-10-13). Ponowny przebieg
115 testów tych modułów był zielony. Uzupełniono PostGIS KNN, jawny unknown wskaźników sąsiednich działek,
pomiar działki 141801_4.0701.23/8 oraz testy regresji API/PDF/ZIP.

Pokrycie terenu dodano od importu do odbioru: dwanaście klas PT → wersjonowany PostGIS → port analizy → API →
JSONB snapshot → cache → macierz jakości/kompletność → UI/warstwa/legenda → PDF/mapa/tabele → warstwy pochodne ZIP.
Kontrakt i metoda: `docs/data_sources/land_cover_contracts.md`; decyzja: ADR-028.

## Pomiary niezależne

11 rzeczywistych geometrii z 3 powiatów (1261, 2816, 1418). Oczekiwania odczytane z oryginalnych GML bez GDAL,
PostGIS i kodu aplikacji; osobny skrypt Shapely porównany z produkcyjnym PostGIS. Udziały obu obszarów 100% ±0,01 pp;
odległości dróg i lasów ±0,05 m; pola ±0,05 m². Dodatkowo **QGIS 4.2.3 (2026-10-19)** odczytał te GML przez OGR i potwierdził wszystkie 11 działek,
w tym odległości i relacje dróg (wymagane ≥6). Skrypt nie importuje aplikacji, Shapely ani PostGIS.
Wynik: `docs/evaluation/results/au202-204/qgis-cross-check.json`; odtwarzanie:
`backend/scripts/verify_land_cover_with_qgis.py` w oficjalnym obrazie QGIS przypiętym digestem z wyniku.
Maksymalna różnica QGIS względem oczekiwań Shapely: drogi/lasy/budynki **0 m**, pola **1,40×10⁻⁷ m²**.
[Oficjalny obraz QGIS](https://github.com/qgis/qgis-docker); polecenie z katalogu repozytorium
(katalog wyjściowy musi być zapisywalny):

```sh
docker run --rm --platform linux/amd64 --network none --entrypoint /usr/bin/python3 \
  -e QT_QPA_PLATFORM=offscreen -v "$PWD/backend:/work:ro" -v "$PWD/docs/evaluation/results/au202-204:/out" \
  qgis/qgis@sha256:56c69c92e316aa679e5c30ebf7fcfd89fd1124ec728106ec0cced1c71a28d877 \
  /work/scripts/verify_land_cover_with_qgis.py \
  --fixtures /work/tests/fixtures/source_contracts/bdot_land_cover --output /out/qgis-cross-check.json
```

Dodatkowo istniejące testy drogi porównują 30 działek i mają wcześniejszą niezależną kontrolę GDAL.

Działka 141801_4.0701.23/8: **10 budynków do 50 m**, najbliższy budynek 14,53 m; las **12,73 m**.
Bufory 8/12 m: 0 m² działki, 16 m: 23,67 m². Różnica względem 11 budynków OnGeo pozostaje jawnym sanity checkiem
między migawkami, bez wymuszania liczby w implementacji. Ruciane-Nida 281604_5.0011.107 (ok. 26,6 ha): las 0 m.

## Live smoke i artefakty

Izolowany PostGIS i backend; trzy pełne oficjalne paczki, nie mini-paczki testowe. Import 1261/2816/1418:
22 731 / 20 236 / 26 187 obiektów pokrycia. Wydania mają źródłowe SHA-256 i daty stanu danych.

`python backend/scripts/live_smoke_corpus.py --base-url <izolowany backend> --concurrency 3 ...`:
**30/30 HTTP 200, 5xx=0, brak odpowiedzi=0**. Dla 10 działek z powiatów zaimportowanych droga/zabudowa/pokrycie są
available; dla pozostałych 20 no_coverage. Status nagłówkowy partial wynika również z pozostałych sekcji źródłowych,
nie jest wymuszany przez nową sekcję. Pierwszy przebieg: `docs/evaluation/results/au202-204/live-smoke.{json,md}`.
Powtórzenie 2026-10-19 po sprawdzeniu `/health/ready` i unieważnieniu cache wyłącznie w izolowanej bazie testowej: ponownie **30/30 HTTP 200, 5xx=0, brak odpowiedzi=0**,
`docs/evaluation/results/au202-204/live-smoke-2026-10-19.{json,md}`.

Dodatkowy rzeczywisty POST dla 141801_4.0701.23/8: HTTP 200, wszystkie trzy nowe sekcje available.
PDF i ZIP pobrano przez podpisane odnośniki API. `verify_audit_package.py`: **OK, 12 plików**.
ZIP ma `layers/land_cover.geojson`, `layers/forest_context.geojson`, drogi i budynki; redystrybucję kontroluje katalog.
Przeglądarka produkcyjnego frontendu: karta pokazuje źródło, datę, udziały, las i bufory; przełącznik warstwy jest
sprawdzany testem integracyjnym ResultPanel oraz ręcznie przez UI (wyłącz/włącz). Formularz uruchomiono przyciskiem
„Analizuj działkę” na końcowym obrazie produkcyjnym. Podgląd PDF obejmuje mapę z legendą i obie tabele udziałów oraz
budynki/drogę; układ skontrolowano po renderowaniu stron 10–12 i 15–17 do PNG (raport ma 33 strony).
Osobno sprawdzono 146510_8.0502.1/3 bez pokrycia: API/UI/PDF mają no_coverage także dla pomiaru lasu,
odległość null, brak etykiety starego zapisu; ZIP **OK, 7 plików**. Regresja używa rzeczywistej geometrii i zamrożonego źródła.
Usunięto także wyścig inicjalizacji mapy w teście BK-406 — zdarzenie kafla czeka na podłączenie obsługi.
Benchmark AU-106 ma kontrolowaną latencję 150 ms zamiast 30 ms, aby koszt parsowania na współdzielonym CPU
nie dominował pomiaru równoległego I/O; wymagany próg ≤60% pozostaje bez zmian. Osobny moduł: **10 testów — sukces**.
Bezpieczny wyciąg wartości, SHA-256 PDF/ZIP i wynik kontroli manifestu:
`docs/evaluation/results/au202-204/live-proof-2026-10-19.json` (bez tokenów i podpisanych URL).

## Bramki automatyczne

Frontend: **59 plików / 683 testy**, lint, typecheck i produkcyjny build — sukces.
Pokrycie linii **98,98%**; nowy `LandCoverCard.tsx` **100%**, `ResultPanel.tsx` **94,76%**.
Backend: **5351 testów — sukces**, 3 testy `docker_cli` pominięte zgodnie z poleceniem.
Pokrycie **95,33%** (próg 80%); nowe pliki pokrycia terenu **96–100%**. Pełny przebieg: 748,60 s.
Ostrzeżenia dotyczą istniejącej deprecjacji klienta HTTP FastAPI oraz przyszłego wymogu HarfBuzz-Subset w WeasyPrint; bez błędów.
Końcowy przebieg skupiony: **27 testów**, Ruff bez uwag, mypy **17 bazowych / 17 obecnych** (brak regresji).
Weryfikacja odbywa się na zamrożonej kopii repozytorium bez `.env`, aby równoległe zmiany nie zmieniały kodu w trakcie testów.
Polecenia:

- Backend: obraz testowy, REPO_ROOT=/repo, kopia repo bez .env montowana tylko do odczytu,
  `pytest -m 'not docker_cli' --cov=app --cov-report=term-missing --cov-fail-under=80`.
- Frontend: `docker build --build-context shared=./shared --target test -t dzialki-frontend-test ./frontend`,
  następnie `npm run typecheck && npm run test:coverage && npm run build` w kontenerze.
- `ruff check .`, `python scripts/check_mypy_baseline.py`, `git diff --check`.

## Wdrożenie

**Aktualizacja 2026-10-19:** migracje oraz import siedmiu pełnych paczek wykonano już w głównej lokalnej bazie
na osobne polecenie użytkownika. [Protokół wdrożenia i E2E](au202-204-local-deployment-2026-10-19.md):
30/30 HTTP 200, droga/zabudowa/udziały dostępne na 30 działkach, las 27 dostępnych / 3 częściowo poza zasięgiem;
PDF/ZIP, UI i zachowanie dziewięciu wcześniejszych analiz potwierdzone. Poniższy opis odnosi się do wcześniejszego
odbioru na izolowanych bazach i wymagań dla kolejnych instalacji.

Migracja 039 po 038 dodaje tabelę i nullable snapshot, bez przepisywania historii. Aktualny wspólny łańcuch ma też
późniejszą migrację 040 z równoległej pracy AU-201. Operator musi wykonać migracje i ponownie zaimportować pełne
paczki starszych wydań: sam deploy nie uzupełnia danych. Starszy import daje no_coverage/LAND_COVER_NOT_IMPORTED.
Statusy są rozłączne; testy obejmują timeout, pusty wynik, brak klasy PT, zmianę CRS i uszkodzony snapshot.
W API/PDF nie ma pola prawnego dostępu do drogi; obowiązkowa nota interpretacyjna pozostaje w API/UI/PDF/ZIP.
