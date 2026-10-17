# Rejestracja dokumentów KIMPZP — naprawa współbieżności

Data: 2026-10-17. Kontynuacja [odbioru AU-108–111](au108-111-verification.md).

## Problem i poprawka

Live smoke sprzed poprawki wykazał `MPZP_DOCUMENT_PERSISTENCE_FAILED` dla
`126105_9.0001.580/4`: dwie transakcje odczytywały brak źródła `kimpzp`, a następnie
próbowały je utworzyć. PostgreSQL odrzucał jedną przez `uq_data_sources_source_id`.
Analogiczny wyścig istniał przy wspólnym artefakcie i wydaniu dokumentu.

Adapter `SqlAlchemyDocumentRepository` rejestruje teraz źródło, artefakt, wydanie i akt
przez `INSERT … ON CONFLICT DO NOTHING`, po czym odczytuje istniejący rekord.
Zachowuje metadane wcześniej zarejestrowanego źródła. Blokada `FOR UPDATE` na akcie
utrzymuje spójność jego dokumentu i wersji do `commit`/`rollback` wywołującego.
Zmiana pozostaje w infrastrukturze modułu dokumentów, zgodnie z ADR-001;
nie wymaga migracji ani zmiany kontraktu odpowiedzi.

Dodatkowa kontrola wykazała, że ponowne zapisanie identycznej ekstrakcji usuwało
jednostki prawne cytowane przez wcześniejsze analizy. Zapis treści blokuje teraz
wiersz wersji i porównuje metadane ekstrakcji, wszystkie strony, bloki tekstu oraz
drzewo jednostek. Identyczna ekstrakcja zachowuje ich ID. Zmieniona ekstrakcja nadal
jest zapisywana. Pobieranie i parsowanie dokumentu odbywa się przed rejestracją.

## Regresja na rzeczywistym dokumencie

`backend/tests/test_document_concurrency.py` korzysta z pełnych **99 stron** uchwały
Kraków Stare Miasto XII/131/11, oryginalnych metadanych i SHA-256 dokumentu
`db82d81d8a30b021782b6a4277763bdc0c820f191f31758a115ee940f4cb3f3c`.
Są to niezmienione fixtury `mpzp_live/krakow_stare_miasto_xii_131_11`.
Test nie rekonstruuje PDF z tekstu; sprawdza zapis rzeczywistej ekstrakcji.

Dwie sesje na różnych połączeniach PostgreSQL (różne `pg_backend_pid()`) wykonują
równoległe transakcje. Bariera wymusza na starym kodzie dwa odczyty braku rekordu
przed wstawieniem. Obejmujemy:

- nowe i istniejące źródło, ten sam i różne akty,
- wspólny artefakt/wydanie oraz brak duplikatów pełnego łańcucha,
- wszystkie 99 stron i 1550 jednostek prawnych,
- ponowny równoległy zapis istniejącej wersji oraz trwałość ID cytowań,
- rzeczywistą zmianę ekstrakcji, zachowanie metadanych źródła i rollback bez pozostałości.

Moduł otrzymuje jednorazową bazę przez istniejący mechanizm `conftest.py`.
Przed czyszczeniem danych test dodatkowo wymaga nazwy `test_migrations_*`.

Na wersji repozytorium sprzed poprawki regresja daje **5 failed, 1 passed**:
cztery konflikty unikalności oraz utratę ID przy ponownym zapisie. Po poprawce
wszystkie sześć przypadków przechodzi. Łącznie z magazynem, kompozycją i wznowieniem
analizy: **61 passed**, Ruff bez uwag, mypy bez nowych zgłoszeń (17 znanych).

Dowody: [czerwony przebieg](results/au108-111/kimpzp-concurrency/regression-before.txt),
[testy po poprawce](results/au108-111/kimpzp-concurrency/focused-verification.txt).

## Pełna weryfikacja

Backend: **5272 passed, 0 failed, 3 deselected** (`docker_cli`), **662,57 s**.
Pokrycie aplikacji **95,30%**, adaptera dokumentów **96,30%**, próg 80% zaliczony. Ruff bez uwag, mypy bez nowych
zgłoszeń (17 znanych). Kontener Python 3.13/PostGIS 16, oddzielna świeża baza,
`REPO_ROOT=/repo`, kopia repo bez `.env`, repo/kod/testy montowane tylko do odczytu.
Polecenie: `pytest -m "not docker_cli" --cov=app --cov-report=term-missing --cov-report=json:/evidence/coverage.json --cov-fail-under=80 -q`.
Skan sekretów: zero zgłoszeń; `git diff --check` bez uwag.
[Pełny log backendu](results/au108-111/kimpzp-concurrency/backend-verification.txt),
[pokrycie adaptera](results/au108-111/kimpzp-concurrency/backend-coverage-summary.json).

