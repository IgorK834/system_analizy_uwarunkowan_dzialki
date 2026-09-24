# Stan początkowy projektu — BK-001

Data audytu: **2026-09-23**. Baseline rozwoju w `main` to commit
`8418bddc73bc76f590e021af122262f0a44bcaa4` (dodanie `backlog.md`).
Wskazany w zadaniu wcześniejszy punkt odniesienia backlogu to
`64ca3e85ab74ae811280fda01e485e89cdf4f29e`. Różnica między tymi
commitami to wyłącznie `backlog.md`; żaden z nich nie zawiera lokalnych zmian
wymienionych niżej. SHA identyfikuje kod, nie stan usług zewnętrznych ani
lokalnej bazy danych.

## Granica między commitem a drzewem roboczym

Na wejściu do BK-001 `git status --short` wskazywał dwie zmiany śledzone:
`backend/tests/fixtures/source_contracts/README.md` i
`docs/data_sources/catalog.yaml`, oraz nieśledzone:
`backend/_imp.py`, `docs/data_sources/ru_contracts.md`, `docs/progress/`.
Lokalny ignorowany `.env` był obecny. Żadnemu z tych plików ani ich zawartości
nie przypisujemy statusu commita `8418bdd`. Nie były modyfikowane ani usuwane
przez BK-001. Aktualny stan roboczy zawiera ponadto zmiany BK-001 opisane w
tym dokumencie i katalogu `docs/evaluation/results/baseline/`.

Szczególnie istotny jest katalog źródeł: w **commicie** GDOŚ, ISOK i NMT mają
status `research`, a w zastanych zmianach roboczych ich wpisy zostały zmienione
na `production` i dodano m.in. podglądowe WMS POG/KIUT. Poniższa tabela
dotyczy wyłącznie commita `8418bdd`, a nie niezacommitowanego katalogu.

## Architektura i działający przepływ

System jest jednym wdrożeniem Compose: Next.js 16/React 19 z MapLibre,
FastAPI i PostgreSQL 16/PostGIS 3.4. Backend zachowuje starszą ścieżkę
`router → services → ORM` obok stopniowo rozwijanych modułów `app/modules`
(`location`, `documents`, `imports`, `planning` itd.). Granice nowych modułów
opisuje i sprawdza [ADR-001](adr/ADR-001-modular-monolith.md); obecność
szkieletu modułu nie oznacza ukończenia jego funkcji. Alembic ma migracje
`001`–`014`. Importy przestrzenne mają model źródeł, artefaktów i aktywnego
`data_release`, lecz utworzenie tabel nie dowodzi, że baza jest zasilona.

1. `POST /analyze` przyjmuje `map`, `address` z wybraną sugestią albo
   `parcel_id`. `initiation.resolve_parcel` sprowadza je do ULDK
   (`GetParcelByXY` lub `GetParcelById`); adres nie jest po cichu geokodowany
   ponownie. Osobno działa `GET /api/v1/search/addresses`.
2. Orkiestrator sprawdza cache po identyfikatorze działki. Przy odświeżeniu
   parsuje geometrię ULDK w `EPSG:2180`, oblicza pole, obwód i techniczne
   odsunięcie od granic. GeoJSON dla odpowiedzi jest w `EPSG:4326`.
3. Równolegle pobiera kontekst KIUT, ISOK, GDOŚ i NMT oraz wskaźnik pokrycia
   podglądu KIUT. ISOK i GDOŚ obliczają przecięcia przestrzenne; NMT zwraca
   wysokości minimalną, maksymalną i deniwelację. Braki i błędy sekcji mają
   statusy i provenance. Istnieje jednak błąd semantyczny KIUT: adapter może
   zamienić błąd transportu na pustą listę sieci.
