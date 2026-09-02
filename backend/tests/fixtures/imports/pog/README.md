# Fixtures POG (Plan Ogólny Gminy) — regresja importu

Fixtures pokrywają **dwa różne schematy APP**, ponieważ struktura eksportów POG
realnie różni się między producentami oprogramowania GIS. Importer nie zgaduje
typu warstwy — każda warstwa jest jawnie przypisana do jednego z czterech typów
POG, a surowe atrybuty źródłowe są zachowywane jako dowód.

## `krakow/` — schemat A (EPSG:2178, polskie nazwy atrybutów)

Cztery warstwy: `planning_zone`, `ouz`, `downtown_area`,
`social_infrastructure_standard`. Atrybuty w konwencji krakowskiego MSIP
(`SYMBOL`, `OZNACZENIE`, `STANDARD`). CRS EPSG:2178 zgodnie z hipotezą kontraktu
`pog_pilot_krakow` (status `research` — kontrakt niepotwierdzony).

## `wroclaw/` — schemat B (EPSG:2180, inne nazwy atrybutów)

Te same cztery typy warstw, ale inny producent: atrybuty `zone_code`, `code`,
`service`, CRS EPSG:2180. Pokazuje, że reader mapuje różne schematy na te same
cztery typy domenowe.

## `unknown_crs.geojson` — przypadek negatywny

Deklaruje nieznany kod EPSG (`EPSG::999999`). Odczyt warstwy musi zakończyć się
kontrolowanym `VectorReadError`, aby import nie przyjął danych w nierozpoznanym
układzie współrzędnych.

## Uwaga o danych

Geometrie są **syntetyczne** (małe, metryczne prostokąty). Repozytorium nie
redystrybuuje rzeczywistych danych POG do czasu potwierdzenia kontraktu i
licencji źródła (patrz `docs/data_sources/catalog.yaml`, wpis `pog_pilot_krakow`,
status `research`).
