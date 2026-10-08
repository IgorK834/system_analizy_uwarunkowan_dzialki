# Zamrożone odpowiedzi ULDK (AU-002)

Rzeczywiste odpowiedzi usługi `https://uldk.gugik.gov.pl/` pobrane 2026-10-06 (HTTP 200, `text/plain`).
Test `tests/test_uldk.py::test_frozen_uldk_responses_are_classified` ładuje je bajt w bajt i sprawdza
klasyfikację odpowiedzi oraz kod HTTP zwracany przez API.

| Plik | Zapytanie | Treść | Klasyfikacja |
|---|---|---|---|
| `brak_wynikow_baltyk.txt` | `GetParcelByXY`, punkt 54,9°N 18,5°E (Bałtyk) | `-1 brak wyników` + druga linia komunikatu usługi | po ponowieniu `ParcelNotFoundError` → 404 `PARCEL_NOT_FOUND` |
| `brak_wynikow_nieistniejaca_dzialka.txt` | `GetParcelById`, `141801_4.0701.9999/9` | jw. (bajt w bajt ta sama odpowiedź) | jw. |
| `zero_bez_danych.txt` | `GetParcelById` z nieistniejącym polem w `result` | `0` i pusta linia danych | `ParcelNotFoundError` → 404 (bez ponowienia) |
| `odpowiedz_pusta.txt` | `GetParcelById` z pustym `id` | pusta treść (0 bajtów) | `InvalidUldkResponseError` → 502 `UPSTREAM_INVALID_RESPONSE` |
| `niepoprawny_parametr.txt` | nieznana metoda `request` | tekst bez kodu statusu (`niepoprawny parametr …`) | `InvalidUldkResponseError` → 502 `UPSTREAM_INVALID_RESPONSE` |
| `poprawna_146510_8.0502.1_3.txt` | `GetParcelById`, `146510_8.0502.1/3` (76 wierzchołków) | `0` + `id|SRID=2180;POLYGON(...)|id` | wynik działki |
| `blad_z_komunikatem.txt` | — | `-1 błąd wewnętrzny usługi powiatowej` | `UldkServiceUnavailableError` → 503 `UPSTREAM_UNAVAILABLE` |

`blad_z_komunikatem.txt` jest **jedynym plikiem skonstruowanym ręcznie**: usługa nie zwraca innego komunikatu
po `-1` na żądanie, więc plik odtwarza udokumentowaną formę „kod i komunikat w jednej linii”. Pozostałe pliki są
nieprzetworzonymi odpowiedziami usługi. Strona HTML zwracana przy zapytaniu bez parametrów (ok. 38 kB) nie jest
zamrożona; jej pierwszy token (`<!DOCTYPE`) trafia do tej samej gałęzi co `niepoprawny_parametr.txt`.

Obserwacja z pobrania: ULDK odpowiada `-1 brak wyników` także dla zapytania z niepoprawnymi współrzędnymi
(`xy=abc,def,2180`), więc „brak wyników” nie rozróżnia braku działki, błędu wejścia i przejściowej awarii źródła.
