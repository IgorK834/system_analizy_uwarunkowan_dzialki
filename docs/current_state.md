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
`001`–`030` (027–030: symbol strefy, warunki wartości, cache i rejestr zużycia modelu językowego). Importy przestrzenne mają model źródeł, artefaktów i aktywnego
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
   (`waiting_for_zone_symbol` w bazie) razem z przypiętą kopią uchwały, planem i
   kandydatami (`manual_zone_context`). `POST /analyze/resume` (od AU-005 wymaga tokenu dostępu analizy) przyjmuje ręczny
   symbol, parsuje wyłącznie przypiętą kopię (bez ponownego pobrania) i zapisuje
   wynik `partial` z nieustalonym udziałem strefy oraz flagami weryfikacji
   (BK-204, ADR-005). Symbol (do 40 znaków, także ze spacją, przecinkiem i
   plusem) jest sprowadzany do formy kanonicznej jedną regułą dla API i UI
   (`shared/zone-symbol-rules.json`, aneks PV3-04 w ADR-005).
5. POG/OUZ ma discovery, pobieranie wektorów i realne
   przecięcia. Discovery w Rejestrze Urbanistycznym idzie od AU-105 przez **WFS `app-pog`** (cztery zapytania z `bbox` kafla, budżet 6 żądań HTTP na
   analizę, cache 24 h i deduplikacja — [ADR-023](adr/ADR-023-discovery-pog-ru-wfs-budzet-zadan.md)); WMS RU służy wyłącznie podglądowi mapy. Discovery nie zastępuje geometrii analitycznej. Wewnętrzny
   `PogAnalysisResult` potrafi zachować listę stref, ale obecny `PogResult`
   przekazuje do API/bazy/UI/PDF głównie strefę dominującą. Relacja MPZP–POG
   jest informacyjną oceną `compatibility_assessment` par stref
   zidentyfikowanych przestrzennie, z jawną regułą, datą stanu prawnego i
   uzasadnieniem (BK-205, ADR-005) — nie urzędową wykładnią prawa.
6. `save_analysis` utrwala działkę, analizę, sekcje, ostrzeżenia, statusy oraz
   `SourceRecord` także dla częściowego wyniku. Odpowiedź może mieć status
   `complete`, `partial` albo wymagać uzupełnienia; od AU-104 status wynika z **kompletności sekcji** (`completeness`, `headline`, [ADR-022](adr/ADR-022-definicja-complete-i-status-naglowkowy.md)). Brak danych źródłowych nie
   jest dowodem braku ograniczenia; `null` nie oznacza zera.
7. `GET /report/{analysis_id}` odtwarza raport z **zapisanego snapshotu** przez
   Jinja2/WeasyPrint, bez ponownej analizy. ~~Opcjonalna miniatura mapy może
   pobierać bieżący OSM WMS oraz nakładki KIMPZP/KIUT~~ — domknięte w BK-501–503
   (2026-09-29): raport v2 ma 10 sekcji i tabelę mapowania pól API, pełne tabele
   stref MPZP/POG z evidence, a mapy są zamrażane przy zapisie analizy
   (`analyses.report_map_snapshot`, migracja 025, EPSG:2180, hash semantyczny) i
   renderowane lokalnie bez WMS (ADR-010,
   `docs/evaluation/results/bk-501-503-verification.md`).

8. Macierz kompletności i świeżości sekcji (BK-504, 2026-09-29): `save_analysis`
   wystawia raz `SectionQualityMatrix` (10 sekcji, status wg kontraktu źródła,
   `source_id`, `fetched_at`, wydanie, manual review, świeżość per źródło względem
   `analyzed_at`, `policy_version`, `reason_codes`, `matrix_sha256`) i zapisuje ją w
   `analyses.section_quality` (migracja `026`); API, UI, PDF i cache ją czytają.
   Reguły świeżości są w katalogu źródeł (tylko `isok`, `gdos`, `nmt`, `nmt_wcs`,
   7 dni, decyzja projektowa); pozostałe źródła mają świeżość `unknown` — nie ma
   globalnego TTL. Wiek na dzień eksportu jest osobnym ostrzeżeniem (ADR-011).
9. Pakiet audytowy (BK-505): `GET /report/{analysis_id}/audit.zip` — deterministyczny
   ZIP ze snapshotu (`analysis.json`, `sources.json`, GeoJSON EPSG:4326, README,
   `manifest.json` z SHA-256), hash paczki w nagłówku, weryfikacja offline; warstwy
   i surowe atrybuty źródeł bez zgody katalogu (`redistribution`) nie są kopiowane.

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
- ~~NMT nie jest osobną sekcją `AnalyzeResponse`/UI/PDF~~ — domknięte w
  BK-301/BK-302 (2026-09-28): `AnalyzeResponse.terrain` z czterema statusami,
  snapshot `analyses.terrain` (migracja 022), cache `terrain-v1.0`, panel UI,
  sekcja PDF oraz spadek/ekspozycja/profil z WCS NMT 1 m (ADR-006,
  `docs/evaluation/results/bk-301-302-verification.md`).
- KIUT/GESUT nie daje wiarygodnych odległości ani pewnego rozróżnienia błędu
  pobrania od braku sieci.
- ~~Brak trwałej macierzy jakości sekcji i pakietu audytowego~~ — domknięte w
  BK-504/BK-505 (2026-09-29, ADR-011,
  `docs/evaluation/results/bk-504-505-verification.md`). Zostaje: reguły świeżości
  dla źródeł bez publikowanej częstotliwości aktualizacji (`unknown` do czasu
  decyzji właściciela danych) i potwierdzenie wartości `redistribution` przez
  właścicieli danych.
- Brak deterministycznego przeglądarkowego E2E (BK-701). ~~Brak zamrożonego
  podkładu mapy PDF~~ — mapy PDF są zamrożone w snapshocie (BK-503); podkład
  wyłącznie jako zapisany artefakt z SHA-256, domyślnie neutralne tło.

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

## Badania ilościowe BK-601–BK-603 (2026-09-30)

- BK-601: badanie poprawności na zamrożonym korpusie (`evaluate_reference_corpus.py
  --study`): pola i statusy sekcji, rejestr 107 błędów i ograniczeń z kategorią przyczyny
  i dowodem, zamrożony manifest, determinizm. Metryki nagłówkowe są 1,0/0 pp, bo runner
  odczytuje zamrożone obserwacje; poziom pól ujawnia m.in. literał `NULL` z KIMPZP jako
  symbol strefy.
- BK-602: eksperyment centroid vs przecięcie: kontrole ręczne, dolne granice z udziałów
  korpusu (3 z 11 przypadków musi stracić strefę) i symulacja na 30 rzeczywistych
  działkach. Wynik dokładny na realnych strefach niezmierzony (RU WFS nieosiągalny).
- BK-603: korpus 21 anotowanych próbek parsera MPZP; precision 1,00, recall 0,25 (0,20 na
  zbiorze końcowym), confidence informacyjne, ale niedoszacowane.
- Ustalenia poza zakresem: odrzucenie legacy `srsName` RU w `pog_fetch` oraz `NULL` jako
  symbol strefy w `mpzp.py`.

Szczegóły: [odbiór BK-601–603](evaluation/bk-601-603-verification.md).

## Parser MPZP v3 — przygotowanie (Epic 20, 2026-10-01)

Wykonane: **PV3-03** — ewaluator wielosilnikowy (`legacy` działa, `v3` i `hybrid` zarejestrowane i
niedostępne), metryka `source_consistent` (na korpusie BK-603 legacy: 51/75 poprawnych wartości ze
spójnym źródłem; 9 z 72 wartości zgodnych z wymaganą pochodzi z innej strony), porównanie sparowane,
odtwarzanie odpowiedzi modelu (`--llm-replay`), `--live` zablokowany bez flagi, klucza i dostawcy.
**PV3-02 częściowo** — protokół anotacji, profil walidatora `final-v2`, narzędzia drugiego anotatora,
zamrożenia i logu pobrań; **korpus nie został zbudowany** (lista źródeł czeka na zatwierdzenie
właściciela, anotację muszą wykonać ludzie). **PV3-01** — spike’iem (`backend/scripts/llm_spike.py`) zmierzono 2026-10-02 model
`gemini-3.8-flash` na 10 blokach (schemat 30/30, cytaty 261/261, p95 8,3 s, ok. 0,04 USD na analizę od 2027;
wynik mechaniczny GO); **decyzja właściciela GO z 2026-10-05 wpisana w ADR-012** (z progami, warunkami i zamrożoną bramką jakości dla Task 20.17). Stan i polecenia:
[odbiór PV3-01–03](evaluation/pv3-01-03-verification.md).

## Parser MPZP v3 — fundament (PV3-04–06, 2026-10-02)

Zacommitowane w `main` (stan zweryfikowany w AU-009, 2026-10-08): **PV3-04** — wspólna reguła symbolu strefy (forma kanoniczna
NFKC + przycięcie + zwinięcie odstępów, ≤ 40 znaków, jeden plik przypadków dla backendu i UI),
tolerancyjne na odstępy dopasowanie symbolu w tekście bez scalania `MN` z `MN.1`, migracja `027`
(`analyses.resolved_zone_symbol` 20 → 50), `MPZP_RESULT_SCHEMA_VERSION` 2.2. **PV3-05** — drzewo
struktury dokumentu z warstwą normalizacji (liście dzielą znormalizowany tekst bez luk, strony
monotoniczne) i `ZoneBlock` z zakresem znaków, stronami i ścieżką. **PV3-06** — resolver zakresu strefy
(strategie 1–6 i 0) i silnik `v3` w ewaluatorze: na korpusie BK-603 `source_consistent` 88/88 (legacy
51/75), zasięg zakresu 294/294 (każdy układ 1–6 = 1,0), zanieczyszczenie `zone_section` 0/428.
**Wyniki są rozwojowe** (resolver rozwijano na tych 21 próbkach, a etykiety układów nadał asystent AI);
niezależna ocena wymaga zbioru końcowego z PV3-02. Produkcja nadal używa trybu `legacy`; przełączenie
na bloki to Task 20.14 (z podniesieniem wersji kontraktu). Stan i polecenia:
[odbiór PV3-04–06](evaluation/pv3-04-06-verification.md).

## Parser MPZP v3 — silnik wartości, warunki, pewność (PV3-07–09, 2026-10-03)

Zacommitowane w `main` (stan zweryfikowany w AU-009, 2026-10-08): **PV3-07** — jeden deterministyczny silnik wartości liczbowych
oparty o leksykon (`quantity_engine`/`quantity_lexicon`/`quantity_normalization`), współdzielony przez
aplikację i ewaluator (ewaluator nie ma już własnej normalizacji); wersja parsera `mpzp-parser/3.0-det`.
Na korpusie BK-603 (anotacje AI, wynik rozwojowy): silnik `v3` precision 1,000 (248/248), recall 0,992
(248/250; rozwojowy 89/89, końcowy 159/161), `legacy` (to, co działa na produkcji) recall 0,832, precision
1,000 — wcześniej 0,248. Poprawność **wartości** wśród znalezionych jest niższa niż detekcja (v3: 0,940;
skan symulowany 0,43 — pozostałe błędy w raporcie odbioru). **PV3-08** — wartość z warunkiem (typ dachu,
podstrefa, typ budynku, położenie) to `conditional`, nie `conflict`; pole płaskie API jest `null`, gdy nie
ma jednej wartości bezwarunkowej; warunki widać w UI, PDF i paczce audytowej (`audit-exporter/1.1.0`);
migracja `028` (JSONB `conditions`, `value_kind`). **PV3-09** — pewność jako skalibrowane
prawdopodobieństwo z cech dowodu zamiast stałych mnożników; artefakt
`backend/app/core/mpzp_confidence_calibration.json`, pasma i próg ręcznej weryfikacji z pomiaru;
na podziale `final` ECE 0,059, pasmo wysokie 0/297 błędów, niskie 23/137, pasmo średnie puste.
`MPZP_RESULT_SCHEMA_VERSION` 2.2 → 2.5 (stare snapshoty nie są trafieniem cache). Tryb zakresu
pozostaje `legacy` (Task 20.14). Stan, liczby i ograniczenia:
[odbiór PV3-07–09](evaluation/pv3-07-09-verification.md), [ADR-013](adr/ADR-013-mpzp-quantity-engine-conditions-calibration.md).

