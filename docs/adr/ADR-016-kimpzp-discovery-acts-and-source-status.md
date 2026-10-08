# ADR-016: Discovery MPZP z KIMPZP jako lista aktów z rozłącznym statusem źródła

- Status: **Zaakceptowany technicznie (2026-10-06)**
- Zakres: AU-004 (Task 21.4, audyt 2026-10-05, pozycje B4 i R3).
- Powiązane: ADR-001 (granice modułów: port w `application`, adapter w `infrastructure`), ADR-004 (wektor stref
  i evidence parametrów), ADR-005 (ręczny symbol strefy), ADR-011 (macierz jakości sekcji), ADR-015 (polityka błędów).
- Rozstrzygnięcie wyboru aktu przy wielu aktach w punkcie: **AU-101 / Task 22.1** (poza zakresem tego ADR).

## Kontekst

Dla działki z raportu OnGeo `141801_4.0701.23/8` (Góra Kalwaria) KIMPZP zwraca dwa bloki „Obowązujące MPZP”
(pisownia usługi) — `IV/30/2024` z linkiem „Tekst uchwały” `…/uch/IV_30_2024.pdf` oraz `576/XLVII/2010` — a po drugim
bloku tabelę „Zmiany tekstowe” z uchwałą `LIV/467/2021`. Dotychczasowy parser:

1. zakładał pary `<th>`/`<td>`, a ta gmina ma `<td><b>Klucz</b></td><td>Wartość</td>` — bloki planu nie dawały rekordu;
2. jedynym rekordem zostawała tabela zmian, więc wynik to `plan_id='LIV/467/2021'`, `uchwala_url=None`, a analiza
   zgłaszała `MPZP_DOCUMENT_OR_SYMBOL_MISSING` bez wskazania planu;
3. wybierał pierwszy rekord, więc nawet poprawnie odczytane dwa akty prowadziłyby do cichego wyboru jednego;
4. odpowiedzi „brak serwisu dla wskazanego obszaru”, `<oms_error>` (Warszawa) i `ServiceExceptionReport` (Bielsko-Biała),
   wszystkie z HTTP 200, kończyły się statusem `no_mpzp` — błąd źródła i brak danych wyglądały jak brak planu.

Pobranie odpowiedzi dla 30 działek korpusu i dodatkowych punktów (2026-10-06) pokazało pięć formatów w jednej usłudze:
bloki planu, tabele atrybutów Esri/GeoServer, pionowe pary `<th>`/`<td>`, warstwy QGIS Server z zagnieżdżonymi
obiektami oraz komunikaty tekstowe; segmenty różnych usług gminnych są sklejane separatorem `<hr/>`.

## Decyzje

### 1. Wynik punktu to lista aktów, nie „pierwszy rekord”

Adapter `app/modules/planning/infrastructure/kimpzp_feature_info.py` (za portem `KimpzpFeatureInfoParser` w
`application/ports.py`, udostępnianym przez `composition.kimpzp_feature_info_parser()`) zwraca `KimpzpPointResult`:
akty (`KimpzpAct`: numer uchwały, data, nazwa, „obowiązuje od”, „utracił moc”, status, linki tekstu/legendy/rysunku/BIP/WWW,
dziennik, symbole stref, zmiany) posortowane malejąco wg „obowiązuje od”, status źródła i kody przyczyn. Akty tego samego
numeru uchwały z kilku rekordów (np. trzy warstwy skali w Krakowie) są scalane.

### 2. Zmiany planu nigdy nie są aktem

Tabela zagnieżdżona w bloku planu albo następująca po nim (do kolejnego bloku) jest listą `amendments` tego aktu. Tabela
poprzedzona etykietą „Zmiany…” bez bloku planu jest pomijana, a nie awansowana na akt. Własność pilnują test
zagnieżdżenia i fuzz na rzeczywistych odpowiedziach (`tests/test_kimpzp_feature_info.py`).

### 3. Numer uchwały tylko z pola rekordu planu i po walidacji formatu

Numer pochodzi wyłącznie ze znanych kluczy rekordu planu (np. „Nr uchwały”, `numer_uchwaly`, „Uchwalenie”, „Uchwała”,
`numer`) albo z opisu `dokumentuchwalajacy` („Uchwała nr …”) i musi mieć co najmniej dwa człony rozdzielone ukośnikiem.
Nazwy plików (`XXI_232_20_rys`), numery porządkowe planu (`001`), UUID oraz wartości pod nieznanymi nagłówkami nie są
numerem uchwały. Warstwy informacji dodatkowych (`dod_info_*`) odwołują się do uchwały, ale nie są rekordem planu. Link
musi być bezwzględnym `http(s)://` — nazwa pliku nie jest linkiem.

### 4. Rozłączne statusy źródła

`available | no_match | no_coverage | unavailable | unknown`:

