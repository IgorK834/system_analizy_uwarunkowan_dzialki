# Korpus regresyjny parsera MPZP

Korpus chroni zachowanie parsera na zamrożonych, ręcznie zweryfikowanych
przykładach wielu formatów i gmin. Nie jest dowodem poprawności parsera dla
wszystkich gmin Polski ani źródłem aktualnego stanu prawnego.

| Gmina / dokument | Format | Źródło | Data pobrania | Syntetyczny |
|---|---|---|---|---|
| Bielsko-Biała, VIII/187/2024 | PDF tekstowy | <https://bip.um.bielsko.pl/api/files/322981> | 2026-07-14 | nie |
| Legnica, XIII/161/25 | PDF z natywną tabelą | <https://www.inforlex.pl/download/akty_pdf,U70,2025,65,1124.pdf> | 2026-07-15 | nie |
| Łódź, LXXVIII/2337/23, 6.8.MW/U | statyczny HTML | <https://mapa.mpu.lodz.pl/_wypisy/190/6.8.MW_U.html> | 2026-07-15 | nie |
| Kraków, „Morelowa” | PDF tekstowy | <https://www.bip.krakow.pl/_inc/rada/posiedzenia/show_pdfdoc.php?id=121322> | 2026-07-15 | nie |
| Pisz, XXV/254/98, Wiartel | realny PDF rastrowy | <https://bip.pisz.hi.pl/download.php?id=9857> | 2026-07-15 | nie |

`expected.json` ma jednolity rdzeń: `document_format`, symbole stref i listę
oczekiwań parametrów. Opcjonalne `structural_expectations` służy przypadkowi
OCR. Ponieważ ograniczone wyszukiwanie znalazło prawdziwy skan, format piątego
fixture'a to uczciwe `pdf_scan_real`, a nie przewidziane dla fallbacku
`pdf_scan_synthetic`.

## Odtwarzanie snapshotów

Polecenia należy uruchamiać z katalogu `backend/`. Builder używa produkcyjnego
fetchera i ekstraktora, zapisuje `pages.json`, `source.json` oraz opcjonalny
`tables.json`, gdy `pdfplumber` naprawdę zwróci tabele.

```bash
python -m scripts.build_mpzp_text_fixture \
  --url https://bip.um.bielsko.pl/api/files/322981 \
  --output-dir tests/fixtures/mpzp_documents/bielsko_biala_uchwala_viii_187_2024 \
  --note "Uchwała Nr VIII/187/2024, Bielsko-Biała"

python -m scripts.build_mpzp_text_fixture \
  --url https://www.inforlex.pl/download/akty_pdf,U70,2025,65,1124.pdf \
  --output-dir tests/fixtures/mpzp/legnica_szpital \
  --note "Uchwała Nr XIII/161/25, Legnica"

python -m scripts.build_mpzp_text_fixture \
  --url https://mapa.mpu.lodz.pl/_wypisy/190/6.8.MW_U.html \
  --output-dir tests/fixtures/mpzp/lodz_mw_u \
  --note "Uchwała Nr LXXVIII/2337/23, Łódź"

python -m scripts.build_mpzp_text_fixture \
  --url 'https://www.bip.krakow.pl/_inc/rada/posiedzenia/show_pdfdoc.php?id=121322' \
  --output-dir tests/fixtures/mpzp/krakow_morelowa \
  --note "MPZP Morelowa, Kraków"

python -m scripts.build_mpzp_text_fixture \
  --url 'https://bip.pisz.hi.pl/download.php?id=9857' \
  --output-dir tests/fixtures/mpzp/pisz_wiartel_scan \
  --note "Uchwała Nr XXV/254/98, Wiartel, realny skan bez warstwy tekstowej"
```

## Ważne ograniczenie prawne

Każdy `expected.json` opisuje dokładnie wersję dokumentu z daty pobrania.
Jeżeli gmina później zmieni uchwałę albo sposób publikacji, zamrożony tekst
nadal jest prawidłowym testem regresyjnym kodu, ale nie może być używany jako
źródło bieżącego prawa. Znane luki ekstraktorów są opisane w README każdego
fixture'a i celowo nie są ukrywane przez ręczne poprawianie snapshotów.