## Parser MPZP v3 — ścieżka modelu językowego (PV3-10/11, 2026-10-03/05)

Zacommitowane w `main` (stan zweryfikowany w AU-009, 2026-10-08): **PV3-10** — port `StructuredExtractionProvider` w warstwie
`application` i adapter Gemini (REST, `httpx`) w `infrastructure/llm`: stały host, HTTPS, bez przekierowań,
limity rozmiaru i czasu, ponowienia z `Retry-After`, wyłącznik awaryjny, wykrywanie innego modelu niż
skonfigurowany, brak klucza i treści żądania w logach i wyjątkach; **wyłączony domyślnie**
(`mpzp_llm_enabled=false`, klucz `SecretStr` z `GEMINI_API_KEY`), bez ruchu sieciowego. **PV3-11** — kontrakt
wyjścia (dosłowny cytat, surowa wartość, cytat zakresu, jawne „nie znaleziono”), wersjonowana instrukcja i
schemat ze skrótami w provenance, usługa jednego żądania na blok z podziałem dużych bloków bez gubienia i
dublowania cytatów oraz złote odpowiedzi dla układów 1–6. Testy offline (305), pokrycie nowych modułów
98–100%. **Nic nie zmierzono na żywo**, a złote odpowiedzi to nie nagrania modelu; ścieżka nie jest
podłączona do analizy (Task 20.12–20.14), więc zachowanie aplikacji i kontrakt API bez zmian. Stan i
ograniczenia: [odbiór PV3-10/11](evaluation/pv3-10-11-verification.md), aneks w
[ADR-012](adr/ADR-012-mpzp-llm-extraction.md).


## Parser MPZP v3 — bramki, cache i tryby parsera (PV3-12–14, 2026-10-05)

Zacommitowane w `main` (stan zweryfikowany w AU-009, 2026-10-08): **PV3-12** — weryfikator kandydatów modelu
(`planning/domain/candidate_verifier.py`) z bramkami G1–G8 względem tekstu bloku strefy; jedyna droga
wartości z modelu do wyniku; przyjęty kandydat ma status `ai_candidate` (nigdy `verified`) i
`extraction_method=llm_verified`, strona i zakres znaków pochodzą z dopasowania w bloku. **PV3-13** — tabela
`mpzp_llm_extractions` (migracja **029** po head `028`) z wyjściem modelu i skrótami wejścia (bez treści
żądania i danych użytkownika), idempotentny zapis, retencja i `python -m app.modules.planning purge-llm-cache`;
evidence parametru niesie `model_id`, `prompt_version`, `response_sha256`. **PV3-14** — `MPZP_PARSER_MODE`
(`legacy` domyślny i bajtowo bez zmian, `v3`, `hybrid_shadow`, `hybrid`), model tylko dla par bez wartości,
z konfliktem albo niskim zakresem, scalanie z pierwszeństwem rdzenia, `MPZP_LLM_UNAVAILABLE` + `partial` przy
niedostępności, ten sam potok we wznowieniu po ręcznym symbolu, sygnatura cache z trybem, wersją parsera,
promptu i modelem; silnik `hybrid` w ewaluatorze. **Nic nie zmierzono na żywo** i nie ma nagranych odpowiedzi
modelu; domyślne zachowanie się nie zmienia (tryb `legacy`, `mpzp_llm_enabled=false`). Stan, testy i
ograniczenia: [odbiór PV3-12–14](evaluation/pv3-12-14-verification.md), aneks w
[ADR-012](adr/ADR-012-mpzp-llm-extraction.md).

## Parser MPZP v3 — limity, dane, bramka jakości (PV3-15–17, 2026-10-05)

Zacommitowane w `main` (stan zweryfikowany w AU-009, 2026-10-08): **PV3-15** — limity ścieżki modelu w analizie (żądania, tokeny na
analizę/dokument/żądanie, termin propagowany od startu analizy) i między analizami (adapter
`infrastructure/llm/budget.py`: częstotliwość, współbieżność, twarde limity doby i miesiąca w rejestrze
`mpzp_llm_usage`, migracja **030**), wspólny wyłącznik awaryjny, 10 scenariuszy wstrzykiwania błędów przez
prawdziwy adapter (także w pełnej analizie na PostGIS) — zawsze wynik deterministyczny, `partial`, kod
ostrzeżenia i licznik. **PV3-16** — [ADR-014](adr/ADR-014-llm-data-handling.md): lista dozwolonych pól
żądania, redakcja sekretów w logach, skaner sekretów w CI, korpus prompt injection (14 przypadków), kill
switch plikowy; korpus ujawnił i naprawiono kwadratowy koszt rdzenia dla długiego tokenu. **PV3-17** —
**zablokowane**: brak zbioru końcowego (Task 20.2), biegu hybrydy na żywo i przeglądu ręcznego przez
człowieka; narzędzie bramki i bieg `legacy`/`v3` są w `evaluation/results/parser-v3/` (decyzja
`NOT_DECIDABLE`). Stan i ograniczenia: [odbiór PV3-15–17](evaluation/pv3-15-17-verification.md).

## Parser MPZP v3 — oznaczenie, monitoring i dokumentacja (PV3-18–20, 2026-10-05)

Zacommitowane w `main` (stan zweryfikowany w AU-009, 2026-10-08): **PV3-18** — wartość z modelu językowego jest w UI, PDF i pakiecie audytowym zawsze
oznaczona („odczyt automatyczny (model językowy), zweryfikowany z cytatem — wymaga potwierdzenia”) wraz z cytatem, stroną,
warunkami i provenance (model, wersja instrukcji, skrót odpowiedzi); wartość deterministyczna nigdy nie jest tak oznaczana;
UI ma filtr „do ręcznej weryfikacji”, a brak danych (`null`) jest różny od 0 i od braku ograniczenia; odczyt nie jest
przedstawiany jako interpretacja prawna. Pakiet audytowy `audit-exporter/1.2.0` niesie blok `model_provenance` — z odpowiedzi
modelu tylko skrót i zweryfikowany cytat, a cytat zgodnie z polem `redistribution` źródła. **PV3-19** — metryki ścieżki
(opóźnienie, tokeny, koszt szacowany, odrzucenia per bramka, cache, degradacje), komponent `components.llm` w `GET /health`
(`ok`/`degraded`/`disabled`, nigdy „failed”, bez wpływu na gotowość), przypięcie modelu i promptu (`model_pin.json`,
kontrola w czasie działania, w CI i skryptem) oraz skrypt kontroli dryfu. **PV3-20** — [runbook](operations/mpzp-llm.md) z
próbą (kontener i proces), ADR-012/ADR-014 z tabelami artefaktów i skrótów. **Nie ma** biegu `--live` na zbiorze złotym,
więc przypięta para (`gemini-3.8-flash`, `mpzp-extraction/1`) jest nieoceniona; progi alarmów to propozycja bez kalibracji na
ruchu; bramka z Task 20.17 pozostaje `NOT_DECIDABLE`, a domyślny stan to `legacy` i `mpzp_llm_enabled=false`.
Zbiór ewaluacyjny BK-603 ma anotacje asystenta AI bez niezależnego przeglądu człowieka i jest rozwojowy dla silnika v3.
Stan i ograniczenia: [odbiór PV3-18–20](evaluation/pv3-18-20-verification.md).

## Parser MPZP v3 — jeden silnik, regresja, bramka przełączenia (PV3-21, 2026-10-05)

Zacommitowane w `main` (stan zweryfikowany w AU-009, 2026-10-08), **częściowo**: domyślny tryb **nie** został przełączony, bo bramka z Task 20.17 jest
`NOT_DECIDABLE` i nie było okresu cienia — zamiast tego przełączenie blokuje zapis przesłanek
`backend/app/core/mpzp_parser_rollout.json` oceniany przez `scripts/check_parser_default_switch.py` (test CI). W repozytorium
jest jeden silnik ekstrakcji: liczby z `quantity_engine`, zapisy opisowe z nowego `planning/domain/descriptive_engine.py` —
parser MPZP i reguły planistyczne nie mają już własnych wzorców. Wersje: `mpzp-parser/3.1-det`, kontrakt 2.6, `mpzp-rules/2.0`.
Regresja parsera działa w trybach `legacy` i `v3`, opisuje stan faktyczny (dopisane luki zamknięte przez silnik; zostały tylko
dwa prawdziwe braki wartości). Wycofanie do `legacy` opisane w [runbooku §10](operations/mpzp-llm.md) i sprawdzone próbą
(17/17 kroków). Progi okresu cienia to propozycja do potwierdzenia przez właściciela. Stan i ograniczenia:
[odbiór PV3-21](evaluation/pv3-21-verification.md).

### Kontrakt wyniku MPZP, cache i migracje po PV3 (opis zbiorczy, stan 2026-10-05)

- **Wersja kontraktu:** `MPZP_RESULT_SCHEMA_VERSION` = **2.6** (2.2 symbol strefy, 2.3 silnik ilości, 2.4 warunki, 2.5 skalibrowana
  pewność, 2.6 jeden silnik zapisów opisowych — PV3-21); zapisy starsze nie są trafieniem cache. PV3-18–20 **nie zmieniły**
  wersji ani pól API; PV3-21 nie zmienił pól API.
- **`conditions` i `value_kind` (PV3-08):** każda wartość parametru niesie warunki (`kind`, `label`, `quote`) i rodzaj
  `unconditional` / `conditional` / `conflict`; pole płaskie strefy jest `null`, gdy nie ma jednej wartości bezwarunkowej
  (brak wartości ≠ 0). Migracja `028` (JSONB `conditions`, `value_kind`).
- **Provenance modelu (PV3-13/14):** wartość z modelu ma `review_status = ai_candidate`, `extraction_method = llm_verified`,
  `model_id`, `prompt_version`, `response_sha256` (pola emitowane **wyłącznie** dla wartości z modelu; wartość deterministyczna
  ich nie ma), nigdy nie wypełnia pól płaskich i zawsze wymaga ręcznej weryfikacji; więzy bazy `ck_mpzp_parameters_llm_candidate`.
- **Ostrzeżenia i status:** niedostępność modelu / limit / termin / błąd potoku → wynik deterministyczny, `MPZP_LLM_UNAVAILABLE`
  z kodem przyczyny i status `partial`; odrzucenia bramek G1–G8 → `MPZP_LLM_CANDIDATES_REJECTED` z licznikami per bramka i `partial`;
  provenance jest zapisane także dla wyniku niepełnego (snapshot, pakiet audytowy: `model_provenance.warnings`).
- **Cache i rejestr:** tabela `mpzp_llm_extractions` = migracja **029** (zadanie nazywało ją „027”, ale 027/028 zajęły PV3-04/08);
  klucz bloku = SHA-256 (dokument, blok, wersja promptu, wersja schematu, model, parametry), tylko `ok` w retencji
  (`MPZP_LLM_CACHE_RETENTION_DAYS`, 180 dni) jest trafieniem; zapis zawiera wyjście modelu i skróty wejścia, nigdy treść żądania.
  Rejestr zużycia `mpzp_llm_usage` = migracja **030**. Sygnatura cache analizy zawiera tryb parsera, wersję parsera oraz — w trybach
  z modelem — wersje promptu i schematu, model i stan flagi. Head migracji po PV3: `030_mpzp_llm_usage` (po AU-001: `031_source_url_text`, niżej).
