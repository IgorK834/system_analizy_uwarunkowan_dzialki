# ADR-020: Akty MPZP z Rejestru Urbanistycznego, przecięcie z granicą aktu, pierwszeństwo informacyjne i stan ustalenia MPZP

- Status: **Zaakceptowany technicznie (2026-10-10)**; wartości progowe i reguła pierwszeństwa czekają na potwierdzenie
  właściciela (patrz „Do potwierdzenia”).
- Zakres: AU-101 (Task 22.1, pozycje audytu R3, B4, §6 Etap 1 pkt 1) i AU-103 (Task 22.3, pozycje B11, R3).
  AU-102 (parser) opisuje [ADR-021](ADR-021-mpzp-zone-section-detection.md).
- Powiązane: ADR-001 (port w `application`, adapter w `infrastructure`), ADR-004 (RU nie jest źródłem `vector_zones`),
  ADR-011 (macierz jakości sekcji), ADR-016 (discovery KIMPZP jako lista aktów), ADR-015 (błędy bez surowego 500).

## Kontekst

Źródło `mpzp_ru` (Rejestr Urbanistyczny, WFS `app-mpzp`) było w `catalog.yaml` jako `production`, ale żaden kod
runtime go nie używał. AU-004 sprowadził KIMPZP do listy aktów w punktach działki, lecz przy wielu aktach nie wybiera
żadnego i nie ma geometrii: nie da się powiedzieć, który akt faktycznie przecina działkę ani w jakiej części.
Pomiary na żywych usługach (2026-10-10), na działkach korpusu referencyjnego i `141801_4.0701.23/8`:

1. RU zwraca akty dla `bbox` działki, ale **filtruje po obwiedni** — wielokąt aktu może nie dotykać `bbox`, a tym bardziej
   działki (Kraków 540/15: obwiednie nachodzą, wielokąty nie; Góra Kalwaria 23/8: projekt cz. II leży 5,99 m od działki).
2. **Kolejność osi dla URN EPSG:2180 to północ, wschód** — zarówno w parametrze `bbox`, jak i w `gml:posList` odpowiedzi
   (potwierdzone na Górze Kalwarii, Bielsku-Białej i Krakowie).
3. RU jest **niekompletny** i zawiera projekty: dla Góry Kalwarii zwraca osiem aktów, wszystkie w statusie „w opracowaniu”;
   `IV/30/2024` jest wyłącznie w KIMPZP. Spośród 30 działek korpusu RU wskazuje akt dla dwóch: obowiązujący plan „Starówka”
   w Bielsku-Białej (tam KIMPZP zwraca błąd usługi) i projekt we Wzgórzu Wawelskim w Krakowie.
4. Akt będący tylko uchwałą o przystąpieniu ma numer uchwały o przystąpieniu w identyfikatorze (`XXIV_302_2026_P1`) —
   to nie jest numer planu. Numer uchwały uchwalającej leży w `DokumentFormalny.numerIdentyfikacyjny`.
5. Usługa bywa zawodna: HTTP 400 z `ows:ExceptionReport`, zerwanie połączenia bez odpowiedzi, strony po 10 obiektów.
6. UI i PDF pokazywały jedno zdanie „Nie sprawdzono albo nie znaleziono stref MPZP…” dla zupełnie różnych sytuacji
   (brak usługi, pusta odpowiedź, błąd źródła, wiele planów), a macierz jakości sekcji `mpzp` miała „brak źródła”.

## Decyzje

### 1. Kanał `mpzp_ru` jako adapter za portem (AU-101)

Port `RuMpzpActsSource` (`modules/planning/application/ports.py`): `fetch_acts(bbox)` i `fetch_related(dokumenty, rysunki)`.
Adapter `modules/planning/infrastructure/ru_mpzp_acts.py` działa na `OgcClient` (HTTPS, allowlista hostów, limity bajtów,
stron i obiektów, ponowienia — także zerwanego połączenia). Adres WFS pochodzi z katalogu źródeł (zasób `boundaries`), a
`composition.ru_mpzp_acts_source()` przechodzi guard `ensure_source_runnable("mpzp_ru")`. Wyłącznik: `RU_MPZP_ACTS_ENABLED`.

- `bbox` w kolejności `N_min,E_min,N_max,E_max,urn:ogc:def:crs:EPSG::2180`; współrzędne odpowiedzi są odwracane do `(x = wschód, y = północ)`.
- **Kontrola osi:** obwiednia zwróconego aktu musi nachodzić na `bbox` zapytania. Rozjazd oznacza zmianę kolejności osi po
  stronie usługi i daje geometrię `null` z kodem `MPZP_RU_GEOMETRY_AXIS_SUSPECT` (kanał `unknown`) — a nie fałszywe „akt poza
  działką”. Pierwsza wersja kontroli wymagała przecięcia *wielokąta* z `bbox` i uznawała rzeczywisty projekt z Krakowa za
  dane z zamienionymi osiami; test regresyjny używa zamrożonej odpowiedzi.