Frontend: ponowiono build obrazu i `npm run typecheck && npm run test:coverage && npm run build && npm run lint`.
**672 passed**, 57 plików, pokrycie linii **99,05%** (instrukcje 98,03%, funkcje 99,23%,
gałęzie 91,31%); wszystkie polecenia zaliczone.
[Log frontendu](results/au108-111/kimpzp-concurrency/frontend-verification.txt).

## Końcowy live smoke całego korpusu

Pełne **30 działek**, bez `--limit`, 19:59:08–20:00:29 UTC, **80,955 s**.
Oddzielna baza odbiorowa: przed uruchomieniem **0 analiz, 0 źródeł KIMPZP**;
trzy zaimportowane gminy POG jak w poprzednim odbiorze, brak paczek BDOT10k.
Włączone NMT WCS, RU MPZP i discovery RU POG, LLM wyłączony.
Równoległość 3, timeout 180 s, brak wcześniejszych wyników do wykorzystania z cache.

| Pomiar | Wynik |
|---|---:|
| HTTP 200 z API | **30/30** |
| 5xx API / błędy transportu klienta / ponowienia 429 | **0 / 0 / 0** |
| `MPZP_DOCUMENT_PERSISTENCE_FAILED` | **0** (poprzednio 1) |
| Źródła `kimpzp` w bazie | **1** |
| Odwołania parametrów do istniejących jednostek właściwej wersji | **39/39** |
| Brakujące jednostki lub odwołania do złej wersji | **0** |
| Zapisane wersje dokumentów | **2** (99 i 95 stron; 1550 i 1885 jednostek) |
| `partial` / `complete` | **30 / 0** |
| p50 / p95 / maksimum | **3,823 / 26,844 / 47,407 s** |
| Macierze jakości z poprawnym odciskiem | **30/30** |
| Sekcje bez daty i bez powodu jej braku | **0** |
| Nadrzędny zakres z WCS | **30/30** |
| Ostrzeżenia różnicy wysokości > 0,5 m | **7** |

Dla działki `126105_9.0001.580/4` zapis jest poprawny: 14 parametrów z istniejącymi
jednostkami prawnymi, bez ostrzeżenia o utracie zapisu. Łącznie dla trzech pierwszych
działek Krakowa zachowano 34 odwołania; pozostałe 5 pochodzi z czwartej analizy.
Przypadki mają te same liczby stref MPZP/POG co wcześniej; skrypt porównania nie wykazał
regresji HTTP, statusów analiz ani liczby stref. Zmiany czasów i dostępności usług
zewnętrznych nie są dowodem przyspieszenia wynikającego wyłącznie z tej poprawki.

Dowody: [stan początkowy](results/au108-111/kimpzp-concurrency/live-initial-state.txt),
[raport per działka](results/au108-111/kimpzp-concurrency/live-smoke.md),
[JSON i porównanie przebiegów](results/au108-111/kimpzp-concurrency/live-smoke.json),
[kontrola zapisów i cytowań](results/au108-111/kimpzp-concurrency/persistence-acceptance.json),
[skrypt kontroli](results/au108-111/kimpzp-concurrency/inspect-persistence.py),
[odpowiedzi źródeł](results/au108-111/kimpzp-concurrency/upstream-summary.json),
[środowisko i skróty sprawdzonych plików](results/au108-111/kimpzp-concurrency/environment.json).

## Ograniczenia odbioru

Wynik smoke zalicza próg zero 5xx API i zero błędów transportu. Wszystkie analizy nadal
są częściowe: w bazie odbiorowej brak lokalnych wydań POG dla korpusu i paczek BDOT10k;
MPZP jest niepełny w 26 analizach, ISOK w 18, GDOŚ w 3. Brak danych pozostaje jawnym
stanem i nie oznacza braku ograniczenia. Wcześniejszy brak potwierdzenia dokładnie
dwóch historycznych okruchów 0,18 m² (AU-110) pozostaje aktualny.