- **Zdrowie i pakiet (PV3-18/19):** `GET /health` → `components.llm`; pakiet audytowy `audit-exporter/1.2.0` (`model_provenance`).

## Stan repozytorium po audycie 2026-10-05 (AU-009, 2026-10-08)

- **Punkt odniesienia audytu:** tag `audit-2026-10-05` wskazuje commit `178f70b` (2026-10-05, ostatni commit przed audytem).
- **Dokumenty w historii git:** `docs/adr`, `docs/evaluation` (≈ 28 MB), `docs/progress` (≈ 16 MB) i `backlog.md` są śledzone;
  `.gitignore` nie wyklucza już żadnego z nich (wcześniej 43 pliki pod `docs/` były ignorowane, a README i ten dokument linkowały do
  nich martwymi odnośnikami po `git clone`). Prywatne pozostają wyłącznie `context.md` i `ANALIZA_ARCHITEKTURY_I_PLAN.md`.
  `git status --ignored --short docs` nie wymienia żadnych ignorowanych plików.
- **Status PV3:** prace PV3-04…21 są w historii `main` (wcześniejsze sekcje tego dokumentu opisywały je jako „niezacommitowane”;
  etykiety poprawiono). Domyślny tryb parsera nadal `legacy` — bramka przełączenia (Task 20.17) pozostaje zablokowana.
- **Rozmiar artefaktów:** żaden plik w `docs/evaluation/results/**` nie przekracza 5 MB, więc Git LFS nie jest używany (`git lfs`
  nie jest zainstalowany); `.gitattributes` oznacza dokumenty binarne i zawiera regułę LFS do włączenia, gdy pojawi się większy
  artefakt; `test_large_binary_artifacts_are_tracked_by_lfs_or_stay_under_the_limit` pilnuje progu.
- **Odnośniki:** `backend/tests/test_readme_links.py` sprawdza, że każdy lokalny link w `README.md` i tym dokumencie wskazuje plik
  śledzony przez git (`git ls-files`); działa w CI i w świeżym `git clone`.
- **Sprzątanie:** usunięto roboczy skrypt `backend/_imp.py` i przypadkowy `docs/progress/.DS_Store` (`.DS_Store` jest w `.gitignore`).
- **Praca po audycie (AU-001–AU-010):** AU-001–004 (zapis, ULDK, błędy API, KIMPZP), AU-005–007 (token resume, limiter,
  single-flight), AU-008 (lista podpowiedzi adresowych), AU-010 (podatności, lockfile, lint) opisano w kolejnych sekcjach.
  Zadania bez numeru w tym wykazie (AU-101 i dalsze) są poza zakresem.

## Audyt 2026-10-05 — naprawy P0: zapis, ULDK, błędy API (AU-001–AU-003, 2026-10-06)

Zacommitowane w `main` (AU-009, 2026-10-08). Decyzje: [ADR-015](adr/ADR-015-api-errors-request-id-and-text-column-policy.md); odbiór:
[AU-001–003](evaluation/au-001-003-verification.md). Dokumenty wskazane w zadaniach (`docs/audit/2026-10-05-raport-audytu.md`,
`docs/audit/2026-10-06-backlog-po-audycie.md`) **nie istnieją w repozytorium** — źródłem były treści zadań.

- **AU-001 (zapis analizy):** adres zapytania NMT zawierał cały wielokąt działki, a `source_records.source_url` było `VARCHAR(1000)`
  → `StringDataRightTruncation` i HTTP 500 po 10–36 s pracy. Dokładnie 8 z 30 działek korpusu przekracza 1000 znaków przy starym
  adresie (Warszawa 1, Kraków 2, Legnica 3, Pisz 2 — zgodnie z audytem). Teraz `terrain.source.source_url` =
  adres bazowy + `polygon_sha256` + `vertex_count` (ok. 157 znaków), pełny adres tylko w logu `DEBUG`. Migracja
  **`031_source_url_text`** (head) zmienia 18 kolumn adresów i odnośników na `TEXT`; rollback przycina do poprzedniego limitu ze
  znacznikiem `…`. Pozostałe kolumny `String(n)` mają jawną decyzję w `tests/column_length_policy.py` (174 kolumny: 18 `text`, 40
  `clipped` = `ClippedString(n)` przycina z `…`, 116 `strict` = nadmiar to jawny błąd, nie cicha zmiana skrótu/klucza). Błąd
  `DataError`/`IntegrityError` przy zapisie to `PersistenceError` → **503 `PERSISTENCE_FAILED`** z pełnym kontekstem w logu
  (bez parametrów instrukcji). Znaleziona przy okazji ta sama klasa błędu: identyfikator aktu `mpzp-document:<url>` (klucz
  unikalny `VARCHAR(200)`) — `bounded_act_identifier` skraca go skrótem SHA-256 (wartości ≤ 200 znaków bez zmian).
- **AU-002 (ULDK):** kod statusu to pierwszy token pierwszej linii. `-1 brak wyników` ponawiane raz po 300 ms, drugie `-1` →
  **404 `PARCEL_NOT_FOUND`** z komunikatem „ULDK nie zwróciło działki dla tej lokalizacji (brak działki albo chwilowy błąd
  źródła)”; inne `-1 …` → 503; pusta odpowiedź, brak kodu i nieznany kod → **502 `UPSTREAM_INVALID_RESPONSE`**; błędy transportu
  (połączenie, DNS) → 503 zamiast surowego wyjątku `httpx`. Liczniki per kod odpowiedzi: `GET /health/upstream` (`X-Admin-Key`).
  Zamrożone, rzeczywiste odpowiedzi ULDK: `tests/fixtures/source_contracts/uldk/` (jedyny plik skonstruowany ręcznie jest opisany
  w README katalogu).
- **AU-003 (błędy z `request_id`):** `X-Request-ID` (przyjmuje poprawny, inaczej UUID4) w każdej odpowiedzi i w logu (`[request_id]`);
  każdy błąd ma ciało `ErrorResponse` (`error`, `detail`, `request_id`) — `detail` bez zmian dla dotychczasowych klientów;
  nieobsłużony wyjątek → JSON `INTERNAL_ERROR` **z nagłówkami CORS** (handler `Exception` działa poza `CORSMiddleware`), bez stack
  trace. Mapowanie wyjątków domenowych na kody jest w jednym miejscu (`app/routers/error_handlers.py`). Frontend: osobne komunikaty
  dla 422, 429 (z odliczaniem z `Retry-After`), 5xx („Błąd po stronie serwera. Kod zgłoszenia: …”, bez zachęty do ponawiania),
  502/503 i 0 (tylko prawdziwy brak sieci).
- **Kontrakt wyniku:** pola `AnalyzeResponse` **bez zmian** — `*_SCHEMA_VERSION`, `RESULT_CONTRACT_VERSION` i
  `docs/report/field-mapping.md` nie wymagały aktualizacji. Zmieniła się wartość `terrain.source.source_url`; zapisy sprzed zmiany
  mają dawny, pełny adres.
- **Obserwacja z danych rzeczywistych (nie naprawiana tutaj):** NMT `GetMinMaxByPolygon` odrzuca poligony > 100 000 m² błędem w
  treści ze statusem HTTP 200 — sekcja terenu działki 23,7 ha (`281603_4.0001.431/66`) jest `unavailable` (`SERVICE_REPORTED_ERROR`),
  analiza kończy się 200.
- **Weryfikacja:** kontener jak w CI: **3784 passed**, pokrycie **94,35%**; frontend: 513 testów, pokrycie 97,39%, `typecheck` i `next build` zielone. Test na żywych
  usługach: 8/8 działek z audytu → HTTP 200, brak `StringDataRightTruncation` w logu; wymuszony błąd (baza wyłączona) → JSON z `request_id` i nagłówkami
  CORS; w przeglądarce 404 / 429 z odliczaniem / 5xx z kodem zgłoszenia (nie „brak sieci”). Szczegóły i ograniczenia: [odbiór AU-001–003](evaluation/au-001-003-verification.md).


## Audyt 2026-10-05 — parser KIMPZP: akty w punkcie, zmiany, „brak serwisu” (AU-004, 2026-10-06)

Zacommitowane w `main` (AU-009, 2026-10-08). Decyzje: [ADR-016](adr/ADR-016-kimpzp-discovery-acts-and-source-status.md); odbiór:
[AU-004](evaluation/au-004-verification.md).

- **Stan zastany:** dla `141801_4.0701.23/8` (Góra Kalwaria, raport OnGeo) parser zakładał pary `<th>`/`<td>`, a gmina zwraca
  `<td><b>Klucz</b></td><td>…</td>`; rekordem zostawała tabela „Zmiany tekstowe” → `plan_id='LIV/467/2021'`, `uchwala_url=None`,
  ostrzeżenie `MPZP_DOCUMENT_OR_SYMBOL_MISSING` bez planu. Błędy usług gminnych z HTTP 200 (Warszawa `<oms_error>`,
  Bielsko-Biała `ServiceExceptionReport`) i „brak serwisu dla wskazanego obszaru” kończyły się „nie znaleziono MPZP”.
- **Teraz:** port `KimpzpFeatureInfoParser` (`modules/planning/application`), adapter BeautifulSoup
  `modules/planning/infrastructure/kimpzp_feature_info.py`, typy i agregacja w `modules/planning/domain/kimpzp_discovery.py`.
  Wynik punktu to lista aktów (numer uchwały, data, nazwa, „obowiązuje od”, „utracił moc”, status, linki tekstu/legendy/rysunku/
  BIP/WWW, dziennik, symbole, zmiany) malejąco wg „obowiązuje od”; tabele zmian są wyłącznie `amendments`. Statusy rozłączne
  `available|no_match|no_coverage|unavailable|unknown`; „brak serwisu” → `no_coverage` + `KIMPZP_NO_SERVICE_FOR_AREA`; błąd usługi →
  `MPZP_DISCOVERY_UNAVAILABLE` (`error`). Kilka aktów → `MPZP_MULTIPLE_ACTS_AT_POINT` (różne akty w punktach →
  `MPZP_MULTIPLE_ACTS_ON_PARCEL`), bez wyboru dokumentu i bez parsowania (rozstrzygnięcie: AU-101 / Task 22.1).
- **Kontrakt:** nowe pole `AnalyzeResponse.mpzp_discovery` (`MpzpDiscoverySection` 1.0); `MPZP_RESULT_SCHEMA_VERSION` **2.7**
  (wyniki 2.6 nie są serwowane z cache); snapshot `analyses.mpzp_discovery` — migracja **`032_mpzp_discovery`** (head, kolumna
  nullable bez uzupełniania wstecz). Macierz jakości: MPZP bez stref z rozpoznanym aktem → `partial` (`MPZP_ACT_WITHOUT_ZONE`),
  `no_coverage`/`unavailable` z discovery. Raport PDF: Tabela 3.5; `docs/report/field-mapping.md` odświeżony. UI: karta
  „Akty wskazane przez KIMPZP” w sekcji MPZP (klikalne tylko linki HTTPS).
- **Fixtures:** 10 nieprzetworzonych odpowiedzi KIMPZP z manifestem SHA-256 w `backend/tests/fixtures/source_contracts/kimpzp/`
  (skrypt odświeżania `backend/scripts/capture_kimpzp_fixtures.py`); test własności (tabela zagnieżdżona) i fuzz (600 mutacji).
- **Skutek uboczny:** literał `NULL` nie jest już symbolem strefy (defekt RC-01 badania korpusu usunięty w kodzie; sonda badania
  zwraca `None`).