- Dokumenty i rysunki są pobierane po `resourceId` (`gml:id` = segmenty identyfikatora połączone `_`), tylko dla aktów
  przecinających działkę, po 8 identyfikatorów na żądanie.
- **Błąd nigdy nie jest „brakiem aktów”:** każdy `OgcError` wraca jako `RuActsResponse.error_code`, a serwis ustawia stan
  kanału `unavailable`; odpowiedź niepełna (przerwana paginacja) bez aktu to `unknown`.
- **RU nie jest źródłem stref:** akt nie trafia do `mpzp_zones`; guard `ensure_mpzp_vector_zones_source` pozostaje.

### 2. Przecięcie w EPSG:2180 i odrzucanie aktów dotykających `bbox`

`intersect_act_with_parcel` (domena, czysta funkcja) liczy `intersection_area_sqm` i `intersection_pct` na geometriach w
EPSG:2180 (po `make_valid`, gdy trzeba; flaga `MPZP_ACT_GEOMETRY_REPAIRED`). Akt jest odrzucany z diagnostyką
(`MpzpStatus.rejected_acts[]`): `no_overlap` (z odległością w metrach), `boundary_touch_only`, `below_min_area`
(przecięcie < `MIN_INTERSECTION_AREA_SQM` = 0,5 m², szum digitalizacji), `geometry_unusable`. Test rozstrzygający
przypadek z audytu: akt cz. II (`XXIV/302/2026`) zwrócony dla `bbox` 23/8 **nie przecina** działki (odległość 5,994 m) —
wynik wskazuje `IV/30/2024` z KIMPZP i jawnie opisuje akt cz. II jako odrzucony projekt.

### 3. Numer uchwały i status prawny

`resolution_number` pochodzi z dokumentu uchwalającego (`document_number` > `document_title`); dla projektu znanego tylko z
uchwały o przystąpieniu jest `null`, a `document_kind = commencement`. Numer z identyfikatora RU
(`IV_30_2024_P1` → `IV/30/2024`) jest ostatecznością i tylko dla aktu o statusie obowiązującym/utraconym bez dokumentu
(`resolution_number_basis = identifier`). Status prawny: kod INSPIRE `legalForce` → `binding`; `elaboration`/`adoption` →
`not_yet_binding` (data „obowiązuje od” projektu to data wersji rekordu, UI i PDF jej nie pokazują jako daty wejścia w życie);
`obsolete`, „obowiązuje do” ≤ dziś → `not_binding`; „obowiązuje od” w przyszłości → `not_yet_binding`; inny kod → `unknown`.

### 4. Scalenie kanałów i zgodność

Ten sam akt z RU i KIMPZP jest łączony po numerze uchwały (a bez numeru — po znormalizowanym linku do tekstu); `sources` zawiera
oba kanały. Status łączny jest ostrożny: jeśli którykolwiek kanał uznaje akt za pozbawiony mocy lub nieobowiązujący, taki jest
wynik. `channel_agreement`: `agree`, `mismatch` (oba kanały wskazują akty obowiązujące, a zbiory się różnią →
ostrzeżenie `MPZP_CHANNEL_MISMATCH` z oboma listami), `single_channel` (RU nie ma aktu — to normalne przy niepełnym RU, nie
niezgodność), `none`. Projekt RU nie konkuruje z obowiązującym aktem KIMPZP.

### 5. Reguła pierwszeństwa informacyjnego `mpzp-act-precedence/1.0`

Jawna, wersjonowana (jak `planning_compatibility`), zapisana w `mpzp_status.precedence_rule_version`:

| Reguła | Skutek |
|---|---|
| R1 akt bez mocy albo jeszcze nieobowiązujący (utracił moc, projekt) | `not_in_force`, nie uczestniczy |
| R2 akty uczestniczące, których części działki są rozłączne (obie granice znane) | `complementary`, bez weryfikacji |
| R3 akty uczestniczące, które mogą się nakładać (wspólna część albo brak granicy któregokolwiek) | `unresolved` + `manual_review_required` |
| R4 jedyny akt uczestniczący | `applies` (status nieustalony → `manual_review_required`) |

**Późniejsza data „obowiązuje od” sama nie rozstrzyga** — uchwała może obejmować tylko część obszaru wcześniejszego planu, a
zakres zmiany zna dopiero jej tekst. Rozstrzygają jawne sygnały danych: status, „utracił moc”/„obowiązuje do” (R1) i
rozłączność geometrii (R2). Zmiany planu (KIMPZP „Zmiany tekstowe”, RU `dokumentZmieniajacy`) są listą `amendments` aktu,
nigdy osobnym aktem; ich obecność daje informacyjny kod `MPZP_ACT_HAS_AMENDMENTS`. Jedyny rozstrzygnięty akt
(`MpzpActsResolution.selected`) zastępuje brak wyboru z AU-004 — dokument tego aktu może być wtedy sparsowany; przy braku
rozstrzygnięcia dokument nadal nie jest wybierany po cichu.

