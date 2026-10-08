# Odbiór AU-010 (Task 21.10): podatności, lockfile, lint i typy w CI

- Data: 2026-10-08. Stan: **zaimplementowane i zacommitowane w `main` (AU-009, 2026-10-08)**.
- Decyzje: [ADR-018](../adr/ADR-018-dependency-lockfiles-multistage-images-and-ci-gates.md). Audyt: pozycje R4, R5 (dokumenty `docs/audit/…`
  wskazane w zadaniu nie istnieją w repozytorium; źródłem była treść zadania).
- Artefakty: [`results/au-010/01-gates.txt`](results/au-010/01-gates.txt) — wyniki `npm audit` przed i po, `pip-audit`, testy negatywne,
  zawartość obrazów `runtime` i `test`.

## Kryteria akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| `npm audit --omit=dev` → 0 podatności critical/high | **spełnione — 0 podatności** (także pełny `npm audit`, z narzędziami deweloperskimi) | przed: 7 (2 krytyczne: `next`, `maplibre-gl`; 3 wysokie: `nanoid`, `sharp`, `source-map-js`; 2 umiarkowane), bramka `--audit-level=high` zwraca 1; po: 0, kod 0 — [01-gates.txt](results/au-010/01-gates.txt) |
| `pip-audit` czysty | **spełnione** (lock produkcyjny i dev, `--require-hashes`) | `No known vulnerabilities found` ×2 |
| CI jest czerwone po wprowadzeniu znanej podatnej wersji (test negatywny) | **spełnione lokalnie; przebieg na GitHub Actions nie wykonany** | te same polecenia co w krokach CI na „gałęzi” z podatną wersją: `npm audit --omit=dev --audit-level=high` z `package.json`+lockfile z HEAD → kod 1; `pip-audit` z `jinja2==3.1.2` (6 podatności PYSEC) → kod 1; `ruff` z nieużywanym importem → kod 1; zgłoszenie `mypy` spoza bazy → kod 1 (`tests/test_check_mypy_baseline.py`). Wypchnięcie gałęzi z podatnym pinem i obserwacja czerwonego przebiegu w Actions wymaga push — nie zrobiono |
| Dwukrotny build backendu daje identyczną listę pakietów | **spełnione** | dwa buildy `--no-cache` obrazu `runtime`: `diff` list `pip freeze --all` pusty; w CI osobne zadanie `backend-reproducible` |
| Obraz `runtime` nie zawiera `pytest` ani katalogu `tests/` | **spełnione** | `/app`: `alembic alembic.ini app pyproject.toml requirements.lock shared`; `pytest`, `respx`, `pytest_cov`, `ruff`, `mypy` nieobecne; `scripts/` też nieobecne (w obrazie `test`: są) |
| Lockfile backendu z hashami, instalacja `--require-hashes` | **spełnione** | `requirements.lock` (58 pakietów) i `requirements-dev.txt` (47) — każdy wpis `==` z ≥ 1 skrótem SHA-256 (`tests/test_supply_chain.py`); `RUN pip install --require-hashes --no-deps` w obu etapach |
| Lint i typy w CI | **spełnione** | `ruff check .` (zestaw `E4,E7,E9,F`: 33 zgłoszenia naprawione — 24× E402 w orkiestratorze, 8× F401, 1× E741 — plus wyłączone `tests/fixtures`); `mypy app/modules` z bazą 19 znanych zgłoszeń (bramka tylko na NOWE); `eslint` + `tsc` w zadaniu `frontend` |
| Dependabot | **spełnione** | `.github/dependabot.yml`: `npm`, `pip`, `docker` (backend, frontend), `docker-compose`, `github-actions` |
| Obrazy bazowe przypięte digestem | **spełnione** | `python:3.13-slim@sha256:bf44cdfc…`, `node:22-slim@sha256:c3de60bf…` (oba etapy), `postgis/postgis:16-3.4@sha256:44126d87…`; test `test_base_images_are_pinned_by_digest` |
| Dokumentacja: `docs/current_state.md` i ADR | **spełnione** | sekcja AU-010 w `current_state.md`, ADR-018, README (sekcja „Testy, jakość i łańcuch dostaw”) |
| ≥ 80% pokrycia | **spełnione** | wyniki poniżej |

## Zmiany

- **Frontend:** `next` 16.2.10 → **16.3.8**, `maplibre-gl` 5.24.0 → **6.13.0** (zadanie wskazywało 6.12.0), `postcss` override 8.5.19 → 8.5.29,
  override `baseline-browser-mapping` 2.11.27, `vitest` i `@vitest/coverage-v8` 4.1.10 → 4.1.11, nowe: `eslint` 9.39.5, `typescript-eslint`,
  `eslint-plugin-jsx-a11y`, `eslint-plugin-react-hooks`, `@eslint/js`. Lockfile przeliczony (`nanoid` 3.3.20, `sharp` 0.35.5, `source-map-js` 1.2.2,
  `undici` 7.30.0). Migracja MapLibre 6: tylko nazwane eksporty (12 plików: `import * as maplibregl` / `import type * as maplibregl`,
  2 mocki testów), trzy zawężenia typów (`usePogTileActivity`, `PreviewOverlays`, `pogLayers`) oraz **jawny adres workera** (`lib/maplibreWorker.ts`) —
  bez niego mapa po `next build` nie ładuje kafli. Naprawiona przy
  okazji sprzeczność ARIA: `role="switch"` miał `aria-pressed` (przełącznik używa tylko `aria-checked`).
