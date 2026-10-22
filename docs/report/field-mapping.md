# Mapowanie pól `AnalyzeResponse` na raport PDF v2

> Plik generowany: `python backend/scripts/export_report_field_mapping.py`.
> Źródło prawdy: `backend/app/modules/reporting/domain/field_mapping.py`;
> test `backend/tests/test_report_field_mapping.py` wymaga, aby każda ścieżka
> liścia kontraktu API pasowała do wzorca i aby ten plik był zgodny z kodem.

Wzorce: `[]` — element listy, `*` — dowolny ciąg znaków (także kropki).
Pierwszy pasujący wzorzec wygrywa. Ten sam wykaz jest załącznikiem A każdego PDF
(z liczbą wartości obecnych i null w danej analizie).

## Sekcje raportu

1. Identyfikacja i geometria działki
2. Podsumowanie wykrytych uwarunkowań
3. Miejscowy plan zagospodarowania przestrzennego (MPZP)
4. Plan ogólny gminy (POG) oraz OUZ, OZS i OSDIS
5. Środowisko: zagrożenie powodziowe i ochrona przyrody
6. Teren (NMT)
7. Infrastruktura i transport
8. Jakość i kompletność analizy
9. Źródła i provenance
10. Ograniczenia interpretacyjne

## Tabela mapowania

