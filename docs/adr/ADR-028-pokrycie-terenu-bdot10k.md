# ADR-028: pokrycie terenu i lasy z lokalnych wydań BDOT10k

Data: 2026-10-17. Status: przyjęta. Zakres: AU-204 / Task 23.4; rozszerzenie ADR-025.

## Kontekst

Drogi i zabudowa korzystają już z paczek powiatowych BDOT10k. Pokrycie terenu jest dostępne w tych samych
paczkach, lecz poprzedni importer pomijał wszystkie klasy PT. Brak importu nie może oznaczać zerowego udziału lasu.

## Decyzja

Importer zapisuje dwanaście klas PT w `bdot_landcover`, z identyfikatorem wydania, klasy, rodzajem i geometrią
EPSG:2180. `bdot_releases.class_stats.land_cover.complete` potwierdza obecność wszystkich klas i brak odrzuconych
obiektów. Zmiana wersji importera wymaga nowego wydania także dla identycznego SHA paczki; stare wydania są niezmienne.
Migracja 039 następuje po faktycznym head 038, bez zmieniania migracji historycznych.

Port jest w `analysis/application/landcover.py`, PostGIS w `analysis/infrastructure/landcover.py`,
wiązanie w composition. Orkiestrator wykonuje zapytania na sesji sekwencyjnie w wątku. Zapis JSONB w analyses
zapewnia odczyt API, PDF i ZIP z tej samej migawki, bez ponownego pobierania danych.

Udziały liczymy osobno dla działki i pierścienia 200 m. Scalamy nakładające się obiekty jednej klasy;
nie normalizujemy wyniku do 100%. Luki i nakładanie klas powodują status `unknown` z powodem.
Las to źródłowe `OT_PTLZ_A/rodzaj=las`, a nie cała klasa terenów zadrzewionych. Pomiar do granicy poligonu lasu,
promień ograniczony konfiguracją; `no_match` wymaga sprawdzenia pokrycia całego promienia. Bufory 8/12/16 m
są orientacyjne, bez interpretacji przepisów. Nie pomniejszają obszaru zabudowy.

Warstwy pochodne trafiają do ZIP przez istniejącą bramkę redystrybucji (`derived_only`). API niesie osobny
status pomiaru lasu, źródło, wydanie, datę stanu danych i przyczynę nieustalenia. Macierz jakości i kompletność
obejmują `land_cover`. Null w starszym zapisie daje `unknown`, uszkodzony zapis — `unavailable`.

## Konsekwencje

Wdrożenie kodu i migracji nie uzupełnia automatycznie starszych wydań; operator uruchamia ponownie import pełnych
paczek. Taka analiza otrzymuje nową wersję kontraktu i sygnaturę cache (uwzględniającą konfigurację sekcji).
Nie dodajemy usługi WFS budynków ani prawnego dostępu do drogi. Wskaźniki sąsiednich działek pozostają jawnie
`unknown`, bo brak ich geometrii. Mierzone dane zabudowy i drogi zachowują istniejące porty i adaptery.