- **Backend:** `requirements.txt` → `requirements.in` + `requirements.lock` + `requirements-dev.in` + `requirements-dev.txt`; `Dockerfile`
  wieloetapowy (`deps` → `app-base` → `test` / `runtime`); Compose: `backend` i `address-index-sync` budują `runtime`, nowa usługa `backend-test`
  (profil `test`); `pyproject.toml` (`ruff`, `mypy`); `scripts/check_mypy_baseline.py` + `mypy-baseline.txt`.
- **CI:** trzy zadania (`backend`, `backend-reproducible`, `frontend`); polecenia testów backendu: `docker compose --profile test run … backend-test pytest`.
- **Testy:** `tests/test_supply_chain.py` (17), `tests/test_check_mypy_baseline.py` (7), zaktualizowane `test_requirements.py`,
  `test_backend_dockerfile.py`, `test_docker_compose_db.py`, `test_ci_config.py`, `test_llm_composition.py`.

## Weryfikacja

- **Frontend** — obraz `--target test` (Node 22): `npm audit --omit=dev --audit-level=high` (0), `npm run lint`, `npm run typecheck`,
  `npm run test:coverage` (**537 testów**, pokrycie **97,72%** linii) i `npm run build` zielone.
- **Backend** — kontener `backend-test` jak w CI (kopia repozytorium bez `.env` zamontowana tylko do odczytu jako `REPO_ROOT=/repo`):
  `pytest -m "not docker_cli" --cov=app --cov-report=term-missing --cov-fail-under=80` → **4088 passed**, 3 deselected, pokrycie **94,59%** (`scripts/check_mypy_baseline.py` poza zakresem pokrycia `app`).
- **Przegląd wizualny MapLibre 6** — przebieg w przeglądarce na produkcyjnym buildzie (Next 16.3.8 + MapLibre 6.13.0): na pierwszym przebiegu **wykryto regresję**: `maplibre-gl 6` liczy adres workera wewnątrz biblioteki (`new URL("./maplibre-gl-worker.mjs", import.meta.url)` z nazwą pliku w zmiennej), czego Turbopack nie potrafi zbundlować — po `next build` adres wskazywał stronę główną (HTML zamiast JavaScriptu), konsola: `Failed to load module script … text/html` i `Worker failed to load`, kafle nie były rysowane; 534 testy jednostkowe tego nie wykrywały (MapLibre jest w nich mockowany). Naprawa: `lib/maplibreWorker.ts` (statyczne `new URL("../node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs", import.meta.url)` + `setWorkerUrl`, wołane przed utworzeniem mapy w `MapView`), 3 nowe testy. Po naprawie na produkcyjnym buildzie: worker `/_next/static/media/maplibre-gl-worker.*.mjs` ładuje się, 20–24 kafli OSM, brak błędów workera w konsoli ([02-map-maplibre6-final-build.jpg](results/au-010/02-map-maplibre6-final-build.jpg)); kontrolki nawigacji i panel POG obecne; pomiar popovera AU-008 na tym buildzie: 5/5 przy 1440×900, 1024×768 i 375×812.

## Ograniczenia i decyzje do potwierdzenia

1. **Brak przebiegu na GitHub Actions** (wymaga push gałęzi) — bramki odtworzono lokalnie tymi samymi poleceniami.
2. **Lock Pythona generowany ręcznie** (Dependabot nie przelicza `requirements.lock`); `pymupdf` (AGPL) pozostaje — decyzja AU-407.
3. **ESLint bez `eslint-config-next`** (podatność `braces` bez poprawki) i bez reguł kompilatora React — ADR-018 §2.
4. **`mypy` ma 19 znanych zgłoszeń** w `app/modules/*` i dodatkowe 13 poza zakresem — jawna baza, nie naprawa.
5. **Regresja wizualna MapLibre 6** — nie istnieje automatyczny test wizualny (Task 24.10); sprawdzono ręcznie w przeglądarce bez danych POG
   (lokalne wydanie POG nie jest załadowane w środowisku testowym), więc kafle MVT i inspektor są pokryte testami jednostkowymi/integracyjnymi
   (537 testów), a nie obrazem; poza tym jedynym wykrytym ręcznie defektem był worker (patrz wyżej).
6. `backend/scripts/live_smoke_corpus.py` (Task 21.11) nadal nie istnieje — pomiaru „korpus bez 5xx” nie wykonano.