Efekt dla działki z audytu: `IV/30/2024` i `576/XLVII/2010` (oba „obowiązujące” w KIMPZP, bez granic) są w stanie `ambiguous` z
`manual_review_required`; system nie twierdzi, że nowszy plan wyparł starszy.

### 6. Rozłączny stan ustalenia MPZP `mpzp_status` (AU-103)

Sześć stanów: `available`, `ambiguous`, `no_match`, `no_coverage`, `unavailable`, `unknown` — każdy z `reason_code`, etykietą
i komunikatem z jednego miejsca (`app/shared/mpzp_status_text.py`), tym samym w API, UI, PDF i paczce audytowej. Tabela
decyzyjna (`modules/planning/domain/mpzp_status.py`): strefy z przecięcia wektorowego lub ręczny symbol → `available`; akt
obowiązujący → `available`/`ambiguous`; brak aktu: którykolwiek kanał `unavailable` → `unavailable` (**`no_match` nigdy nie
występuje przy błędzie źródła**, także gdy drugi kanał mówi `no_match`), `unknown` → `unknown`, same akty bez mocy →
`no_match` (`MPZP_ONLY_NON_BINDING_ACTS`), KIMPZP „brak serwisu” → `no_coverage`, w pozostałych przypadkach `no_match`.
`mpzp_status.source` to źródło kanału rozstrzygającego — macierz jakości sekcji `mpzp` bierze z niego `source_id` (koniec
„brak źródła” także przy błędzie i braku pokrycia).

### 7. Kontrakt, snapshot, raport i paczka

`AnalyzeResponse.mpzp_acts[]` i `mpzp_status`; `MPZP_RESULT_SCHEMA_VERSION` 2.7 → **2.8** (akty) → 2.9 (AU-102, parser) →
**3.0** (stan); `RESULT_CONTRACT_VERSION` zmienia się razem z nią, więc zapisy 2.x nie są trafieniem cache. Migracje `033_mpzp_acts`
i `034_mpzp_status` (kolumny JSONB nullable, bez uzupełniania wstecz: `NULL` = zapis sprzed zmiany, UI pokazuje wtedy osobny
komunikat „zapis sprzed wprowadzenia stanów”, a nie „nie znaleziono planu”). Po wznowieniu ręcznym kod powodu zmienia się na
`MPZP_MANUAL_ZONE_SELECTED`. PDF: ramka stanu, Tabela 3.6 (akty), Tabela 3.7 (kanały i akty odrzucone), Mapa 3a (granice aktów
w EPSG:2180, zamrożona w snapshocie map). Paczka audytowa `audit-exporter/1.3.0`: `layers/mpzp_acts.geojson`
(RU: `redistribution: allowed`; akt tylko z KIMPZP nie ma geometrii), `analysis.json` ze stanem, sekcja README o sześciu stanach.
Geometrie w odpowiedzi: część aktu na działce i granica przycięta do okna wokół działki, uproszczona (≤ 4000 wierzchołków, inaczej
pominięta z kodem `MPZP_ACT_BOUNDARY_OMITTED`).

## Konsekwencje

- Dla gmin, w których KIMPZP zawodzi, a RU ma obowiązujący plan, wynik wskazuje akt z udziałem działki i dokumentem; dla gmin
  z samym KIMPZP lista aktów wygląda jak w AU-004, ale z jednolitym stanem i jawnym `ambiguous`.
- Dodatkowe 2–3 żądania HTTP do RU na analizę (równolegle z KIMPZP); błąd lub wyłączenie kanału nie zatrzymuje analizy.
- Cache analiz jest unieważniony przez zmianę wersji kontraktu (jednorazowo).

## Do potwierdzenia przez właściciela

1. Próg `MIN_INTERSECTION_AREA_SQM` = 0,5 m² (szum granic) — decyzja inżynierska, nie prawna.
2. Reguła `mpzp-act-precedence/1.0`: czy przy nakładających się aktach nowszy plan ma domyślnie wypierać starszy (obecnie: nie,
   wymagana ręczna weryfikacja). Zmiana reguły wymaga nowej wersji reguły i wpisu w tym ADR.
3. `freshness_policy` źródła `mpzp_ru`: **nie wprowadzono** (źródło zostaje bez reguły, jak przed AU-101 — świeżość `unknown`, bez TTL). To samo `source_id` opisuje
   też wektorowe strefy z importu, dla których 7-dniowy limit wieku byłby błędny; reguła dla zapytania na żywo (np. 7 dni jak GDOŚ/ISOK/NMT) wymaga osobnego rozróżnienia
   kanału i decyzji właściciela, a zmienia `quality_policy_version`.
4. `redistribution: allowed` dla `mpzp_ru` jest ostrożną interpretacją publicznego GetCapabilities (bez ograniczeń dostępu);
   nie jest opinią prawną.
