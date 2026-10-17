# Odbiór AU-108–AU-111 — Task 22.8–22.11

Weryfikacja końcowa: 2026-10-17. Kod implementowano 2026-10-16; stan źródeł nie jest datą wdrożenia.

Aktualizacja 17.10.2026: wyścig KIMPZP i utratę cytowań przy ponownym zapisie naprawiono.
[Ponowny odbiór po poprawce](kimpzp-concurrency-verification.md) zawiera nowy smoke i testy współbieżności.
Wyniki poniżej zachowują historię pierwszego przebiegu, w którym wykryto usterkę.

## Zakres i stan implementacji

| Zadanie | Stan | Dowód |
|---|---|---|
| AU-108 / 22.8 | Zaimplementowane | Wspólny endpoint `/api/v1/coverage`, 2479 granic PRG, lista wydań POG, daty, powiaty BDOT10k, `NO_LOCAL_RELEASE`, zlecenie importu przez istniejącą kolejkę |
| AU-109 / 22.9 | Zaimplementowane | WCS 1 m nadrzędny; pomiar usługowy zachowany jako kontrola; ścisły próg > 0,5 m i `NMT_CROSSCHECK_DIFF`; jedna tabela wysokości |
| AU-110 / 22.10 | Było zaimplementowane; zweryfikowane i rozszerzone testy | ADR-026 pkt 11, progi AND, flagi i zachowanie okruchów w snapshotach; regresja z rzeczywistej geometrii i testy granic progów |
| AU-111 / 22.11 | Zaimplementowane | Data zestawu lub jawny powód braku, podstawa daty, rejestr źródeł, macierz jakości UI/PDF i reguły świeżości |

Decyzje i ograniczenia danych opisuje [ADR-027](../adr/ADR-027-pokrycie-wysokosci-i-daty-danych.md).

## Odbiór API i interfejsu

W osobnej bazie PostGIS zaimportowano produkcyjną ścieżką trzy rzeczywiste, zamrożone pliki GML:
Sejny (TERC 200901), Jejkowice (241203) i Barczewo (281401). Przeglądarka pobrała rzeczywiste
odpowiedzi API; włączono warstwę pokrycia, sprawdzono listę wydań i dat importu.
Bielsko-Biała bez lokalnego wydania pokazała „Brak lokalnych danych (≠ brak planu)”; kliknięcie
„poproś o import” utworzyło zlecenie #1 w lokalnej kolejce. Worker nie był uruchamiany w tym
scenariuszu; poprawność jego cyklu i deduplikacji sprawdzają testy Task 22.7.

Dowody:

- [metadane wydań trzech gmin](results/au108-111/coverage-summary.json),
- [warstwa pokrycia i panel importów](results/au108-111/coverage-ui.jpg),
- [lista wydań po przewinięciu](results/au108-111/coverage-ui-three-communes.jpg),
- [zlecenie importu dla gminy bez lokalnych danych](results/au108-111/import-request-ui.jpg).

Granice PRG są migawką pobraną 16.10.2026, uproszczoną wyłącznie do podglądu. Nie są źródłem
ustaleń planistycznych; brak aktu w wykazie RU nie jest urzędowym dowodem braku planu.

## NMT i PDF

Zamrożona rzeczywista odpowiedź analizy `141801_4.0701.23/8` z dowodów AU-004 zawierała
117,9–118,5 m z usługi i 118,269–118,959 m z WCS. Po odczycie nową ścieżką zakres nadrzędny
wynosi **118,269–118,959 m**, deniwelacja **0,69 m**; maksymalna różnica kontroli wynosi
0,459 m, więc nie generuje ostrzeżenia > 0,5 m. Testy obejmują równość, wartości poniżej i ponad
progiem oraz awarię kontroli usługowej i zastępczy pomiar bez rastra.

PDF wyrenderowano i sprawdzono wizualnie; tabela 6.2 nie powtarza zakresu wysokości, a tabela
8.1 ma kolumnę „Stan na” i jawny powód nieznanej daty. Poprawiono szerokości kolumn po dodaniu
nowego pola. Dowody: [wysokości](results/au108-111/terrain.png), [macierz jakości](results/au108-111/quality.png).

Dodatkowo wyrenderowano `TerrainCard` w jsdom z pełną sekcją NMT odczytaną przez backend z tej
rzeczywistej odpowiedzi: jeden Hmin 118,269 m, jeden Hmax 118,959 m, deniwelacja 0,69 m,
źródło NMT_WCS i brak poprzedniej pary usługowej. [Test odbiorowy](results/au108-111/au109-ui-acceptance.test.tsx),
[wejście po normalizacji](results/au108-111/au109-terrain-ui.json), [wynik](results/au108-111/au109-ui-acceptance.txt).
To dodatkowy test odbiorowy, poza 672 testami pełnego przebiegu frontendu.

