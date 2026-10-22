# Odbiór AU-205 / AU-206 — odległości ISOK i GDOŚ

Data realizacji: 2026-10-21/22. Zmiany wykonano bez commita.

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

