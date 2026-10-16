# ADR-024 — Import POG z WFS: równoległe pobieranie stron, postęp, checkpoint i wznawianie

Status: przyjęta 2026-10-12 (AU-106, Task 22.6, audyt R3). Powiązane: [ADR-001](ADR-001-modular-monolith.md) (granice modułów), [ADR-008](ADR-008-pog-release-complete-snapshot.md)
(wydanie POG jako kompletny snapshot), [ADR-003](ADR-003-pog-act-provenance-chain.md) (łańcuch provenance), [ADR-023](ADR-023-discovery-pog-ru-wfs-budzet-zadan.md) (ustalenia o usłudze RU).
Runbook i pomiary: [docs/operations/pog-import.md](../operations/pog-import.md).

## Kontekst

Import POG Bielska-Białej (1397 obiektów) trwał 5,5–6 min: `OgcClient.fetch_wfs` pobierał strony po kolei, a Rejestr Urbanistyczny zwraca **po 10 obiektów na stronę niezależnie od `count`**
(117 stron stref po ok. 2,8 s). Przebieg w tle kończył się bez żadnego wyjścia (pusty log), więc operator nie wiedział, czy proces żyje, a przerwanie w połowie oznaczało pobieranie od zera.
Dodatkowo artefakt źródłowy niósł atrybuty zmienne w czasie (`timeStamp`, `next`), więc nawet dwa nieprzerwane importy tych samych danych dawały różne skróty artefaktu.

## Decyzja

1. **Równoległe pobieranie stron** (`OgcClient.fetch_wfs(..., concurrency=N)`). Pierwsza strona ujawnia `numberMatched` i rozmiar strony; pozostałe `startIndex` są pobierane przez pulę
   wątków (limit grzeczności `IMPORT_WFS_CONCURRENCY`, domyślnie 4, `--concurrency` 1–8) i **składane w kolejności `startIndex`**, więc wynik nie zależy od kolejności zakończenia
   żądań. Zachowane są wszystkie zabezpieczenia trybu sekwencyjnego: spójność `numberReturned`, wykrywanie powtórzonej strony i powtórzonych identyfikatorów (`complete=false`), łączny limit bajtów
   (liczony wspólnie dla wątków), limity cech i stron, ponowienia przejściowego HTTP 4xx per strona. Strona krótsza niż zamówiona w środku zbioru (serwer zmienił rozmiar strony) przełącza
   resztę na tryb sekwencyjny od faktycznego indeksu. Brak `numberMatched` (nie da się zaplanować stron) to stary tryb sekwencyjny w całości. Pierwszy błąd przerywa planowanie kolejnych stron i
   jest zgłaszany jako przyczyna (nie wtórne przerwania). „Większy `count`” nie jest dostępny — RU go ignoruje (zob. ru_contracts.md); klient dalej wysyła `count=100` i adaptuje się do rozmiaru strony.
2. **Magazyn stron i wznawianie.** `FilePageStore` (`infrastructure/page_store.py`) zapisuje każdą stronę atomowo (plik tymczasowy + `os.replace`) z SHA-256 i rozmiarem w `meta.json`; strona
   z innym skrótem jest traktowana jak brak. Magazyn jest związany z **tokenem zapytania** (typ, SRS, filtr, `numberMatched`, rozmiar i identyfikatory pierwszej strony): zmiana stanu usługi
   unieważnia zapisane strony. Pierwsza strona jest zawsze pobierana z sieci. Katalog: `$IMPORT_ARTIFACT_STORAGE_DIR/resume/<klucz>/<NN-rola>/`, klucz = SHA-256 z (źródło, TERYT, `--act-id`, URL WFS,
   typy, filtr). Po udanej publikacji katalog jest usuwany; po `--dry-run` zostaje (kolejny przebieg nie pobiera stron ponownie).
3. **Artefakt zależny wyłącznie od treści.** Z korzenia złożonego `FeatureCollection` usuwane są `timeStamp`, `next` i `previous` (`VOLATILE_WFS_ATTRIBUTES`); `numberReturned`/`numberMatched`
   ustawia się z faktycznej liczby obiektów. Ten sam zbiór obiektów w tej samej kolejności daje te same bajty, ten sam `artifact_hash` i — przez `version_label = pog-<skrót(artefakt:parser)>` — **to samo wydanie**
   (publikacja jest idempotentna wobec istniejącego wydania). Skrót artefaktu starszych wydań (z `timeStamp`) się nie zmienia, ale nowy import tych samych danych da inny skrót niż stary — raz.
4. **Checkpoint w `import_runs.checkpoint`.** Faza pobierania ma własny wiersz (`importer_version = pog-download-v1`), zapisywany w **osobnej transakcji** (nowa sesja na każdą operację), żeby
   wycofanie głównej transakcji importu (błąd) nie kasowało postępu. Statusy: `running`, `interrupted`, `failed`, `fetched`, `succeeded`, `abandoned`. Kolejne uruchomienie z tym samym
   kluczem wznawia najnowszy wiersz wznawialny (`resumes` rośnie), `--fresh` oznacza poprzedni `abandoned`. Zapis co ≤ 10 s razem z komunikatem postępu; jego błąd jest logowany i nie
   przerywa importu. Wiersz nie wymaga migracji (kolumny `status`, `checkpoint`, `stats` istniały).
5. **Postęp i wyjście.** `ImportProgress` (czysty moduł `application/progress.py`, wątkowo-bezpieczny) zbiera zdarzenia stron (`WfsPageEvent`) i liczy ETA z tempa pobierania z sieci (strony z magazynu
   go nie zawyżają; nieznana liczba obiektów kolejnych typów daje „n/d”, nie zgadywanie). `HeartbeatReporter` drukuje linię na **stderr co ≤ `IMPORT_PROGRESS_INTERVAL_SECONDS` (10 s)** także bez nowych stron.
   **stdout** to jedna linia JSON z wynikiem, czasami faz i postępem — także przy błędzie i przerwaniu.
