# ADR-026 — Krajowy stan POG: rejestr aktywnych wersji aktów, import z GML Rejestru Urbanistycznego i kafle dla całego kraju

Status: decyzje techniczne przyjęte 2026-10-13/14 (issues #300, #258, #301 — część POG, #335, #303). **Wartości wymagające decyzji właściciela danych/produktu są oznaczone „Proponowany”
(sekcja „Decyzje do potwierdzenia”) i działają w wariancie ostrożnym, odwracalnym.**
Powiązane: [ADR-001](ADR-001-modular-monolith.md) (granice modułów), [ADR-007](ADR-007-pog-vector-tiles-and-shared-presentation.md) (kafle MVT), [ADR-008](ADR-008-pog-release-complete-snapshot.md)
(wydanie jako snapshot — dla POG zastąpione), [ADR-009](ADR-009-pog-inspector-area-summaries-layer-state.md) (inspektor, agregaty, stan warstwy), [ADR-003](ADR-003-pog-act-provenance-chain.md) (provenance),
[ADR-015](ADR-015-api-errors-request-id-and-text-column-policy.md) (kolumny tekstowe), [ADR-023](ADR-023-discovery-pog-ru-wfs-budzet-zadan.md), [ADR-024](ADR-024-import-pog-rownolegly-wznawianie-postep.md) (import z WFS).
Kontrakt kanału: [ru_contracts.md](../data_sources/ru_contracts.md#publiczne-api-ru-jako-kanał-importu-krajowego-adr-026-2026-10-13). Runbook: [pog-import.md](../operations/pog-import.md).
Odbiór i pomiary: [pog-national-verification.md](../evaluation/pog-national-verification.md).

## Kontekst

Model sprzed tej decyzji obsługiwał **jedną gminę naraz**. `_activate_release` wygaszał wszystkie inne wydania źródła `pog_app`, `publish_pog` przenosił do nowego wydania wyłącznie akty z importowanej paczki,
a `active_pog_release` zwracało jedno wydanie. Import gminy B odcinał więc gminę A od **analizy działki, kafli mapy i inspektora obiektu**. Błąd odtworzono na PostGIS przed zmianą modelu
(`tests/test_pog_multi_commune.py`; zapis czerwonego przebiegu: `docs/evaluation/results/pog-national/regression-before-fix.txt`): realny akt Sopotu jako gmina A, ten sam akt przesunięty o 40 km jako gmina B.

Drugi problem to kanał danych. WFS RU zwraca strony po 10 obiektów i jeden typ na żądanie (zob. ADR-024): Bielsko-Biała (1397 obiektów) to 2–6 minut, a gminy największe przekraczają limit 5 000 obiektów typu,
więc import ~465 aktów tą drogą trwałby dziesiątki godzin. Frontend RU korzysta jednak z publicznego API, które wystawia **wykaz bieżących wersji aktów POG** i **plik GML całego aktu** (zob. ru_contracts.md).

## Decyzja

1. **Kanał krajowy = publiczne API RU** (`POST /api/public/published/query`, `GET /api/public/published/{id}/download`), zasób `ru_published` źródła `pog_app` w `catalog.yaml`. Jeden plik = jeden akt (granica,
   strefy, OUZ, OZS, OSDIS, dokumenty formalne) w PL-2000 strefy gminy; czytnik (`ru_gml_reader`) przelicza do EPSG:2180 z kolejnością osi z rejestru EPSG, odrzuca plik, który nie jest dokładnie jednym aktem
   tej gminy (kontrola TERC), nie zgaduje układu (allowlista `{2176–2180, 4258, 4326, 3857}`) i jako artefakt źródłowy zachowuje **bajty pliku bez zmian**. WFS (ADR-024) zostaje kanałem zapasowym.
   Wykaz jest zapisywany w `pog_ru_catalog` (data ostatniego widzenia, status, `is_current`).
2. **Rejestr aktywnych wersji aktów niezależny od wydań.** Stan krajowy N to zbiór wpisów `pog_registry_entries` spełniających `state_from_id ≤ N AND (state_to_id IS NULL OR state_to_id > N)`; numery stanów
   (`pog_registry_states`: `import`, `withdrawal`, `rollback`, `migration`) rosną przy każdej zmianie rejestru. Najwyżej jeden wpis na akt jest otwarty (częściowy indeks unikalny). Numer stanu jest nadawany
   i zatwierdzany pod jedną globalną blokadą doradczą (`pog-registry`, po blokadach per akt `pog-act:<id>` w stałej kolejności), więc numery rosną zgodnie z kolejnością zatwierdzania, a stan o numerze N nie zmienia się po
   tym, jak ktokolwiek go odczytał. **Wydanie (`data_releases`) jest rekordem audytowym przebiegu/artefaktu** i dla `pog_app` zostaje nieaktywne; **nie ma kopii „przeniesionych” aktów** — wersja aktu jest zapisana raz,
   wpis rejestru mówi, w jakich stanach jest aktywna. Akt bez zmian treści (`content_hash`) nie tworzy nowej wersji: aktualizowany jest tylko token RU w miejscu. Wycofanie i cofnięcie wersji tworzą nowe stany
   (`withdrawal`/`rollback`) i nie kasują historii. Zastępuje to ADR-008 dla źródła `pog_app` (zob. aneks tamtego ADR).
3. **Zachowane niezmienniki:** idempotentny ponowny import (ten sam plik ⇒ brak nowych wersji, brak nowego stanu); kontrola regresji liczby obiektów (spadek > 50%) **per akt**, nie dla wydania;
   rozdzielność „projekt / akt wiążący / brak planu” (status prawny z urzędowego kodu, nigdy z lokalnych domysłów); „brak danych ≠ brak ograniczeń”, `null` ≠ 0, błąd źródła nigdy nie jest „brakiem planu”;
   obliczenia w EPSG:2180; WMS tylko do podglądu.
4. **Przypięcie analizy.** `PogResult` niesie `registry_state_id` i `pinned_act_versions` (identyfikator wersji, `content_hash`, wydanie audytowe, SHA artefaktu). Odczyt historyczny, raport PDF i pakiet audytowy odtwarzają dokładnie
   te wersje, także po późniejszych importach (test `test_pog_national_analysis_e2e.py`). Wersja kontraktu `POG_RESULT_SCHEMA_VERSION` = 2.7 (2.6: przypięcie; 2.7: okruchy stref), więc wyniki starszego kontraktu
   nie są serwowane z cache.
5. **Cache analiz po gminie działki.** Sygnatura cache zawiera `pog_scope_version`: największy numer stanu, który dotknął aktów gminy działki (TERYT z identyfikatora). Import innej gminy **nie** unieważnia analiz tej działki;
   nowa wersja, wycofanie albo cofnięcie aktu jej gminy — tak. Bez rozpoznanego TERYT używany jest numer globalny.
6. **Import krajowy** (`python -m app.modules.imports pog-national`): plan z wykazu (`plan_sync`: akt z tokenem `publishedActId` w rejestrze jest pomijany, `--full` ignoruje tokeny, filtry `--terc`/`--limit` stosowane przed
   limitem), pula wątków `--concurrency` 1–6 (domyślnie 3), wspólny rozstawiacz żądań (`RU_PUBLISHED_MIN_INTERVAL_SECONDS` = 1,2 s; RU odpowiada 429 powyżej kilku żądań/s), ważony budżet pamięci (bajty GML w locie),
   izolacja błędów per akt (kody: `qa_rejected` — wada danych źródła, `rate_limited`, `not_xml`, `too_large`, `gml_*`, `transport`), ponowienia kodów przejściowych, wznowienie tym samym poleceniem (wiersz `import_runs` z `pog-national-v1`
   i tokeny w rejestrze), postęp z ETA na stderr, **jedna linia JSON na stdout**, kody wyjścia 0 / 1 / 3 jak w ADR-024. **Wycofanie** aktu następuje wyłącznie po imporcie, tylko dla aktów z tokenem RU nieobecnych w *kompletnym* wykazie
   (suma kontrolna `totalElements`, wszystkie strony, brak powtórzeń), z blokadą masowego wycofania (więcej niż max(5, 10% aktów z tokenem) wymaga `--allow-mass-withdrawal`); niekompletny wykaz nie wycofuje niczego.
   `--dry-run` i `--plan-only` nie publikują.
7. **Harmonogram i import „na żądanie”.** Usługa `pog-sync` (profil Compose `sync`, wzorzec `address-index-sync`) działa pod sesyjną blokadą doradczą `pog-sync` (druga instancja kończy cykl statusem `locked`), uzupełnia tabele mapy,
   przetwarza kolejkę `import_jobs` (`FOR UPDATE SKIP LOCKED`, dzierżawa, ponowienia z odroczeniem, deduplikacja częściowym indeksem unikalnym) i uruchamia import przyrostowy po upływie `POG_SYNC_INTERVAL_SECONDS`. Wynik każdego
   przebiegu trafia do `import_runs`, stan do `GET /health/imports`. **Analiza działki w gminie, której RU ma plan, a lokalnego stanu brak, zleca import w tle** (deduplikacja per gmina, 6 h przerwy po niepowodzeniu)
   i **mówi o tym wprost w odpowiedzi** (ostrzeżenia `POG_IMPORT_QUEUED` / `POG_IMPORT_PENDING` / `POG_IMPORT_FAILED_RECENTLY`); analiza korzysta wtedy z discovery RU (ADR-023). Zlecenia kończące się wadą danych źródła (`qa_rejected`, `gml_*`) nie są ponawiane.
8. **Kafle dla całego kraju.** Tabele do wyświetlania `pog_map_acts`, `pog_map_zones`, `pog_map_overlays` (EPSG:3857, trzy stopnie uproszczenia geometrii zależne od zoomu, indeks GiST po pełnej geometrii, gotowe atrybuty kafla
   liczone przy publikacji **tą samą funkcją co analiza**) powstają przy publikacji wersji aktu i są niezmienne; o widoczności decyduje rejestr (tablica wersji aktywnych w stanie N). Kafel to jedno zapytanie
   `ST_AsMVT` z `ST_AsMVTGeom(extent 4096, buffer 64)`, pięć warstw logicznych (`act_boundary`, `zones`, `ouz`, `downtown`, `social_infrastructure_standard`) w jednym protobufie. **Progi zoomu pochodzą z serwera**
   (`layer_min_zoom`): granice aktów w kolorze statusu od z0 (przegląd krajowy, wypełnienie do z13), strefy od z11, OUZ/OZS/OSDIS od z12, etykiety tekstowe w kaflach od z13 (symbole stref od z14, symbole obszarów od z16 po stronie klienta).
   Obowiązujące i projekty to **osobne źródła** (`?edition=binding|project`: osobny cache i ETag). Adres `…/states/{state_id}/{z}/{x}/{y}.mvt`; klucz cache = stan + schemat + edycja + z/x/y, więc **nowy stan = nowe klucze**
   (unieważnienie przez wersjonowanie, bez przeczyszczania; stare wpisy wypiera LRU). Metadane stanu (`/api/v1/map/pog/state`) niosą zasięg i kompletność każdego aktu; liczone raz na stan i raz na wersję aktu.
   Migracja `037_pog_registry` zamienia dotychczasowe aktywne wydanie w pierwszy stan (`migration`); tabele mapy dla istniejących danych buduje `pog-map-rebuild` (do tego czasu `map_ready = false`).
9. **Wybór silnika kafli: `ST_AsMVT` w backendzie, bez kontenera kafli.** Pomiar na stanie krajowym (464 akty, 518 tys. obiektów): zimne p95 ≤ 57 ms, największy kafel 199 KB (zob. „Wynik pomiaru”), więc dodatkowy
   proces (pg_tileserv/martin), jego utrzymanie i druga ścieżka autoryzacji nie dają zysku. Kryterium zmiany decyzji: p95 zimnego kafla > 500 ms albo rozmiar > 500 KB na z ≥ 11.
10. **Pokrycie krajowe (issue 301, część POG).** `GET /api/v1/coverage/pog` (lista gmin ze stanem, wersją, datą importu i ostatnią nieudaną próbą), `GET /api/v1/coverage/pog/commune/{terc}` i `GET /api/v1/coverage/pog/point`.
    **Cztery rozłączne stany gminy:** `imported`; `in_ru_not_imported` („plan w RU, jeszcze niezaimportowany”, także po odrzuceniu przez QA); `no_act_in_ru` („brak aktu POG w wykazie RU”, tylko ze świeżego i kompletnego wykazu;
    nie dowodzi braku planu); `unknown` („źródło niedostępne”, wykaz nie zsynchronizowany albo przeterminowany). Żaden komunikat nie mówi „brak planu” dla gminy niezaimportowanej.
11. **Okruchy stref (issue 303).** Strefa z udziałem w działce < `POG_SLIVER_MIN_PCT` (0,5%) **oraz** polem < `POG_SLIVER_MIN_SQM` (1 m²) jest artefaktem styku sąsiednich stref: poza `zones`, w `topological_slivers` z flagą
    `topological_sliver`, z notą (liczba, pole, udział) w raporcie i w UI oraz ostrzeżeniem `POG_TOPOLOGICAL_SLIVERS_OMITTED`. Okruchy zajmują powierzchnię — wlicza się je do oceny pokrycia działki; jedyne przecięcie
    nigdy nie jest nazwane okruchem. Suma udziałów wierszy tabeli różni się od 100% najwyżej o udział okruchów (jawna odchyłka w stopce tabeli).
12. **Granice modułów (ADR-001).** Porty w `application` (`RuActsGateway`, `ActPublisher`, `RegistryGateway`, `RunRecorder`, `PogTileRepository`, `PogStateQueryRepository`), adaptery w `infrastructure`, wiązanie w `composition.py` (warstwa `api` importuje wyłącznie kompozycję);
    wspólny kontrakt rejestru w `app/shared/pog_registry.py` jest wolny od frameworków (stałe i fragmenty SQL jako tekst); `SourceMetadata.source_id` pochodzi z katalogu.

## Wynik pomiaru (żywy RU, 2026-10-13; szczegóły i surowe pliki: pog-national-verification.md)

| Miara | Wynik |
|---|---|
| Próbka 28 gmin (30 aktów), współbieżność 3 | 154 s ściennie, 225 MB GML, 35 żądań do RU, 0 × HTTP 429 |
| Ekstrapolacja z próbki na 465 aktów | ok. 40 min, 3,6 GB GML, baza ok. 4,5 GB |
| **Pełny import krajowy** (435 aktów ponad próbkę) | **23,2 min** (1392 s), **3,0 GB** pobrane, 440 żądań do RU, 0 × HTTP 429; 434 z 435 aktów; baza **3,96 GB**, artefakty 3,0 GB |
| Przebieg przyrostowy bez zmian | 10,2 s ściennie (5 stron wykazu + ponowienie jednego aktu) |
| Kafel (stan krajowy, 464 akty), zimne p95 niepustych kafli | z6 13 ms · z11 57 ms · z14 30 ms · maks. rozmiar 199 KB (z11), 0 kafli > 500 KB |
| Krok lokalnej analizy POG działki | p50 51 ms · p95 83 ms (120 działek z losowych stref wszystkich aktów) |

Limity „zatrzymaj się i zgłoś” (pierwszy zasilany stan > 20 GB lub > 48 h) nie zostały zbliżone: 3,0 GB pobrane, 7 GB na dysku (baza + artefakty), 26 min łącznie.

## Decyzje do potwierdzenia przez właściciela (Proponowany)

1. **Redystrybucja kafli z atrybutami POG.** Katalog ma `redistribution: derived_only`. Kafle niosą uproszczone geometrie i atrybuty (symbol, parametry, od z13 nazwa i profile) — traktowane jako pochodne, serwowane
   wyłącznie przez API aplikacji (bez publicznego CDN i bez publikowania plików kafli). *Proponowany: bez zmian.*
2. **Parametry odświeżania.** `POG_SYNC_INTERVAL_SECONDS` = 86 400 (raz na dobę), `POG_NATIONAL_CONCURRENCY` = 3, odstęp żądań 1,2 s, jedna instancja `pog-sync`. *Proponowany: wartości domyślne; okno nocne do ustalenia z operatorem RU.*
3. **Akt z wadą danych w źródle** (Bolków, akt 3438: `minUdzialPowierzchniBiologicznieCzynnej` = 150% w strefie `…/1POG-51SU`) jest **odrzucany w całości** (`qa_rejected`), gmina pozostaje „plan w RU — jeszcze niezaimportowana”
   z podaniem obiektu i wartości. Alternatywa (import z parametrem `null` i ostrzeżeniem) przywracałaby dane gminy kosztem przyjęcia błędnego pliku. *Proponowany: odrzucenie; wada do zgłoszenia wydawcy aktu.*
4. **Masowe wycofanie:** blokada powyżej max(5, 10%) aktów z tokenem RU; wyższy próg wymaga jawnej zgody operatora. *Proponowany: wartości domyślne.*
5. **Import na żądanie** jest domyślnie włączony (`POG_ON_DEMAND_IMPORT_ENABLED=true`), z 3 próbami i 6 h przerwy po niepowodzeniu. *Proponowany: włączony.*
6. **Retencja:** wszystkie artefakty GML (3,0 GB dla kraju) i wszystkie wersje aktów są zachowywane bez czyszczenia (odtwarzalność raportów). *Proponowany: bez czyszczenia; polityka retencji po roku pracy.*
7. **Numer stanu na publikację aktu** (po imporcie krajowym: 464 stany) zamiast jednego stanu na przebieg. Zaleta: brak okna z częściowym stanem w jednym numerze, każdy akt widoczny od razu po publikacji; koszt: więcej numerów i unieważnień
   cache gmin. *Proponowany: stan na akt; grupowanie stanów per przebieg to osobna zmiana.*
8. **Warszawa i Kraków** nie mają bieżącego aktu POG w wykazie RU (2026-10-13) — to stan `no_act_in_ru`, analiza korzysta z discovery i MPZP. Decyzja produktowa, czy wskazywać użytkownikowi inny kanał dla tych miast.
9. **Limity kafla:** 20 000 obiektów i 4 MB na kafel (HTTP 413 powyżej); na stanie krajowym największy kafel ma 199 KB. *Proponowany: bez zmian.*

## Konsekwencje

- Import dowolnej liczby gmin nie ukrywa żadnej z nich; mapa, inspektor, analiza, raport i pakiet audytowy czytają jeden spójny stan. Pełne wdrożenie kraju to ok. 26 minut i 7 GB.
- Zapis jest tańszy niż w ADR-008 (brak kopiowania geometrii niezmienionych aktów), a odczyt droższy o złączenie z rejestrem (indeksowane; krok analizy 51–83 ms).
- Pamięć procesu API trzyma dwa małe cache per numer stanu (tablica wersji, metadane stanu) — numer stanu jednoznacznie wyznacza zbiór wersji, więc nie wymagają unieważniania; `pog-map-rebuild` po migracji ustawia `map_ready`.
- Pierwsze zapytanie o metadane stanu po starcie procesu trwa ok. 1,6 s (sprawdzenie kompletności tabel mapy dla 464 wersji), kolejne 3–7 ms.
- Migracja `037_pog_registry`: 7 nowych tabel (stany i wpisy rejestru, katalog RU, kolejka zleceń, trzy tabele mapy) oraz kolumny numeru stanu w agregatach `pog_area_summaries`; nowe kolumny tekstowe są ujęte w polityce długości (`document_link` w `pog_map_acts` jako `Text`).
- Nierozwiązane: brak kafli granic gmin/powiatów (przegląd krajowy używa granic aktów), brak widoku 3D zabudowy (z13), brak ortofotomapy i wyszukiwarki działek na mapie POG; potwierdzenie kanału importu (publiczne API frontendu RU nie jest
  usługą OGC z kontraktem) wymaga ręcznej kontroli fixtur przed zmianą po stronie RU (`capture_ru_pog_national.py`).
