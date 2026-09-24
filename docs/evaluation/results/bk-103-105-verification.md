# Odbiór BK-103, BK-104 i BK-105

Data odbioru: 24 września 2026 r.

## Zakres kontraktu

Implementacja zamyka jeden przepływ danych POG:

1. oficjalne APP 3.0 z WFS RU jest mapowane na sześć typów domenowych;
2. cały snapshot przechodzi krytyczne QA przed publikacją;
3. artefakt, konfiguracja parsera i release są wersjonowane w PostGIS;
4. analiza przypina `data_release_id` i czyta lokalnie strefy, OUZ, OZS oraz
   OSDIS;
5. `PogResult` w wersji 2.0 przechowuje pełne listy i provenance w bazie,
   cache, API, UI i PDF.

WMS pozostaje wyłącznie źródłem podglądu. Obliczenia są wykonywane w
EPSG:2180. Brak wartości parametru pozostaje `null`; źródłowe `0` pozostaje
zerem. Projekty i akty w toku nie są przedstawiane jako wiążące.

## Artefakty źródłowe

Katalog `backend/tests/fixtures/ru/` zawiera snapshoty `DescribeFeatureType`,
schemat APP 3.0 i pełne `GetFeature` dla:

- `AktPlanowaniaPrzestrzennego`;
- `DokumentFormalny`;
- `StrefaPlanistyczna`;
- `ObszarUzupelnieniaZabudowy`;
- `ObszarZabudowySrodmiejskiej`;
- `ObszarStandardowDostepnosciInfrastrukturySpolecznej`.

`manifest.json` zapisuje URL, czas UTC, SHA-256 i potwierdzone warunki dostępu
każdego pliku. Skrypt `backend/scripts/fetch_ru_contracts.py` odtwarza pełny
zestaw atomowo poza zwykłym CI. Zwykłe testy nie używają internetu.

## Scenariusze końcowe

Testy integracyjne potwierdzają:

- realne metadane Bielska-Białej i wszystkie sześć typów przechodzą bez
  warunku dla konkretnego TERYT;
- `null`, `0`, przecinek dziesiętny, brak jednostki, obcy namespace, błędny CRS
  i nieznany status mają osobne rozstrzygnięcia;
- relacje akt–strefa–dokument oraz OSDIS przechodzą parse → JSON → odczyt;
- niekompletna paginacja, pusta geometria i spadek liczności przekraczający
  50% odrzucają cały import;
- błąd importu B nie zmienia aktywnego A, jawny odczyt historycznego A działa
  po poprawnej publikacji B, a ponowne A nie duplikuje wersji;
- dwie równoległe transakcje zostawiają dokładnie jeden aktywny release;
- po zablokowaniu wywołania discovery RU analiza działa z lokalnego PostGIS i
  zwraca SHA-256, `data_release_id` oraz OSDIS;
- zmiana zestawu aktywnych wydań zmienia sygnaturę cache i powoduje miss;
- działka 1000 m² zachowuje SJ=620 m²/62%, SU=280 m²/28% i
  SN=100 m²/10% przez DB → cache/API → HTML/PDF; UI ma trzy wiersze, a `null`
  i `0` są rozróżnione;
- ręczne wznowienie MPZP zachowuje pełny snapshot POG v2.

## Migracja i rollback

Migracja `015_pog_v2_versioned_release` jest dodana po
`014_utilities_preview`. Stare rekordy `pog_data` otrzymują
`schema_version='1.0'`, `legacy_partial=true` i `result_v2=null`. Migracja nie
tworzy brakujących stref ani nie podnosi kompletności historycznego wyniku.

`alembic downgrade 014_utilities_preview` usuwa wyłącznie pola i tabelę dodane
przez rewizję 015. Oznacza to utratę danych POG v2, dlatego round trip jest
sprawdzany wyłącznie na odizolowanej bazie testowej. Po rollbacku historyczne
pola płaskiego POG v1 pozostają.

## Polecenia odbiorowe

```bash
cd backend
pytest tests/test_pog_ru_mapping.py tests/test_ru_contracts.py \
  tests/test_imports_pog.py tests/test_cache.py tests/test_persistence.py -q

cd ..
docker compose build backend
docker compose run --rm -v "$PWD:/repo:ro" -e REPO_ROOT=/repo backend \
  pytest -m 'not docker_cli' --cov=app --cov-report=term-missing \
  --cov-fail-under=80 -v

cd frontend
npm ci
npm run typecheck
npm run test:coverage -- --run
npm run build
```

Wynik frontendu: 13 plików i 79 testów przeszło; coverage wyniosło 94,49%
statements, 83,79% branches, 96,94% functions i 96,82% lines. Typecheck i build
produkcyjny zakończyły się kodem 0.

Wynik backendu w świeżej bazie PostGIS: 1159 testów przeszło, 3 testy oznaczone
`docker_cli` pominięto. Pomiar coverage wykonano w dwóch procesach z jednym
plikiem `.coverage`, ponieważ pojedynczy proces z WeasyPrint i coverage
przekraczał limit pamięci lokalnego kontenera przy końcowych testach raportu.
Pierwszy proces wykonał 1118 testów, a drugi wszystkie 41 testów mapy i PDF z
`--cov-append`. Wspólny wynik to 88,16% aplikacji; próg 80% pozostał bez zmian.
Zmodyfikowane moduły POG mają od 82% do 100% pokrycia.

Lokalne polecenia równoważnego pomiaru przy ograniczonej pamięci Dockera:

```bash
mkdir -p /tmp/bk-103-105-cov
docker compose run --rm -v "$PWD:/repo:ro" -v /tmp/bk-103-105-cov:/cov \
  -e REPO_ROOT=/repo -e COVERAGE_FILE=/cov/.coverage backend \
  pytest -m 'not docker_cli' \
  --ignore=tests/test_report_map.py --ignore=tests/test_report_pdf.py \
  --cov=app --cov-report= --cov-fail-under=0 -q

docker compose run --rm -v "$PWD:/repo:ro" -v /tmp/bk-103-105-cov:/cov \
  -e REPO_ROOT=/repo -e COVERAGE_FILE=/cov/.coverage backend \
  pytest tests/test_report_map.py tests/test_report_pdf.py \
  --cov=app --cov-append --cov-report=term-missing --cov-fail-under=80 -q
```

Izolowany test migracji wykonał pełny upgrade 001→015, downgrade 015→014 i
ponowny upgrade: 2 testy przeszły. Zestaw skoncentrowany na RU/POG, cache,
persistence, architekturze, resume i PDF: 98 testów przeszło.

Odbiór interfejsu przed BK-701 jest realizowany przez test komponentu
`ResultPanel` z trzema strefami oraz integracyjny test wygenerowanego HTML/PDF.
Nie wymaga dostępu do zewnętrznego RU.