- **Weryfikacja:** kontener jak w CI: **3902 passed**, pokrycie **94,50%**; frontend 522 testy, pokrycie 97,43%, `typecheck` i
  `next build` zielone. Żywe usługi: działka z audytu zwraca `IV/30/2024` (`…/uch/IV_30_2024.pdf`) i `576/XLVII/2010`,
  `LIV/467/2021` tylko jako zmiana; smoke korpusu 31/31 HTTP 200, 0 × 5xx (Warszawa i Bielsko-Biała: `unavailable`, nie „brak planu”).

## Audyt 2026-10-05 — dostęp i obciążenie: token resume, limiter, single-flight (AU-005–AU-007, 2026-10-08)

Zacommitowane w `main` (AU-009, 2026-10-08). Decyzje: [ADR-017](adr/ADR-017-resume-token-rate-limit-and-single-flight.md); odbiór:
[AU-005–007](evaluation/au-005-007-verification.md). Dokumenty `docs/audit/…` wskazane w zadaniach nadal **nie istnieją** w repozytorium.
Pola `AnalyzeResponse` **bez zmian** — `*_SCHEMA_VERSION`, `RESULT_CONTRACT_VERSION` i `docs/report/field-mapping.md` nie wymagały
aktualizacji; brak nowej migracji (head nadal `032_mpzp_discovery`).

- **AU-005 (token resume):** `POST /analyze/resume` przyjmuje `access_token` (pole body) albo nagłówek `X-Analysis-Token`. Zależność
  `authorized_resume_request` działa przed `get_db`, więc brak/zły/cudzy token to `403` o identycznej treści dla analizy istniejącej i
  nieistniejącej, bez odczytu bazy; `404`/`409` tylko po poprawnym tokenie. Wspólna `ensure_analysis_access` (też raport i dokument).
  Frontend (`useResumeAnalysis`, `resumeAnalysis`, `app/page.tsx`) wysyła token z wyniku i nie wysyła żądania, gdy wynik go nie ma;
  osobny komunikat dla 403.
- **AU-006 (jeden limiter):** `client_key()` ignoruje `X-Forwarded-For`, dopóki `RATE_LIMIT_TRUST_FORWARDED_FOR=true` **i** adres połączenia
  nie jest w `RATE_LIMIT_TRUSTED_PROXIES`; wtedy klientem jest wpis liczony od końca o `TRUSTED_PROXY_COUNT`. Domyślnie `false`
  (`.env.example`, Compose, `Settings`; lokalny `.env` poprawiony). Usunięto limiter i `_client_key()` z `modules/location/api/router.py`.
  Każda trasa publiczna ma limiter (progi: kafle WMS/MVT 1200, geokodowanie 60, wyszukiwarka adresów 30, pozostałe odczyty i sondy 300
  na minutę na klienta) i zwraca 429 z `Retry-After`; pokrycie tras pilnuje test oparty na OpenAPI. Pomiar z audytu (50 żądań,
  limit 5/min, rotowany `X-Forwarded-For`) przepuszcza 5, także przy `TRUST=true` bez zaufanego peera. Limiter nadal działa w procesie
  (N workerów = N × limit); port 8000 w Compose nadal jest publikowany na wszystkich interfejsach.
- **AU-007 (single-flight):** `app/services/singleflight.py` — rejestr lotów w procesie + sesyjna blokada doradcza PostgreSQL
  (`pg_try_advisory_lock(hashtextextended(parcel_identifier, 0))`, osobne połączenie `NullPool`/`AUTOCOMMIT`, limit oczekiwania 90 s).
  `run_analysis`: trafienie w cache bez blokady; chybienie i `force_refresh` przez blokadę, lider po jej zdobyciu ponownie sprawdza cache;
  `force_refresh` przyjmuje wynik nie starszy niż jego żądanie. Identyfikacja ULDK jest współdzielona przez identyczne żądania. Awaria
  lidera → jeden czekający przejmuje rolę (po drugiej awarii błąd jest propagowany); timeout → `503 ANALYSIS_IN_PROGRESS` +
  `Retry-After`. Metryki: `analysis_singleflight_waiters` i liczniki w `GET /health/upstream`, log `singleflight=leader|wait`.
  Wyłącznik `ANALYSIS_SINGLEFLIGHT_ENABLED`. Przy okazji: `get_or_create_parcel` znosi wyścig pierwszego zapisu tej samej działki
  (savepoint + ponowny odczyt) — wcześniej drugi zapis kończył się 503 `PERSISTENCE_FAILED`.
- **Weryfikacja:** kontener jak w CI: **4057 passed**, pokrycie **94,59%** (`rate_limit.py` 100%, `access_control.py` 100%, `singleflight.py` 94%);
  frontend: 524 testy, pokrycie 97,49%, `typecheck` i `next build` zielone. Żywy stos (prawdziwe usługi): 50 żądań z rotowanym
  `X-Forwarded-For` przeszło 5 (reszta 429); 6 równoległych `POST /analyze` → 1 analiza, 1 wiersz, 1 lider i 5 czekających; UI
  wznawia analizę tokenem z wyniku (200), ponowne wznowienie → 409. `live_smoke_corpus.py` (Task 21.11) nadal nie istnieje. Szczegóły i
  decyzje do potwierdzenia: [odbiór AU-005–007](evaluation/au-005-007-verification.md).

## Audyt 2026-10-05 — lista podpowiedzi adresowych (AU-008, 2026-10-08)

Zacommitowane w `main` (AU-009, 2026-10-08). Odbiór: [AU-008](evaluation/au-008-verification.md); nowy ADR nie był potrzebny (zmiana
lokalna: CSS i jeden komponent).

- **Stan zastany:** `ul.suggestions` był elementem przepływu w `.search-panel` (`z-index: 5`), a `.map-controls` (`z-index: 6`) leży
  nad nim — przy 1440×900 lista (y 339–599) była pod panelem POG (od y 358), `document.elementFromPoint` w środku każdej z 5
  pozycji zwracał panel POG (przy 375×812 — 3 z 5).
- **Teraz:** lista to popover (`position: absolute` pod polem w `.address-field`), więc nie przesuwa panelu POG; panel z otwartą
  listą dostaje `search-panel-suggesting` (`z-index: 8`, nad `.map-controls` i `.result-stack`); lista przewija się
  (`max-height: clamp(8rem, calc(100dvh - 20rem), 24rem)`, `overflow-y: auto`). Klawiatura i `aria-activedescendant` bez zmian;
  doszedł Escape i zamykanie klikiem poza panelem.
- **Weryfikacja:** pomiar `elementFromPoint` w przeglądarce: 5/5 pozycji w liście przy 1440×900 i 375×812 (przed zmianą 0/5 i 2/5),
  panel POG nieprzesunięty, przewijanie przy 1440×520, prawdziwe kliknięcie 5. pozycji uruchamia analizę tej pozycji; testy:
  SearchPanel (6 nowych) i kontrakt CSS (4 nowe).

## Audyt 2026-10-05 — podatności, lockfile, lint i typy w CI (AU-010, 2026-10-08)

Zacommitowane i wypchnięte do `main` (AU-009, 2026-10-08). Decyzje: [ADR-018](adr/ADR-018-dependency-lockfiles-multistage-images-and-ci-gates.md);
odbiór: [AU-010](evaluation/au-010-verification.md).

- **Stan zastany:** `npm audit --omit=dev` — 7 podatności (2 krytyczne: `next 16.2.10`, `maplibre-gl 5.24.0`; wysokie: `nanoid`, `sharp`,
  `source-map-js`); `backend/requirements.txt` z samymi dolnymi granicami, bez lockfile'a, z `pytest`/`respx`/`pytest-cov` w
  zależnościach produkcyjnych; obraz produkcyjny kopiował `tests/` i `scripts/`; CI bez `ruff`, `mypy`, `eslint`; obrazy bazowe na ruchomych tagach.
- **Frontend:** `next 16.3.8`, `maplibre-gl 6.13.0` (migracja major: tylko nazwane eksporty oraz jawny adres workera w `lib/maplibreWorker.ts` — bez niego mapa po `next build` nie ładuje kafli; wykryte ręcznie w przeglądarce, nie przez testy jednostkowe), poprawione `postcss`, `nanoid`, `sharp`,
  `source-map-js`, `vitest 4.1.11`, `undici`; `npm audit` (także pełny) = 0 podatności. Nowy `npm run lint` (ESLint 9, flat config
  bez `eslint-config-next`, z powodu podatnego `braces` bez poprawki).
- **Backend:** `requirements.in` + `requirements.lock` (58 pakietów z SHA-256) i `requirements-dev.txt` (47, `ruff`, `mypy`, `pip-audit`,
  `pytest`…); `Dockerfile` wieloetapowy — `runtime` (domyślny) bez `pytest`, `tests/`, `scripts/`, `test` z narzędziami; Compose: `backend-test`
  (profil `test`); obrazy bazowe przypięte digestem; `ruff` (`E4,E7,E9,F`; naprawiono 33 zgłoszenia), `mypy app/modules` z bazą 19 znanych
  zgłoszeń (`mypy-baseline.txt`, bramka na NOWE); `pip-audit` czysty. Dwa buildy `--no-cache` dają identyczną listę pakietów.
