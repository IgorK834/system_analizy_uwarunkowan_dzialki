# Działki o dużej liczbie wierzchołków (AU-001)

Zamrożone, rzeczywiste geometrie z korpusu referencyjnego BK-002, które 2026-10-05 kończyły analizę HTTP 500
(`StringDataRightTruncation` w `source_records.source_url`, bo adres zapytania NMT zawierał cały wielokąt).
Pliki pobrano 2026-10-06 z usług urzędowych; geometria jest identyczna z `reference_corpus/artifacts/parcels/`
(sprawdza to `tests/test_long_geometry_analysis.py`).

| Działka | Wierzchołki | Długość adresu NMT z wielokątem |
|---|---|---|
| `146510_8.0502.1/3` (`real-001-…`) | 76 (1 pierścień) | 2874 znaki |
| `126105_9.0001.580/4` (`real-005-…`) | 134 (4 pierścienie) | 5035 znaków |

Dla każdego przypadku:

- `<case>.uldk.txt` — nieprzetworzona odpowiedź ULDK `GetParcelById` (EPSG:2180, `SRID=2180;POLYGON(...)`);
- `<case>.wkt` — WKT wysyłany do NMT, czyli `shapely.wkt` geometrii z ULDK (dokładnie to, co buduje adapter);
- `<case>.nmt.txt` — nieprzetworzona odpowiedź NMT `GetMinMaxByPolygon` na ten WKT (`text/plain`).

Liczba wierzchołków obejmuje powtórzony punkt zamykający pierścień (`shapely.get_num_coordinates`).
