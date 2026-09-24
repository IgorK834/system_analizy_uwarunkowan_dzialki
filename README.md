# System analizy uwarunkowań przestrzennych działki

Aplikacja do automatycznej analizy potencjału inwestycyjnego działki na podstawie danych GIS, planów miejscowych (MPZP), planów ogólnych gmin (POG), obszarów uzupełnienia zabudowy (OUZ), uzbrojenia terenu, ryzyka powodziowego oraz form ochrony przyrody.

## Uruchomienie

Domyślną ścieżką uruchomienia projektu jest **Docker Compose**. Cały system (backend, frontend, baza PostgreSQL/PostGIS) startuje w kontenerach — nie wymaga lokalnej instalacji PostgreSQL ani innych zależności systemowych na macOS.

### Pierwsze uruchomienie

```bash
cp .env.example .env
docker compose up --build
```

Tylko baza danych (bez backendu):

```bash
cp .env.example .env
docker compose up -d db
```

Tylko backend z bazą:

```bash
docker compose up -d db backend
curl http://localhost:8000/health
```

Po uruchomieniu całego zestawu frontend jest dostępny pod adresem
`http://localhost:3000`, a API pod `http://localhost:8000`.

### Warstwy podglądowe WMS

Nakładki MPZP, POG i uzbrojenia terenu KIUT nie odpytują usług Geoportalu
bezpośrednio z przeglądarki. Frontend pobiera bezpieczny rejestr z
`GET /api/v1/map/preview-sources`, a kafle z
`GET /api/v1/map/tiles/{mpzp|pog|kiut}/{z}/{x}/{y}.png`. Backend weryfikuje PNG
i zapisuje je w named volume `map_tile_cache`.

Każde źródło ma osobny timeout, limit współbieżności i okres ważności w
`backend/app/core/wms_preview_sources.json`. MPZP i POG są świeże przez 24
godziny, KIUT przez 6 godzin; podczas przejściowej awarii może zostać podany
starszy kafel. Nagłówek `X-Tile-Cache` rozróżnia odpowiedzi `MISS`, `HIT` i
`STALE`.

Warstwy służą wyłącznie do podglądu. Brak obiektów nie potwierdza braku planu
ani sieci, a KIUT nie jest używany do wyznaczania odległości do przyłączy.
Źródła, ograniczenia i informacje o buforowaniu są stale widoczne w panelu
warstw oraz w nocie strony.

`GET /api/v1/map/coverage/kiut?lon=&lat=` odpytuje wskaźnikową warstwę WMS
`gesut` i zwraca `covered`, `not_covered` albo `unknown`. Timeout i błąd usługi
zawsze dają `unknown`, nigdy `not_covered`. Ten sam wynik jest zapisywany jako
`utilities_preview` w snapshotcie analizy i trafia do panelu oraz raportu PDF;
nie zawiera odległości ani liczby sieci.

### Lokalny indeks podpowiedzi adresowych

Autocomplete korzysta z lokalnego PostgreSQL/PostGIS zasilanego oficjalnymi,
pełnymi i przyrostowymi paczkami słowników PRG Adresy / EMUiA GUGiK. Pierwszy
pełny import jest zadaniem utrzymaniowym i może pobierać duży wolumen danych:

```bash
docker compose --profile maintenance run --rm address-index-sync
```

Podczas developmentu można ograniczyć import do województwa, powiatu albo
gminy (TERYT ma odpowiednio 2, 4 albo 7 cyfr):

```bash
docker compose --profile maintenance run --rm address-index-sync \
  python -m app.modules.location.infrastructure.dictionary_import \
  sync --mode full --scope 14
```

Kolejne aktualizacje używają checkpointu `verId` poprzedniego wydania:

```bash
docker compose --profile maintenance run --rm address-index-sync \
  python -m app.modules.location.infrastructure.dictionary_import \
  sync --mode incremental --scope 14
```