## Testy i jakość

Frontend: **672 testy**, 57 plików, typecheck, test:coverage, build produkcyjny i ESLint — zaliczone.
Pokrycie: linie **99,05%**, instrukcje 98,03%, funkcje 99,23%, gałęzie 91,31%.
Nowy `CoverageLayer.tsx`: **100%** we wszystkich czterech miarach.
[Pełny log](results/au108-111/frontend-verification.txt).

Backend: **5266 passed, 0 failed, 3 deselected** (`docker_cli`), 657,69 s;
pełny przebieg w kontenerze Python 3.13/PostGIS 16, z kopią repo bez `.env`,
`REPO_ROOT=/repo`, repo i kodem montowanymi tylko do odczytu. Pokrycie aplikacji **95,31%**;
nowe moduły pokrycia i dat — 100%, `services/terrain.py` — 98%.
Ruff zaliczony; mypy nie ma nowych zgłoszeń względem bazy (17 znanych).
Skan sekretów: zero zgłoszeń; `git diff --check` bez uwag.
[Pełny log backendu](results/au108-111/backend-verification.txt),
[pokrycie istotnych modułów](results/au108-111/backend-coverage-summary.json).

Regeneracja oczekiwanych odpowiedzi parsera MPZP dodała wyłącznie cztery pola metadanych źródła;
automatyczne porównanie potwierdziło brak zmian wcześniejszych wartości domenowych i wyników parsera.
Fixture'y wejściowe pozostają niezmienione. Nie zmieniano historycznych migracji; nowa 038 jest po head 037.

## Live smoke pełnego korpusu

Pełne **30/30** przypadków `BK-002-real-parcels-2026-09-23`, bez `--limit`, 17.10.2026,
16:33:39–16:39:59 UTC (380,1 s). Równoległość 3, timeout 180 s, `force_refresh=false`.
Baza analiz była pusta: powstały nowe analizy #1–30. Włączone NMT WCS, RU MPZP i discovery
RU POG; parser LLM wyłączony. Kod stanowi drzewo robocze nad `f530b5f186ede3a3295b3db397de9984a2e1fbd1`;
`commit=unknown` w surowym raporcie wynika z braku `.git` w obrazie API.
Dokładne wersje obrazu są w [metryce środowiska](results/au108-111/environment.json);
odpowiadają obecnym lockfile'om repozytorium, nie starszemu baseline z opisu zadania.

Polecenie uruchomione wewnątrz kontenera API:

```sh
python scripts/live_smoke_corpus.py \
  --base-url http://localhost:8000 \
  --manifest /repo/backend/tests/fixtures/reference_corpus/manifest.json \
  --output /tmp/au108-live-smoke.md \
  --json-output /tmp/au108-live-smoke.json
```

| Pomiar | Wynik |
|---|---:|
| HTTP 200 z API | **30/30** |
| HTTP 5xx z API / błędy transportu klienta smoke | **0 / 0** |
| Ponowienia po 429 | **0** |
| `partial` / `complete` | **30 / 0** |
| p50 / p95 / maksimum odpowiedzi | **49,266 / 64,704 / 76,098 s** |
| NMT dostępny; nadrzędny zakres równy rastrowi WCS | **30/30** |
| `NMT_CROSSCHECK_DIFF` (ściśle > 0,5 m) | **7** |
| Macierz jakości 1.2 z poprawnym odciskiem | **30/30** |
| Sekcje bez daty i bez powodu jej braku | **0** |
| `NO_LOCAL_RELEASE` | **30/30** |

W ISOK/GDOŚ/NMT/RU każdy odczytany rekord źródła ma datę albo jawny powód jej braku.
Żywe odpowiedzi tych kanałów nie pozwoliły ustalić dat zestawów — pozostają `unknown`;
KIMPZP podał daty uchwał w 13/30 rekordów, np. 13.04.2011. Rejestr źródeł może mieć więcej
niż jeden rekord na analizę (GDOŚ); dawne techniczne rekordy KIUT i dwie nieudane próby
usługi NMT dostają jawny domyślny powód przy odczycie.