6. **Kody wyjścia.** `0` import zakończony (także `--dry-run`); `1` błąd (`rejected` — odrzucone QA, `failed` — wyjątek); `2` błąd użycia; **`3` import niepełny** (`interrupted` po SIGINT/SIGTERM albo
   `incomplete` przy nieukończonym pobieraniu, np. awarii usługi) — „uruchom ponownie”. SIGINT/SIGTERM kończą trwające żądania, zapisują postęp i wychodzą z kodem 3; drugi sygnał przerywa natychmiast
   (JSON nadal jest drukowany).
7. **Granice (ADR-001).** Port postępu i magazynu stron są zdefiniowane po stronie użycia (`FetchObserver`, `WfsPageStore`), implementacje w `infrastructure`, wiązanie w `composition.py`
   (`PogImportOptions`); nowe moduły nie importują cudzej infrastruktury. Dotychczasowe wywołania `run_pog_command` bez opcji zachowują dawne zachowanie (sekwencyjnie, bez magazynu).

## Wynik pomiaru (żywy RU, 2026-10-12, `--dry-run`, przebiegi starego i nowego kodu naprzemiennie)

| Gmina | Cechy APP | Stan wyjściowy | Po zmianie | Stosunek |
|---|---:|---:|---:|---:|
| Bielsko-Biała | 1397 | 359 s | 187 s / 123 s | 52% / 34% |
| Legnica | 983 | 213 s | 105 s | 49% |
| Sopot | 785 | 205 s | 68 s | 33% |

Kryterium „≤ 60% stanu wyjściowego” jest spełnione, ale nie z dużym zapasem w najgorszym przebiegu (52%): usługa RU skaluje się nieregularnie z równoległością (4 → ok. 2,7×; 8 w tym samym
przebiegu bez dalszej poprawy), a w Legnicy 24% czasu to sekwencyjne pobieranie metadanych CSW po pobraniu stron. **Nie zmierzono** kolejnych kroków (CSW równolegle ze stronami WFS, nakładanie typów) —
oszczędność zależy od zachowania RU i nie była potrzebna do kryterium.

## Konsekwencje

- Import gminy trwa 1–3 minut zamiast 3,5–6; operator widzi postęp, ETA i kod wyjścia, a przerwanie albo awaria RU nie kasuje pobranych stron.
- Wątki pobierające strony współdzielą jednego klienta `httpx` (bezpieczny wątkowo) i licznik bajtów; liczba równoległych połączeń do RU jest ograniczona do `--concurrency` (≤ 8).
- Pierwszy import po zmianie da inny skrót artefaktu niż poprzedni import tych samych danych (z powodu usunięcia `timeStamp`/`next`) — to jednorazowe, a kolejne są deterministyczne.
- Magazyn stron zajmuje miejsce na wolumenie `import_artifacts` (kilka MB na gminę) do czasu udanej publikacji.
- Nierozwiązane: `max_pages=500`/`max_features=10 000` na typ (≤ 5 000 obiektów typu przy stronach po 10) — import największych gmin skończy się jawnym błędem limitu; podniesienie limitów to osobna decyzja.

## Do potwierdzenia przez właściciela

1. Domyślna równoległość 4 (limit grzeczności wobec usługi rządowej) — pomiar nie wykazał zysku z 8, ale usługa jest zmienna.
2. Wznawianie domyślnie włączone (`--no-resume` wyłącza); alternatywą jest wznawianie tylko na jawne `--resume`.
3. Kod wyjścia 3 dla importu niepełnego jako kontrakt dla automatyzacji (cron, CI).

## Aneks (2026-10-13, [ADR-026](ADR-026-krajowy-stan-pog-rejestr-wersji-aktow.md)): import krajowy używa tej samej infrastruktury, ale innego kanału

Ten ADR dotyczy importu **jednej gminy z WFS** (`pog --act-id … --teryt …`) i pozostaje w mocy jako **kanał zapasowy**. Import dla całego kraju ([ADR-026](ADR-026-krajowy-stan-pog-rejestr-wersji-aktow.md))
czyta pliki GML całych aktów z publicznego API RU (`pog-national`), bo WFS ze stronami po 10 obiektów jest dla ~465 aktów za wolny (dziesiątki godzin) i kończy się limitem 5 000 obiektów typu przy największych gminach.
Zachowane z tego ADR: kody wyjścia **0/1/2/3** (3 = „uruchom ponownie”), jedna linia JSON na stdout, postęp i ETA na stderr co ≤ `--progress-interval`, wznawianie tym samym poleceniem (w imporcie krajowym
wznowienie opiera się na tokenach `publishedActId` w rejestrze zamiast na magazynie stron), artefakt zależny wyłącznie od treści (`content_hash` wersji aktu), granice ADR-001 (port w `application`, adapter w `infrastructure`,
wiązanie w `composition.py`). Dodane: równoległość **aktów** (`--concurrency` 1–6, domyślnie 3, limit grzeczności wobec RU), budżet pamięci na bajty GML w locie, izolacja błędów per akt i wycofywanie aktów
wycofanych z RU bez kasowania historii.

Kontrola regresji liczby obiektów (spadek > 50% względem poprzedniej wersji) jest od ADR-026 liczona **per akt**, a nie dla całego wydania: duży akt innej gminy nie maskuje ani nie wywołuje regresji
(`test_pog_per_act_controls.py`). Wiersze `import_runs` importu krajowego mają `importer_version = pog-national-v1`.
