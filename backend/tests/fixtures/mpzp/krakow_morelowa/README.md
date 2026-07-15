# Kraków — MPZP obszaru „Morelowa”

- Dokument: uchwała Rady Miasta Krakowa w sprawie MPZP obszaru „Morelowa”.
- Źródło:
  <https://www.bip.krakow.pl/_inc/rada/posiedzenia/show_pdfdoc.php?id=121322>.
- Data pobrania: 15 lipca 2026 r. (`2026-07-15T18:15:11.209355+00:00`).
- Format: prawdziwy PDF z warstwą tekstową; fixture nie jest syntetyczny.

Ręczna kontrola § 17 ust. 4 pkt 1 dla `MN.1` potwierdza: 60% powierzchni
biologicznie czynnej, 30% powierzchni zabudowy, intensywność 0,01–0,5 oraz
wysokości 11 m i 9,5 m dla dachu płaskiego. Escapowanie kropki w symbolu
działa. Parser empirycznie zwraca prawdziwy konflikt wysokości 11/9,5 m i
wymaga ręcznej weryfikacji.

## Znane ograniczenia

Warianty językowe „minimalny wskaźnik terenu biologicznie czynnego”,
„maksymalny wskaźnik powierzchni zabudowy” i zwarty zakres intensywności nie
pasują do obecnych anchorów. Cztery parametry są dlatego jawnie oczekiwane
jako nieznalezione. Liczne wcześniejsze wystąpienia `MN.1` powodują też
wieloznaczność segmentacji.

Bieżąca wersja `pdfplumber` zwróciła pięć struktur tabelarycznych z układu
stron 1, 4 i 24, więc builder zgodnie z kontraktem zapisał `tables.json`. Nie
są to tabele z ustaleniami `MN.1`; fixture nie zastępuje tabelarycznego
przypadku Legnicy i nie pokrywa OCR.

Odtworzenie z katalogu `backend/`:

```bash
python -m scripts.build_mpzp_text_fixture \
  --url 'https://www.bip.krakow.pl/_inc/rada/posiedzenia/show_pdfdoc.php?id=121322' \
  --output-dir tests/fixtures/mpzp/krakow_morelowa \
  --note "Uchwała w sprawie MPZP obszaru Morelowa, strefa MN.1, Kraków"
```
