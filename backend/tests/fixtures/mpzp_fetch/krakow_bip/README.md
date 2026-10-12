# Zamrożone strony BIP Krakowa (AU-102, śledzenie stron BIP)

`landing_kleparz.html` i `card_kleparz_text.html` to **wszystkie linki `<a href>`** realnych, publicznych stron
Biuletynu Informacji Publicznej Miasta Krakowa (adresy i skróty SHA-256 pełnych stron w `provenance.json`),
pobrane 2026-10-10 produkcyjnym fetcherem (weryfikacja TLS włączona). Reszta szablonu strony (menu graficzne,
skrypty, stopka) została pominięta; linki, ich teksty i atrybuty są dosłowne, więc test sprawdza wybór linku
na prawdziwym układzie strony (82 i 48 linków), a nie na zmyślonym przykładzie.

- `landing_kleparz.html` — strona planu „Kleparz” (`?dok_id=137657`): lista załączników jako kart (tekst, rysunek, GML,
  prognoza, ekofizjografia).
- `card_kleparz_text.html` — karta załącznika „Tekst planu” (`…/n/298025/karta`) z jednym linkiem do pliku
  (`/plik.php?zid=298025&wer=0&new=t&mode=shw`).

Odtworzenie: `python -m scripts.build_mpzp_live_corpus` pobiera dokumenty korpusu `mpzp_live`; sam adres strony
dokumentu (`landing_url` w `manifest.json`) prowadzi teraz do tego samego pliku co bezpośredni `source_url`
(skrót SHA-256 pliku zgodny — sprawdzone na żywo 2026-10-10 dla trzech planów krakowskich).
