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

## Wymagania

- Docker
- Docker Compose

## Baza danych (PostgreSQL 16 + PostGIS 3.4)

Baza działa jako kontener `db` w sieci Docker. Backend łączy się z nią po hoście `db:5432` — **nie** przez `localhost`.

Dane są przechowywane w named volume `postgres_data`:

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
