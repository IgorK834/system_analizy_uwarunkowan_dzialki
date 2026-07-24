# Pilot MPZP Kraków — fixture regresyjny

Oficjalny kontrakt WFS MSIP Kraków (warstwy, pola i EPSG:2178) zweryfikowano
24 lipca 2026 r. w BIP Miasta Krakowa oraz odpowiedziach GetCapabilities i
DescribeFeatureType. Warunki MSIP wymagają pisemnej zgody na ciągłe dalsze
udostępnianie fragmentów serwisu, dlatego `mpzp_pilot_krakow` ma status
`contract_required` i produkcyjny guard blokuje publikację.

Z tego powodu repozytorium nie redystrybuuje rzeczywistych geometrii Krakowa.
Testy topologii używają syntetycznego, metrycznego wycinka o schemacie zgodnym
z potwierdzonym kontraktem. Pola przecięcia 600 m² + 600 m² policzono
niezależnie dla prostokątów 10×60 m; po uzyskaniu pisemnej zgody należy
zastąpić fixture rzeczywistym wycinkiem i dołączyć ręczny zrzut/raport QGIS.