4. Discovery MPZP odpytuje KIMPZP. Ścieżka korzysta z kandydatów symboli
   wywnioskowanych także z WMS GetFeatureInfo i z pobranego dokumentu MPZP
   z ekstrakcją PDF/HTML/OCR; nie ma jeszcze deterministycznego przypisania
   lokalnej geometrii wszystkich stref MPZP. To użycie WMS do discovery jest
   ograniczeniem stanu bazowego wobec docelowej zasady „WMS tylko podgląd”.
   Przy braku wektora wynik jest zapisywany jako `waiting_for_user_input`
   (`waiting_for_zone_symbol` w bazie). `POST /analyze/resume` przyjmuje ręczny
   symbol, ponownie pobiera dokument i aktualizuje snapshot.
5. POG/OUZ ma discovery (także przez WMS), pobieranie wektorów i realne
   przecięcia. Discovery WMS nie zastępuje geometrii analitycznej. Wewnętrzny
   `PogAnalysisResult` potrafi zachować listę stref, ale obecny `PogResult`
   przekazuje do API/bazy/UI/PDF głównie strefę dominującą. Zgodność MPZP–POG
   jest oceną heurystyczną, nie urzędową wykładnią prawa.
6. `save_analysis` utrwala działkę, analizę, sekcje, ostrzeżenia, statusy oraz
   `SourceRecord` także dla częściowego wyniku. Odpowiedź może mieć status
   `complete`, `partial` albo wymagać uzupełnienia. Brak danych źródłowych nie
   jest dowodem braku ograniczenia; `null` nie oznacza zera.
7. `GET /report/{analysis_id}` odtwarza raport z **zapisanego snapshotu** przez
   Jinja2/WeasyPrint, bez ponownej analizy. Opcjonalna miniatura mapy może
   jednak pobierać **bieżący** OSM WMS oraz nakładki KIMPZP/KIUT. Jej raster
   nie jest zatem zamrożonym dowodem stanu źródeł z chwili analizy; awaria
   podkładu nie blokuje pozostałej części PDF.

`GET /api/v1/map/preview-sources` i
`GET /api/v1/map/tiles/{mpzp|pog|kiut}/{z}/{x}/{y}.png` podają wyłącznie
podglądowe WMS z walidacją i cache kafli. Warstwy te nie służą do obliczania
przecięć ani odległości sieci. Lokalny indeks adresowy PRG/EMUiA ma import
pełny/przyrostowy i endpoint statusu, ale wymaga pierwszego zasilenia;
ograniczony fallback UUG nie zapewnia pełnych sugestii krótkich prefiksów.

## Źródła na commicie `8418bdd`

Statusy są dosłownie z `docs/data_sources/catalog.yaml` w tym commicie.
`production` opisuje deklarację katalogu, a nie zmierzoną dostępność lub
kompletność danych na danej działce. Kanał oznacza protokół źródłowy.

| ID | Status katalogu | Kanał | Rola lub ograniczenie |
| --- | --- | --- | --- |
| `uldk` | production | REST | Identyfikacja i geometria działki. |
| `emuia_uug` | production | REST | Geokodowanie/fallback adresów. |
| `prg_address_dictionary` | production | SOAP → lokalny PostGIS | Słowniki do indeksu adresowego; wymagają importu. |
| `kimpzp` | production | WMS | Discovery MPZP i podgląd, nie pełna geometria stref. |
| `egib_geometry_warsaw` | production | WFS | Pilotażowy import geometrii działek Warszawy; nie zastępuje ULDK w `/analyze`. |
| `egib` | no_redistribution | WFS | Pełne atrybuty EGiB bez prawa redystrybucji. |
| `kiut_gesut` | contract_required | WFS | Kontrakt wektorowy niepotwierdzony; odrębny od podglądu WMS. |
| `mpzp_pilot_krakow` | contract_required | WFS | Techniczny kontrakt jest w fixture, zgoda na redystrybucję niepotwierdzona. |
| `pog_app` | research | WMS w katalogu | Badawcza deklaracja POG/APP; kod obsługuje też inne ścieżki discovery/GML. |
| `gdos` | research | WFS | Adapter realnych przecięć istnieje; status katalogu nie odzwierciedla jego ścieżki runtime. |
| `isok` | research | WFS | Adapter realnych przecięć istnieje; status katalogu nie odzwierciedla jego ścieżki runtime. |
| `nmt` | research | file w katalogu | Kod używa usługi REST NMT; deklaracja katalogu jest niespójna z runtime. |
| `planned_restrictions` | placeholder | WFS | Przyszłe warstwy; brak działającej analizy. |