- **CI:** zadania `backend` (build `runtime`+`test`, kontrola zawartości runtime, `ruff`, `mypy`, `pip-audit`, testy z `--cov-fail-under=80`),
  `backend-reproducible`, `frontend` (`npm audit --omit=dev --audit-level=high`, ESLint, `tsc`, testy, build).
  Przebieg na `main` w GitHub Actions: wszystkie 3 zadania zielone; test negatywny na jednorazowej gałęzi z podatnym `next` — zadanie Frontend
  czerwone na `npm audit` (PR #369 zamknięty bez scalenia).
- **Zmiana poleceń:** testy backendu uruchamia się w usłudze `backend-test` (`docker compose --profile test run --rm backend-test pytest …`),
  a nie w `backend` (obraz produkcyjny nie zawiera `pytest`); skrypty operacyjne z README działają z hosta (`python3 backend/scripts/…`).
- **Weryfikacja:** backend w kontenerze `backend-test` jak w CI: **4088 passed**, pokrycie **94,59%**; frontend: `npm audit` 0, ESLint, `tsc`, **537 testów**,
  pokrycie 97,72%, `next build` zielone; przebieg w przeglądarce na buildzie produkcyjnym (mapa z kaflami, worker, popover AU-008 5/5).

## Audyt 2026-10-05 — akty MPZP z Rejestru Urbanistycznego, scalenie kanałów (AU-101, Task 22.1, 2026-10-10)

**W historii `main`** (commity `ecbcbb7`…`fcf6a9b`; w chwili odbioru były to zmiany niezacommitowane nad `61f26da`). Decyzje:
[ADR-020](adr/ADR-020-mpzp-acts-ru-channel-precedence-and-state.md); odbiór: [AU-101–103](evaluation/au-101-103-verification.md).

- **Stan zastany:** brak adaptera RU, `mpzp_acts` i `mpzp_status` (zadanie nie było wykonane). KIMPZP wskazywał akty w punktach bez geometrii (AU-004),
  więc działka na styku dwóch planów (Góra Kalwaria `141801_4.0701.23/8`: `IV/30/2024` oraz cz. II) nie miała rozstrzygnięcia, które akty ją naprawdę obejmują.
- **Teraz:** adapter `modules/planning/infrastructure/ru_mpzp_acts.py` na `OgcClient` (WFS 2.0 `app-mpzp`, osie `bbox` północ–wschód dla `urn:ogc:def:crs:EPSG::2180`,
  stronicowanie po 10, pobranie dokumentów po `resourceId` w paczkach po 8, kontrola zamienionych osi po obwiedniach); logika domenowa w
  `modules/planning/domain/mpzp_acts.py` (przecięcie granicy aktu z działką w EPSG:2180 z `make_valid`, minimalne pole 0,5 m²; odrzucenie aktów tylko dotykających obwiedni
  z diagnostyką; status prawny INSPIRE `legalForce/obsolete`; numer uchwały z dokumentu, a nie z dokumentu „przystąpienie”; scalenie z KIMPZP po numerze i po linkach; zgodność kanałów
  `agree|mismatch|single_channel|none`; reguła pierwszeństwa **informacyjna** `mpzp-act-precedence/1.0` — sama data „obowiązuje od” nie rozstrzyga, nierozstrzygnięte akty wracają oba
  z `manual_review_required`); serwis `services/mpzp_acts.py` (kanał RU nigdy nie zgłasza wyjątku; `RU_MPZP_ACTS_ENABLED=false` → kanał nieodpytany).
- **Kontrakt:** `AnalyzeResponse.mpzp_acts[]` (numer, tytuł, daty, status, link, `intersection_area_sqm`/`intersection_pct`, `sources`, zgodność kanałów, granica w WGS84 z
  przycięciem okna i limitem 4000 wierzchołków — `MPZP_ACT_BOUNDARY_OMITTED`), ostrzeżenie `MPZP_CHANNEL_MISMATCH`; `MPZP_RESULT_SCHEMA_VERSION` 2.8 → (3.0 po AU-103); migracja
  **`033_analysis_mpzp_acts`** (JSONB nullable). Akty nie są strefami; RU nie zasila `vector_zones`.
- **UI / PDF / ZIP:** lista aktów z linkiem do uchwały i mapa granic aktów (`MpzpActsList`), raport — Tabele 3.6/3.7 i „Mapa 3a” (tylko gdy są akty), pakiet audytowy — `layers/mpzp_acts.geojson`
  (RU `redistribution: allowed` wg katalogu; eksporter **1.3.0**).
- **Fixtures:** realne odpowiedzi RU (Góra Kalwaria, Kraków — szkic planu zwracany dla obwiedni, Bielsko-Biała, Dygowo) w `backend/tests/fixtures/source_contracts/ru_mpzp_acts/`
  (skrypt `scripts/capture_ru_mpzp_fixtures.py`); test odrzuca fałszywe „zamienione osie” na prawdziwym szkicu krakowskim.
- **Żywe usługi (2026-10-10):** działka z audytu → `IV/30/2024` i `576/XLVII/2010`, stan `ambiguous`, cz. II odrzucona (5,994 m styku); Bielsko-Biała: akt tylko z RU (`available`, 11,84% działki);
  Dygowo: `no_coverage`; Warszawa: `unavailable` (nie „brak planu”).
- **Do decyzji właściciela:** minimalne pole przecięcia 0,5 m²; reguła pierwszeństwa jest tylko informacyjna; reguła świeżości dla kanału `mpzp_ru` nie została wprowadzona (zostaje `unknown`, jak dotąd; to samo `source_id` opisuje wektorowe strefy z importu);
  `redistribution: allowed` dla RU wymaga potwierdzenia prawnego.

## Audyt 2026-10-05 — jawny stan MPZP (AU-103, Task 22.3, 2026-10-10)

Decyzje: [ADR-020](adr/ADR-020-mpzp-acts-ru-channel-precedence-and-state.md); odbiór: [AU-101–103](evaluation/au-101-103-verification.md).

- **Stan zastany:** brak aktów, błąd źródła i „nie znaleziono” zlewały się w jeden komunikat „Nie sprawdzono albo nie znaleziono stref MPZP przecinających działkę”; w macierzy jakości wiersz `mpzp`
  miał „brak źródła”.
- **Teraz:** `AnalyzeResponse.mpzp_status` — `available | ambiguous | no_match | no_coverage | unavailable | unknown` z `reason_code`, kanałami (`kimpzp`, `mpzp_ru`) i źródłem rozstrzygającym;
  tabela decyzyjna w `modules/planning/domain/mpzp_status.py`: „brak serwisu” → `no_coverage`, pusta odpowiedź działającej usługi → `no_match`, timeout/5xx → `unavailable`, dwa nierozstrzygnięte
  akty → `ambiguous`; **`no_match` nigdy przy błędzie żadnego odpytanego kanału**. Komunikaty z jednego modułu `shared/mpzp_status_text.py` (UI, PDF, pakiet audytowy); wiersz `mpzp` macierzy
  jakości dostaje właściwe `source_id`. Po ręcznym wskazaniu strefy (resume) stan jest przeliczany (`_status_after_manual_zone`).
- **Kontrakt:** `MPZP_RESULT_SCHEMA_VERSION` **3.0** (`RESULT_CONTRACT_VERSION` pochodzi z niego; wyniki 2.x nie są serwowane z cache); migracja **`034_mpzp_status`** (head; JSONB nullable);
  test na każdy z 6 stanów w backendzie (`test_mpzp_status_contract.py`) i frontendzie (`MpzpStatusBanner`, fixtures generowane z backendu); moduły frontendu dodane do `coverage.include`.

## Audyt 2026-10-05 — sekcje stref w uchwale: zakresy, wprowadzenia, powody braku (AU-102, Task 22.2, 2026-10-11)

Decyzje: [ADR-021](adr/ADR-021-mpzp-zone-section-detection.md); przyczyny i pomiar: [au-102-zone-section-causes](evaluation/au-102-zone-section-causes.md); odbiór: [AU-101–103](evaluation/au-101-103-verification.md).

- **Stan zastany:** Kraków `MW/U.4` — 0 parametrów w `legacy` i `v3`; na 9 realnych uchwałach (41 stref) tylko 23 strefy miały parametr lub poprawny powód (56%), metryka surowa (parametr z sekcji strefy) 46%/49%.
- **Teraz:** wspólny rozwijacz zakresów symboli (`shared/zone_symbol.py`) w obu trybach i w silniku ilości; wprowadzenia „Ustalenia szczegółowe dla terenu…”, lista po „oznaczone symbolami”, zdanie wyznaczające strefę, wyjątek-liść
  we wspólnym § (`planning/domain/zone_scope.py`); silnik opisowy `descriptive/2` (przeznaczenie z etykiet i zwrotów, `możliwość lokalizacji`, `dopuszcza się`); dowód opisowy z miejsca w bloku (`v3`) albo z segmentów strefy (`legacy`);
  jawne `ZONE_NOT_IN_DOCUMENT` / `SCANNED_NO_OCR` (`services/mpzp_parser_reasons.py`); fetcher idzie za stronami BIP (dokument → karta → plik, do 2 skoków, ta sama witryna, SSRF) — na żywo trzy plany krakowskie
  dały pliki o SHA-256 zgodnym z korpusem.
- **Zbiór i pomiar:** `backend/tests/fixtures/mpzp_live` (9 uchwał z 7 gmin, 41 stref, 110 oczekiwanych parametrów, bez PDF-ów), `scripts/evaluate_mpzp_live.py`, `tests/test_mpzp_live_corpus.py`. Po zmianach **41/41 (100%)** w obu trybach
  (próg 70%), metryka surowa 100%/100%, odtworzenie oczekiwanych parametrów `v3` 94% (103/110), `legacy` 78%; `legacy` nadal zwraca 224 wartości spoza sekcji strefy (`v3`: 0).
  **Adnotacje przygotował asystent AI bez przeglądu człowieka; zbiór nie jest ślepy; brak Warszawy (dziennik nie odpowiada).**
- **Wersje:** parser `mpzp-parser/3.2-det` (blokowy `3.2-det+scope.2`), `descriptive/2`; kontrakt 3.0 (historia: 2.9); migawka BK-603 zamrożona ponownie po przeglądzie (same dodatki), kalibracja pewności powtórzona
  (ECE finalnego zbioru 0,0588), złote odpowiedzi modelu odtworzone (identyczne).
- **Weryfikacja (AU-101–AU-103):** backend w kontenerze jak w CI: **4491 passed**, w tym przebiegu 1 niezwiązany błąd (`test_readme_links`: martwy link do nieistniejącej noty `au-011-013-verification.md` w README — naprawiony 2026-10-12), pokrycie **94,59%**; `ruff` czysto, `mypy` bez nowych zgłoszeń; frontend 573 testy, pokrycie 97,78%, `eslint`/`tsc`/`next build` zielone; live smoke korpusu 30/30 HTTP 200, 0 × 5xx; UI/PDF/ZIP sprawdzone na żywo (zob. [odbiór](evaluation/au-101-103-verification.md)).

## Audyt 2026-10-05 — kompletność analizy i status nagłówkowy (AU-104, Task 22.4, 2026-10-12)

Decyzje: [ADR-022](adr/ADR-022-definicja-complete-i-status-naglowkowy.md); odbiór: [AU-104–106](evaluation/au-104-106-verification.md).

- **Stan zastany:** `complete` wymagał wektora MPZP z lokalnego importu (`vector_intersection`), więc w konfiguracji domyślnej status był zawsze `partial` (0 z 22 analiz audytu, 0 z 30 w live smoke) i nie niósł informacji.
- **Teraz:** `AnalyzeResponse.completeness {checked, total, complete, headline, notes, sections[]}` i `headline` („5 z 8 sekcji sprawdzono; brak: strefy POG, OUZ, OZS, OSDIS, relacja MPZP–POG. Poza zakresem: uzbrojenie terenu, droga i dostęp.”).
  `complete` ⇔ wszystkie sekcje w zakresie są sprawdzone (`available` albo potwierdzone `no_match`); MPZP może być ustalone z aktu i dokumentu (`assignment_method=document_candidate`), a lokalny wektor jest wyższą jakością,
  nie warunkiem. Poza zakresem (jawnie wymienione): transport (brak kontraktu źródła — **od Task 23.2–23.3 transport i zabudowa sąsiednia są w zakresie**, reguła `completeness/1.1`, zob. sekcja BDOT10k niżej), uzbrojenie terenu (tylko podgląd KIUT), relacja MPZP–POG przy `no_match`. Status prawny POG, ręczna weryfikacja i świeżość nie obniżają
  kompletności (są w `notes` i komunikacie). `status` zostaje dla zgodności i wynika z kompletności. Reguła `completeness/1.0` w jednym miejscu (`services/completeness.py`).
- **Zapis i wersje:** migracja **`035_completeness`** (head; `analyses.completeness` JSONB nullable, bez uzupełniania wstecz; odczyt starego zapisu → `origin=reconstructed`, zapisany `status` bez zmian; **`result_contract_version` poszerzone z 64 do 128 znaków**
  — kontrakt `…+completeness-v1.0` miał 71 znaków i bez tego każda analiza kończyła się 503; wykryte dopiero w live smoke). `COMPLETENESS_RESULT_SCHEMA_VERSION` 1.0 w `RESULT_CONTRACT_VERSION`; raport `report-v2/2026.10.12-1`; pakiet audytowy
  `audit-exporter/1.4.0`; `docs/report/field-mapping.md` odświeżony (693 ścieżki).
- **Prezentacja:** UI — pasek kompletności (`CompletenessBar`) zamiast plakietki `partial`; PDF strona 1 — ten sam komunikat, tabela sekcji i zastrzeżenia; ZIP — `analysis.json: completeness` i wiersz README.
- **Wynik na korpusie (live smoke 2026-10-12, 30 działek, 0 × 5xx):** `complete` = **0/30**; sprawdzone sekcje: 2 z 8 (1 analiza), 3 z 8 (17), 4 z 8 (10), 5 z 8 (2). Brakowało: strefy POG i OUZ/OZS/OSDIS (30/30: 12 działek ma
  POG `binding`, ale pokrycie `partial` — discovery bez analizy powierzchniowej; 18 nie ma obiektów POG w RU), relacji MPZP–POG (30/30), MPZP (26/30: błąd KIMPZP albo brak ustalonej strefy), terenu (20/30: WCS NMT „Server disconnected” — w przebiegu godzinę wcześniej 12/30, usługa jest zmienna)
  i ochrony przyrody (1/30). MPZP sprawdzone w 4 analizach (3 × akt i dokument — Kraków, 1 × `no_match`); lokalnego wektora na korpusie nie ma. **Blokuje POG** (ADR-022). Od końca do końca (`tests/test_completeness_e2e.py`: orkiestrator → PostGIS → API →
  cache → PDF) `complete` jest osiągalny bez wektora MPZP — strefa z aktu i dokumentu + POG z wektorami + ISOK, GDOŚ i NMT.

## Audyt 2026-10-05 — discovery POG w RU przez WFS, budżet żądań (AU-105, Task 22.5, 2026-10-12)

Decyzje: [ADR-023](adr/ADR-023-discovery-pog-ru-wfs-budzet-zadan.md); odbiór: [AU-104–106](evaluation/au-104-106-verification.md).

- **Stan zastany:** 46–288 żądań `GetFeatureInfo` WMS RU na analizę (4 967 na 30 działek korpusu) **oraz błąd poprawności**: RU czyta `bbox` dla `EPSG:2180` w kolejności północ,wschód (WMS 1.3.0 i WFS), a klient wysyłał wschód,północ — odpytywał
  lustrzane współrzędne. Z 16 działek korpusu z `binding` w poprzednim silniku 9 (Kraków, gmina 281603) było fałszywych, a 5 działek (Legnica) z obowiązującym POG dostało `unknown`.
- **Teraz:** `services/pog._discover_from_catalog` pobiera cztery typy (akt, OZS, OUZ, strefy) po jednym zapytaniu WFS z `bbox` kafla 50 m (**4–5 żądań**, 123 na 30 działek, 0,13 s mediana), przynależność do działki rozstrzyga przecięcie w EPSG:2180;
  port `RuPogFeaturesSource` + adapter `ru_pog_features` + `CachedRuPogFeaturesSource` (cache (typ, kafel) 24 h, deduplikacja równoległych analiz, błąd zapamiętywany 60 s); budżet `RU_POG_REQUEST_BUDGET` = 6 z alarmem `ru_pog.budget_exceeded`
  w `/health/upstream`; poprawiona tabela osi WMS (`EPSG:2180 → yx`). `POG_RESULT_SCHEMA_VERSION` 2.5 (cache unieważniony).
- **Porównanie z poprzednim silnikiem:** 7 działek `binding` (Bielsko-Biała) identyczne w polach kontraktu POG; 9 fałszywych `binding` → `unknown`; 5 przeoczonych → `binding` — [test porównawczy](../backend/tests/test_pog_discovery_baseline.py) z zamrożonym stanem „przed” i niezależnym parserem prawdy terenowej.
  **Kryterium „identyczne jak przed zmianą” nie jest spełnione dla 9 działek, dla których wynik „przed” był błędny — decyzja do potwierdzenia przez właściciela.**

## Audyt 2026-10-05 — import POG: równolegle, z postępem i wznawianiem (AU-106, Task 22.6, 2026-10-12)

Decyzje: [ADR-024](adr/ADR-024-import-pog-rownolegly-wznawianie-postep.md); runbook i pomiary: [operations/pog-import.md](operations/pog-import.md); odbiór: [AU-104–106](evaluation/au-104-106-verification.md).

- **Stan zastany:** import Bielska-Białej (1397 cech) 359 s (zmierzone 2026-10-12; w audycie 5,5 min), strony po 10 pobierane sekwencyjnie, brak wyjścia w tle, brak wznawiania, artefakt z `timeStamp` (inny skrót przy każdym imporcie).
- **Teraz:** równoległe strony WFS (`--concurrency`, domyślnie 4), postęp z ETA na stderr co ≤ 10 s i jeden JSON na stdout, kody wyjścia 0/1/2/**3** (import niepełny), magazyn stron z SHA-256 i checkpoint w `import_runs.checkpoint`
  (`pog-download-v1`: `running|interrupted|failed|fetched|succeeded|abandoned`), wznowienie po przerwaniu daje ten sam artefakt i to samo wydanie, artefakt bez `timeStamp`/`next`. RU ignoruje `count` > 10, więc większa strona nie jest dostępna.
- **Pomiar (żywy RU, `--dry-run`, stary i nowy kod naprzemiennie):** Bielsko-Biała 359 s → 187 s / 123 s (52% / 34%), Legnica 213 s → 105 s (49%), Sopot 205 s → 68 s (33%).

- **Weryfikacja (AU-104–AU-106):** backend w kontenerze jak w CI: **4744 passed**, 0 failed, pokrycie **94,76%**; `ruff` czysto, `mypy` bez nowych zgłoszeń; frontend 51 plików / 597 testów, pokrycie 97,8%, `eslint`/`tsc`/`next build` zielone; live smoke korpusu 30/30 HTTP 200, 0 × 5xx, 123 żądania WFS do RU na 30 analiz (maks. 5 na analizę), `complete` 0/30 (blokuje POG, ADR-022). Zastrzeżenia i punkty do decyzji właściciela: [odbiór](evaluation/au-104-106-verification.md).

## Droga i zabudowa sąsiednia z BDOT10k (Task 23.1–23.3, BK-304/BK-305; #226, #227, #307, #308, #309; 2026-10-13)

**Niezacommitowane** (drzewo robocze nad `fcf6a9b`, razem z AU-104–106). Decyzje i pozycje „Proponowany”: [ADR-025](adr/ADR-025-bdot10k-droga-i-zabudowa-sasiednia.md); kontrakty: [road_contracts.md](data_sources/road_contracts.md),
[building_contracts.md](data_sources/building_contracts.md); odbiór: [bdot10k-road-buildings-verification.md](evaluation/bdot10k-road-buildings-verification.md).

- **Stan zastany:** issues #226 i #227 były zamknięte, a w kodzie istniał tylko model i przypadek użycia drogi (`find_road_context` na jednym fixture Krakowa) — bez importu, infrastruktury, integracji z analizą, UI, PDF i eksportu; zabudowy sąsiedniej nie było wcale.
  `transport` był „poza zakresem” w kompletności (brak kontraktu źródła).
- **Kanał danych:** WFS `PobieranieBDOT10k` wydaje tylko katalog paczek, więc źródłem są paczki powiatowe (GML 3.2, EPSG:2180) z `opendata.geoportal.gov.pl`. EGiB/KIEG dla budynków **nie istnieje** jako kanał wektorowy (odpowiada WMS, warstwa niepytalna, zakaz
  automatycznego pobierania); budynki pochodzą z `OT_BUBD_A` (85–97% obiektów w korpusie ma geometrię z EGiB). Wpisy katalogu: `bdot10k` (`production`, `contract_confirmed`, `redistribution: derived_only`, świeżość 365 dni od importu) i `kieg_wms` (`research`).
- **Import:** `python -m app.modules.imports bdot10k --powiat <TERYT> [--package <ZIP|https>] [--dry-run]` → artefakt z SHA-256 → wydanie powiatu (`data_releases.scope_key = powiat:<TERYT>`; po jednym aktywnym wydaniu **na powiat**) → `bdot_roads` (`OT_SKJZ_L`, osie jezdni),
  `bdot_buildings` (`OT_BUBD_A`), `bdot_releases` (granica powiatu z `OT_ADJA_A` jako zasięg). Migracja **`036_bdot10k_context`** (head w chwili wdrożenia; obecny head to `037_pog_registry`, zob. sekcja o krajowym stanie POG). Sześć powiatów korpusu: 156 tys. osi i 350 tys. budynków, 0 odrzuceń; czasy 2–25 s (emulacja amd64).
- **Wynik w analizie:** `road_context` (najbliższa oś jezdni: odległość od poligonu działki, relacja, kategoria zarządzania, klasa, promień wyszukiwania 25→500 m) i `neighbour_buildings` (na działce i w buforach 50/100/200 m: liczba, pola, pokrycie, odległości, kondygnacje; do 100 obiektów na liście).
  Statusy rozłączne: `available`, `no_match`, `no_coverage`, `unavailable`, `unknown`; brak wydania to `no_coverage`, **nigdy „brak drogi” ani „brak budynków”**. Wynik nie zawiera pola prawnego dostępu do drogi publicznej — UI, PDF i pakiet audytowy niosą zdanie, że bliskość geometryczna nie jest potwierdzeniem takiego dostępu.
- **Kompletność i jakość:** `completeness/1.1` — transport i zabudowa wchodzą do zakresu (poza zakresem zostaje domyślnie tylko uzbrojenie terenu); macierz jakości ma 11 sekcji (`transport`, `buildings`, `source_id=bdot10k`); `no_coverage` nie ma czasu pobrania, więc świeżość jest „nieustalona”.
- **Wersje i cache:** `road-v1.0`, `buildings-v1.0` w `RESULT_CONTRACT_VERSION`, `quality-v1.1`, raport `report-v2/2026.10.13-1`, `audit-exporter/1.5.0`; `analyses.result_contract_version` poszerzone do 256 znaków; `docs/report/field-mapping.md` odtworzony.
- **Weryfikacja:** pomiar niezależny (Shapely z `gml:posList` oraz GDAL `ogr2ogr`, **nie QGIS**) 30/30 działek; backend w kontenerze jak w CI: **5011 passed, pokrycie 95,29%**; frontend **54 pliki / 621 testów, pokrycie 97,81%, `eslint`/`tsc`/`next build`/`npm audit` zielone**; `ruff` czysto, `mypy` bez nowych zgłoszeń (17 w bazie).
  Live smoke 2026-10-13 (30 działek, 0 × 5xx): droga i zabudowa `available` 30/30, zgodność z pomiarem niezależnym 30/30, `complete` nadal 0/30 (POG). Przeglądarka 1440×900 i 375×812, PDF (30 s.) i ZIP zweryfikowane dla działki z wydaniem i poza pokryciem.
- **Niespełnione / do decyzji:** #226 zakładał EGiB WFS (kanał nie istnieje); porównanie w QGIS wykonano w GDAL; #309 „11 budynków dla `141801_4.0701.23/8`” niesprawdzone (działka poza korpusem, powiat 1418 niepobrany); wartości D1–D8 w ADR-025 czekają na właściciela.

## POG dla całej Polski: krajowy stan, import z GML RU, mapa i analiza (ADR-026; #300, #258, #301, #335, #303; 2026-10-13/14)

- **Błąd i model.** Dotychczas jedno aktywne wydanie POG na źródło: import gminy B ukrywał gminę A w analizie, kafelkach i inspektorze (test `test_pog_multi_commune.py`, czerwony przebieg w `results/pog-national/regression-before-fix.txt`).
  Teraz **temporalny rejestr aktywnych wersji aktów** (`pog_registry_entries` nad rosnącymi `pog_registry_states`, najwyżej jeden otwarty wpis na akt, numery stanów pod globalną blokadą doradczą); wydanie jest rekordem audytowym i dla `pog_app`
  zostaje nieaktywne, brak kopii „przeniesionych” aktów. Migracja **`037_pog_registry`** (obecny head): 7 tabel (stany, wpisy, katalog RU `pog_ru_catalog`, kolejka `import_jobs`, `pog_map_acts/zones/overlays`), zamiana dotychczasowego aktywnego wydania w stan
  `migration`, przywrócenie aktów ukrytych przez dawny model (test danych `test_migration_037_data.py`). ADR-008 dla POG zastąpiony (aneks), ADR-007/009/024 uzupełnione aneksami.
- **Kanał danych.** Publiczne API RU (wykaz bieżących wersji i plik GML całego aktu w PL-2000; kontrakt, BOM, limit 429, układy: `ru_contracts.md`), zamrożone fixtury `backend/tests/fixtures/ru_national/` (5 stron wykazu i 4 realne akty), skrypt
  `backend/scripts/capture_ru_pog_national.py`; WFS (ADR-024) zostaje zapasem. **Import:** `pog-national` (równolegle 1–6, wznawialnie, przyrostowo po tokenie `publishedActId` i `content_hash`, wycofania tylko z kompletnego wykazu z blokadą masową, kody wyjścia 0/1/3),
  `pog-sync` (profil Compose `sync`, blokada doradcza, kolejka, import na żądanie z jawną informacją w odpowiedzi analizy), `pog-withdraw`, `pog-rollback`, `pog-map-rebuild`, `pog-enqueue`, `pog-status`; `GET /health/imports`.
- **Pomiar (2026-10-13):** pełny import krajowy 434 z 435 aktów w **23,2 min**, **3,0 GB** pobrane, baza **3,96 GB** (+3,0 GB artefaktów), 440 żądań do RU, 0 × HTTP 429; przyrostowy bez zmian 10 s; stan #464: 464 akty w 461 gminach (327 `binding`, 112 `in_progress`, 25 `project`),
  518 338 obiektów. Jeden akt odrzucony przez kontrolę jakości: Bolków (udział biologicznie czynny 150% w źródle).
- **Kafle i mapa.** `GET /api/v1/map/pog/state`, `…/states/{state_id}/{z}/{x}/{y}.mvt?edition=binding|project`, `…/features/{id}`, `…/summary`; progi zoomu z serwera (granice aktów od z0, strefy z11, OUZ z12, etykiety z13/z14/z16), osobne źródła obowiązujących i projektów,
  przegląd krajowy w kolorze statusu (`overview_color`, styl `2026.10.14-1`). **Zimne p95 niepustych kafli: z6 13 ms, z11 57 ms, z14 30 ms; największy kafel 199 KB; 0 kafli > 500 KB.** Silnik: `ST_AsMVT` w backendzie (bez kontenera kafli — wyniki uzasadniają).
- **Analiza i cache.** `pog.registry_state_id`, `pog.pinned_act_versions`, cache unieważniany zmianą aktów **gminy działki**; krok lokalnej analizy POG p50 51 ms / p95 83 ms; `POG_RESULT_SCHEMA_VERSION` 2.7 (2.6: przypięcie, 2.7: okruchy stref — issue 303); `docs/report/field-mapping.md` odtworzony (834 ścieżki).
- **UI.** Nowe: panel pokrycia importu (462 gminy w RU / 461 zaimportowanych / 1 czeka, cztery rozłączne stany gminy, ostatnia nieudana próba), inspektor z akt/uchwałą/Dziennikiem/datami/odnośnikami i **profilami jako etykietami (bez `KPT-*`)**, granica aktu na przeglądzie krajowym,
  komunikaty zależne od zoomu („od przybliżenia 12” zamiast „brak obszarów”), nota o pominiętych okruchach. Glify etykiet hostowane lokalnie (`frontend/public/map-fonts`). Zachowane: wybór i analiza działki, panel wyniku.
- **Weryfikacja:** backend w kontenerze jak w CI **5233 passed, pokrycie 95,29%** (moduły nowe ≥ 86%), `ruff` czysto, `mypy` bez nowych zgłoszeń (baza 17); frontend **56 plików / 661 testów, pokrycie 97,97%**, `eslint`/`tsc`/`next build`/`npm audit` czysto;
  live smoke 30 działek: 0 × 5xx, **12/12 działek w gminach z wiążącym POG w RU ma strefy POG (`available`)**, `complete` nadal 0/30 (blokują MPZP i BDOT10k, nie POG); przeglądarka 1440×900 i 375×812. Odbiór: [pog-national-verification.md](evaluation/pog-national-verification.md).
- **Niespełnione / do decyzji:** brak widoku 3D (z13), kafli granic gmin/powiatów, ortofotomapy i wyszukiwarki działek na mapie POG; Warszawa i Kraków bez aktu w wykazie RU; brak zewnętrznego dashboardu i alertów (jest `/health/imports` i flaga `stale`); wartości „Proponowany”
  (redystrybucja kafli, parametry odświeżania, akt z wadą danych) w ADR-026.

## Task 22.8–22.11 (AU-108–111), 2026-10-16

Stan zastany zawierał kolejkę importu POG na żądanie i harmonogram (Task 22.7,
ADR-026), panel importów gmin oraz osobne endpointy `/api/v1/coverage/pog`
i `/api/v1/coverage/bdot10k`. **AU-110 było już zaimplementowane**: dwa ścisłe progi
(< 0,5% i < 1 m²), `topological_slivers` w snapshocie, flaga, pominięcie w tabeli,
nota w UI/PDF, zachowanie pola w bilansie; ADR-026 pkt 11. Uzupełniono testy
równości progów oraz regresję na zamrożonej rzeczywistej geometrii Bielska-Białej.
Dane RU zmieniły się od audytu: zapis wersji 20260901T095300 ma jeden okruch SW
0,1081 m²; historyczny przypadek SW+SU 0,18 m² pozostaje testem kontrolnym.

Uzupełniono AU-108: wspólny `GET /api/v1/coverage`, warstwa „Pokrycie” z granicami
PRG 2479 gmin (wyłącznie wizualizacja), statusy uchwalony/projekt/brak aktu w RU/
brak lokalnych danych/nieustalony, wydania i daty w istniejącej liście gmin,
powiaty BDOT10k z datami, `NO_LOCAL_RELEASE` i akcja „poproś o import” korzystająca
z istniejącej kolejki. Błąd odczytu pokrycia daje kontrolowane 503, bez pozornego
braku planów. Granice PRG są migawką pobraną 16.10.2026, z parametrami i skrótami
odpowiedzi; data pobrania nie jest datą danych planistycznych.

AU-109: jeden nadrzędny zakres Hmin/Hmax z WCS 1 m, także gdy kontrola usługowa
jest niedostępna. GetMinMaxByPolygon jest kontrolą krzyżową w snapshocie;
`NMT_CROSSCHECK_DIFF` pojawia się ściśle powyżej 0,5 m. Bez rastra wartości pochodzą
z usługi z rzeczywistą rozdzielczością siatki i adnotacją o mniejszej dokładności.
PDF ma jeden zakres w tabeli 6.1; starsze wyniki z dwiema seriami są sprowadzane
do nadrzędnego rastra podczas odczytu, bez nadpisania zapisu. Zamrożona odpowiedź
`141801_4.0701.23/8` daje w prezentacji 118,269–118,959 m (deniwelacja 0,69 m).

AU-111: `SourceMetadata.data_as_of`, podstawa daty, jawny status known/unknown
i powód braku; pobranie pozostaje osobnym `fetched_at`. Obsługa dat ISO 19139,
Last-Modified, dat uchwał KIMPZP/RU oraz lokalnych metadanych POG; przy niepełnej
dacie jawny powód, nigdy czas pobrania w jej zastępstwie. „Stan na” jest w macierzy
UI i PDF. Reguły świeżości mogą wskazywać datę zestawu; UI i eksport stosują tę samą
podstawę. Metadane przechodzą do snapshotów i rejestru źródeł. Migracja **038** po
zastanym head **037**, bez zmian migracji historycznych. `terrain` 1.1, `quality`
1.2, nowa sygnatura cache i odświeżone mapowanie pól raportu. Zasady i ograniczenia:
[ADR-027](adr/ADR-027-pokrycie-wysokosci-i-daty-danych.md).

Weryfikacja i ograniczenia odbioru: [notatka AU-108–111](evaluation/au108-111-verification.md).

Końcowa weryfikacja 17.10.2026: backend **5266 passed, 0 failed**, pokrycie **95,31%**;
frontend **672 testy**, typecheck/lint/build zaliczone, pokrycie linii **99,05%**;
dodatkowy odbiór komponentu NMT na rzeczywistym snapshocie działki audytowej — zaliczony.
Pełny live smoke **30/30 HTTP 200, 0 × 5xx API, 0 błędów transportu klienta**, wszystkie
analizy `partial` (lokalne wydania POG trzech gmin poza korpusem, brak paczek BDOT10k
w bazie odbiorowej i niepełne wyniki usług). NMT WCS dostępny 30/30, **7 ostrzeżeń
`NMT_CROSSCHECK_DIFF`**, macierze jakości z poprawnymi odciskami 30/30, zero sekcji bez
daty i bez powodu jej braku. Niepotwierdzony dosłowny historyczny komunikat AU-110
„2 artefakty, 0,18 m²”; dostępna rzeczywista wersja ma jeden okruch 0,1081 m².
Smoke ujawnił także obsłużony wyścig pierwszej rejestracji źródła KIMPZP (1 ostrzeżenie
`MPZP_DOCUMENT_PERSISTENCE_FAILED`), pierwotnie opisany jako otwarta usterka w notatce odbioru.

## KIMPZP: współbieżna rejestracja dokumentów i trwałość cytowań (2026-10-17)

Naprawiono wyścig `SELECT → INSERT` w adapterze dokumentów: źródło, artefakt, wydanie
i akt są rejestrowane atomowo (`ON CONFLICT DO NOTHING`), z zachowaniem metadanych
istniejącego źródła. Blokady wierszy aktu i wersji synchronizują zapis do granicy
transakcji wywołującego. Identyczna ekstrakcja zachowuje ID jednostek prawnych,
do których odwołują się wcześniejsze analizy; zmieniona ekstrakcja nadal jest zapisywana.
Sześć regresji na pełnych 99 stronach rzeczywistej uchwały Kraków Stare Miasto obejmuje
niezależne połączenia PostgreSQL, zimne/istniejące źródło, ten sam/różne akty, powtórzenie
zapisu, metadane i rollback. Opis poprawki i ponownej weryfikacji:
[odbiór KIMPZP](evaluation/kimpzp-concurrency-verification.md).

Ponowna pełna weryfikacja: backend **5272 passed**, pokrycie **95,30%**, Ruff i kontrola
mypy zaliczone; frontend **672 passed**, pokrycie linii **99,05%**, typowanie/lint/build
zaliczone. Końcowy live smoke 30 działek: **30/30 HTTP 200, 0 × 5xx API, 0 błędów
transportu, 0 × `MPZP_DOCUMENT_PERSISTENCE_FAILED`** (poprzednio 1). Kontrola bazy:
jeden rekord `kimpzp`, 39/39 odwołań parametrów prowadzi do istniejących jednostek
właściwej wersji dokumentu; dwie wersje mają komplet 99/95 stron i 1550/1885 jednostek.
Wszystkie analizy nadal `partial` z powodu lokalnego pokrycia i braków usług opisanych
w odbiorze. Bazy testów i smoke były świeże i odseparowane; nie zmieniano zapisanych
wyników analizy w środowisku użytkownika.


## AU-202–204 / Task 23.2–23.4 — droga, zabudowa i pokrycie terenu (2026-10-17)

Przy rozpoczęciu tej pracy droga i zabudowa miały już ścieżkę API–UI–PDF–ZIP (ADR-025 i wcześniejszy protokół
`docs/evaluation/bdot10k-road-buildings-verification.md`). Potwierdzono to ponownymi testami. Uzupełniono brakujący
pomiar działki 141801_4.0701.23/8 na realnym wydaniu powiatu 1418: 10 budynków do 50 m (OnGeo: 11, inna migawka),
odległości zgodne z niezależnym odczytem geometrii w tolerancji 0,05 m. Brak pola prawnego dostępu do drogi pozostaje
chroniony testami; UI/API/PDF zawierają zastrzeżenie. Wskaźniki sąsiednich działek są jawnie unknown.

Dodano klasę `bdot_landcover`, import dwunastu klas PT, port i adapter PostGIS, obliczenia pól przecięć i udziałów
na działce oraz w pierścieniu 200 m, pomiar lasu i orientacyjne bufory 8/12/16 m. Dwanaście klas i kompletność importu
są w class_stats wydania; starszy import samych dróg/budynków daje no_coverage/LAND_COVER_NOT_IMPORTED.
Wynik jest zapisany w analyses.land_cover (migracja 039 po 038), macierz jakości ma sekcję land_cover ze źródłem bdot10k.
API, karta UI z warstwą i legendą, tabela i mapa PDF oraz warstwy ZIP używają tego samego snapshotu.
Luki/nałożenia geometrii powodują unknown; awaria źródła unavailable; braki danych nie dają zer.

Kontrakt land_cover/1.0, buildings/1.1, quality/1.3, completeness/1.2; RESULT_CONTRACT_VERSION unieważnia poprzedni cache.
Konfiguracja cache uwzględnia włączenie sekcji, promień lasu i limit kandydatów. Kontrakt i rzeczywiste zamrożone geometrie:
`docs/data_sources/land_cover_contracts.md`, `backend/tests/fixtures/source_contracts/bdot_land_cover/`.
Decyzja: `docs/adr/ADR-028-pokrycie-terenu-bdot10k.md`. Dokładne wyniki odbioru i ograniczenia:
`docs/evaluation/au202-204-acceptance.md`.

Wdrożenie wymaga migracji oraz ponownego importu pełnych paczek BDOT10k przez operatora. Mini-paczki fixtures są
wyłącznie do testów. Ta praca nie uzupełnia ani nie modyfikuje użytkowej bazy danych; weryfikacja działa na osobnym
PostGIS. Nie tworzono commitów.

Odbiór 2026-10-19: QGIS 4.2.3 niezależnie odczytał GML i potwierdził odległości/relacje dróg oraz
budynki, lasy, udziały i bufory dla 11 działek (tolerancja 0,05 m / 0,05 m²; sumy 100% ±0,01 pp).
Świeży live smoke po unieważnieniu cache tylko w izolowanej bazie: 30/30 HTTP 200, bez 5xx i błędów połączenia.
Wyniki i powtarzalne polecenia są w protokole AU-202–204.
Końcowa bramka: backend **5351 testów, 95,33% pokrycia**; frontend **683 testy, 98,98% linii**,
lint, typecheck i build produkcyjny. Ruff i mypy bez regresji.

## AU-112 / Task 22.12 — dokładny BK-602 (2026-10-17)

Wykonane w drzewie roboczym. [Odbiór BK-601–603](evaluation/bk-601-603-verification.md)
ma teraz wynik dokładny POG: **13 działek, 3 gminy** (Bielsko-Biała 7,
Legnica 5, Sopot 1), dwa identyczne przebiegi offline. Zamrożono surowe strony
WFS i geometrie EPSG:2180 z SHA-256. Wszystkie 30 działek pierwotnego korpusu
odpytano; 18 pustych odpowiedzi zachowano jawnie. Osobne rozszerzenie korpusu
zawiera działkę `226401_1.0002.2/66` z rzeczywistą odpowiedzią ULDK; pierwotne
etykiety i geometrie pozostają bez zmian. Centroid pomija strefę w **5/13**,
wskazuje inną niż dominująca w **1/13** (Legnica: centroid poza działką,
pominięcie 100%). Ograniczenie „RU nieosiągalny” zdjęto. Żądania: URN bbox
północ,wschód; krótki `srsName=EPSG:2180` dla odpowiedzi wschód,północ;
stronicowanie po 10 i `--resume` tylko dla nieudanych odczytów.

Produkcja `_canonical_crs` obsługuje legacy `gml/srs/epsg.xml#2180`; test
parsuje niezmienioną rzeczywistą odpowiedź RU. `NULL` był już odfiltrowywany
przez obecny adapter KIMPZP (`clean_value` zamiast usuniętej funkcji starego
`mpzp.py`); jawny test na odpowiedzi Pisza zamyka ten punkt audytu.
Nie zmienia się `AnalyzeResponse`, schemat bazy ani decyzja architektoniczna.

## AU-113 / Task 22.13 — adresy przed importem PRG (2026-10-17)

Wykonane w drzewie roboczym: **20/20** rzeczywistych zapisów adresu daje wynik
z fallbacku UUG, w tym **6/6** z audytu. Potwierdzono też 20/20 przez endpoint
API na bazie z pustym indeksem. Normalizacja NFC, odstępów, `ul.`/`ulica`,
`al.`, kolejności miejscowość–ulica i ulicy–numeru–miejscowości działa przed
żądaniem UUG. Nazwy gmin z zamrożonego PRG przywracają polskie znaki nazw
miejscowości (także wielowyrazowych). To pomocniczy słownik nazw, nie import
punktów adresowych; dla nieznanej nazwy są najwyżej 6 wariantów, bez
usuwania numeru. Nie gwarantuje to obsługi wszystkich wsi w Polsce.

`SearchPanel` czyta `/api/v1/search/addresses/status`; pusty indeks (także
aktywne wydanie bez rekordów) pokazuje „Wyszukiwanie ograniczone: indeks
adresowy niezaimportowany”. Błąd statusu nie jest utożsamiany z pustym indeksem.
Zmiana schematu JSON UUG, brak słownika lub niefinitywne współrzędne są błędem
dostawcy, nie sukcesem z pustą listą. Zamrożone surowe odpowiedzi z SHA-256:
`backend/tests/fixtures/uug/au113/`. Odtwarzalna sonda:
`backend/scripts/freeze_uug_address_cases.py`. Bez zmiany `AnalyzeResponse`
i bez nowej migracji.

## AU-201 / Task 23.1 — podstawa czterech grup BDOT10k (2026-10-17)

Dotychczasowy import dróg i budynków był już wdrożony. Zachowano równolegle
rozwijany import pokrycia terenu AU-204 (migracja 039) i uzupełniono
**`OT_SULN_L` → `bdot_powerlines`** (migracja **040**, po 039; EPSG:2180,
GiST, FK do wydania, walidacja kontraktu). Powiatowe wydanie obejmuje teraz
osie jezdni, budynki, 12 klas pokrycia terenu i linie energetyczne.
`bdot10k-import/3`, etykieta `-v3` umożliwiają uzupełnienie tego samego hash
paczki wcześniej zaimportowanego bez PT/SULN, zachowując stare wydania.

Pełne oficjalne paczki **1261, 0262, 2461** zaimportowano i powtórzono na
osobnej bazie: 52 138 osi, 140 456 budynków, 30 050 obiektów pokrycia i 1 052
linie. Import 23,70 / 2,39 / 6,97 s; ponowienie zwraca te same wydania
i liczności. Pomiary rozmiarów oraz decyzje opisuje
`docs/adr/ADR-029-bdot10k-import-czterech-warstw.md`; kontrakt dopisano do
[road_contracts.md](data_sources/road_contracts.md). Katalog `bdot10k` pozostaje
`contract_confirmed`, weryfikacja 17.10.2026. Mini-paczki kontraktowe trzech
powiatów zawierają niezmienione pełne klasy SULN i niezależny pomiar długości.

Kolejka z Task 22.7 obsługuje `bdot_county`; POST
`/api/v1/coverage/bdot10k/county/{powiat}/import` i formularz na ekranie pokrycia
zlecają import. Worker: `python -m app.modules.imports pog-sync --no-schedule`
(kolejka bez cyklicznego importu kraju). Pokrycie pokazuje liczności czterech
grup; brak klasy to `null`, potwierdzona pusta klasa to 0. Timeout podlega
ponowieniu, nieznany schemat zatrzymuje publikację. Sam import linii nie
wyprowadza napięcia w kV, stref prawnych ani nowej sekcji analizy uzbrojenia;
to podstawa wektorowa dla dalszej analizy. Istniejące sekcje dróg, budynków
i pokrycia terenu zapewniają ścieżkę API/UI/PDF/audyt. Weryfikacja zbiorcza
i artefakty tej sesji są w `docs/evaluation/results/au112-113/`.

### Ponowny odbiór AU-112, AU-113 i AU-201 — 2026-10-19

[Notatka odbioru](evaluation/au112-113-au201-verification.md) zawiera dowody,
manifesty testowanego kodu i instrukcje odtworzenia. Świeże wyszukiwanie przez
API przy pustym indeksie: **20/20**, w tym **6/6** zapytań audytu. Live smoke
30 działek z `--force-refresh`: **30/30 HTTP 200, 0 × 5xx, 0 braków odpowiedzi**.
Drogi, zabudowa i pokrycie są `available` na 16 działkach w trzech
zaimportowanych powiatach; pozostałe 14 ma `no_coverage`. Analizy pozostają
`partial` z jawnymi brakami innych źródeł i nieustalonymi pomiarami.

Powtórzono podpisane pobranie PDF i ZIP z API; manifest ZIP zgodny **11/11**,
kontrola renderu 38 stron PDF bez problemów z układem. Dowody nie zawierają
tokenów analizy ani podpisanych URL. Skan sekretów: **0 trafień**.
Weryfikacja korzystała z odizolowanej bazy i kopii repozytorium bez `.env`,
montowanej tylko do odczytu. Zachowano zmiany pokrycia terenu z drugiego czatu.

Pełny backend: **5351 passed**, pokrycie **95,33%**; po sześciu ostatnich zmianach
pokrycia terenu dodatkowo **81 passed** na końcowym snapshotcie. Frontend ponownie
w całości: **683 passed**, 97,99% instrukcji / 98,98% linii, lint, typy i build
zaliczone. Ruff bez zgłoszeń, mypy bez nowych zgłoszeń (17 w bazie / 17 bieżących).
Końcowy smoke po tych zmianach ponownie **30/30 HTTP 200, 0 × 5xx**.
Pełne logi i oba manifesty hashy wskazuje notatka odbioru. Zmiany pozostają
w drzewie roboczym; nie tworzono commitów ani nie wdrażano migracji na bazie użytkownika.

## Lokalne wdrożenie BDOT10k i AU-202–204 — 2026-10-19

Na późniejsze polecenie użytkownika zaktualizowano **główną lokalną bazę `dzialki`**:
037 → 038 → 039 → **040_bdot_powerlines**, po wykonaniu prywatnej kopii `pg_dump`.
Wcześniejsze dziewięć analiz zachowano bez zmian (identyczny skrót uporządkowanego JSON rekordów).
Siedem pełnych oficjalnych paczek (0221, 0262, 1261, 1418, 1465, 2461, 2816) daje
181 761 osi jezdni, 437 789 budynków, 115 968 obiektów pokrycia i 5 334 linie energetyczne.
Wszystkie 740 852 geometrie są poprawne, niepuste i w EPSG:2180. Ponowny import 1418 nie tworzy duplikatów.

Lokalny korpus: **30/30 HTTP 200, 0 × 5xx**; drogi, budynki i udziały pokrycia `available` na 30 działkach.
Pomiar lasu: 27 `available`, 3 `no_coverage/null` z powodu wyjścia obszaru wyszukiwania poza pokrycie powiatowe;
macierz jakości tych trzech sekcji jest `partial`. Pozostałe braki źródeł nadal utrzymują nagłówkowy status analiz
`partial`. Nie jest to potwierdzenie kompletności MPZP/POG/ISOK ani pokrycia kraju.

Po restarcie API/UI, snapshot/cache, PDF i ZIP zweryfikowano ponownie dla Góry Kalwarii oraz częściowego
zasięgu lasu w Bielsku-Białej. Poprawiono mylący opis `no_coverage` w PDF i dodano regresję na zamrożonej
rzeczywistej odpowiedzi. Aplikacja: <http://localhost:13000/>, API: <http://localhost:8000>;
lokalny plik override ustawia port i CORS, nie trafia do Git. Kontenery pozostawiono uruchomione.
[Protokół, polecenia i artefakty](evaluation/au202-204-local-deployment-2026-10-19.md).
Wszystkie zmiany nadal bez commitów. Powyższe wcześniejsze wpisy o izolowanych bazach opisują historyczny odbiór,
przed tym poleceniem wdrożenia.
