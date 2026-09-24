# ADR-003: Łańcuch provenance aktu POG — WFS, CSW i dokumenty formalne

- Status: Zaakceptowany
- Data: 2026-09-24
- Zadanie: BK-107 (Task 2.7), zależności BK-102, BK-103, BK-105, BK-106
- Kontekst szerszy: `context.md` §19, ADR-001, ADR-002

## Kontekst

Wynik POG po BK-105 wskazywał wydanie danych i SHA artefaktu, ale nie
pozwalał przejść od parametru strefy do urzędowego źródła ani ustalić, z której
publikacji aktu i których dokumentów formalnych pochodzi. Dokumenty łączono
z aktem porównaniem sufiksu URI (`endswith`), a unikalność w bazie nie
uwzględniała wersji dokumentu, więc dwie wersje o tym samym identyfikatorze
nie mogły współistnieć.

## Decyzja

### Łańcuch

```text
cecha (strefa/OUZ/OZS/OSDIS: idIIP + wersjaId + URL GML)
  → akt/wersja (act_identifier, act_version, publication_id = gml:identifier,
                version_started_at = poczatekWersjiObiektu,
                valid_from/valid_to = obowiazujeOd/Do, URL GML wersji)
  → metadane CSW (rekord ISO 19139: fileIdentifier, MD_Identifier,
                  data publikacji, URL karty GetRecordById, SHA-256 rekordu)
  → dokumenty formalne (idIIP + wersja, relacja, daty, link, SHA-256 rekordu,
                        status: current | superseded | unavailable | unresolved)
  + wydanie danych (data_release_id, etykieta, SHA-256 artefaktu, czas pobrania)
```

### Reguły łączenia

1. **Po identyfikatorze i wersji, nigdy po tytule.** Odwołania xlink są
   parsowane do `(typ, przestrzeń nazw, lokalnyId, wersja)`
   (`parse_app_reference`). Wersja w odwołaniu, jeśli podana, musi się zgadzać.
2. **Dokumenty** (`resolve_act_documents`):
   - dokładnie jeden rekord pasujący do odwołania aktu → `resolved`;
   - odwołanie bez wersji pasujące do wielu wersji → wszystkie `unresolved`;
   - akt wskazuje konkretną wersję → pozostałe wersje tego dokumentu są
     `unresolved` (nie są podpinane do tej wersji aktu);
   - dokument wskazuje inną wersję aktu → `unresolved` z notą;
   - odwołanie aktu bez rekordu dokumentu → wpis `unavailable`.
   Unikalność w PostGIS obejmuje wersję dokumentu, więc rekordy o tym samym
   tytule i różnych wersjach nie są scalane.
3. **CSW.** Rekord jest wiązany wyłącznie, gdy `MD_Identifier/code` równa się
   URI przestrzeni nazw aktu
   (`https://www.gov.pl/zagospodarowanieprzestrzenne/app/AktPlanowaniaPrzestrzennego/{przestrzenNazw}/`).
   pycsw RU nie obsługuje filtra po identyfikatorze zasobu, więc zapytanie
   zawęża wyniki po TERYT w tytule, ale tytuł niczego nie wiąże (w próbce
   Sopotu odpowiedź zawiera rekordy POG i MPZP — wiązany jest tylko POG).
4. **Klient.** CSW jest pobierane wspólnym `OgcClient` (BK-102: allowlista,
   limity, brak przekierowań na obcy host, bezpieczny parser XML). Awaria CSW nie
   blokuje importu: wynik ma jawne ostrzeżenie `csw_unavailable` i brak
   metadanych (provenance niepełnego wyniku jest zachowane).
5. **SHA-256** rekordów XML jest liczony z postaci kanonicznej C14N 2.0
   (`rewrite_prefixes=True`), więc nie zależy od prefiksów ani serializacji.
   Odpowiedź CSW trafia do artefaktu importu (`90-csw-{TERYT}.xml`), a SHA
   artefaktu obejmuje WFS i CSW.
6. **Linki.** Klikalne są wyłącznie adresy zweryfikowane składniowo jako
   bezwzględny HTTPS z nazwą hosta, bez danych logowania, adresu IP, hosta
   lokalnego i niestandardowego portu (`is_verified_https_url`). Frontend
   ponownie sprawdza protokół; tytuły są escapowane (React, Jinja2 autoescape).
   Oficjalny URL GML jest składany z usługi, z której obiekt faktycznie
   zaimportowano (`source_reference`), z filtrem FES po idIIP i wersji.
7. **Snapshot.** Pełny łańcuch jest częścią `PogResult` (`schema_version`
   `2.2`) zapisanego w `pog_data.result_v2`. Odczyt starej analizy (API,
   cache, PDF) nie odpytuje katalogu ani usług i nie czyta bieżącej wersji aktu.

### Model danych (migracja `017_pog_act_provenance`)

- `planning_act_versions`: `publication_id`, `version_started_at`,
  `legal_valid_from`, `legal_valid_to`, `source_reference`.
- `pog_formal_documents`: `publication_id`, `short_name`,
  `identification_number`, `relation`, `document_date`, `effective_date`,
  `repeal_date`, `record_sha256`, `link_verified`, `resolution_status`
  (`CHECK`), `resolution_note`; unikalność
  `(planning_act_version_id, document_identifier, coalesce(document_version,''))`.
- `pog_act_metadata_records`: zamrożone rekordy CSW wersji aktu.

Downgrade usuwa dodane kolumny i tabelę; przywrócenie unikalności 015 wymaga
usunięcia dodatkowych wersji dokumentów (zostaje najstarszy rekord) — operacja
stratna, wykonywana wyłącznie na odizolowanej bazie.

## Konsekwencje

- UI i PDF pokazują identyfikatory, wersję, publikację, wydanie z SHA, link do
  GML i karty CSW oraz dokumenty z SHA; nieaktualny, niedostępny lub
  nierozstrzygnięty dokument ma widoczne ostrzeżenie.
- Zmiana metadanych CSW przy niezmienionym WFS tworzy nową wersję aktu (hash
  snapshotu obejmuje metadane) — wynik zawsze wskazuje materiał faktycznie użyty.
- Wersja kontraktu `2.2` zmienia sygnaturę cache (`pog-v2.2`).
