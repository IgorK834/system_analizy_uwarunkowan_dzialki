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

### Cache kafelków MPZP

Nakładka MPZP nie odpytuje Geoportalu bezpośrednio. Frontend korzysta z
endpointu `GET /api/v1/map/tiles/mpzp/{z}/{x}/{y}.png`, a backend zapisuje
zweryfikowane obrazy PNG w named volume `map_tile_cache`. Dzięki temu kolejne
wejście w ten sam obszar mapy nie czeka ponownie na wygenerowanie `GetMap`.

Domyślnie kafel jest świeży przez 24 godziny, może zostać podany jako `STALE`
przez 7 dni podczas awarii WMS, a cache ma limit 5 GB. Parametry można zmienić
zmiennymi `MAP_TILE_*` opisanymi w `.env.example`. Nagłówek `X-Tile-Cache`
pozwala rozróżnić odpowiedzi `MISS`, `HIT` i `STALE`.

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

## Wymagania

- Docker
- Docker Compose

## Baza danych (PostgreSQL 16 + PostGIS 3.4)

Baza działa jako kontener `db` w sieci Docker. Backend łączy się z nią po hoście `db:5432` — **nie** przez `localhost`.

Dane są przechowywane w named volume `postgres_data`, a kafelki MPZP w
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
