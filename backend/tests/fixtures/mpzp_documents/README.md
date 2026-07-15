# Fixture dokumentów MPZP

Katalog zawiera statyczny tekst wyekstrahowany z prawdziwych, publicznych
aktów prawa miejscowego. Testy `pytest` czytają wyłącznie zapisane pliki JSON
i nigdy nie wykonują żądań HTTP.

Każdy fixture ma `pages.json` oraz `source.json`. Opcjonalny `tables.json`
jest tworzony wyłącznie wtedy, gdy produkcyjna ekstrakcja `pdfplumber`
znajdzie natywną tabelę; zawiera listę obiektów z numerem strony i wierszami.

## Bielsko-Biała — Uchwała Nr VIII/187/2024

- Gmina: Bielsko-Biała.
- Dokument: Uchwała Nr VIII/187/2024 Rady Miejskiej w Bielsku-Białej z dnia
  19 grudnia 2024 r.
- Zakres planu: rejon ulic Józefa Lompy, Cieszyńskiej i gen. Władysława
  Sikorskiego.
- Źródło BIP: <https://bip.um.bielsko.pl/api/files/322981>.
- Data pobrania fixture: 14 lipca 2026 r. Szczegółowy czas UTC i parametry
  ekstrakcji znajdują się w `source.json`.
- Status prawny danych: prawdziwy, jawny publicznie akt prawa miejscowego,
  udostępniony przez urząd miasta zgodnie z ustawą o dostępie do informacji
  publicznej.

Empiryczna ekstrakcja `pdfplumber` zwróciła 13 stron i `quality_score=1.0`.
Sekcja `230_U` zaczyna się na stronie 5 i jest kontynuowana na stronie 6,
`230_UMW` rozpoczyna się na stronie 6, a `230_ZP` na stronie 7. Te numery
pochodzą bezpośrednio z `pages.json`, a nie z przewidywanego układu PDF.

Fixture można odtworzyć z katalogu `backend/`:

```bash
python -m scripts.build_mpzp_text_fixture \
  --url https://bip.um.bielsko.pl/api/files/322981 \
  --output-dir tests/fixtures/mpzp_documents/bielsko_biala_uchwala_viii_187_2024 \
  --note "Uchwała Nr VIII/187/2024, MPZP Józefa Lompy/Cieszyńska/Sikorski, Bielsko-Biała"
```

Skrypt korzysta z produkcyjnego, zabezpieczonego fetchera oraz produkcyjnej
ekstrakcji `pdfplumber`, włącznie z tabelami zapisywanymi do opcjonalnego
`tables.json`. Jest reużywalny dla innych gmin i regionów: wystarczy zmienić
`--url`, `--output-dir` oraz opcjonalny `--note`.
