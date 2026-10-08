# Zamrożone odpowiedzi KIMPZP GetFeatureInfo (AU-004)

Rzeczywiste odpowiedzi zbiorczej usługi
`https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego`
(WMS 1.1.1, `GetFeatureInfo`, warstwa `plany_granice`, `info_format=text/html`, EPSG:2180, okno 1×1 m)
pobrane 2026-10-06 skryptem `backend/scripts/capture_kimpzp_fixtures.py`. Pliki są **nieprzetworzone**
(bajt w bajt); `manifest.json` zawiera pełny URL zapytania, czas pobrania, status HTTP, typ treści,
rozmiar i SHA-256 każdego pliku, a test `tests/test_kimpzp_feature_info.py` sprawdza zgodność SHA-256.

Dla działki odpytywany jest jej `representative_point` (ten punkt `discover_mpzp` zawsze uwzględnia
w próbce); dla Kalet i Inowrocławia — wskazany punkt EPSG:2180.

| Plik | Gmina | Format odpowiedzi | Oczekiwany wynik parsera |
|---|---|---|---|
| `gora_kalwaria_141801_4.0701.23_8.html` | Góra Kalwaria | dwa bloki „Obowązujące MPZP” (pisownia usługi), pary `<td><b>Klucz</b></td><td>…</td>`, wiersze-linki, tabela „Zmiany tekstowe” po drugim bloku | `available`; akty `IV/30/2024` (tekst `…/uch/IV_30_2024.pdf`) i `576/XLVII/2010`; `LIV/467/2021` i `XXXIX/366/2017` wyłącznie jako zmiany `576/XLVII/2010`; flaga `MPZP_MULTIPLE_ACTS_AT_POINT` |
| `krakow_126105_9.0001.580_4.html` | Kraków | trzy tabele atrybutów Esri (wiersz `<th>` + wiersze danych) z tym samym obiektem | `available`; jeden akt `XII/131/11`, symbol `KP.1`, link WWW (BIP) |
| `bielsko_biala_246101_1.0056.155_3.html` | Bielsko-Biała | `ServiceExceptionReport` (`LayerNotDefined`) z HTTP 200 | `unavailable` (`KIMPZP_SERVICE_ERROR`) — nie „brak planu” |
| `pisz_281603_4.0001.496_5.html` | Pisz | QGIS Server: tabele „Layer” z zagnieżdżonymi obiektami `mpzp_meta`, `dod_info_*`, `mpzp` | `available`; akt `XXI/232/20`, symbol `2KDZ`; `dod_info_pow` (odwołanie do uchwały) nie jest aktem; nazwy plików (`…_tekst__gmina_pisz.pdf`) nie są linkami |
| `legnica_026201_1.0009.1319_4.html` | Legnica | pionowe pary `<th>`/`<td>` (`numer`, `symbol`) | `available`; akt `XXVI/277/04`, symbol `22 KD G1/2(Z1/4)` |
| `warszawa_146510_8.0502.1_3.html` | Warszawa | `<oms_error>` z HTTP 200 | `unavailable` (`KIMPZP_SERVICE_ERROR`) — nie „brak planu” |
| `dygowo_321606_2.0029.362.html` | Dygowo | tekst „brak serwisu dla wskazanego obszaru” | `no_coverage` (`KIMPZP_NO_SERVICE_FOR_AREA`) |
| `ruciane_nida_281604_5.0011.107.html` | Ruciane-Nida | tekst „Ruciane-Nida: brak wyniku dla wskazanego obszaru” | `no_match` (`KIMPZP_NO_RESULT_FOR_AREA`) |
| `kalety_punkt_500000_300000.html` | Kalety | iGeoMap: dwa dokumenty z parami `<th>`/`<td>` (plan rastrowy) + segment „brak wyniku” drugiej usługi | `available`; akty `389/XLIII/2023` (obowiązuje od 2023-05-29) i `101/XII/2011`; `Numer planu` (`001`) i `Identyfikator` (UUID) nie są numerem uchwały; flaga wielu aktów |
| `inowroclaw_punkt_450000_550000.html` | Inowrocław | GeoServer APP: tabela atrybutów, numer uchwały tylko w opisie `dokumentuchwalajacy`, data `8/11/12, 12:00 AM` | `available`; akt `XXIII/322/2012`, obowiązuje od 2012-08-11 |

Obserwacje z pobrania (2026-10-06):

- Odpowiedź KIMPZP to sklejone odpowiedzi usług gminnych rozdzielone `<hr/>`; jedna usługa może zwrócić
  kilka dokumentów `<html>` (po jednym na obiekt), a segmenty różnych usług mogą mieć różne statusy
  (Kalety: dwa akty i „brak wyniku”).
- Warszawa i Bielsko-Biała zwracają błąd usługi gminnej z HTTP 200 dla wszystkich działek korpusu
  referencyjnego. Do AU-004 parser traktował je jak „nie znaleziono planu” (`status=no_mpzp` w
  `tests/fixtures/reference_corpus/artifacts/evidence/real-001…/real-012…`), czyli błąd źródła wyglądał
  jak brak planu.
- Komunikat „brak serwisu dla wskazanego obszaru” nie ma prefiksu gminy; „brak wyniku …” — ma.
- Odświeżenie: `python backend/scripts/capture_kimpzp_fixtures.py --force` (nadpisuje pliki i manifest);
  zmiana treści wymaga przeglądu oczekiwań w tej tabeli i w testach.