**Dlaczego 0 analiz kompletnych:** baza odbiorowa ma trzy wyżej wymienione gminy POG,
które leżą poza korpusem, oraz nie ma paczek BDOT10k. POG i obszary dodatkowe są niepełne
w 30 analizach, droga i zabudowa mają `no_coverage` 30/30. Ponadto brakowało wiarygodnego
wyniku MPZP w 26, ISOK w 24 i GDOŚ w 3 analizach. Bliskość i brak danych nadal nie oznaczają
braku ograniczenia. KIUT/GESUT pozostaje poza zakresem z powodu niepotwierdzonego kontraktu.
Nie porównujemy liczby stref do wcześniejszego przebiegu z pełnym importem krajowym POG
ani przebiegu z sześcioma paczkami BDOT10k — mają inne lokalne pokrycie.

**Błędy źródeł i dodatkowa usterka:** ISOK odpowiedział 24 × HTTP 400; GDOŚ 1 × HTTP 500
w zapytaniach składowych i 3 × błąd sekcji; usługa kontrolna NMT była niedostępna 2 razy,
ale raster dał wynik. Błędy źródeł zostały obsłużone, nie spowodowały 5xx API.
Dla `126105_9.0001.580/4` pojawił się `MPZP_DOCUMENT_PERSISTENCE_FAILED`: dwie równoległe
analizy próbowały pierwszy raz utworzyć źródło `kimpzp`, co naruszyło `uq_data_sources_source_id`.
Obsłużono to ostrzeżeniem i wycofaniem transakcji; cytowalna struktura dokumentu dla tej
próby nie została zapisana. Usterkę współbieżności naprawiono w kolejnym kroku;
wynik pierwszego smoke sam w sobie nie potwierdzał jej usunięcia.

Dowody: [raport per działka](results/au108-111/live-smoke-2026-10-17.md),
[surowy JSON smoke](results/au108-111/live-smoke-2026-10-17.json),
[weryfikacja utrwalonych zakresów i metadanych](results/au108-111/live-details-2026-10-17.json),
[skrypt kontroli zapisów](results/au108-111/inspect-live.py),
[liczby odpowiedzi źródeł](results/au108-111/upstream-summary-2026-10-17.json),
[dowód konfliktu zapisu MPZP](results/au108-111/mpzp-persistence-warning.txt).

Próg live smoke (**zero 5xx API i zero błędów transportu klienta**) jest spełniony.
Pełna kompletność analiz na żywych źródłach nie jest potwierdzona.

## Kryteria odbioru i granice potwierdzenia

| Kryterium | Ocena | Podstawa / ograniczenie |
|---|---|---|
| AU-108: trzy gminy, wydania i daty, jednoznaczny brak danych lokalnych | Potwierdzone | API i przeglądarka: Sejny, Jejkowice, Barczewo; Bielsko-Biała bez wydania; zlecenie w istniejącej kolejce |
| AU-109: jeden zakres dla `141801_4.0701.23/8` | Potwierdzone na rzeczywistej zamrożonej odpowiedzi | Backend wybiera 118,269–118,959 m; test komponentu UI i render PDF; ta działka nie należy do 30 przypadków live smoke |
| AU-109: ostrzeżenie ściśle ponad 0,5 m | Potwierdzone | Testy 0 / 0,4999 / 0,5 / 0,5001 / 1 m oraz ostrzeżenia w analizach live |
| AU-110: pomijanie okruchów, snapshot i bilans pola | Potwierdzone | Zastana implementacja i rozszerzone regresje: oba ścisłe progi, brak usuwania istotnej strefy, realna geometria w EPSG:2180 |
| AU-110: dosłownie „pominięto 2 artefakty styku (0,18 m²)” dla Bielska-Białej | Niepotwierdzone dla dostępnej rzeczywistej wersji | Zamrożony pakiet z rzeczywistego importu RU, wersja `20260901T095300`, zawiera SK i jeden okruch SW 0,1080956 m²; historyczna para SW+SU 0,18 m² jest wyłącznie testem kontrolnym, nie nowym dowodem z usługi |
| AU-111: ISOK/GDOŚ/NMT/RU mają datę lub jawny powód jej braku | Potwierdzone w testach zamrożonych odpowiedzi i live smoke | ISO 19139, brak dat w rzeczywistych odpowiedziach usług, Last-Modified, daty uchwał, zapis/odczyt i UI/PDF; brak daty źródła pozostaje unknown |
| Dokumentacja, wersje kontraktów, migracja po aktualnym head | Potwierdzone | `current_state.md`, ADR-027, mapowanie pól; terrain 1.1, quality 1.2; migracja 038 po 037 |

Próg 80% jest przekroczony po obu stronach aplikacji. Testy `docker_cli` są wyłączone zgodnie
z zakresem zleconej komendy. Weryfikacja odbywa się lokalnie, w izolowanych kontenerach;
nie jest wdrożeniem ani potwierdzeniem konfiguracji produkcyjnej.
