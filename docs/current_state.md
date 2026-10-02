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
`001`–`026`. Importy przestrzenne mają model źródeł, artefaktów i aktywnego
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
   kandydatami (`manual_zone_context`). `POST /analyze/resume` przyjmuje ręczny
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
wynik mechaniczny GO); **decyzję go/no-go zapisuje właściciel w ADR-012**. Stan i polecenia:
[odbiór PV3-01–03](evaluation/pv3-01-03-verification.md).

## Parser MPZP v3 — fundament (PV3-04–06, 2026-10-02)

Wykonane lokalnie, **niezacommitowane**: **PV3-04** — wspólna reguła symbolu strefy (forma kanoniczna
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