| Odpowiedź | Status | Kod |
|---|---|---|
| co najmniej jeden akt lub symbol strefy | `available` | (`KIMPZP_PARTIAL_SERVICE_ERROR`, gdy część punktów/usług zawiodła) |
| „<gmina>: brak wyniku dla wskazanego obszaru”, dokument HTML bez tabel | `no_match` | `KIMPZP_NO_RESULT_FOR_AREA` |
| „brak serwisu dla wskazanego obszaru” | `no_coverage` | `KIMPZP_NO_SERVICE_FOR_AREA` |
| `<oms_error>`, `ServiceExceptionReport`, błąd HTTP/sieci wszystkich punktów | `unavailable` | `KIMPZP_SERVICE_ERROR` |
| pusta lub nierozpoznana odpowiedź | `unknown` | `KIMPZP_EMPTY_RESPONSE` / `KIMPZP_UNRECOGNIZED_RESPONSE` |

Status wielu segmentów/punktów: `available` > `unavailable` > `unknown` > `no_match` > `no_coverage`. Tylko `no_match`
daje ostrzeżenie `MPZP_NOT_FOUND`; pozostałe mają własne kody (`KIMPZP_NO_SERVICE_FOR_AREA`, `MPZP_DISCOVERY_UNAVAILABLE`
z `severity=error`, `MPZP_DISCOVERY_UNKNOWN`) i w macierzy jakości status sekcji MPZP `no_coverage`/`unavailable`.

### 5. Bez cichego wyboru aktu

`MpzpDiscoveryResult.plan_id` i `uchwala_url` są wypełnione wyłącznie przy dokładnie jednym akcie obowiązującym lub o
nieznanym statusie (akt `not_binding` się nie liczy). Kilka aktów w jednym punkcie → `MPZP_MULTIPLE_ACTS_AT_POINT`, różne
akty w różnych punktach → `MPZP_MULTIPLE_ACTS_ON_PARCEL`; dokument nie jest pobierany ani parsowany, a strefa nie jest
przypisywana. To zmiana zachowania: wcześniej przy różnych planach w punktach używano pierwszego. Rozstrzygnięcie, który
akt decyduje o przeznaczeniu (np. nowszy akt zmieniający, zasięg zmiany), należy do AU-101.

### 6. Sekcja `mpzp_discovery` w kontrakcie API, zapisie i raporcie

`AnalyzeResponse.mpzp_discovery` (`MpzpDiscoverySection`, `schema_version` 1.0): status, kody, akty ze zmianami, akt użyty
w analizie (`selected_act`), flagi wielu aktów, kandydaci symboli, liczba punktów i nieudanych zapytań, źródło. Linki
klikalne w UI tylko po weryfikacji HTTPS (`verified_links`, `document_url_verified`) — linki `http://` (np. Góra
Kalwaria) są pokazywane jako tekst. Snapshot: `analyses.mpzp_discovery` (JSONB, migracja `032_mpzp_discovery`, bez
uzupełniania wstecz; `NULL` = zapis sprzed AU-004 → brak sekcji). `MPZP_RESULT_SCHEMA_VERSION` 2.6 → 2.7 zmienia
`RESULT_CONTRACT_VERSION`, więc wyniki 2.6 nie są serwowane z cache. Raport PDF: Tabela 3.5 i wersja w Tabeli 9.3;
mapowanie pól w `docs/report/field-mapping.md`. Macierz jakości: MPZP bez stref z rozpoznanym aktem → `partial`
(`MPZP_ACT_WITHOUT_ZONE` + flaga wielu aktów).

## Konsekwencje

- Warszawa i Bielsko-Biała (8 działek korpusu) przestają raportować „nie znaleziono MPZP” — wynik to `unavailable` z
  błędem źródła. Dotychczasowe artefakty korpusu (`status: no_mpzp`) opisują stan sprzed zmiany.
- Ścieżka rastrowa (`brak_wektorow`) działa jak wcześniej: tylko kontrakt JSON z `vector_available=false` i bez obiektów.
  Akt rastrowy z odpowiedzi HTML (np. Kalety) nie uruchamia trybu ręcznego symbolu — to decyzja AU-101.
- Dla jednego aktu bez symbolu strefy (Inowrocław, Góra Kalwaria po rozstrzygnięciu) ostrzeżenie
  `MPZP_DOCUMENT_OR_SYMBOL_MISSING` wskazuje teraz numer planu i brakujący element.
- KIMPZP jest usługą okresu przejściowego (komunikat Geoportalu z 2026-07-06); fixtures mają manifest SHA-256 i skrypt
  odświeżania `backend/scripts/capture_kimpzp_fixtures.py`, a zmiana formatu gminy da `unknown`, nie błędny plan.

## Alternatywy odrzucone

- **Wybór najnowszego aktu** (wg „obowiązuje od”) — w Górze Kalwarii nowszy plan obejmuje fragment („cz. I”), a starszy
  plan ma zmiany tekstowe; wybór bez analizy zasięgu byłby cichą interpretacją prawną. Odłożone do AU-101.
- **Osobny parser per gmina** — formaty powtarzają się między gminami (iGeoMap, QGIS, Esri), a pole numeru uchwały ma
  kilka nazw; jeden parser oparty o klucze i walidację numeru obsługuje 10 zamrożonych wariantów.
- **Ignorowanie komunikatów tekstowych** — dawało „brak planu” dla gmin poza KIMPZP i dla błędów usług.
