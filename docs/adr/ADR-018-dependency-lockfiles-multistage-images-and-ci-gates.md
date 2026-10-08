# ADR-018: Lockfile z hashami, obrazy wieloetapowe i bramki jakości w CI

- Status: **Zaakceptowany technicznie (2026-10-08)**
- Zakres: AU-010 (Task 21.10, audyt 2026-10-05, pozycje R4 i R5).
- Powiązane: ADR-001 (granice modułów — `mypy` zaczyna od `app/modules/*`), ADR-014 (skaner sekretów w CI).

## Kontekst

`npm audit --omit=dev` (2026-10-05) wykazywał 6 podatności, w tym 2 krytyczne: `next 16.2.10` (obejście middleware/proxy, DoS
server actions, SSRF, zatruwanie cache — kilkanaście zgłoszeń do `16.3.7`) i `maplibre-gl 5.24.0` (obejście sanitizera XSS,
poprawka w `6.x`), oraz `nanoid`, `sharp`, `postcss`. `backend/requirements.txt` miał same dolne granice i nie było lockfile'a;
`pytest`, `respx` i `pytest-cov` były zależnościami produkcyjnymi, a obraz kopiował `tests/` i `scripts/` do produkcji. CI nie
uruchamiał `ruff`, `mypy` ani `eslint`; obrazy bazowe wskazywały ruchome tagi.

## Decyzje

### 1. Frontend: aktualizacja zamiast przypięcia, migracja MapLibre 5 → 6

- `next` `16.2.10` → `16.3.8`, `maplibre-gl` `5.24.0` → `6.13.0` (poprawka luki w `≥ 6.5`; zadanie wskazywało `6.12.0`, wybrano najnowszą
  stabilną, bo różnica nie dotyka używanego API). Migracja major okazała się mała: MapLibre 6 eksportuje tylko nazwane symbole,
  więc `import maplibregl from "maplibre-gl"` → `import * as maplibregl` (w typach `import type * as maplibregl`), mocki w testach
  zwracają nazwane eksporty, a trzy miejsca wymagały zawężenia typów (`ErrorEvent` ze `sourceId`, wyrażenia stylu jako `never`).
  **Worker:** wersja 6 ładuje osobny moduł `maplibre-gl-worker.mjs` z adresu liczonego w bibliotece, którego Turbopack nie
  bundluje (po `next build` adres wskazuje stronę główną — HTML zamiast JS, mapa nie rysuje kafli; testy jednostkowe mockują
  MapLibre i tego nie widzą). `lib/maplibreWorker.ts` podaje adres jawnie (`new URL("../node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs",
  import.meta.url)` + `setWorkerUrl`), więc bundler emituje plik do `_next/static/media`. Alternatywa „pin do wersji 5.x z poprawką” nie
  istnieje (luka dotyczy ≤ 6.4.0, a gałąź 5 jej nie dostała), więc przypięcie nie wchodziło w grę.
- Lockfile przeliczony; `overrides` (`postcss`, `baseline-browser-mapping`) przypinają wersje poprawione, bo `next` zagnieżdża własne
  kopie. `npm audit --omit=dev` i pełny `npm audit` zwracają 0 podatności; `vitest`/`@vitest/coverage-v8` 4.1.11 usuwają lukę
  w `@vitest/mocker`, a `jsdom` dostał poprawioną `undici`.
- Lockfile jest generowany `npm@11` (`npm 10.9.9` z `node:22-slim` ulega awarii `Cannot read properties of null (reading 'edgesOut')`
  przy tym zestawie peerów `vitest`); format (`lockfileVersion 3`) jest zgodny, a `npm ci` w obrazie (`npm 10`) działa.

### 2. ESLint bez `eslint-config-next`

`eslint-config-next` ciągnie `@next/eslint-plugin-next → fast-glob → micromatch → braces`, a `braces` ma wysoką podatność
(GHSA-vfj7-8cjw-p6xm) **bez opublikowanej poprawki** — `npm audit` przestałby być czysty. Konfiguracja (`eslint.config.mjs`, ESLint 9
flat) składa się z `@eslint/js`, `typescript-eslint`, `eslint-plugin-jsx-a11y` i reguł `rules-of-hooks`/`exhaustive-deps`.
Reguły kompilatora React z `eslint-plugin-react-hooks@7` (np. `set-state-in-effect`) są wyłączone: istniejące efekty (odczyt
`localStorage` po hydracji, reset stanu przy zmianie wydania) łamią je celowo, a ich naprawa to refaktoryzacja, nie zmiana narzędzi.
Reguły Next (np. `no-img-element`) nie mają zastosowania. Wyjątki są jawne i opisane komentarzem przy linii (tabela przewijana z
`tabIndex`, `Escape` w inspektorze, brakująca zależność w efekcie montującym warstwy).

