# Plan doboru źródeł dla nowego zbioru końcowego (PV3-02) — do zatwierdzenia przez właściciela

Stan: **nic nie zostało pobrane.** Dokument jest listą do zatwierdzenia przed pobraniem
(wymóg Task 20.2) i planem przesiewu. Nie zawiera ocen układu żadnego dokumentu, bo bez pobrania
nie da się ich uczciwie wydać.

## 1. Czego potrzebuje zbiór

Minimum z `docs/evaluation/mpzp_annotation_protocol.md`, pkt 2: ≥ 20 próbek, ≥ 10 gmin
niewykorzystanych wcześniej (wykluczone: Kraków, Legnica, Łódź, Bielsko-Biała, Stare Miasto,
Warszawa, Białystok, Szczytno, Ostrów Wielkopolski, Raszków, Krzemieniewo, Mogilany, Pisz),
≥ 4 województwa, ≥ 5 tabel parametrów w PDF, 4 prawdziwe skany, 4 dokumenty HTML lub wypisy oraz
każda ze strategii zakresu 1–6.

Dwie luki z BK-603 wyznaczają priorytet przesiewu: w pobranych dokumentach końcowych **nie
znaleziono tabeli parametrów** (jedyna tabela to Legnica, rozwojowa), a prawdziwych skanów były
dwa. Są to formaty najrzadsze, więc przesiew zaczyna się od nich.

## 2. Kwoty przesiewu (kandydatów więcej niż potrzeba)

Przesiew zakłada, że część dokumentów odpadnie (układ okaże się tekstowy, projekt zamiast aktu,
błąd TLS). Proponowane ilości do **pobrania przesiewowego**:

| Stratum | Potrzeba w zbiorze | Do przesiewu |
|---|---:|---:|
| tabela parametrów w PDF | 5 | 10 |
| prawdziwy skan (PDF bez warstwy tekstowej) | 4 | 8 |
| HTML / wypis | 4 | 6 |
| tekst PDF, strategie 1–6 (reszta próbek) | 7 | 12 |
| razem | 20 | 36 |

Z jednego dokumentu można wziąć kilka próbek (różne strefy, różne układy), więc 20 próbek nie
wymaga 20 dokumentów, ale **10 nowych gmin** wymaga dokumentów z co najmniej 10 różnych gmin.
Przesiew należy rozłożyć na co najmniej 6 województw, żeby po odpadnięciu zostało ≥ 4.

## 3. Ziarna kandydatów (nieweryfikowane)

Poniższe adresy zwróciło wyszukiwanie 2026-10-01; **nie otwierano ich ani nie pobierano**, nie
wiadomo, czy są aktami (a nie projektami), jaki mają układ i czy mają warstwę tekstową. Służą jako
punkt startu do przesiewu, nie jako lista zatwierdzona.

| Gmina (województwo) | Źródło (z wyszukiwania) | Uwaga |
|---|---|---|
| Będzino (zachodniopomorskie) | https://bip.bedzino.pl/userfiles/file/uchwaly/2010/2010_357_XXXVIII.pdf | uchwała XXXVIII/357/10, 2010 |
| Swarzędz (wielkopolskie) | https://bip.swarzedz.pl/fileadmin/BIP/Zagospodarowanie_przestrzenne/Plany/2023/211_-_Keplera_Zalasewo/694-2023_2114353.pdf | uchwała LXVI/694/2023 |
| Gdańsk (pomorskie) | https://baw.bip.gdansk.pl/UrzadMiejskiwGdansku/document/548514/Uchwa%C5%82a-V_24_15 | uchwała V/24/15 |
| Gdańsk (pomorskie) | https://baw.bip.gdansk.pl/UrzadMiejskiwGdansku/document/551553/Uchwa%C5%82a-LII_1282_22 | uchwała LII/1282/22 |
| Sopot (pomorskie) | https://bip.sopot.pl/m,74,plany-zagospodarowania-przestrzennego-obowiazujace.html | wykaz planów, nie akt |
| Piaseczno (mazowieckie) | https://bip.piaseczno.eu/uchwaly/429 | rejestr planów, nie akt |

Wykluczone z wyszukiwania: dokumenty oznaczone jako **projekt** uchwały (Zatory, Poddębice) —
projekt bez uchwalenia nie jest aktem prawa miejscowego i nie wchodzi do zbioru.

Brakujące ziarna (do uzupełnienia w kolejnej iteracji wyszukiwania, najlepiej przez właściciela,
który zna gminy użytkowników): gminy z województw dolnośląskiego, lubelskiego, podkarpackiego,
kujawsko-pomorskiego, opolskiego, świętokrzyskiego i lubuskiego; tabele (plany o „kartach terenu”
lub tabelarycznych ustaleniach wskaźników); skany (plany sprzed ok. 2005 r., zwykle w BIP jako
obrazy); wypisy i uchwały w HTML (serwisy planistyczne miast).

## 4. Procedura przesiewu po zatwierdzeniu

1. Pobranie wyłącznie dokumentów z zatwierdzonej listy, produkcyjnym fetcherem (limit 50 MB,
   `follow_redirects=False`, weryfikacja TLS włączona), z zapisem URL, rozmiaru i SHA-256.
2. Klasyfikacja: akt czy projekt, warstwa tekstowa czy skan, tabela parametrów czy tekst, strategia
   zakresu strefy 1–6, liczba stref. Wynik trafia do tabeli przesiewu w README korpusu — **bez uruchamiania
   żadnego silnika**.
3. Wybór próbek do zbioru wg kwot; dokumenty odrzucone zapisuje się z powodem w README korpusu.
4. Migawki (`build_mpzp_text_fixture.py`), potem anotacja przez ludzi wg protokołu.

## 5. Zatwierdzenie

- [ ] Właściciel zatwierdza kwoty przesiewu (pkt 2) i limit pobrań (liczba dokumentów, łączny rozmiar).
- [ ] Właściciel zatwierdza listę adresów do przesiewu (pkt 3 po uzupełnieniu).
- [ ] Właściciel wskazuje pierwszego i drugiego anotatora (osoby) oraz osobę rozstrzygającą.
- [ ] Właściciel potwierdza podstawę prawną wykorzystania tekstów (protokół, pkt 9).

Dopóki te punkty nie są odhaczone, żaden dokument nie jest pobierany.