| Pole API (wzorzec) | § | Element raportu / uzasadnienie pominięcia | Rodzaj |
|---|---|---|---|
| `analysis_id` | 1 | Tabela 1.1 — identyfikator analizy | metadane |
| `status` | 1 | Tabela 1.1 — status analizy; macierz 8.1 | metadane |
| `analyzed_at` | 1 | Tabela 1.1 — data analizy (stan danych) | metadane |
| `parcel.parcel_identifier` | 1 | Tabela 1.1 — identyfikator działki | fakt źródłowy |
| `parcel.geometry_geojson` | 1 | Mapa 1 — obrys działki zamrożony w EPSG:2180 (snapshot mapy) | fakt źródłowy |
| `parcel.metrics.*` | 1 | Tabela 1.2 — pole [m², ha], obwód [m], poprawność i naprawa geometrii | wynik obliczenia |
| `parcel.buildable_area_geojson` | 1 | Mapa 1 — obszar po technicznym odsunięciu od granic | przybliżenie |
| `parcel.source.*` | 9 | Tabela 1.1 — źródło geometrii; Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `buildable_area_sqm` | 1 | Tabela 1.2 — szacowany obszar zabudowy [m²] | przybliżenie |
| `mpzp_zones[].intersection_geojson` | 3 | Mapa 3 — przecięcia stref MPZP (zamrożone w EPSG:2180) | wynik obliczenia |
| `mpzp_zones[].intersection_area_sqm` | 3 | Tabela 3.1 — pole przecięcia [m²] | wynik obliczenia |
| `mpzp_zones[].intersection_pct` | 3 | Tabela 3.1 — udział w działce [%] | wynik obliczenia |
| `mpzp_zones[].touches_boundary` | 3 | Tabela 3.1 — oznaczenie „tylko styk granicy” | wynik obliczenia |
| `mpzp_zones[].is_dominant` | 3 | Tabela 3.1 — oznaczenie „największy udział” (pole pomocnicze) | wynik obliczenia |
| `mpzp_zones[].assignment_method` | 3 | Tabela 3.1 — sposób przypisania strefy | metadane |
| `mpzp_zones[].manual_review_required` | 8 | Tabela 3.1 — plakietka weryfikacji; macierz 8.1 | metadane |
| `mpzp_zones[].manual_selection.*` | 3 | Tabela 3.4 — decyzja użytkownika i przypięty dokument (tryb ręczny) | dane ręczne |
| `mpzp_zones[].parameters[].review_status` | 3 | Tabele 3.2 i 3.3 — znacznik odczytu automatycznego i status kandydata (tylko wartości z modelu) | odczyt automatyczny |
| `mpzp_zones[].parameters[].model_id` | 3 | Tabela 3.3 — provenance odczytu automatycznego: model | odczyt automatyczny |
| `mpzp_zones[].parameters[].prompt_version` | 3 | Tabela 3.3 — provenance odczytu automatycznego: wersja instrukcji | odczyt automatyczny |
| `mpzp_zones[].parameters[].response_sha256` | 3 | Tabela 3.3 — provenance odczytu automatycznego: skrót (SHA-256) odpowiedzi modelu | odczyt automatyczny |
| `mpzp_zones[].parameters[].*` | 3 | Tabela 3.3 — evidence parametrów (odsyłacze [E#]: strona, segment, SHA-256) | fakt źródłowy |
| `mpzp_zones[].source.*` | 9 | Tabela 3.1 — kolumna „źródło”; Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `mpzp_zones[].*` | 3 | Tabela 3.1 (symbol, ID, akt, wersja, wydanie) i Tabela 3.2 (przeznaczenie, parametry z jednostkami) | fakt źródłowy |
| `manual_zone_required` | 3 | Tabela 3.4 — analiza wstrzymana do podania symbolu; macierz 8.1 | metadane |
| `manual_zone_context.document.preview_path` | 3 | *pominięte:* ścieżka endpointu podglądu dokumentu dla UI — w PDF nieaktywna; dokument opisuje SHA-256 | metadane |
| `manual_zone_context.raster_preview_source_key` | 3 | *pominięte:* klucz źródła podglądu rastrowego UI — WMS nie jest renderowany w PDF (BK-503) | metadane |
| `manual_zone_context.symbol_max_length` | 3 | *pominięte:* reguła walidacji formularza UI | metadane |
| `manual_zone_context.symbol_allowed_pattern` | 3 | *pominięte:* reguła walidacji formularza UI | metadane |
| `manual_zone_context.*` | 3 | Tabela 3.4 — plan, kandydaci, status i SHA-256 przypiętego dokumentu, komunikat | dane ręczne |
| `mpzp_acts[].intersection_geojson` | 3 | Mapa 3a — część działki objęta aktem (zamrożona w EPSG:2180) | wynik obliczenia |
| `mpzp_acts[].boundary_geojson` | 3 | Mapa 3a — granica aktu w oknie wokół działki (linia przerywana) | fakt źródłowy |
| `mpzp_acts[].intersection_area_sqm` | 3 | Tabela 3.6 — pole części działki objętej aktem [m²] | wynik obliczenia |
| `mpzp_acts[].intersection_pct` | 3 | Tabela 3.6 — udział działki objęty aktem [%] | wynik obliczenia |
| `mpzp_acts[].precedence` | 3 | Tabela 3.6 — pierwszeństwo informacyjne (reguła mpzp-act-precedence) | wynik obliczenia |
| `mpzp_acts[].manual_review_required` | 8 | Tabela 3.6 — plakietka „wymaga weryfikacji”; macierz 8.1 | metadane |
| `mpzp_acts[].amendments[].*` | 3 | Tabela 3.6 — zmiany aktu (numer, data, nazwa, link do tekstu zmiany) | fakt źródłowy |
| `mpzp_acts[].*` | 3 | Tabela 3.6 — akt MPZP: numer, tytuł, daty, status prawny, dokument, legenda, rysunek, źródła, kody powodów | fakt źródłowy |
| `mpzp_status.schema_version` | 9 | Tabela 9.3 — wersje kontraktów snapshotu | metadane |
| `mpzp_status.source.*` | 9 | Tabela 3.7 — źródło rozstrzygające stan; Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `mpzp_status.channels[].source.*` | 9 | Tabela 3.7 — źródło kanału; Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `mpzp_status.channels[].*` | 3 | Tabela 3.7 — stan kanału KIMPZP / Rejestru Urbanistycznego, liczba aktów i kody powodów | fakt źródłowy |
| `mpzp_status.rejected_acts[].*` | 3 | Tabela 3.7 — akty odrzucone z diagnostyką (powód, odległość od działki) | wynik obliczenia |
| `mpzp_status.*` | 3 | Ramka „Stan ustalenia MPZP” na początku sekcji 3 (stan, etykieta, komunikat, kod powodu) i Tabela 3.7 (zgodność kanałów, wersja reguły pierwszeństwa) | metadane |
| `mpzp_discovery.schema_version` | 9 | Tabela 9.3 — wersje kontraktów snapshotu | metadane |
| `mpzp_discovery.source.*` | 9 | Tabela 3.5 — wiersz „Źródło”; Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `mpzp_discovery.acts[].amendments[].*` | 3 | Tabela 3.5 — kolumna „Dokumenty i zmiany” (rodzaj, numer, daty, nazwa, opis, linki zmiany) | fakt źródłowy |
| `mpzp_discovery.acts[].*` | 3 | Tabela 3.5 — akty wskazane przez KIMPZP: uchwała, nazwa, status, daty, linki tekstu/legendy/BIP, format odpowiedzi gminnej | fakt źródłowy |
| `mpzp_discovery.*` | 3 | Tabela 3.5 — status źródła z kodami, akt użyty w analizie albo flaga wielu aktów, punkty próbki i kandydaci symboli; charakter discovery opisuje nota pod tabelą | metadane |
| `pog.schema_version` | 9 | Tabela 9.3 — wersje kontraktów snapshotu | metadane |
| `pog.status` | 4 | *pominięte:* historyczny alias statusu (ADR-002) — raport pokazuje kanoniczne legal_status | metadane |
| `pog.raw_attributes` | 4 | *pominięte:* surowe atrybuty rekordu źródłowego bez normalizacji — nie są ustaleniem; dostępne w API, a w pakiecie audytowym (BK-505) tylko gdy katalog źródeł zezwala na redystrybucję surowych danych | metadane |
| `pog.legal_status_evidence.*` | 4 | Tabela 4.1 — podstawa statusu prawnego | fakt źródłowy |
| `pog.coverage_evidence.*` | 4 | Tabela 4.1 — podstawa zakresu danych | fakt źródłowy |
| `pog.act.metadata.metadata_url_verified` | 4 | Tabela 4.7 — decyduje o klikalności odnośnika (tylko zweryfikowany HTTPS) | metadane |
| `pog.act.metadata.references[]` | 4 | *pominięte:* lista technicznych odnośników rekordu CSW — pakiet audytowy (BK-505); raport pokazuje identyfikator i SHA-256 rekordu | metadane |
| `pog.act.metadata.*` | 4 | Tabela 4.7 — rekord metadanych CSW (ID, daty, SHA-256) | fakt źródłowy |
| `pog.act.formal_documents[].link_verified` | 4 | Tabela 4.8 — decyduje o klikalności odnośnika | metadane |
| `pog.act.formal_documents[].*` | 4 | Tabela 4.8 — dokumenty formalne aktu | fakt źródłowy |
| `pog.act.*_verified` | 4 | Tabela 4.7 — decyduje o klikalności odnośnika | metadane |
| `pog.act.*` | 4 | Tabela 4.7 — provenance aktu (ID, wersja, publikacja, wydanie, SHA-256) | fakt źródłowy |
| `pog.zones[].geometry_geojson` | 4 | Mapa 4 — strefy w wybranym trybie tematycznym | wynik obliczenia |
| `pog.zones[].area_sqm` | 4 | Tabela 4.2 — pole przecięcia [m²] | wynik obliczenia |
| `pog.zones[].area_pct` | 4 | Tabela 4.2 — udział w działce [%] | wynik obliczenia |
| `pog.zones[].source.*` | 9 | Tabela 4.2 — kolumna „źródło”; Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `pog.zones[].gml_url_verified` | 4 | Tabela 4.2 — decyduje o klikalności odnośnika GML | metadane |
| `pog.zones[].primary_profile[].*` | 4 | Tabela 4.3 — profil podstawowy (kod, etykieta, słownik) | fakt źródłowy |
| `pog.zones[].additional_profiles[].*` | 4 | Tabela 4.3 — profile dodatkowe | fakt źródłowy |
| `pog.zones[].max_*` | 4 | Tabela 4.3 — parametry z jednostkami (bez uśredniania stref) | fakt źródłowy |
| `pog.zones[].min_*` | 4 | Tabela 4.3 — parametry z jednostkami (bez uśredniania stref) | fakt źródłowy |
| `pog.zones[].*` | 4 | Tabela 4.2 — strefa (ID, symbol, rodzaj, etykieta, wersja obiektu, GML) | fakt źródłowy |
| `pog.topological_slivers[].*` | 4 | Nota pod tabelą 4.2 — liczba, pole i udział pominiętych artefaktów styku sąsiednich stref (AU-110); okruchy nie są wierszami tabeli | wynik obliczenia |
| `pog.dominant_zone_id` | 4 | Tabela 4.2 — oznaczenie „największy udział” (pole pomocnicze) | wynik obliczenia |
| `pog.ouz[].geometry_geojson` | 4 | Mapa 4 — OUZ (wzór i obrys przerywany) | wynik obliczenia |
| `pog.ouz[].area_*` | 4 | Tabela 4.4 — OUZ: pole [m²] i udział [%] | wynik obliczenia |
| `pog.ouz[].touches_boundary` | 4 | Tabela 4.4 — OUZ: styk granicy | wynik obliczenia |
| `pog.ouz[].source.*` | 9 | Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `pog.ouz[].*` | 4 | Tabela 4.4 — OUZ: ID, symbol, etykieta, wersja obiektu | fakt źródłowy |
| `pog.downtown_areas[].geometry_geojson` | 4 | Mapa 4 — OZS (wzór i obrys kropkowany) | wynik obliczenia |
| `pog.downtown_areas[].area_*` | 4 | Tabela 4.5 — OZS: pole [m²] i udział [%] | wynik obliczenia |
| `pog.downtown_areas[].touches_boundary` | 4 | Tabela 4.5 — OZS: styk granicy | wynik obliczenia |
| `pog.downtown_areas[].source.*` | 9 | Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `pog.downtown_areas[].*` | 4 | Tabela 4.5 — OZS: ID, symbol, etykieta, wersja obiektu | fakt źródłowy |
| `pog.social_infrastructure_standard_areas[].geometry_geojson` | 4 | Mapa 4 — OSDIS (wzór i obrys kreska-kropka) | wynik obliczenia |
| `pog.social_infrastructure_standard_areas[].area_*` | 4 | Tabela 4.6 — OSDIS: pole [m²] i udział [%] | wynik obliczenia |
| `pog.social_infrastructure_standard_areas[].touches_boundary` | 4 | Tabela 4.6 — OSDIS: styk granicy | wynik obliczenia |
| `pog.social_infrastructure_standard_areas[].source.*` | 9 | Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `pog.social_infrastructure_standard_areas[].*` | 4 | Tabela 4.6 — OSDIS: ID, symbol, etykieta, wersja obiektu | fakt źródłowy |
| `pog.in_ouz` | 4 | Tabela 4.4 — decyzja „działka w OUZ” wg jawnych progów | wynik obliczenia |
| `pog.ouz_intersection_*` | 4 | Tabela 4.4 — łączne pole [m²] i udział [%] OUZ | wynik obliczenia |
| `pog.touches_ouz_boundary` | 4 | Tabela 4.4 — styk z granicą OUZ | wynik obliczenia |
| `pog.in_downtown_area` | 4 | Tabela 4.5 — decyzja „działka w OZS” | wynik obliczenia |
| `pog.area_ratio` | 4 | Tabela 4.1 — udział strefy dominującej (pole zgodności v1) | wynik obliczenia |
| `pog.planning_zone` | 4 | Tabela 4.1 — strefa dominująca (pole zgodności v1) | fakt źródłowy |
| `pog.zone_type` | 4 | Tabela 4.1 — strefa dominująca (pole zgodności v1) | fakt źródłowy |
| `pog.manual_review_required` | 8 | Tabela 4.1 — plakietka weryfikacji; macierz 8.1 | metadane |
| `pog.compatibility_assessment.schema_version` | 9 | Tabela 9.3 — wersje kontraktów | metadane |
| `pog.compatibility_assessment.*` | 4 | Tabela 4.9 — relacja MPZP–POG: wynik, reguła, stan prawny, pary stref, źródła (analiza informacyjna) | wynik obliczenia |
| `pog.presentation_style.*` | 4 | Mapa 4 — legenda i wersja stylu POG (zamrożone) | metadane |
| `pog.source.*` | 9 | Tabela 4.1 — pewność; Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `pog.*` | 4 | Tabela 4.1 — status prawny, zakres danych, aktualność, uchwała | fakt źródłowy |
| `risks[].geometry_geojson` | 5 | Mapa 5 — obiekty ISOK/GDOŚ (zamrożone, przycięte do kadru) | fakt źródłowy |
| `risks[].intersection_*` | 5 | Tabele 5.1/5.2 — pole [m²] i udział [%] przecięcia | wynik obliczenia |
| `risks[].touches_boundary` | 5 | Tabele 5.1/5.2 — styk granicy | wynik obliczenia |
| `risks[].distance_m` | 5 | Tabele 5.1/5.2 — odległość od poligonu działki w EPSG:2180 | wynik obliczenia |
| `risks[].proximity_status` | 5 | Tabele 5.1/5.2 — przecięcie albo najbliższy obiekt w promieniu | wynik obliczenia |
| `risks[].description` | 5 | Tabele 5.1/5.2 — opis prezentacyjny (pochodny z pól strukturalnych) | metadane |
| `risks[].source.*` | 9 | Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `risks[].*` | 5 | Tabele 5.1/5.2 — klasa, okres powtarzalności, poziom, forma ochrony, nazwa, ID, ostrzeżenia | fakt źródłowy |
| `risk_sections[].schema_version` | 9 | Tabela 9.3 — wersje kontraktów | metadane |
| `risk_sections[].source.*` | 9 | Tabela 5.x — źródło sekcji; Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `risk_sections[].union_*` | 5 | Tabela 5.x — łączne pokrycie (suma mnogościowa) | wynik obliczenia |
| `risk_sections[].proximities[].*` | 5 | Tabela 5.x — wynik per klasa Q10/Q100/Q500 albo warstwa GDOŚ: status, odległość i promień | wynik obliczenia |
| `risk_sections[].nearest_distance_m` | 5 | Tabela 5.x — najmniejsza odległość od poligonu działki w sekcji | wynik obliczenia |
| `risk_sections[].preliminary_assessment.*` | 5 | Nota Tabeli 5.1 — stan kontraktu wstępnej oceny WORP; bez wyliczania przed potwierdzeniem | metadane |
| `risk_sections[].*` | 5 | Tabela 5.x — status sekcji, relacja, liczność obiektów, powód, ostrzeżenia; macierz 8.1 | wynik obliczenia |
| `terrain.elevation_method` | 6 | Tabela 6.1 — nadrzędny produkt wysokościowy WCS lub usługa zastępcza | metadane |
| `terrain.elevation_note` | 6 | Nota tabeli 6.1 — siatka, produkty i układy wysokości | metadane |
| `terrain.crosscheck.*` | 6 | Kontrola krzyżowa w snapshocie; ostrzeżenie NMT_CROSSCHECK_DIFF powyżej 0,5 m; jeden zakres w tabeli 6.1 | metadane |
| `terrain.schema_version` | 9 | Tabela 9.3 — wersje kontraktów | metadane |
| `terrain.relief.schema_version` | 9 | Tabela 9.3 — wersje kontraktów | metadane |
| `terrain.relief.profile.line_geojson` | 6 | *pominięte:* linia profilu opisana współrzędnymi początku i końca w EPSG:2180 (Tabela 6.4) | metadane |
| `terrain.source.*` | 9 | Tabela 6.1 — źródło; Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `terrain.relief.source.*` | 9 | Tabela 6.5 — źródło rastra; Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `terrain.relief.raster.*` | 6 | Tabela 6.5 — okno rastra WCS, NoData, środowisko | metadane |
| `terrain.relief.profile.*` | 6 | Tabela 6.4 i wykres — profil wysokościowy | wynik obliczenia |
| `terrain.relief.*` | 6 | Tabele 6.2–6.3 — spadek, klasy nachylenia, ekspozycja | wynik obliczenia |
| `terrain.*` | 6 | Tabela 6.1 — Hmin, Hmax, deniwelacja, siatka, status | wynik obliczenia |
| `road_context.schema_version` | 9 | Tabela 9.3 — wersje kontraktów | metadane |
| `road_context.nearest.road_geojson` | 7 | *pominięte:* oś jezdni jest w warstwie layers/road_context.geojson pakietu audytowego; PDF podaje odległość w tabeli 7.3 (mapy raportu nie mają warstwy dróg) | wynik obliczenia |
| `road_context.nearest.link_geojson` | 7 | *pominięte:* najkrótszy odcinek działka → oś jezdni jest w warstwie layers/road_context.geojson pakietu audytowego | wynik obliczenia |
| `road_context.nearest.distance_m` | 7 | Tabela 7.3 — odległość od poligonu działki do osi jezdni [m] (EPSG:2180) | wynik obliczenia |
| `road_context.nearest.relation` | 7 | Tabela 7.3 — relacja geometryczna (przecina, styka się, poza działką) | wynik obliczenia |
| `road_context.nearest.*` | 7 | Tabela 7.3 — kategoria, klasa, numer, ulica, nawierzchnia, położenie ze źródła | fakt źródłowy |
| `road_context.source.*` | 9 | Tabela 7.3 — przypis źródła; Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `road_context.release.*` | 7 | Tabela 7.3 — wydanie danych: powiat, stan danych, import, SHA-256 paczki | metadane |
| `road_context.interpretation_note` | 7 | Uwaga interpretacyjna pod nagłówkiem „Transport i dostęp do drogi” i w sekcji 10 | metadane |
| `road_context.*` | 7 | Tabela 7.3 — status, promień i metadane wyszukiwania, liczba osi w kontakcie z działką | wynik obliczenia |
| `land_cover.schema_version` | 9 | Tabela 9.3 — wersja kontraktu pokrycia terenu | metadane |
| `land_cover.features_geojson[]` | 5 | Geometrie w mapie 5 i layers/land_cover.geojson w ZIP. | wynik obliczenia |
| `land_cover.forest.geometry_geojson` | 5 | Las w mapie 5 i layers/forest_context.geojson w ZIP. | wynik obliczenia |
| `land_cover.forest.buffers[].geometry_geojson` | 5 | Orientacyjne bufory w mapie 5 i layers/forest_context.geojson. | przybliżenie |
| `land_cover.source.*` | 9 | Sekcja 5 — źródło BDOT10k i stan danych | fakt źródłowy |
| `land_cover.*` | 5 | Sekcja 5 — udziały na działce i w 200 m, las i bufory orientacyjne | wynik obliczenia |
| `neighbour_buildings.schema_version` | 9 | Tabela 9.3 — wersje kontraktów | metadane |
| `neighbour_buildings.buildings[].geometry_geojson` | 7 | *pominięte:* obrysy budynków są w warstwie layers/neighbour_buildings.geojson pakietu audytowego; PDF podaje tabelę 7.5 (mapy raportu nie mają warstwy budynków) | fakt źródłowy |
| `neighbour_buildings.buildings[].distance_m` | 7 | Tabela 7.5 — odległość od działki do obrysu [m] | wynik obliczenia |
| `neighbour_buildings.buildings[].relation` | 7 | Tabela 7.5 — położenie względem działki | wynik obliczenia |
| `neighbour_buildings.buildings[].overlap_with_parcel_sqm` | 7 | Tabela 7.5 — pole części obrysu na działce (pośrednio: położenie) | wynik obliczenia |
| `neighbour_buildings.buildings[].*` | 7 | Tabela 7.5 — funkcja, kondygnacje, pole obrysu, źródło geometrii i wersja ze źródła | fakt źródłowy |
| `neighbour_buildings.on_parcel.*` | 7 | Tabela 7.4 — budynki na działce i pokrycie działki | wynik obliczenia |
| `neighbour_buildings.buffers[].*` | 7 | Tabela 7.4 — liczba, pola, udział pierścienia, odległość i wielkość obrysów w buforach | wynik obliczenia |
| `neighbour_buildings.source.*` | 9 | Tabela 7.4 — przypis źródła; Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `neighbour_buildings.release.*` | 7 | Tabela 7.4 — wydanie danych: powiat, stan danych, SHA-256 paczki | metadane |
| `neighbour_buildings.interpretation_note` | 7 | Uwaga pod nagłówkiem „Zabudowa sąsiednia” i w sekcji 10 | metadane |
| `neighbour_buildings.*` | 7 | Tabele 7.4–7.5 — status, definicja pierścienia, bufory, pole działki i liczba kandydatów | wynik obliczenia |
| `utilities_preview.source.*` | 9 | Tabela 7.1 — źródło; Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `utilities_preview.*` | 7 | Tabela 7.1 — pokrycie powiatu danymi GESUT w KIUT (bez geometrii sieci) | fakt źródłowy |
| `infrastructure[].network_geometry_geojson` | 7 | Mapa 1 — przebieg sieci (warstwa przybliżenia, bez wniosków obliczeniowych) | przybliżenie |
| `infrastructure[].protection_zone_geojson` | 7 | Mapa 1 — techniczna strefa bufora (przybliżenie) | przybliżenie |
| `infrastructure[].source.*` | 9 | Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `infrastructure[].*` | 7 | Tabela 7.2 — bufor [m], pole [m²], podstawa i pewność reguły, wpływ na obszar zabudowy | przybliżenie |
| `section_quality.legend` | 8 | Tabela 8.2 — legenda statusów, świeżości i kodów powodów | metadane |
| `section_quality.sections[].*` | 8 | Tabela 8.1 — macierz sekcji: status, źródło, pobranie, Stan na (data lub jawny powód braku), wydanie, weryfikacja, świeżość, powody; tabela 8.3 — wiek na dzień eksportu | metadane |
| `section_quality.*` | 8 | Tabele 8.1 i 9.3 — wersja polityki, punkt odniesienia, pochodzenie i suma kontrolna macierzy | metadane |
| `completeness.sections[].*` | 1 | Strona 1 — tabela kompletności: sekcja, stan (sprawdzono/brak/poza zakresem), podstawa ustalenia MPZP, status źródła i znacznik weryfikacji | metadane |
| `completeness.notes[]` | 1 | Strona 1 — zastrzeżenia pod tabelą kompletności (MPZP bez lokalnego wektora, status prawny POG) | metadane |
| `completeness.headline` | 1 | Strona 1 — komunikat nagłówkowy kompletności | metadane |
| `completeness.*` | 1 | Strona 1 — liczba sprawdzonych sekcji, zakres, wersja reguły i pochodzenie kompletności; tabela 9.3 — wersja reguły | metadane |
| `headline` | 1 | Strona 1 — komunikat nagłówkowy kompletności (wiersz „Kompletność analizy”) | metadane |
| `warnings[].*` | 8 | Tabela 8.4 — ostrzeżenia analizy (kod, poziom, źródło, treść) | metadane |
| `sources[].*` | 9 | Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie) | fakt źródłowy |
| `access_token` | 9 | *pominięte:* sekret dostępu do API — nigdy nie jest umieszczany w dokumencie PDF | metadane |

Liczba ścieżek liści kontraktu objętych mapowaniem: 1019.
