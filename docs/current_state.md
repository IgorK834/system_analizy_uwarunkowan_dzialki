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
5. POG/OUZ ma discovery (także przez WMS), pobieranie wektorów i realne
   przecięcia. Discovery WMS nie zastępuje geometrii analitycznej. Wewnętrzny
   `PogAnalysisResult` potrafi zachować listę stref, ale obecny `PogResult`
   przekazuje do API/bazy/UI/PDF głównie strefę dominującą. Relacja MPZP–POG
   jest informacyjną oceną `compatibility_assessment` par stref
   zidentyfikowanych przestrzennie, z jawną regułą, datą stanu prawnego i
   uzasadnieniem (BK-205, ADR-005) — nie urzędową wykładnią prawa.
6. `save_analysis` utrwala działkę, analizę, sekcje, ostrzeżenia, statusy oraz
   `SourceRecord` także dla częściowego wyniku. Odpowiedź może mieć status
   `complete`, `partial` albo wymagać uzupełnienia. Brak danych źródłowych nie
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
