# Fixtures kontraktów Rejestru Urbanistycznego

Fixtury pobrano ręcznie 3 września 2026 r. z publicznych usług Rejestru
Urbanistycznego. Testy czytają je wyłącznie lokalnie; odświeżanie nie jest
częścią `pytest` ani CI. Dokładny URL, czas UTC i SHA-256 każdego pliku zapisuje
`manifest.json`.

| Plik | Publiczne źródło | Zachowany zakres |
|---|---|---|
| `wms_pog_capabilities_1_3_0.xml` | `https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/wms-pog/wms` | Pełna odpowiedź WMS GetCapabilities 1.3.0: warstwy POG, CRS i formaty GetMap. |
| `wfs_pog_capabilities_2_0_0.xml` | `https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-pog/wfs` | Pełna odpowiedź WFS GetCapabilities 2.0.0: typy APP POG, CRS, format GML 3.2 i `CountDefault=100`. |
| `csw_capabilities_2_0_2.xml` | `https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/csw` | Odpowiedź z jawną negocjacją `version=2.0.2`, w tym `GetRecords` i jego `outputSchema`. |
| `wfs_pog_getfeature_246101.xml` | WFS POG, filtr po `idIIP/.../przestrzenNazw` zawierającym JPT `246101` | Jedna realna cecha `AktPlanowaniaPrzestrzennego` dla planu ogólnego Bielska-Białej. Odpowiedź przycięto do identyfikatorów, tytułu, statusu i metadanych wersji. |

Pełna odpowiedź GetFeature dla aktu Bielska-Białej miała około 300 KB, głównie
przez geometrię granicy i odwołania `wydzielenie`. Elementy te nie są potrzebne
do RPO-A01 i zostały usunięte przez czystą funkcję
`app.core.ru_contracts.trim_wfs_getfeature`. Zachowane węzły, wartości i URI
przestrzeni nazw pochodzą z realnej odpowiedzi; fixture nie zawiera danych
syntetycznych ani danych osobowych.

## Zmierzone ograniczenia

- WMS deklaruje `EPSG:2180` na nadrzędnej warstwie usługi, natomiast nazwane
  warstwy potomne jawnie powtarzają `EPSG:4326` i `CRS:84`. Parser uwzględnia
  dziedziczenie i zwraca wszystkie trzy wartości. `EPSG:3857` nie występuje w
  deklaracjach XML.
- Stan ten różni się od pomiaru z 2 września 2026 r., według którego WMS
  deklarował tylko `EPSG:4326` i `CRS:84`. Fixtura utrwala odpowiedź faktycznie
  pobraną dzień później, bez dopisywania oczekiwanych CRS.
- WFS ma domyślny `EPSG:2180` dla wszystkich sześciu typów i deklaruje
  `CountDefault=100`.
- JPT `246101` miał w dniu pobrania jeden pasujący akt POG w WFS. Nie wynika z
  tego kompletność jego stref ani pozostałych danych przestrzennych.
- Zapytanie CSW bez parametru `version` zwracało domyślnie Capabilities 3.0.0,
  dlatego fixture 2.0.2 i skrypt odświeżający negocjują tę wersję jawnie.

Sposób świadomego odświeżenia i zasady przeglądu zmian opisuje
`docs/data_sources/ru_contracts.md`.
