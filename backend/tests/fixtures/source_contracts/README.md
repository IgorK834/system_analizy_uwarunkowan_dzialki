# Fixtures kontraktów źródeł danych

Małe, zanonimizowane fragmenty odpowiedzi urzędowych usług, potwierdzające
kontrakt zadeklarowany w `docs/data_sources/catalog.yaml`. Służą testom
kontraktowym, aby deklaracje katalogu (warstwy, CRS, wersja protokołu) były
weryfikowane wobec realnej struktury odpowiedzi usługi, a nie wymyślane.

| Plik | Źródło | Zakres |
|---|---|---|
| `kimpzp_getcapabilities.xml` | KIMPZP (WMS), GUGiK | Przycięty GetCapabilities: wersja WMS 1.1.1, CRS EPSG:2180, nazwy warstw z warstwą queryable `plany_granice`. |
| `warsaw_parcels_getcapabilities.xml` / `warsaw_parcels_describe.xml` | WFS BGiK Warszawa | Przycięte kontrakty warstwy `wfs:dzialki`: WFS 2.0, EPSG:2178 i dozwolone pola geometrii działek bez danych właścicieli. |
| `krakow_mpzp_getcapabilities.xml` / `krakow_mpzp_describe.xml` | WFS MSIP Kraków | Przycięty techniczny kontrakt warstw granic i przeznaczeń MPZP. Nie jest potwierdzeniem prawa do produkcyjnej redystrybucji. |

Uwagi:

- Fixture może potwierdzać sam kontrakt techniczny źródła
  `contract_required`, ale nie zmienia jego statusu prawnego. Tak jest dla
  Krakowa: URL, warstwy, pola i CRS są znane, natomiast guard nadal blokuje
  publikację do czasu uzyskania pisemnej zgody wymaganej przez warunki MSIP.
- Źródła `research` i `placeholder` bez potwierdzonego kontraktu technicznego
  celowo nie mają fixtures kontraktowych.
- Usługi ULDK i UUG są usługami REST (nie WMS/WFS), więc nie mają
  odpowiednika GetCapabilities/DescribeFeatureType — ich kontrakt potwierdza
  publiczna dokumentacja GUGiK oraz testy adapterów `test_uldk.py` /
  `test_geocoding.py`.
- Zawartość jest przycięta i zanonimizowana: bez danych osobowych i bez pełnej
  listy metadanych usługi.
