# Odbiór AU-205 / AU-206 — odległości ISOK i GDOŚ

Data realizacji: 2026-10-21/22 (zmiany zacommitowano później; powtórna weryfikacja z 2026-10-27 jest opisana na końcu).

## Kontrakt źródeł

Publiczne GetCapabilities sprawdzono 20.10.2026. Oba WFS-y deklarują brak opłat
i brak ograniczeń dostępu, obsługują EPSG:2180 i wymagają uwzględnienia
formalnej kolejności osi dla URN. GDOŚ potwierdził wszystkie 14 używanych warstw,
w tym geometrię punktową pomników i turbin. ISOK potwierdził `HazardArea`,
`RiskZone` i `ExposedElement`; tylko `HazardArea` ma potwierdzony kontrakt dla
wdrożonych obliczeń. WORP pozostaje `contract_required`.

Dowody: `backend/tests/fixtures/source_contracts/gdos_proximity/`,
`backend/tests/fixtures/source_contracts/isok_proximity/` oraz wcześniejsze
nieprzetworzone odpowiedzi `gdos_*.xml` i `isok_*.xml`.

## Pomiar akceptacyjny

Dla `141801_4.0701.23/8` pełna odpowiedź HazardArea w buforze 1 km zawierała
7 cech. Niezależny pomiar Shapely na pełnej geometrii dał dla Q10
**163,394733 m**, czyli mieści się w kryterium OnGeo **164 m ± 5 m**.
Najbliższe punkty: działka `POINT (652147.068906 460444.158444)`, strefa
`POINT (652271.37280026 460550.20739872)`.

Żywe zapytania GDOŚ z 21.10.2026 dla tej samej działki potwierdziły OChK
„Warszawski” z przecięciem **100%** oraz `numberMatched=0` dla punktowych i
powierzchniowych pomników przyrody w promieniu **200 m**. Nieprzetworzone puste
odpowiedzi i skróty pełnej odpowiedzi OChK zapisano w
`backend/tests/fixtures/source_contracts/gdos_proximity/`.

Dla rzeczywistej działki korpusu `281604_5.0011.107` w gminie Ruciane-Nida
zamrożono także dwie pełne odpowiedzi GDOŚ z 21.10.2026. Test kontraktowy
potwierdza „Puszczę Piską” na 100% działki oraz przecięcie 2 766,727 m²
(1,041285%) z „Ostoją Piską”.

## Zakres E2E

- API: schemat ryzyka 2.0, status/odległość/promień per klasa i warstwa,
  warunkowy stan WORP;
- zapis/cache/jakość: snapshot JSON, podpis `risk-v2.0`, jedno źródło po
  `source_id`, właściwy `source_id` w macierzy jakości;
- UI/mapa: osobne karty sekcji, stany braku/niedostępności, odległości,
  punkty GDOŚ jako warstwa kołowa i jedna pisownia „GDOŚ”;
- PDF/ZIP: tabele bliskości, nota WORP, pola odległości i promienia w GeoJSON;
- failure injection: timeout, HTTP/transport, pusta odpowiedź,
  `ows:ExceptionReport`, niepoprawny XML i zmiana CRS kończą się stanem
  niedostępności albo jawnym brakiem w promieniu, nigdy surowym 500 ani
  komunikatem „brak ograniczenia”.

## Weryfikacja automatyczna i live

- Backend w kontenerze CI, z repozytorium zamontowanym tylko do odczytu i bez
  wartości z `.env`: **5367 passed, 3 deselected, 0 failed**, pokrycie
  **95,27%** przy progu 80%. Port środowiskowy ma pokrycie 100%, adaptery WFS
  97%, a orkiestracja kontekstu 100%. `ruff check .` przeszedł, a kontrola mypy nie
  wykazała nowych zgłoszeń (17 znanych / 17 bieżących).
- Frontend w obrazie `test`: typecheck, **684 testy w 59 plikach**, pokrycie
  97,96% instrukcji / 91,22% gałęzi / 98,97% funkcji / 98,99% linii oraz
  produkcyjny `next build` — wszystko zielone.
