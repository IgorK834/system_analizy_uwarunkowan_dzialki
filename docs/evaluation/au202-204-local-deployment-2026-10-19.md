# Lokalny odbiór wdrożenia AU-202–204 — 2026-10-19

Na polecenie użytkownika wykonano migracje i import pełnych oficjalnych paczek w **głównej lokalnej bazie
Compose `dzialki`**, na istniejącym wolumenie `system_analizy_uwarunkowan_dzialki_postgres_data`.
Nie tworzono commitów. Ten odbiór uzupełnia wcześniejszy [odbiór implementacji](au202-204-acceptance.md),
który korzystał wyłącznie z izolowanych baz.

## Środowisko i zachowanie danych

Przed zmianami: rewizja `037_pog_registry`, 9 zapisanych analiz, bez wydań BDOT10k.
Wykonano kopię `pg_dump -Fc`, sprawdzoną przez `pg_restore --list` (512 wpisów):
`/tmp/au202-204/local-deployment/before-migrations.dump`, prawa `0600`, 8 599 723 B.
SHA-256 i końcowe kontrole zawiera [verification.json](results/au202-204/local-deployment-2026-10-19/verification.json).
Kopia pozostaje poza repozytorium.

Zastosowano kolejno **038_source_dataset_date → 039_land_cover → 040_bdot_powerlines**.
[Log migracji](results/au202-204/local-deployment-2026-10-19/migrations.log) i
[końcowy stan bazy](results/au202-204/local-deployment-2026-10-19/final-state.json).
Skrót MD5 uporządkowanego JSON wszystkich dziewięciu wcześniejszych rekordów `analyses`
przed migracjami i po imporcie, testach oraz restarcie jest identyczny:
`1f9baead1736e4e14257c19fe68161fe`. Nowe analizy odbiorowe zapisano jako kolejne rekordy.
Testów pytest nie uruchamiano przeciwko tej bazie.

Zbudowano aktualny obraz backendu i produkcyjny frontend. Aplikacja działa pod
<http://localhost:13000/>, API pod <http://localhost:8000>; trzy kontenery mają stan healthy.
Port 3000 zajmuje inne środowisko robocze, dlatego użyto lokalnego, ignorowanego przez Git
`docker-compose.override.local.yml` z portem 13000 i odpowiednim CORS. Uruchomienie z katalogu repozytorium:

```sh
docker compose -f docker-compose.yml -f docker-compose.override.local.yml up -d --wait db
docker compose -f docker-compose.yml -f docker-compose.override.local.yml up -d --wait backend frontend
```

Podczas przerwy w pracy kontenery zostały zatrzymane. Ponowne uruchomienie w powyższej kolejności zachowało
zaimportowane dane. Po restarcie powtórzono gotowość API, CORS oraz testy formularza, snapshotu, cache i eksportów.

## Pełne paczki i geometria

Pobrano z oficjalnego katalogu i zaimportowano siedem **pełnych** paczek powiatowych, bez mini-paczek fixtures.
Każde wydanie ma komplet 12 klas PT oraz warstwę SULN; żaden obiekt nie został odrzucony.
Pełny wynik, naprawy geometrii, rozmiary i SHA-256 paczek:
[imports.json](results/au202-204/local-deployment-2026-10-19/imports.json),
[releases.json](results/au202-204/local-deployment-2026-10-19/releases.json).

| Powiat | Nazwa | Stan danych | Osie jezdni | Budynki | Pokrycie terenu | Linie energetyczne |
|---|---|---|---:|---:|---:|---:|
| 0221 | wałbrzyski | 2026-05-07 | 14 185 | 15 543 | 10 335 | 571 |
| 0262 | Legnica | 2026-05-18 | 5 230 | 12 302 | 2 256 | 154 |
| 1261 | Kraków | 2026-05-27 | 33 585 | 90 563 | 22 731 | 607 |
| 1418 | piaseczyński | 2024-12-19 | 25 575 | 87 508 | 26 187 | 1 984 |
| 1465 | Warszawa | 2025-06-01 | 69 505 | 167 099 | 29 160 | 784 |
| 2461 | Bielsko-Biała | 2026-06-03 | 13 323 | 37 591 | 5 063 | 291 |
| 2816 | piski | 2024-09-11 | 20 358 | 27 183 | 20 236 | 943 |
| **Razem** | | | **181 761** | **437 789** | **115 968** | **5 334** |

