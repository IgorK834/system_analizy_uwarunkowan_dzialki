# Pisz — realny skan MPZP części wsi Wiartel

- Dokument: Uchwała Nr XXV/254/98 Rady Miejskiej w Piszu z 6 marca 1998 r.
- Strona BIP: <https://bip.pisz.hi.pl/index.php?wiad=10578>.
- Plik źródłowy: <https://bip.pisz.hi.pl/download.php?id=9857>.
- Data pobrania: 15 lipca 2026 r. (`2026-07-15T18:16:11.056483+00:00`).
- Format: prawdziwy, czterostronicowy PDF rastrowy bez warstwy tekstowej;
  fixture nie jest syntetyczny.

Kandydat został znaleziony po trzech ograniczonych zapytaniach wyszukiwania,
bez ponawiania czterech wcześniej odrzuconych dokumentów. Produkcyjny
`pdfplumber` zwraca pusty tekst dla każdej z czterech stron,
`quality_score=0.0` i `needs_ocr=true`.

Fixture testuje wyłącznie graceful degradation: wynik `partial`, wymaganą
ręczną weryfikację, brak sfabrykowanych parametrów i ostrzeżenie zawierające
„OCR”. Nie testuje treści ani wartości planistycznych Pisza, ponieważ bez OCR
nie ma tekstowego dowodu dla takich asercji.

Znane ograniczenie: fasada parsera mapuje ostrzeżenie OCR na ogólny kod
`TEXT_EXTRACTION_WARNING`; informacja „OCR” pozostaje w komunikacie. Corpus
utrwala rzeczywiste zachowanie zamiast udawać bardziej szczegółowy kod.

Odtworzenie z katalogu `backend/`:

```bash
python -m scripts.build_mpzp_text_fixture \
  --url 'https://bip.pisz.hi.pl/download.php?id=9857' \
  --output-dir tests/fixtures/mpzp/pisz_wiartel_scan \
  --note "Uchwała Nr XXV/254/98, MPZP części wsi Wiartel, Pisz; realny skan rastrowy bez warstwy tekstowej"
```