Katalog jest bramką dla części importów i adapterów, ale serwisy kontekstu
GDOŚ/ISOK/NMT korzystają z własnej konfiguracji. Nie należy z samego statusu
`research` w commicie wywodzić, że te serwisy nie istnieją. Zastane zmiany
katalogu wymagają osobnego przeglądu kontraktów; nie są wynikiem BK-001.

## Rzeczywiste braki względem backlogu

- Brak realnego, ręcznie zweryfikowanego korpusu działek i ilościowej oceny
  poprawności. `backend/tests/fixtures/parcels/cases.json` jest syntetyczny,
  a większość odpowiedzi zewnętrznych ma `null`; walidator sprawdza strukturę.
- Brak pełnego przekazania listy stref POG oraz rozdzielenia statusu prawnego
  od kompletności danych w kontrakcie wyniku.
- Brak domyślnego przypisania MPZP przez przecięcie lokalnej geometrii stref;
  fallback rastrowy wymaga ręcznego symbolu.
- NMT nie jest osobną sekcją `AnalyzeResponse`/UI/PDF, mimo działającego
  adaptera i zapisu kontekstu.
- KIUT/GESUT nie daje wiarygodnych odległości ani pewnego rozróżnienia błędu
  pobrania od braku sieci.
- Brak deterministycznego przeglądarkowego E2E i zamrożonego podkładu mapy PDF.

## Wersje i powtarzalność

Obrazy zadeklarowane w repozytorium: `postgis/postgis:16-3.4`,
`python:3.13-slim`, `node:22-slim`. Nie są przypięte digestem. Backend
`requirements.txt` podaje dolne granice, m.in. FastAPI `>=0.115`, Pydantic
`>=2.9`, SQLAlchemy `>=2.0`, GeoAlchemy2 `>=0.15`, Alembic `>=1.13`,
Shapely `>=2.0`, WeasyPrint `>=62`, Jinja2 `>=3.1`; dokładny lock backendu
jest zadaniem BK-705. Frontendowy lock wskazuje Next.js `16.2.10`, React
`19.2.7`, TypeScript `5.9.3`, MapLibre `5.24.0`, Vitest `4.1.10`.

W odtworzonym obrazie baseline 2026-09-23 działały: Python 3.13.15,
PostgreSQL 16.4/PostGIS 3.4.3, Node 22.23.2 i npm 10.9.8. `pip freeze`
potwierdził m.in. FastAPI 0.141.1, Pydantic 2.13.5, SQLAlchemy 2.0.54,
GeoAlchemy2 0.20.0, Alembic 1.20.0, Shapely 2.1.2, WeasyPrint 70.0 oraz
Jinja2 3.1.6. Są to wyniki konkretnej budowy z nieprzypiętych deklaracji,
a nie gwarantowane wersje dla przyszłej reprodukcji.

`backend/pyproject.toml` przed BK-001 miał `coverage.run.source = ["tests"]`,
podczas gdy CI wymuszał `--cov=app`. BK-001 ustawia domyślne źródło na `app`
bez zmiany progu 80%. Instrukcja uruchomienia, logi, kody wyjścia, coverage
i ograniczenia aktualnego środowiska są w
[wynikach baseline](evaluation/results/baseline/README.md). Skrypt
`scripts/capture_baseline.sh` archiwizuje dokładny commit, uruchamia kroki
CI w osobnym projekcie Compose i nie kopiuje lokalnego `.env`.
