# Pakiet audytowy analizy #362

Pakiet jest samowystarczalnym zapisem wyniku zapisanej analizy działki. Powstał wyłącznie z zapisanego snapshotu — bez ponownego odpytywania źródeł. Umożliwia niezależne sprawdzenie wyniku, geometrii i pochodzenia danych.

## Identyfikacja

- Analiza: #362; działka: BK301_302_E2E_A_bcb6bc.
- Data analizy (UTC): 2026-09-29T21:21:21.494846Z — chwila wykonania analizy. Pakiet nie zawiera czasu eksportu, dzięki czemu ten sam snapshot daje identyczny pakiet.
- Status analizy: partial.
- Format: audit-package/1; eksporter: audit-exporter/1.0.0.
- Polityka jakości: quality-policy/1+2e0adf938c7b; suma kontrolna macierzy jakości (SHA-256): ce63b5d9d489df280a2951f5e115cb3efb0d9d34b7e00f84263bea1bb660ec39.

## Układy współrzędnych

- **Obliczenia** (pola, udziały, odległości): EPSG:2180 (PUWG 1992, metry). Geometria działki w EPSG:2180 jest w `analysis.json`, w polu `computation.parcel_geometry_epsg2180`.
- **GeoJSON** (`parcel.geojson`, `layers/*.geojson`): EPSG:4326 (WGS 84, kolejność: długość, szerokość; RFC 7946) — do prezentacji i wymiany. Nie licz pól ani odległości na tych współrzędnych.

## Zawartość

| Plik | Opis |
| --- | --- |
| `analysis.json` | wynik analizy (sekcje, parametry, statusy, macierz jakości, ostrzeżenia); geometrie są w plikach GeoJSON |
| `sources.json` | rejestr źródeł: identyfikator z katalogu, adres, czas pobrania, wydanie, SHA-256 artefaktu, licencja i zgoda na redystrybucję |
| `parcel.geojson` | obrys działki (EPSG:4326) |
| `layers/buildable_area.geojson` | Obszar po technicznym odsunięciu od granic (przybliżenie) — 1 obiekt(ów), EPSG:4326 |
| `layers/pog_zones.geojson` | Strefy POG przecinające działkę — 1 obiekt(ów), EPSG:4326 |
| `manifest.json` | lista plików z rozmiarem i SHA-256 (bez samego manifestu) oraz pominięte artefakty |
| `README.md` | ten opis |

Brak pliku warstwy nie dowodzi braku ograniczeń: oznacza brak obiektów w wyniku albo pominięcie warstwy (patrz „Pominięte artefakty”). Brak danych to nie brak ograniczenia, a `null` to nie zero.

## Znaczenie statusów sekcji

Status opisuje wynik sprawdzenia według kontraktu źródła, a nie świeżość danych.

| Status | Znaczenie |
| --- | --- |
| `available` — sprawdzono | źródło zwróciło wynik zgodny z kontraktem; pusty wynik oznacza sprawdzony brak obiektów, a nie brak ograniczeń poza zakresem źródła |
| `partial` — częściowo | wynik jest niepełny albo obniżonej pewności (np. strefa bez wektora, podgląd zamiast geometrii, sprzeczne parametry) |
| `no_coverage` — brak pokrycia źródła | źródło potwierdziło brak danych dla tego obszaru (np. poza zasięgiem NMT albo powiat nie publikuje GESUT) — to nie jest błąd źródła i nie dowodzi braku ograniczeń |
| `unavailable` — źródło niedostępne | próba pobrania nie powiodła się (limit czasu, błąd HTTP, odpowiedź niezgodna z kontraktem) — wynik nie jest znany |
| `error` — błąd sprawdzenia | nieoczekiwany błąd przetwarzania sekcji — wynik nie jest znany |
| `unknown` — nieustalone | brak zapisanego wyniku albo niemożliwe rozstrzygnięcie; brak danych nie oznacza braku ograniczenia ani braku planu |
| `out_of_scope` — poza zakresem | sekcja nie jest analizowana, bo brakuje potwierdzonego kontraktu źródła |
| `awaiting_input` — oczekuje na dane użytkownika | analiza wstrzymana do czasu podania danych przez użytkownika |

