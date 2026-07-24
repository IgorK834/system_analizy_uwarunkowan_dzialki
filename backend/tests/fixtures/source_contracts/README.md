# Fixtures kontraktów źródeł danych

Małe, zanonimizowane fragmenty odpowiedzi urzędowych usług, potwierdzające
kontrakt zadeklarowany w `docs/data_sources/catalog.yaml`. Służą testom
kontraktowym, aby deklaracje katalogu (warstwy, CRS, wersja protokołu) były
weryfikowane wobec realnej struktury odpowiedzi usługi, a nie wymyślane.

| Plik | Źródło | Zakres |
|---|---|---|
| `kimpzp_getcapabilities.xml` | KIMPZP (WMS), GUGiK | Przycięty GetCapabilities: wersja WMS 1.1.1, CRS EPSG:2180, nazwy warstw z warstwą queryable `plany_granice`. |

Uwagi:

- Fixtures dołączane są wyłącznie dla źródeł o statusie `production`
  (potwierdzony kontrakt). Źródła `research` / `contract_required` /
  `placeholder` celowo nie mają fixtures kontraktowych, ponieważ ich kontrakt
  nie został potwierdzony (patrz katalog).
- Usługi ULDK i UUG są usługami REST (nie WMS/WFS), więc nie mają
  odpowiednika GetCapabilities/DescribeFeatureType — ich kontrakt potwierdza
  publiczna dokumentacja GUGiK oraz testy adapterów `test_uldk.py` /
  `test_geocoding.py`.
- Zawartość jest przycięta i zanonimizowana: bez danych osobowych i bez pełnej
  listy metadanych usługi.