- Live smoke Task 21.11 na 30 działkach, sekwencyjnie i bez lokalnego limitera:
  **30/30 HTTP 200, 5xx=0, brak odpowiedzi=0**, p50 11,44 s, p95 48,21 s.
  Wszystkie odpowiedzi miały kontrolowany status `partial`; niedostępność
  zmiennych usług publicznych nie spowodowała surowego błędu serwera.
- Rzeczywisty `POST /analyze` dla `141801_4.0701.23/8` zwrócił HTTP 200,
  `risk_sections/2.0`, powódź `available/near` (Q10 163,395 m, Q100
  150,741 m, Q500 144,385 m) i przyrodę `available/intersection` (OChK
  „Warszawski” 100%, Natura 2000 190,091 m, oba rodzaje pomników bez obiektu
  w 200 m). Rejestr źródeł zawierał dokładnie jeden wpis `isok` i jeden
  `gdos`; WORP był jawnie `contract_required`.
- Po wprowadzeniu portu i adapterów powtórzono żądanie z `force_refresh=true`.
  API ponownie zwróciło HTTP 200, a GDOŚ pozostał `available`. ISOK zwrócił w
  tej próbie upstream HTTP 400; sekcja została poprawnie zdegradowana do
  `unavailable/SERVICE_HTTP_ERROR`, z wpisem jakości i bez fałszywego
  `no_match`, surowego 500 lub utraty pozostałych sekcji. Wcześniejsza udana
  odpowiedź z tej samej sesji pozostaje podstawą pomiarów Q10/Q100/Q500.
- PDF tej analizy zwrócił HTTP 200 i prezentował odległości, wiersze pomników
  oraz notę WORP. Paczka audytowa zwróciła HTTP 200; zawierała warstwy
  `flood_risk.geojson` i `nature_protection.geojson` z odległością, promieniem
  i statusem bliskości. `verify_audit_package.py` potwierdził **9/9 plików**
  zgodnych z manifestem.
- Po publikacji nowych wpisów bezpieczeństwa 22.10.2026 podniesiono wyłącznie
  `certifi` do 2026.10.19 i `Pillow` do 13.0.0. Oba przebiegi `pip-audit`
  zwracają brak znanych podatności; `pip check`, 81 testów ścieżek
  obrazowych/PDF oraz ponowna pełna suita 5367 testów przeszły poprawnie.

## Powtórna weryfikacja 2026-10-27

Cel: niezależnie potwierdzić kryteria akceptacji na bieżącym drzewie i na żywych
usługach, zamiast polegać na powyższym protokole. Stos: izolowany projekt compose
(własna baza, port 18000), żywe ISOK i GDOŚ, przeglądarka wbudowana dla UI.

### Kryteria akceptacji (żywe API, kod po poprawkach)

| Kryterium | Wynik |
|---|---|
| Q10 dla `141801_4.0701.23/8`: 164 m ± 5 m | **163,395 m** (Q100 150,741 m, Q500 144,385 m) |
| OChK „Warszawski” | przecięcie **100%**, 1199,011 m² |
| Pomniki przyrody w 200 m | brak obiektu, oba rodzaje (powierzchniowe, punktowe) |
| Natura 2000 (ptaki) | 190,091 m od granicy działki |
| Jedna pozycja źródła | po jednym wpisie `isok` i `gdos` w `sources` i macierzy jakości; UI i PDF: „GDOŚ” (0 wystąpień „GDOS”) |
| WORP | `contract_required` / `WORP_CONTRACT_NOT_CONFIRMED` |
| Ruciane-Nida `281604_5.0011.107` | GDOŚ `available/intersection`, ISOK `available/none_within_radius` |
| PDF | HTTP 200, 33 strony: odległości Q10/Q100/Q500, nota WORP, pomniki 200 m, warstwy informacyjne |
| ZIP audytowy | HTTP 200, `verify_audit_package.py`: 9/9 plików zgodnych; `flood_risk.geojson` (3 cechy) i `nature_protection.geojson` z `distance_m`, `search_radius_m`, `proximity_status` |
| UI w przeglądarce | karty powodzi i przyrody z odległościami, nota WORP, 14 warstw GDOŚ, wpisy ISOK/GDOŚ w macierzy jakości |

### Znaleziska i poprawki