Świeżość jest oceniana osobno, według jawnej reguły wieku przypisanej do źródła i punktu odniesienia (chwila analizy). Źródło bez reguły ma świeżość `unknown` — system nie zgaduje terminu ważności.

| Świeżość | Znaczenie |
| --- | --- |
| `fresh` — aktualne wg reguły | wiek danych w punkcie odniesienia mieści się w regule wieku przypisanej do źródła |
| `stale` — starsze niż reguła | wiek danych w punkcie odniesienia przekracza regułę wieku źródła — dane mogą nie odzwierciedlać bieżącego stanu źródła |
| `unknown` — świeżość nieustalona | brak reguły wieku dla źródła, brak czasu pobrania albo czas nieprawidłowy — system nie zgaduje terminu ważności |

Flaga `manual_review_required` oznacza, że wynik sekcji wymaga ręcznej weryfikacji w materiale źródłowym; nie zmienia statusu sekcji.

## Macierz jakości tej analizy

| Sekcja | Status | Źródło | Pobrano (UTC) | Świeżość | Weryfikacja | Powody |
| --- | --- | --- | --- | --- | --- | --- |
| parcel | available | uldk | 2026-09-29T21:20:51.461050Z | unknown | nie | — |
| mpzp | unknown | brak | brak danych | unknown | nie | nie znaleziono albo nie sprawdzono planu (nie dowodzi braku planu) |
| pog | available | pog_app | 2026-09-29T21:20:51.461050Z | unknown | tak | wynik POG wymaga ręcznej weryfikacji |
| pog_overlays | available | pog_app | 2026-09-29T21:20:51.461050Z | unknown | tak | wynik POG wymaga ręcznej weryfikacji |
| flood | available | isok | 2026-09-29T21:21:01.474616Z | fresh | nie | — |
| nature | available | gdos | 2026-09-29T21:21:01.474743Z | fresh | nie | — |
| terrain | available | nmt | 2026-09-29T21:21:21.490619Z | fresh | nie | — |
| utilities | partial | kiut_wms | 2026-09-29T21:20:51.461050Z | unknown | nie | podgląd nie jest geometrią sieci — brak odległości i liczby sieci |
| transport | out_of_scope | brak | brak danych | unknown | nie | brak potwierdzonego kontraktu źródła danych (BK-305) |
| mpzp_pog_relation | unknown | brak | brak danych | unknown | tak | POG obowiązuje, ale nie ustalono strefy MPZP (brak danych ≠ brak ograniczeń); relacja MPZP–POG wymaga weryfikacji; sekcja wyliczona z innych sekcji — bez własnego źródła |

## Pominięte artefakty (redystrybucja)

Katalog źródeł steruje dołączaniem treści. Poniższych artefaktów nie skopiowano — pozostała referencja, SHA-256 i powód (szczegóły w `manifest.json`).

| Artefakt | Źródła | Powód | SHA-256 |
| --- | --- | --- | --- |
| `analysis.json#/result/pog/raw_attributes` | pog_app | raw_redistribution_not_allowed | `cfc29ab06b37991daecd4c36f3158b5cf76d97b1d35280a7c7295e49953eb602` |

## Weryfikacja integralności

1. Rozpakuj archiwum. `manifest.json` zawiera nazwę, rozmiar i SHA-256 każdego pliku (poza samym manifestem).
2. Sprawdź każdy plik, np. `sha256sum analysis.json` i porównaj z manifestem. Zmiana jednego bajtu zmienia sumę.
3. Skrypt `backend/scripts/verify_audit_package.py <plik.zip|katalog>` sprawdza wszystko offline (tylko biblioteka standardowa Pythona).
4. Hash całej paczki ZIP nie jest w archiwum; podano go w nagłówku odpowiedzi `X-Audit-Package-SHA256`.

## Zastrzeżenia

Pakiet nie jest opinią prawną. Podgląd WMS służy wyłącznie prezentacji i nie jest źródłem obliczeń. Wynik zależy od stanu źródeł w chwili analizy.
