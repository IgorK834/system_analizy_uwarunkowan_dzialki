# BK-001 — dowody punktu startowego

Zapis wykonano **2026-09-23**. Kod bazowy `main`:
`8418bddc73bc76f590e021af122262f0a44bcaa4`; wcześniejszy baseline
backlogu: `64ca3e85ab74ae811280fda01e485e89cdf4f29e`. Pomiary commita i
bieżącego drzewa są rozdzielone. [Status przy wejściu](workspace-at-entry.txt)
zawiera jedynie ścieżki i symbole Git, bez zawartości lokalnego `.env`.

## Środowisko i wynik uruchomienia

| Próba | Polecenie / środowisko | Kod | Wynik |
| --- | --- | ---: | --- |
| Walidacja Compose | `docker compose --project-name bk001-baseline --env-file .env.example -f docker-compose.yml -f scripts/baseline-compose.override.yml config --quiet` | 0 | Składnia i interpolacja konfiguracji poprawna, bez kontaktu z daemonem. |
| Izolowany Docker, czysty commit | `./scripts/capture_baseline.sh` | 1 | Procedura wykonana do końca. Wszystkie kroki poza backend pytest mają kod 0; wynik skryptu 1 zachowuje czerwony stan backendu. |
| Backend, czysty commit w Docker | `pytest -m 'not docker_cli' --cov=app --cov-report=term-missing --cov-report=xml --cov-report=json --cov-fail-under=80 -v` | 1 | 1070 passed, 17 failed, 3 deselected; coverage 88%. Wszystkie awarie wynikają z niedostosowania testów do czwartej sekcji kontekstu NMT. |
| Backend, czysty commit na hoście | to samo polecenie bez Compose/PostGIS | 1 | 981 passed, 56 failed, 63 errors, 1 skipped, 3 deselected. Host `db` jest rozwiązywany tylko w sieci Compose; ten wynik nie zastępuje wyniku Docker. |
| Backend, drzewo robocze na hoście | to samo polecenie | 1 | 979 passed, 58 failed, 63 errors, 1 skipped, 3 deselected. Dwa dodatkowe błędy wynikają z zastanej zmiany katalogu (`gdos` ma inny status) oraz lokalnego ignorowanego `.env`. |
| Zmienione fixtures i CI config | `cd backend && REPO_ROOT=.. python3 -m pytest tests/test_parcel_fixtures.py tests/test_ci_config.py -q` | 0 | 47 passed. |
| Frontend, Docker/Node 22 | obraz `test`, następnie `npm run typecheck` | 0 | Typy poprawne. |
| Frontend, Docker/Node 22 | obraz `test`, następnie `npm run test:coverage` | 0 | Statements 94,47%, branches 83,96%, functions 96,89%, lines 96,81%. |
| Frontend, Docker/Node 22 | obraz `test`, następnie `npm run build` | 0 | Build Next.js zakończony. |

Backendowy raport Docker XML/JSON wskazuje **87,69%** pokrycia `app`
(`8892/10140` linii; raport terminalowy zaokrągla do 88%). Próg 80% został
spełniony, ale przebieg ma kod 1 z powodu 17 testów. PostgreSQL 16.4,
PostGIS 3.4.3 i migracje Alembic zostały uruchomione w odrębnym wolumenie.
Frontend na Node 22.23.2 przeszedł typecheck, coverage i build.

Zapisano [pełny `pip freeze` obrazu backendu](backend-pip-freeze.txt),
[tożsamości obrazów](image-identities.txt),
[wersję PostGIS](db-postgis-version.txt) i wszystkie kody wyjścia w
[docker-run.txt](docker-run.txt). Obraz PostGIS `16-3.4` został uruchomiony
jako `linux/amd64` na hoście `arm64` przez emulację; ten fakt jest widoczny
w [logu wersji PostgreSQL](db-image.txt). Logi hostowego backendu zostały
zredagowane z haseł połączenia z bazą.

Nie wykonano rzeczywistego scenariusza online `/analyze → /analyze/resume →
/report/{analysis_id}` na urzędowych usługach, ponieważ zwykły CI świadomie
korzysta z zamrożonych fixtures i nie powinien zależeć od internetu. Istniejące
testy integracyjne pokrywają połączenie orkiestratora, PostGIS, wznowienia i
PDF, lecz 16 z nich zatrzymuje się podczas budowy nieaktualnego fixture
`ContextResult`, zanim dojdzie do właściwej asercji.

## Reprodukcja w Docker

Z katalogu repozytorium uruchomić:

```bash
./scripts/capture_baseline.sh 8418bddc73bc76f590e021af122262f0a44bcaa4
```

