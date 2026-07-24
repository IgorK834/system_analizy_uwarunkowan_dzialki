# Fixture importu działek

`parcels_2180.geojson` jest małym, całkowicie syntetycznym zbiorem
regresyjnym w EPSG:2180. Nie zawiera danych EGiB ani danych osobowych.
`parcels_2178.geojson` służy wyłącznie do smoke testu CLI z kontraktem
`egib_geometry_warsaw`; również jest zbiorem syntetycznym.

Schemat pól odpowiada publicznemu `DescribeFeatureType` warstwy
`wfs:dzialki` Warszawy, zweryfikowanemu 2026-07-24. Oficjalne, zanonimizowane
wycinki kontraktu i opis warunków użycia znajdują się w
`tests/fixtures/source_contracts/`.

Rzeczywiste rekordy nie są redystrybuowane w repozytorium. Test kontraktu
sprawdza nazwy pól i CRS osobno, a test potoku korzysta z kontrolowanej
geometrii, dzięki czemu przypadki błędów QA są deterministyczne.