Importy wykonywano przez `python -m app.modules.imports bdot10k --powiat <TERYT>` w głównym kontenerze backendu.
Ponowne przetworzenie pełnej paczki 1418 zwraca to samo wydanie #78, `created=0`, `activated=0` i te same liczności:
[reimport-1418.json](results/au202-204/local-deployment-2026-10-19/reimport-1418.json).
Sprawdzenie wszystkich czterech tabel: **740 852 obiekty**, wyłącznie EPSG:2180, bez geometrii pustych, null i nieważnych:
[geometry-integrity.json](results/au202-204/local-deployment-2026-10-19/geometry-integrity.json).

## E2E i ograniczenia źródeł

[Live smoke](results/au202-204/local-deployment-2026-10-19/live-smoke.md) pełnego korpusu:
**30/30 HTTP 200, 0 × 5xx, 0 błędów transportu, 0 ponowień po 429**.
Droga, zabudowa i udziały pokrycia są `available` na wszystkich 30 działkach.
Odległość lasu: 27 × `available`, 3 × `no_coverage`, `distance_m=null`:
`246101_1.0056.110/1`, `246101_1.0004.737/26`, `246101_1.0004.741/132`.
W tych trzech przypadkach obszar wyszukiwania do 5000 m wykracza poza pokrycie powiatowe
(`SEARCH_AREA_PARTIALLY_OUTSIDE_COVERAGE`). Udziały działki i pierścienia 200 m są obliczone,
ale macierz jakości słusznie oznacza całą sekcję jako `partial`. Nie oznacza to odległości 0 ani braku lasu.

Nagłówkowy status 30 analiz pozostaje `partial`, ponieważ brakuje także innych sekcji źródłowych, m.in. MPZP,
POG i ISOK. Odbiór nie potwierdza kompletności tych usług ani pokrycia całej Polski.

Po końcowym restarcie sprawdzono rzeczywiste POST-y, identyczność ponownego odczytu z JSONB oraz cache,
podpisane pobranie PDF/ZIP i zgodność manifestów dla:

- `141801_4.0701.23/8`: droga **3,38 m**, **10 budynków** do 50 m, las **12,73 m**;
  PDF 31 stron, ZIP **12 plików**;
- `246101_1.0056.110/1`: droga **5,21 m**, **38 budynków** do 50 m, las `no_coverage/null`;
  PDF 33 strony, ZIP **11 plików**, jakość pokrycia `partial`.

[Podsumowanie eksportów i skróty](results/au202-204/local-deployment-2026-10-19/exports-summary.json).
PDF-y i ZIP-y do lokalnej inspekcji pozostają w
`/tmp/au202-204/local-deployment/export-proof-final/exports/{gora-kalwaria,bielsko-partial-forest}/`.
W PDF skontrolowano mapę, legendę, tabele, brak pola prawnego dostępu do drogi oraz obowiązkową notę interpretacyjną.
Warstwy pokrycia, lasów (gdy ustalone), drogi i budynków trafiają do ZIP zgodnie z katalogiem redystrybucji.
Artefakty nie zawierają tokenów analizy ani podpisanych adresów eksportu.

Przeglądarka: formularz analizy, trzy karty i przełączniki warstw działają na głównym frontendzie.
Przypadek częściowego zasięgu lasu pokazuje `—` i ostrzeżenie, bez fikcyjnego zera.
Kliknięcia PDF i ZIP po restarcie dają **HTTP 200**; UI pokazuje SHA-256 paczki.
Preflight GET z `X-Analysis-Token` dla origin `http://localhost:13000` ma HTTP 200.

Kontrola PDF wykryła mylący opis `no_coverage` sugerujący, że sama działka jest poza pokryciem.
Poprawiono go na „brak pełnego pokrycia zaimportowanymi danymi BDOT10k”.
Regresja wykorzystuje niezmienioną sekcję API i geometrię rzeczywistej działki Bielska-Białej:
`backend/tests/fixtures/source_contracts/bdot_land_cover/2461-partial-forest.json`.
**1 test — sukces, Ruff bez uwag**; test nie łączy się z bazą. Końcowy PDF zawiera poprawiony komunikat.
Nie zmieniono kontraktu JSON ani obliczeń. Wcześniejsze pełne bramki pozostają udokumentowane w odbiorze implementacji:
backend **5351 testów / 95,33%**, frontend **683 testy / 98,98% linii**, typy, lint i build.