1. **Live smoke nie obejmował nowych sekcji** (wymóg SW8). `summarize_response` nie
   czytał `risk_sections`. Dodano `_summarize_risk`, histogramy `flood_status`,
   `nature_status`, `worp_status`, delty między przebiegami i sekcję w raporcie
   Markdown. Etykieta `available (relacja)` vs `unavailable (powód)` nie pozwala
   zlać awarii źródła z brakiem obiektu w promieniu. Korpus referencyjny **nie
   zawiera** działki Góra Kalwaria, więc przebieg 30/30 z 22.10 nie dotykał
   kryterium 164 m; kryterium sprawdzono osobnym manifestem dwóch działek.
2. **ISOK, wewnętrzny błąd serwera pod kodem 400.** Na żywo kryterium przy pierwszym
   przebiegu wyszło `unavailable/SERVICE_HTTP_ERROR` dla ISOK i GDOŚ. GeoServer ISOK
   zwraca `java.lang.NullPointerException` w `ows:ExceptionReport` z HTTP 400 przy
   ok. połowie pierwszych prób dla tego BBOX-u (5 z 11 żądań); kolejna próba zwykle
   przechodzi. Odpowiedź zamrożono jako
   `source_contracts/isok_proximity/upstream-npe-400.xml`. Adapter ponawia ten
   przypadek (inne 400 nie), `ISOK_MAX_ATTEMPTS` 2 → 3. Po poprawce 6 z 6 kolejnych
   analiz zakończyło się `available`; przy dwóch próbach spodziewane ok. 1/4 strat.
3. **GDOŚ, przejściowe 500 jednej warstwy.** Sekcja jest atomowa (14 warstw), więc
   jedno 500 unieważniało całość. 500 jest ponawiane; trwały 500 nadal daje
   `unavailable`, nigdy brak obiektów (test).
4. **UI, zduplikowany klucz React.** `key` listy odległości zależał od `layer_name`,
   wspólnego dla Q10/Q100/Q500 (`flood-nz-core:HazardArea`); konsola zgłaszała
   błąd, a React może zdublować lub pominąć elementy. Klucz zawiera `category`.
   Test regresyjny w `ResultPanel.test.tsx` pada na starym kodzie i przechodzi po
   poprawce; po poprawce konsola jest czysta (pozostaje niezwiązany 404 dla
   `/api/v1/map/pog/state` w pustej bazie testowej).

5. **Diagnostyka.** Log sekcji niedostępnej zawierał wyłącznie `error_type`, więc
   `INVALID_RESPONSE` nie dawało się odróżnić od przekroczenia limitu bajtów bez
   odtwarzania żądania. Log zawiera teraz także `reason_code` (stały kod, bez
   współrzędnych ani treści odpowiedzi).

Odrzucona hipoteza: podniesienie `ISOK_TIMEOUT_S`. Czasy 33–36 s zmierzono
`curl`-em na całym pobieraniu, a limit httpx dotyczy pojedynczej operacji odczytu;
w backendzie sekcja ISOK trwa 8–11 s. Limit pozostaje 30 s.

### Weryfikacja automatyczna

- Backend (kontener CI, repo tylko do odczytu, kopia bez `.env`): **5376 passed**, 3 deselected, 0 failed,
  pokrycie **95,27%** przy progu 80% (`isok.py` 97%, `gdos.py` 97%, adapter
  `environment_wfs.py` 97%, port i model środowiskowy 100%); adaptery objęte testami
  kontraktowymi, failure injection (timeout, HTTP, pusta odpowiedź, zmiana schematu)
  i nowymi testami ponowień (NPE-400, trwały NPE-400, inny 400 bez ponowienia, trzecia
  próba, GDOŚ 500). `ruff check .` czysty; mypy: 17 znanych / 17 bieżących.
- Frontend: typecheck, `eslint`, **685 testów w 59 plikach**, pokrycie 97,96% instrukcji
  / 91,22% gałęzi / 98,97% funkcji / 98,99% linii, `next build`.