Gotowość można sprawdzić przez
`GET /api/v1/search/addresses/status` albo polecenie `status`. Nowe wydanie jest
publikowane atomowo po kontroli jakości; w czasie importu API nadal czyta
poprzednie. Dopóki nie istnieje pierwsze wydanie, działa ograniczony fallback
UUG, który nie zapewnia pełnych sugestii dla krótkich prefiksów.

### Testy frontendu

Frontend używa Vitest i React Testing Library. Testy z wymaganym pokryciem można
uruchomić bez lokalnego Node.js, w obrazie testowym:

```bash
docker build --target test -t dzialki-frontend-test ./frontend
docker run --rm \
  -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 \
  dzialki-frontend-test \
  npm run test:coverage
```

### Punkt odniesienia BK-001

[Stan projektu na commicie `8418bdd`](docs/current_state.md) rozdziela funkcje
działające od planowanych i od niezacommitowanych zmian katalogu źródeł.
[Wyniki i ograniczenia pomiaru](docs/evaluation/results/baseline/README.md)
zawierają polecenia, logi, kody wyjścia oraz raporty coverage. Izolowany
pomiar dokładnego commita można odtworzyć poleceniem:

```bash
./scripts/capture_baseline.sh
```

Skrypt korzysta z `git archive`, osobnego projektu Compose i przykładowej
konfiguracji `.env.example`; nie kopiuje lokalnego `.env` ani zmian roboczych.

### Rzeczywisty korpus referencyjny BK-002

[Opis doboru, źródeł i odtwarzania offline](docs/evaluation/corpus.md) dokumentuje
30 rzeczywistych działek z oczekiwanymi wynikami domenowymi. Korpus jest
oddzielony od syntetycznych fixtures i można go sprawdzić bez sieci:

```bash
cd backend
pytest tests/test_reference_corpus.py -q
```

### Ground truth i ewaluacja BK-003/BK-004

[Protokół niezależnego ground truth](docs/evaluation/ground_truth_protocol.md)
opisuje źródła, CRS, kolejność transformacji, tolerancje i drugą sesję dla 20%
próby. [Harness ewaluacyjny](docs/evaluation/harness.md) generuje komplet
raportów offline jednym poleceniem:

```bash
python3 backend/scripts/evaluate_reference_corpus.py \
  --corpus backend/tests/fixtures/reference_corpus/manifest.json \
  --output-dir docs/evaluation/results/reference-corpus \
  --mode offline \
  --fail-on-regression
```

## Wymagania

- Docker
- Docker Compose

## Baza danych (PostgreSQL 16 + PostGIS 3.4)

Baza działa jako kontener `db` w sieci Docker. Backend łączy się z nią po hoście `db:5432` — **nie** przez `localhost`.

Dane są przechowywane w named volume `postgres_data`, a kafelki WMS w
`map_tile_cache`:

- `docker compose down` — zatrzymuje kontenery, **zachowuje** dane w wolumenie,
- `docker compose down -v` — zatrzymuje kontenery i **usuwa** wolumen wraz z danymi bazy.

### Debugowanie bazy (psql)

```bash
docker compose exec db psql -U app -d dzialki
```

Przykładowe zapytania kontrolne:

```sql
SELECT PostGIS_Version();
SELECT srid FROM spatial_ref_sys WHERE srid = 2180;
```

Port `5432` jest wystawiony na hosta wyłącznie w celach developerskich (klient DB, debug). Kod aplikacji nie powinien używać `localhost:5432` wewnątrz kontenera backendu.

### Reset bazy danych

```bash
docker compose down -v
docker compose up -d db
```

## Struktura katalogów

```text
backend/    — API FastAPI, serwisy domenowe, modele, testy
frontend/   — aplikacja Next.js z mapą i panelem wyników
docs/       — dokumentacja techniczna
scripts/    — skrypty pomocnicze (migracje, import danych itp.)
```

## Uwaga prawna

Analiza ma charakter **wyłącznie informacyjny** i nie stanowi oficjalnego dokumentu urzędowego ani podstawy do decyzji administracyjnych. Przed podjęciem decyzji inwestycyjnej należy zweryfikować dane w urzędach i u uprawnionych specjalistów.