### 3. Backend: `requirements.in` + `requirements.lock` + `requirements-dev.txt`

- `requirements.in` (dolne granice zależności bezpośrednich) → `requirements.lock` (58 pakietów, dokładne wersje, SHA-256 **wszystkich**
  artefaktów, także dla innych platform) przez `pip-compile --generate-hashes` (pip-tools 7.6). Wersje startowe zachowują zestaw z
  obrazu bazowego z 2026-09-23 (kompilacja z ograniczeniem do ówczesnego `pip freeze`), czyli ten, który przechodzi testy — lock nie
  podnosi niczego przy okazji. Odświeżanie: `--upgrade-package`.
- `requirements-dev.in` (`-c requirements.lock`) → `requirements-dev.txt` (pytest, pytest-asyncio, pytest-cov, respx, ruff, mypy,
  pip-audit, types-PyYAML; 47 pakietów z hashami). Wersje wspólnych pakietów nie mogą się rozjechać z lockiem produkcyjnym —
  pilnuje tego test.
- `requirements.txt` usunięty; produkcyjny lock nie zawiera narzędzi testowych.
- `pymupdf` (AGPL/licencja komercyjna) pozostaje w zależnościach produkcyjnych; decyzja licencyjna to AU-407 (adnotacja w pliku).

### 4. Obraz wieloetapowy

`deps` (system + `pip install --require-hashes --no-deps -r requirements.lock`) → `app-base` (kod aplikacji, użytkownik `app`) →
`test` (dokłada `requirements-dev.txt`, `git`, `tests/`, `scripts/`) oraz `runtime` jako **ostatni** etap, więc domyślny cel
`docker build` to obraz produkcyjny. Runtime nie zawiera `pytest`, `tests/` ani `scripts/` (skrypty operacyjne z README uruchamia
się na hoście: `python3 backend/scripts/…`). Compose: `backend` i `address-index-sync` budują `runtime`; `backend-test` (profil
`test`, współdzieli konfigurację środowiska z `backend` przez kotwicę YAML) buduje `test`. Obrazy bazowe (`python:3.13-slim`,
`node:22-slim`, `postgis/postgis:16-3.4`) są przypięte digestem indeksu wieloplatformowego (działa na amd64 i arm64).

### 5. CI

Trzy zadania: `backend` (build `runtime` i `test`, kontrola, że runtime nie ma narzędzi testowych ani `tests/`/`scripts/`,
`ruff check`, `mypy` z bazą, `pip-audit --require-hashes` lock produkcyjny i dev, testy z `--cov-fail-under=80`),
`backend-reproducible` (dwa buildy `--no-cache` dają identyczną listę pakietów i zawierają wszystko z locka) oraz `frontend`
(`npm audit --omit=dev --audit-level=high`, ESLint, `tsc`, testy z pokryciem, build). `mypy` obejmuje `app/modules/*` (19 znanych
zgłoszeń w `mypy-baseline.txt`; bramka `scripts/check_mypy_baseline.py` odrzuca tylko NOWE, liczone bez numerów linii). `ruff`:
`E4,E7,E9,F` (błędy, nie styl; szersze reguły dałyby setki zgłoszeń stylistycznych). Aktualizacje zależności i przypięć obrazów są ręczne (brak automatu
z PR-ami); lock Pythona odnawia się poleceniem z nagłówka pliku, a podatne wersje wykrywają `npm audit` i `pip-audit` w CI.

## Konsekwencje i ograniczenia

- Lock Pythona generowany na Linuksie (kontener) — hashe obejmują wszystkie pliki wersji, ale zależności warunkowe (np. `uvloop`)
  są rozstrzygnięte dla Linuksa; lokalna instalacja na Windows nie jest wspierana poza obrazem.
- Podniesienie wersji pakietu wymaga ręcznego `pip-compile` (brak automatycznych PR-ów z aktualizacjami); `pip-audit` w CI wykrywa
  podatną wersję niezależnie.
- Reguły kompilatora React (`set-state-in-effect` i pokrewne) oraz reguły Next nie są egzekwowane; lista „poza zakresem” w README.
- `mypy` poza `app/modules/*` (m.in. `app/routers`, `app/services`) ma 13 dodatkowych zgłoszeń i nie jest bramką.
- Bramki CI zweryfikowano na GitHub Actions: przebieg pozytywny na `main` (3 zadania zielone) i negatywny na jednorazowej gałęzi z
  podatnym `next` (zadanie Frontend czerwone na `npm audit`) — szczegóły w odbiorze AU-010.