- Live smoke Task 21.11 na pełnym korpusie 30 działek (żywe ISOK i GDOŚ, kod po
  poprawkach, rozszerzony raport, sekwencyjnie): **30/30 HTTP 200, 5xx = 0, brak
  odpowiedzi = 0**, każda odpowiedź zawiera obie sekcje ryzyka, a WORP jest
  `contract_required` we wszystkich 30. Powódź: 26 `available` (8 przecięć, 13
  `near`, 5 `none_within_radius`) i 4 `unavailable`; przyroda: 28 `available` i 2
  `unavailable`. Żadna awaria nie została zaprezentowana jako brak obiektu. p50
  9,4 s, p95 67,4 s, maksimum 82,5 s (przebieg równoległy do pełnego testu backendu,
  więc opóźnienia są zawyżone). Wszystkie 4 niedostępne sekcje powodziowe dotyczą
  Krakowa (patrz „Otwarte decyzje”).
- Działka odbiorowa `141801_4.0701.23/8` na żywo, kod po poprawkach: 7 z 7 analiz z
  `force_refresh` zwróciło ISOK `available` (przy 8 żądaniach do ISOK dwa 400/NPE
  zostały przeczekane ponowieniem).
- Po zmianie logu w `context.py`: 818 testów modułów kontekstu, ryzyka, ISOK, GDOŚ,
  adapterów, live smoke, audytu i raportu z prawdziwą bazą przeszło poprawnie; ruff
  czysty, mypy bez nowych zgłoszeń. Pełna suita 5376 testów była uruchomiona przed
  tą jedną zmianą logu.

### Otwarte decyzje

- **Działki z Krakowa (`126105`, korpus: real-005…008) nie mają sekcji powodziowej na
  żywo.** ISOK zwraca pełne, nieprzycięte geometrie wielkich stref: dla `real-007`
  odpowiedź ma ok. 59 MiB już przy buforze 0 m (BBOX samej działki), 60 MiB i więcej przy
  500 m i 1 km, a limit adaptera to 32 MiB (`INVALID_RESPONSE`). To nie jest regresja
  bufora 1 km z AU-205, tylko wcześniejsze ograniczenie źródła, ale oznacza, że
  kryterium „udział % bez regresji względem korpusu” (korpus oczekuje dla `real-007`
  przecięć 28,4% / 30,9% / 47,2%) jest spełnione tylko na zamrożonych obserwacjach, a nie
  na żywej usłudze. Do decyzji: podniesienie limitu z przetwarzaniem strumieniowym
  (koszt: ok. 60 MiB i ponad 30 s na działkę), osobne źródło wektorowe zamiast WFS
  INSPIRE albo uznanie Krakowa za jawnie nieobjęty (`unavailable`, nigdy brak strefy).
- ISOK pozostaje zawodny po stronie dostawcy (NPE-400 przy ok. połowie pierwszych
  prób, odpowiedź 15,7 MB). Trzy próby ograniczają skutek, lecz nie eliminują go;
  wynik `partial` z niedostępnym ISOK jest cache'owany 15 minut, więc użytkownik
  potrzebuje `force_refresh`. Do decyzji: zgłoszenie błędu do PGW Wody Polskie,
  ewentualnie dłuższy TTL/krótszy TTL dla `partial` z niedostępnym ISOK.
- WORP pozostaje `contract_required` do potwierdzenia semantyki `RiskZone` i
  `ExposedElement` z właścicielem produktu.
- GDOŚ: w przebiegu 30 działek dwa przypadki (`real-007`, `real-015`) miały
  `nature: unavailable/INVALID_RESPONSE`, niepowtarzalne przy ponownym wywołaniu tych
  samych działek (adapter zwrócił wynik). Przyczyny nie ustalono, bo log nie niósł
  kodu przyczyny; kolejne wystąpienie będzie diagnozowalne po dodaniu `reason_code`.
- Zapisane raporty w `docs/evaluation/results/live-smoke/` (ostatni z 2026-10-13) nie
  mają nowych histogramów. Raportu z przebiegu 30 działek z 2026-10-27 nie dodano do
  repozytorium, bo powstał na pustej bazie testowej (bez POG i BDOT10k) i przy
  obciążeniu maszyny, więc nie nadaje się na punkt odniesienia dla kolejnych porównań.
  Punkt odniesienia powinien zbudować przebieg na docelowym stosie z danymi.

