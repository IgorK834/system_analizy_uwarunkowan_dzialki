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

### Wektorowa mapa POG z lokalnego wydania (BK-401–403)

Mapa analityczna planu ogólnego nie korzysta z WMS: backend wystawia kafle
Mapbox Vector Tile z aktywnego, wersjonowanego wydania POG w PostGIS.

- `GET /api/v1/map/pog/releases/active` — metadane aktywnego wydania
  (`release_id`, SHA-256 artefaktu, zasięg, liczba aktów wg statusu, wersja i
  SHA stylu) oraz `tile_url_template` **przypięty do `release_id`**; 404, gdy
  lokalnego wydania nie ma (to nie jest „brak planu”).
- `GET /api/v1/map/pog/releases/{release_id}` — metadane konkretnego, także
  historycznego wydania (odtworzenie stanu mapy).
- `GET /api/v1/map/pog/releases/{release_id}/{z}/{x}/{y}.mvt?edition=all|binding|project`
  — warstwy `zones`, `ouz`, `downtown`, `social_infrastructure_standard`,
  `act_boundary`; pusty kafel to `200` z pustym protobufem, błędne z/x/y lub
  edycja `422`, nieznane wydanie `404`, przekroczony limit obiektów/bajtów
  `413`; `ETag` + `If-None-Match` → `304`, nagłówki `X-Tile-Cache`,
  `X-Pog-Release`, `X-Pog-Edition`, `X-Pog-Tile-Schema`, `X-Pog-Tile-Features`.

Parametry stref w kaflu liczy ta sama funkcja domenowa co analiza działki, więc
kliknięta cecha ma te same wartości co wynik analizy tej samej geometrii. Limity
i cache ustawiają zmienne `POG_TILE_*` (`backend/app/core/settings.py`).

Kolory, progi, jednostki, etykiety 13 ustawowych stref i wzory OUZ/OZS/OSDIS są
w jednym pliku `shared/pog-presentation.json`, czytanym przez frontend
(`lib/pogZones.ts`, `lib/pogThemes.ts`) i backend (`app/core/pog_presentation.py`,
raport PDF). Oba obrazy kopiują go z kontekstu budowania `shared`
(`additional_contexts` w `docker-compose.yml`). Decyzje: `docs/adr/ADR-007-pog-vector-tiles-and-shared-presentation.md`.

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
docker build --build-context shared=./shared --target test -t dzialki-frontend-test ./frontend
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

Port `5432` jest wystawiony wyłącznie na interfejsie loopback hosta (`127.0.0.1`)
w celach developerskich (klient DB, debug) — baza ma domyślne dane dostępowe i
nie jest widoczna w sieci. Kod aplikacji nie powinien używać `localhost:5432`
wewnątrz kontenera backendu.

### Uprawnienia kontenera backendu i cache analiz

Backend działa jako użytkownik `app`, nie `root`. Nowe wolumeny `map_tile_cache`
i `import_artifacts` dziedziczą właściciela z obrazu. Wolumeny utworzone przez
wcześniejszy obraz są własnością `root` i wymagają jednorazowej naprawy:

```bash
docker compose run --rm --user root --entrypoint chown backend \
  -R app:app /var/cache/dzialki /var/lib/dzialki
```

### Dostęp, limity i status analizy

- **Raport PDF i dokument uchwały** (`GET /report/{id}`,
  `GET /analyze/{id}/pending-document`) wymagają parametru `access_token`. Token
  (pole `access_token` odpowiedzi analizy) to HMAC identyfikatora analizy — samo
  zgadywanie kolejnych ID nie wystarcza. Ustaw stały `ACCESS_TOKEN_SECRET`
  (`openssl rand -hex 32`); bez niego tokeny ważą do restartu backendu.
- **Akceptacja/odrzucenie rastrów** (`POST /api/v1/raster-assets/{id}/accept|reject`)
  wymaga nagłówka `X-Admin-Key`. Klucze konfiguruje `ADMIN_API_KEYS`
  (`operator:klucz,...`); operator w audycie pochodzi z klucza. Bez kluczy
  endpointy są wyłączone.
- **Limity zapytań** (429 + `Retry-After`): `POST /analyze` (20/min), z
  `force_refresh=true` (5/min), raport i dokument (30/min), pokrycie KIUT
  (60/min) — na klienta, w oknie 60 s. Limiter działa w procesie, więc przy N
  workerach efektywny limit rośnie N razy. Za zaufanym reverse proxy ustaw
  `RATE_LIMIT_TRUST_FORWARDED_FOR=true` (używany jest ostatni wpis
  `X-Forwarded-For`).
