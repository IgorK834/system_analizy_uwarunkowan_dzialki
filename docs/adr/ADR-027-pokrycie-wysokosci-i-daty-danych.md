# ADR-027: pokrycie lokalne, nadrzędne wysokości NMT i daty zestawów

- Status: zaakceptowany
- Data: 2026-10-16
- Zadania: Task 22.8 (AU-108), 22.9 (AU-109), 22.11 (AU-111)
- Uzupełnia ADR-006, ADR-011 i ADR-026. Reguła okruchów AU-110 pozostaje z ADR-026 pkt 11.

## Decyzje

`GET /api/v1/coverage` łączy istniejące metadane POG i BDOT10k z granicami PRG.
POG nadal jest wersjonowany rejestrem aktywnych wersji aktów, niezależnie od aktywnego wydania BDOT10k.
Warstwa „Pokrycie” pobiera dane po włączeniu; lista gmin pokazuje wydanie, wersję i datę importu,
a lista powiatów także datę danych. Status lokalnego aktu (uchwalony/projekt/nieustalony)
nie zastępuje informacji o dostępności lokalnych danych. Brak aktu w świeżym wykazie RU
nie jest dowodem braku planu. Nieświeży wykaz nie pozwala potwierdzić nieobecności aktu.

Geometria PRG jest zamrożonym, publicznym zbiorem 2479 gmin pobranym 16.10.2026 z
[usługi GUGiK](https://mapy.geoportal.gov.pl/wss/ims/maps/PRG_gugik_wyszukiwarka/FeatureServer/0).
[Zasady udostępniania PRG](https://www.geoportal.gov.pl/pl/dane/panstwowy-rejestr-granic-prg/)
pozwalają na dowolne wykorzystanie. Parametry pobrania, skróty stron i czas są w
`backend/app/data/commune_boundaries.metadata.json`. Jest to geometria uproszczona
wyłącznie do podglądu; nie służy do przecięć ani obliczeń analizy. Aktualizacja granic
wymaga ponownego pobrania kompletnego zbioru oraz kontroli unikalności TERYT.
Data pobrania granic nie jest datą planów ani datą aktualności PRG (źródło jej nie podało).

Przy `NO_LOCAL_RELEASE` UI pokazuje „brak lokalnych danych (≠ brak planu)” i akcję
„poproś o import”. POST `/api/v1/coverage/pog/commune/{terc}/import` używa istniejącego
mechanizmu Task 22.7: potwierdzenie w katalogu RU, deduplikacja aktywnego zlecenia,
przerwa po nieudanym imporcie i worker harmonogramu. Endpoint ma limit jak odświeżenie
analizy. Bez potwierdzenia aktu albo przy wyłączonej/niedostępnej kolejce nie zgłasza sukcesu.

Nadrzędne Hmin/Hmax/deniwelacja pochodzą z rastra WCS GRID1 (1 m), również przy awarii
GetMinMaxByPolygon. Pomiar usługowy pozostaje w `terrain.crosscheck` wraz ze źródłem,
siatką, maksymalną różnicą skrajnych wysokości i progiem. Różnica **ściśle > 0,5 m**
generuje `NMT_CROSSCHECK_DIFF`. Nie odejmujemy nieudokumentowanego przesunięcia układów.
Bez rastra używamy wartości usługi i podajemy rzeczywiście raportowaną siatkę (np. 2 m
lub 4 m) oraz mniejszą dokładność. Nie wpisujemy 2 m, gdy odpowiedź mówi 4 m.
PDF ma jeden zakres w tabeli 6.1; tabela 6.2 dotyczy pochodnych rastra i nie powtarza
wysokości. Odczyt starszego snapshotu z obiema seriami również wybiera raster do prezentacji,
bez nadpisywania historycznego zapisu w bazie.

`SourceMetadata.data_as_of` jest datą zestawu, `fetched_at` czasem pobrania.
Towarzyszą jej `data_as_of_basis`, jawny `data_as_of_status=known|unknown` i powód braku.
Nie zastępujemy nieznanej daty czasem pobrania, HTTP Date ani ISO dateStamp (data metadanych).
ISOK/GDOŚ/NMT odczytują Last-Modified, a parser ISO 19139 rozróżnia revision/publication/creation.
Last-Modified potwierdza modyfikację udostępnionego zasobu, nie datę pomiaru terenowego;
podstawa pozostaje jawna. W GDOŚ wszystkie składowe muszą podać datę, aby podać wspólny
stan na; wtedy przyjmujemy najstarszą. KIMPZP i RU MPZP używają dat uchwał, lokalne POG
metadanych CSW albo daty uchwały APP/GML. Niekompletna data pozostaje nieznana z powodem.

Daty przechodzą do snapshotów sekcji, macierzy jakości i rejestru źródeł
(`source_records.dataset_metadata`, nowa migracja 038 po zastanym head 037).
Macierz jakości 1.2 uwzględnia je w odcisku; historyczne macierze 1.0/1.1 zachowują
oryginalny odcisk. Brak później dodanej sekcji nie przerywa eksportu starej analizy.
Reguła katalogu może wskazywać `date_basis: fetched_at|data_as_of`; druga wymaga daty
zestawu i przy jej braku daje unknown, bez zastępowania datą pobrania. Domyślne istniejące
reguły nadal odnoszą się do pobrania. Ocena na dzień eksportu i UI respektuje tę samą podstawę.

Kontrakt `terrain` ma wersję 1.1, `quality` 1.2; składniki zmieniają sygnaturę cache.
Granice modułów pozostają zgodne z ADR-001: port granic w application, adapter plikowy
w infrastructure; obliczenia przestrzenne analizy nadal w EPSG:2180, WMS tylko do podglądu.
