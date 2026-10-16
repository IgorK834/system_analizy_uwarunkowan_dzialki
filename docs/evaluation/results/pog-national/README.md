# Wyniki: POG dla całej Polski (ADR-026)

Surowe dowody do [pog-national-verification.md](../../pog-national-verification.md). Wszystkie pomiary z żywego Rejestru Urbanistycznego, 2026-10-13/14, stan krajowy #464.

| Plik / katalog | Zawartość |
|---|---|
| `regression-before-fix.txt` | Czerwony przebieg testu odtwarzającego błąd „import gminy B ukrywa gminę A” (przed zmianą modelu). |
| `live/sample-run{1,2,3}.json`, `*.stderr.log` | Próbka 28 gmin (30 aktów): przebieg 1 (BOM w pliku Jejkowic), 2 (wznowienie, ten sam błąd), 3 (po poprawce; 7,7 s). |
| `live/national-run1.json`, `national-start.txt`, `national-end.txt`, `*.stderr.log` | Pełny import krajowy: 434 z 435 aktów w 1392 s, 2996 MB, 440 żądań do RU. |
| `live/national-run{2,3}-incremental.json` | Przebiegi przyrostowe bez zmian (10 s) z ponowieniem aktu odrzuconego przez QA (Bolków). |
| `live/db-size-after-national.txt` | Rozmiary bazy, tabel i wolumenu artefaktów, liczby obiektów. |
| `live/tiles-binding.json`, `live/tiles-project.json` | Czasy zimne/ciepłe i rozmiary kafli (`backend/scripts/measure_pog_tiles.py`). |
| `live/local-pog-step-benchmark.json` | Czas lokalnej analizy POG działki (120 działek, wszystkie akty). |
| `live/live-smoke-national*.{md,json}` | Live smoke korpusu referencyjnego (30 działek) na stanie krajowym; `*-final` = przebieg na kodzie końcowym. |
| `live/inspector-vs-gml-bielsko-biala.json` | Porównanie 5 stref Bielska-Białej: pola inspektora vs źródłowy GML RU. |
| `live/report-246101_1.0056.155-3.pdf`, `live/audit-246101_1.0056.155-3.zip` | PDF i pakiet audytowy dla działki z gminy zaimportowanej (stan 464). |
| `screenshots/01…18-*.jpg` | Zrzuty ekranu przeglądarki (1440×900 i 375×812), opis w dokumencie odbioru §6. |