Skrypt tworzy archiwum wskazanego commita w katalogu tymczasowym, bierze tylko
`.env.example`, używa osobnego projektu Compose bez portów hosta, wykonuje
budowę obu obrazów, `pip freeze`, migracje przez entrypoint backendu,
`pytest -m 'not docker_cli' --cov=app --cov-fail-under=80`, komendy frontendu
i zapisuje osobne kody wyjścia. Po zakończeniu usuwa tylko własny projekt i
wolumeny oraz tymczasowe archiwum. Nie dotyka lokalnej bazy ani `.env`.
Powtórne uruchomienie nadpisze pliki o tych samych nazwach w tym katalogu;
przed kolejnym pomiarem należy skopiować poprzedni komplet dowodów.

Hostowy pomiar *czystego commita* odtworzono tak (wymaga zależności Pythona
z `backend/requirements.txt`, lecz bez Docker/PostGIS zakończy się błędami
połączenia z `db`):

```bash
snapshot_dir="$(mktemp -d)"
git archive 8418bddc73bc76f590e021af122262f0a44bcaa4 | tar -x -C "$snapshot_dir"
cd "$snapshot_dir/backend"
REPO_ROOT="$snapshot_dir" python3 -m pytest -m 'not docker_cli' \
  --cov=app --cov-report=term-missing --cov-report=xml --cov-report=json \
  --cov-fail-under=80 -v
```

Pełny przebieg tworzy `docker-run.txt`,
`backend-pytest.txt`, `backend-pip-freeze.txt`, `backend-docker-coverage.xml`,
`backend-docker-coverage.json`, `frontend-coverage.txt`,
`frontend-coverage/coverage-summary.json`, logi budowy i wersji obrazów.

## Znany błąd testów w commicie bazowym

Kod `ContextResult` ma cztery sekcje: KIUT, ISOK, GDOŚ i NMT. Helpers w
`test_analysis_orchestrator.py`, `test_analyze_resume.py`,
`test_persistence.py`, `test_report_pdf.py` i `test_source_records.py` nadal
tworzą go z trzema sekcjami. Powoduje to 16 błędów `TypeError` przed właściwą
asercją. `test_context.py::test_analyze_context_runs_three_sections...` nie
mockuje pobrania NMT, więc wykonuje realne wywołanie i łamie próg czasu.
To jeden nieukończony update testów po dodaniu NMT, a nie 17 niezależnych
regresji aplikacji. Minimalna reprodukcja niezależna od bazy:

```bash
cd backend
python3 -m pytest tests/test_source_records.py -q
```

[Log reprodukcji](backend-stale-test.txt): 2 failed, 4 passed,
`TypeError: ContextResult.__init__() missing 1 required positional argument:
'nmt'`. Pełna lista 17 awarii jest w [logu Docker](backend-pytest.txt).
Naprawa testów powinna jawnie dodać `ContextSectionResult(section="nmt",
status="available")`, a test współbieżności także mock NMT i asercje dla
czterech zadań. Nie zmieniono testów w BK-001, aby baseline commita pozostał
wierny stanowi wejściowemu; zadanie wymaga udokumentowania błędnego testu,
nie przypisywania poprawki do commita bazowego.

## Artefakty pomiarów

- [Czysty commit: pełny log backendu](backend-commit-native.txt),
  [coverage XML](backend-commit-coverage.xml),
  [coverage JSON](backend-commit-coverage.json).
- [Drzewo robocze: pełny log backendu](backend-native.txt),
  [coverage XML](backend-coverage.xml), [coverage JSON](backend-coverage.json).
- [Testy zmienionych fixtures](backend-targeted.txt) i
  [reprodukcja błędnego testu](backend-stale-test.txt).
- Frontend: [npm ci](frontend-npm-ci.txt),
  [typecheck](frontend-typecheck.txt),
  [testy i coverage](frontend-coverage.txt),
  [JSON coverage](frontend-coverage-summary.json),
  [build](frontend-build.txt).
- Docker: [zbiorczy wynik](docker-run.txt),
  [backend pytest](backend-pytest.txt),
  [coverage XML](backend-docker-coverage.xml),
  [coverage JSON](backend-docker-coverage.json),
  [frontend coverage](frontend-coverage.txt),
  [frontend coverage JSON](frontend-coverage/coverage-summary.json).

Nie należy łączyć tych raportów w jeden wynik skuteczności systemu.
Syntetyczne fixtures nie zastępują ręcznie sprawdzonego ground truth;
offline testy kontraktów nie potwierdzają bieżącej dostępności usług.