- **Status `complete`**: niedostępność ISOK/GDOŚ lub awaria KIUT obniża status do
  `partial`. Znana luka „brak potwierdzonego kontraktu KIUT/GESUT” jest tylko
  ostrzeżeniem i nie blokuje `complete`.

Wynik analizy `complete` jest serwowany z cache przez `ANALYSIS_CACHE_MAX_AGE_DAYS`
dni (domyślnie 7). Podpis cache obejmuje wersje danych z katalogu, ale nie dane
z usług na żywo (ISOK, GDOŚ, NMT), więc dłuższy TTL oznacza starsze dane o ryzyku.

### Reset bazy danych

```bash
docker compose down -v
docker compose up -d db
```

**Uwaga:** to usuwa wolumen `postgres_data`, czyli **wszystkie** dane w `dzialki`
(zapisane analizy). Nie rób tego bez potwierdzenia, jeśli baza deweloperska
zawiera dane, na których komuś zależy.

#### Baza utknęła w pośredniej rewizji / limit 1600 kolumn (BK-306)

Testy migracji/alembic (`backend/tests/test_alembic_integration.py`,
`test_migration_*.py`, `test_versioned_model.py`) wykonują `alembic
downgrade`/`upgrade`, żeby sprawdzić rollback. Od tej pory (BK-306) robią to
na jednorazowej bazie utworzonej z szablonu `template_postgis`
(`tests/conftest.py::_isolated_migration_database`), a nie na bazie
wskazanej przez `DATABASE_URL` — więc uruchamianie ich lokalnie nie powinno
już dotykać bazy aplikacji.

Jeśli mimo to trafisz na objawy sprzed tej zmiany — `alembic_version`
wskazuje starą rewizję (np. `014_utilities_preview` zamiast najnowszej) i/albo
zapytanie zwraca błąd w rodzaju `tables can have at most 1600 columns`
(PostgreSQL liczy do tego limitu też kolumny już usunięte — `attnum` nie jest
odzyskiwany po `ALTER TABLE ... DROP COLUMN`), to oznacza, że jakiś proces
(stara wersja testów, ręczny `alembic downgrade` na współdzielonej bazie)
zostawił bazę w złym stanie. Sprawdź stan:

```bash
docker compose exec db psql -U app -d dzialki -c "SELECT * FROM alembic_version;"
docker compose exec db psql -U app -d dzialki -c \
  "SELECT count(*) FILTER (WHERE attisdropped) AS dropped, max(attnum) AS max_attnum \
   FROM pg_attribute WHERE attrelid = 'pog_data'::regclass;"
```

Możliwe naprawy, od najmniej do najbardziej inwazyjnej — **zapytaj, zanim
wykonasz którąkolwiek na bazie z danymi, na których komuś zależy**:

1. **Dokończ migrację do head** (nie usuwa danych, jeśli limit kolumn nie
   został jeszcze przekroczony):
   ```bash
   docker compose exec backend alembic upgrade head
   ```
2. **Limit kolumn już przekroczony** (`upgrade head` sam rzuca
   `TooManyColumns`) — kolumn z usuniętymi (`attisdropped`) atrybutami nie da
   się „odzyskać” bez przepisania tabeli. Jedyne wyjście to migracja danych do
   nowej tabeli/bazy (`CREATE TABLE ... AS SELECT` z jawną listą żywych
   kolumn, albo `pg_dump --data-only` + `pg_restore` do świeżo zainicjowanej
   bazy na aktualnym `head`) — zrób to tylko po konsultacji z kimś, kto zna
   wagę danych w tej bazie.
3. **Baza jest tylko środowiskiem testowym bez ważnych danych** — najprościej
   zacząć od zera:
   ```bash
   docker compose down -v
   docker compose up -d db
   docker compose exec backend alembic upgrade head
   ```

## Struktura katalogów

```text
backend/    — API FastAPI, serwisy domenowe, modele, testy
frontend/   — aplikacja Next.js z mapą i panelem wyników
shared/     — artefakty wspólne dla obu obrazów (styl i legenda POG)
docs/       — dokumentacja techniczna
scripts/    — skrypty pomocnicze (migracje, import danych itp.)
```

## Uwaga prawna

Analiza ma charakter **wyłącznie informacyjny** i nie stanowi oficjalnego dokumentu urzędowego ani podstawy do decyzji administracyjnych. Przed podjęciem decyzji inwestycyjnej należy zweryfikować dane w urzędach i u uprawnionych specjalistów.
