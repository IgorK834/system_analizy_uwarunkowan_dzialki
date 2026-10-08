"""Adapter odpowiedzi WMS GetFeatureInfo KIMPZP (``plany_granice``) → ``KimpzpPointResult``.

Zbiorcza usługa KIMPZP skleja odpowiedzi usług gminnych separatorem ``<hr/>``.
Każdy segment ma własny format; zaobserwowane (fixtures
``tests/fixtures/source_contracts/kimpzp/``):

* blok planu (np. Góra Kalwaria): tabela najwyższego poziomu z nagłówkiem
  „Obowiązujące MPZP”/„Nieobowiązujące MPZP”, wiersze ``<td><b>Klucz</b></td><td>…</td>``
  i wiersze-linki („Tekst uchwały”, „Legenda”); po nim tabele zmian
  („Zmiany tekstowe”, „Zmiany”), które są zmianami aktu, a nie aktami;
* tabela atrybutów z wierszem nagłówków ``<th>`` (Kraków/Esri, GeoServer APP);
* pionowe pary ``<th>``/``<td>`` (Legnica, iGeoMap);
* warstwy QGIS Server: tabela „Layer” z zagnieżdżonymi tabelami obiektów (Pisz);
* komunikaty tekstowe: „brak serwisu dla wskazanego obszaru” (``no_coverage``),
  „<gmina>: brak wyniku dla wskazanego obszaru” (``no_match``);
* błędy usług gminnych z HTTP 200: ``<oms_error>``, ``ServiceExceptionReport``
  (``unavailable`` — nigdy „brak planu”).

Parser nie podnosi wyjątków dla żadnego wejścia tekstowego: nierozpoznany
segment daje status ``unknown`` z kodem ``KIMPZP_UNRECOGNIZED_RESPONSE``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from bs4 import BeautifulSoup, Tag

from app.modules.planning.domain.kimpzp_discovery import (
    KIMPZP_EMPTY_RESPONSE,
    KIMPZP_NO_RESULT_FOR_AREA,
    KIMPZP_NO_SERVICE_FOR_AREA,
    KIMPZP_PARTIAL_SERVICE_ERROR,
    KIMPZP_SERVICE_ERROR,
    KIMPZP_UNRECOGNIZED_RESPONSE,
    KimpzpAct,
    KimpzpAmendment,
    KimpzpPointResult,
    KimpzpSourceStatus,
    clean_value,
    combine_statuses,
    fold_text,
    informatization_from_text,
    legal_status_from_text,
    merge_acts,
    normalize_resolution_number,
    parse_source_date,
    resolution_number_from_text,
)

_SEGMENT_SEPARATOR = re.compile(r"<hr\s*/?>", re.IGNORECASE)
_PLAN_BLOCK_HEADER = re.compile(r"^(?:nie\s*)?obow\w*\b.*\b(?:mpzp|plan\w*)\b")
_ERROR_MARKERS: Final[tuple[str, ...]] = (
    "<oms_error",
    "serviceexceptionreport",
    "<serviceexception",
    "exceptionreport",
    "<ows:exception",
)
_NO_SERVICE = "brak serwisu dla wskazanego obszaru"
_NO_RESULT = "brak wyniku dla wskazanego obszaru"
_AMENDMENT_LABEL = re.compile(r"\bzmian\w*")
# Warstwy z informacjami dodatkowymi (strefy ochronne, złoża) odwołują się do
# numeru uchwały, ale nie są rekordem planu.
_NON_PLAN_LAYER = re.compile(r"dod_?info|info_?dod|dodatkow")

# Klucze (po ``fold_text`` i bez dwukropka) w kolejności pierwszeństwa.
_NUMBER_KEYS: Final[tuple[str, ...]] = (
    "nr uchwaly",
    "numer uchwaly",
    "numer_uchwaly",
    "uchwalenie",
    "uchwala nr",
    "plan_id",
    "id_planu",
    "uchwala",
    "numer",
)
_NUMBER_TEXT_KEYS: Final[tuple[str, ...]] = ("dokumentuchwalajacy",)
_RESOLUTION_DATE_KEYS: Final[tuple[str, ...]] = (
    "data uchwaly",
    "data uchwalenia",
    "data_uchwalenia",
    "data",
)
_VALID_FROM_KEYS: Final[tuple[str, ...]] = (
    "obowiazuje od",
    "data obowiazywania",
    "obowiazujeod",
    "wazny_od",
    "wazne_od",
)
_REPEALED_KEYS: Final[tuple[str, ...]] = (
    "utracil moc",
    "obowiazujedo",
    "wazny_do",
    "wazne_do",
)
_NAME_KEYS: Final[tuple[str, ...]] = ("nazwa", "nazwa planu", "nazwa mpzp", "tytul", "name")
_TEXT_URL_KEYS: Final[tuple[str, ...]] = (
    "tekst uchwaly",
    "tresc uchwaly",
    "uchwala_url",
    "url_uchwaly",
    "uchwala",
    "dokument",
)
_LEGEND_KEYS: Final[tuple[str, ...]] = ("legenda", "app_legenda")
_DRAWING_KEYS: Final[tuple[str, ...]] = ("rysunek planu", "rysunek", "rysunek_lacze", "raster")
_BIP_KEYS: Final[tuple[str, ...]] = ("strona bip", "bip")
_WWW_KEYS: Final[tuple[str, ...]] = ("www", "link")
_JOURNAL_KEYS: Final[tuple[str, ...]] = ("dziennik", "ogloszenie")
_STATUS_KEYS: Final[tuple[str, ...]] = ("status", "state")
_INFORMATIZATION_KEYS: Final[tuple[str, ...]] = ("poziom informatyzacji",)
_SYMBOL_KEYS: Final[tuple[str, ...]] = (
    "symbol",
    "zone_symbol",
    "symbol_strefy",
    "oznaczenie",
    "rodzaj oznaczenia",
)
_AMENDMENT_NOTE_KEYS: Final[tuple[str, ...]] = ("zmiany", "zmiana")
_LAYER_KEYS: Final[frozenset[str]] = frozenset({"layer", "feature"})

SourceFormat = Literal["plan_block", "attribute_table", "key_value", "json"]


@dataclass
class _Cell:
    text: str | None
    links: list[tuple[str, str]] = field(default_factory=list)  # (tekst linku, href)

    @property
    def url(self) -> str | None:
        for _, href in self.links:
            return href
        return self.text if _is_http_url(self.text) else None


@dataclass
class _Record:
    values: dict[str, _Cell]
    source_format: SourceFormat
    layer: str | None = None
    block_status: str | None = None
    amendments: list[KimpzpAmendment] = field(default_factory=list)


@dataclass
class _Segment:
    status: KimpzpSourceStatus
    reason: str | None = None
    acts: list[KimpzpAct] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    message: str | None = None


def parse_kimpzp_feature_info(text: str) -> KimpzpPointResult:
    """Parsuje pełną odpowiedź GetFeatureInfo (HTML albo GeoJSON-podobny JSON).

    Nie podnosi wyjątków dla żadnego wejścia tekstowego.
    """
    stripped = (text or "").strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return _parse_json(stripped)
    if not stripped:
        return KimpzpPointResult(status="unknown", reason_codes=(KIMPZP_EMPTY_RESPONSE,))
    segments = [
        _parse_segment(segment)
        for segment in _SEGMENT_SEPARATOR.split(stripped)
        if segment.strip()
    ]
    if not segments:
        return KimpzpPointResult(status="unknown", reason_codes=(KIMPZP_EMPTY_RESPONSE,))
    acts = merge_acts([act for segment in segments for act in segment.acts])
    assigned = {symbol for act in acts for symbol in act.zone_symbols}
    unassigned = [
        symbol
        for segment in segments
        for symbol in segment.symbols
        if symbol not in assigned
    ]
    status = combine_statuses([segment.status for segment in segments])
    # Powody statusu końcowego; „brak wyniku” jednej usługi przy znalezionym
    # akcie innej nie jest powodem, a błąd innej usługi — jest (wynik częściowy).
    reasons = [segment.reason for segment in segments if segment.status == status and segment.reason]
    if status == "available" and any(segment.status == "unavailable" for segment in segments):
        reasons.append(KIMPZP_PARTIAL_SERVICE_ERROR)
    return KimpzpPointResult(
        status=status,
        acts=acts,
        reason_codes=tuple(dict.fromkeys(reasons)),
        unassigned_zone_symbols=tuple(dict.fromkeys(unassigned)),
        vector_available=not any(act.informatization == "raster" for act in acts),
        messages=tuple(segment.message for segment in segments if segment.message),
    )


def _parse_segment(segment: str) -> _Segment:
    lowered = segment.casefold()
    if any(marker in lowered for marker in _ERROR_MARKERS):
        return _Segment(
            status="unavailable",
            reason=KIMPZP_SERVICE_ERROR,
            message=_short_text(segment),
        )
    soup = BeautifulSoup(segment, "html.parser")
    plain = fold_text(soup.get_text(" "))
    tables = soup.find_all("table")
    if not tables:
        if _NO_SERVICE in plain:
            return _Segment(status="no_coverage", reason=KIMPZP_NO_SERVICE_FOR_AREA)
        if _NO_RESULT in plain:
            return _Segment(status="no_match", reason=KIMPZP_NO_RESULT_FOR_AREA)
        if soup.find(["html", "body", "head"]) is not None:
            # Dokument HTML bez żadnej tabeli: usługa nie zwróciła obiektów.
            return _Segment(status="no_match", reason=KIMPZP_NO_RESULT_FOR_AREA)
        return _Segment(
            status="unknown",
            reason=KIMPZP_UNRECOGNIZED_RESPONSE,
            message=_short_text(segment),
        )

    records = _collect_records(tables)
    acts: list[KimpzpAct] = []
    symbols: list[str] = []
    saw_feature = False
    for record in records:
        if record.layer and _NON_PLAN_LAYER.search(fold_text(record.layer)):
            continue
        saw_feature = True
        act = _act_from_record(record)
        symbol = _first_text(record, _SYMBOL_KEYS)
        if act is not None:
            acts.append(act)
        elif symbol:
            symbols.append(symbol)
    if acts or symbols:
        return _Segment(status="available", acts=acts, symbols=symbols)
    if saw_feature:
        return _Segment(
            status="unknown",
            reason=KIMPZP_UNRECOGNIZED_RESPONSE,
            message=_short_text(soup.get_text(" ")),
        )
    return _Segment(status="no_match", reason=KIMPZP_NO_RESULT_FOR_AREA)


# --- rekordy z tabel -------------------------------------------------------------------


def _collect_records(tables: list[Tag]) -> list[_Record]:
    """Rekordy w kolejności dokumentu; tabele zmian przypisane do bloku planu.

    Tabela zagnieżdżona w bloku planu albo następująca po nim (do kolejnego
    bloku) jest tabelą zmian tego aktu. Tabela poprzedzona etykietą „Zmiany…”
    jest tabelą zmian także bez bloku — wtedy jest pomijana, nigdy nie staje
    się aktem.
    """
    records: list[_Record] = []
    blocks: dict[int, _Record] = {}
    current_block: _Record | None = None
    for table in tables:
        enclosing_block = _enclosing_plan_block(table)
        if _is_plan_block(table) and enclosing_block is None:
            current_block = _plan_block_record(table)
            blocks[id(table)] = current_block
            records.append(current_block)
            continue
        owner = blocks.get(id(enclosing_block)) if enclosing_block is not None else current_block
        if owner is not None:
            owner.amendments.extend(_amendments_from_table(table))
            continue
        if _preceded_by_amendment_label(table):
            continue
        records.extend(_generic_records(table))
    return records


def _is_plan_block(table: Tag) -> bool:
    rows = _own_rows(table)
    if not rows:
        return False
    cells = _own_cells(rows[0])
    texts = [text for text in (_cell_text(cell) for cell in cells) if text]
    if len(texts) != 1:
        return False
    return bool(_PLAN_BLOCK_HEADER.match(fold_text(texts[0])))


def _enclosing_plan_block(table: Tag) -> Tag | None:
    parent = table.find_parent("table")
    while parent is not None:
        if _is_plan_block(parent):
            return parent
        parent = parent.find_parent("table")
    return None


def _plan_block_record(table: Tag) -> _Record:
    rows = _own_rows(table)
    header = _cell_text(_own_cells(rows[0])[0]) if rows and _own_cells(rows[0]) else None
    return _Record(
        values=_key_value_pairs(rows[1:]), source_format="plan_block", block_status=header
    )


def _generic_records(table: Tag) -> list[_Record]:
    rows = _own_rows(table)
    if not rows:
        return []
    layer = _layer_name(table)
    first_cells = _own_cells(rows[0])
    if len(first_cells) >= 2 and all(cell.name == "th" for cell in first_cells):
        headers = [_key(_cell_text(cell)) for cell in first_cells]
        records = []
        for row in rows[1:]:
            cells = _own_cells(row)
            if not cells or all(cell.name == "th" for cell in cells):
                continue
            values = {
                header: _cell_value(cell)
                for header, cell in zip(headers, cells, strict=False)
                if header
            }
            if values:
                records.append(_Record(values=values, source_format="attribute_table", layer=layer))
        return records
    values = _key_value_pairs(rows)
    if not values or set(values) <= _LAYER_KEYS:
        return []
    return [_Record(values=values, source_format="key_value", layer=layer)]


def _key_value_pairs(rows: list[Tag]) -> dict[str, _Cell]:
    values: dict[str, _Cell] = {}
    for row in rows:
        cells = _own_cells(row)
        if len(cells) == 2 and _is_key_cell(cells[0]):
            key = _key(_cell_text(cells[0]))
            if key and key not in values:
                values[key] = _cell_value(cells[1])
            continue
        if len(cells) == 1:
            # Wiersz-link bloku planu: <a href=…>Tekst uchwały</a>.
            for label, href in _cell_value(cells[0]).links:
                key = _key(label)
                if key and key not in values:
                    values[key] = _Cell(text=href, links=[(label, href)])
    return values


def _is_key_cell(cell: Tag) -> bool:
    if cell.name == "th":
        return True
    return cell.find(["b", "strong"]) is not None and not _cell_value(cell).links


def _layer_name(table: Tag) -> str | None:
    caption = table.find("caption")
    if caption is not None and caption.find_parent("table") is table:
        return clean_value(caption.get_text(" "))
    parent = table.find_parent("table")
    while parent is not None:
        values = _key_value_pairs(_own_rows(parent))
        if "layer" in values and values["layer"].text:
            return values["layer"].text
        parent = parent.find_parent("table")
    return None


def _preceded_by_amendment_label(table: Tag) -> bool:
    label = table.find_previous(string=lambda value: bool(value and value.strip()))
    if label is None or label.find_parent("table") is not None:
        return False
    return bool(_AMENDMENT_LABEL.search(fold_text(str(label))))


def _amendments_from_table(table: Tag) -> list[KimpzpAmendment]:
    label = table.find_previous(string=lambda value: bool(value and value.strip()))
    kind: Literal["text_change", "change"] = (
        "text_change" if label is not None and "tekstow" in fold_text(str(label)) else "change"
    )
    amendments: list[KimpzpAmendment] = []
    for record in _generic_records(table):
        number_cell = _first_cell(record, ("uchwala", "nr uchwaly", "numer uchwaly", "uchwalenie"))
        amendments.append(
            KimpzpAmendment(
                kind=kind,
                resolution_number=normalize_resolution_number(
                    number_cell.text if number_cell else None
                ),
                name=_first_text(record, ("nazwa planu", "nazwa", "tytul")),
                adopted_on=parse_source_date(_first_text(record, _RESOLUTION_DATE_KEYS)),
                valid_from=parse_source_date(_first_text(record, _VALID_FROM_KEYS)),
                document_url=_safe_url(number_cell.url if number_cell else None),
                bip_url=_safe_url(_first_url(record, _BIP_KEYS)),
            )
        )
    return amendments


def _act_from_record(record: _Record) -> KimpzpAct | None:
    number = None
    for key in _NUMBER_KEYS:
        cell = record.values.get(key)
        if cell is not None:
            number = normalize_resolution_number(cell.text)
            if number:
                break
    if number is None:
        for key in _NUMBER_TEXT_KEYS:
            number = resolution_number_from_text(_text(record, key))
            if number:
                break
    text_url = _safe_url(_first_url(record, _TEXT_URL_KEYS))
    if number is None and not (record.source_format == "plan_block" and text_url):
        return None
    amendments = list(record.amendments)
    note = _first_text(record, _AMENDMENT_NOTE_KEYS)
    if note and ("uchwal" in fold_text(note) or any(char.isdigit() for char in note)):
        amendments.insert(0, KimpzpAmendment(kind="note", raw_text=note))
    symbol = _first_text(record, _SYMBOL_KEYS)
    status_text = record.block_status or _first_text(record, _STATUS_KEYS)
    return KimpzpAct(
        resolution_number=number,
        resolution_date=parse_source_date(_first_text(record, _RESOLUTION_DATE_KEYS)),
        name=_first_text(record, _NAME_KEYS),
        valid_from=parse_source_date(_first_text(record, _VALID_FROM_KEYS)),
        repealed_on=parse_source_date(_first_text(record, _REPEALED_KEYS)),
        legal_status=legal_status_from_text(status_text),
        text_url=text_url,
        legend_url=_safe_url(_first_url(record, _LEGEND_KEYS)),
        drawing_url=_safe_url(_first_url(record, _DRAWING_KEYS)),
        bip_url=_safe_url(_first_url(record, _BIP_KEYS)),
        www_url=_safe_url(_first_url(record, _WWW_KEYS)),
        journal=_first_text(record, _JOURNAL_KEYS),
        informatization=informatization_from_text(_first_text(record, _INFORMATIZATION_KEYS)),
        zone_symbols=(symbol,) if symbol else (),
        amendments=tuple(amendments),
        source_format=record.source_format,
    )


# --- JSON (GeoJSON-podobny kontrakt części usług i testów) ------------------------------


def _parse_json(text: str) -> KimpzpPointResult:
    try:
        data: Any = json.loads(text)
    except ValueError:
        return KimpzpPointResult(status="unknown", reason_codes=(KIMPZP_UNRECOGNIZED_RESPONSE,))
    if not isinstance(data, dict):
        return KimpzpPointResult(status="unknown", reason_codes=(KIMPZP_UNRECOGNIZED_RESPONSE,))
    vector_available = bool(data.get("vector_available", True))
    features = data.get("features")
    if not isinstance(features, list):
        features = []
    acts: list[KimpzpAct] = []
    symbols: list[str] = []
    for feature in features:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict):
            continue
        record = _Record(
            values={
                _key(str(key)): _Cell(text=clean_value(value) if isinstance(value, str) else None)
                for key, value in properties.items()
            },
            source_format="json",
        )
        act = _act_from_record(record)
        symbol = _first_text(record, _SYMBOL_KEYS)
        if act is not None:
            acts.append(act)
        elif symbol:
            symbols.append(symbol)
    if not acts and not symbols:
        return KimpzpPointResult(
            status="no_match",
            reason_codes=(KIMPZP_NO_RESULT_FOR_AREA,),
            vector_available=vector_available,
        )
    merged = merge_acts(acts)
    return KimpzpPointResult(
        status="available",
        acts=merged,
        unassigned_zone_symbols=tuple(dict.fromkeys(symbols)),
        vector_available=True,
    )


# --- pomocnicze ------------------------------------------------------------------------


def _own_rows(table: Tag) -> list[Tag]:
    return [row for row in table.find_all("tr") if row.find_parent("table") is table]


def _own_cells(row: Tag) -> list[Tag]:
    return [cell for cell in row.find_all(["td", "th"]) if cell.find_parent("tr") is row]


def _cell_text(cell: Tag) -> str | None:
    """Tekst komórki bez treści tabel zagnieżdżonych w tej komórce."""
    parts = [
        str(text)
        for text in cell.find_all(string=True)
        if text.find_parent(["td", "th"]) is cell and type(text).__name__ == "NavigableString"
    ]
    return clean_value(" ".join(parts))


def _cell_value(cell: Tag) -> _Cell:
    links = [
        (clean_value(anchor.get_text(" ")) or "", str(anchor.get("href")).strip())
        for anchor in cell.find_all("a")
        if anchor.find_parent(["td", "th"]) is cell
        and _is_http_url(str(anchor.get("href") or "").strip())
    ]
    return _Cell(text=_cell_text(cell), links=links)


def _key(text: str | None) -> str:
    return fold_text(text or "").rstrip(":").strip()


def _text(record: _Record, key: str) -> str | None:
    cell = record.values.get(key)
    return cell.text if cell is not None else None


def _first_cell(record: _Record, keys: tuple[str, ...]) -> _Cell | None:
    for key in keys:
        cell = record.values.get(key)
        if cell is not None and (cell.text or cell.links):
            return cell
    return None


def _first_text(record: _Record, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _text(record, key)
        if value:
            return value
    return None


def _first_url(record: _Record, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        cell = record.values.get(key)
        if cell is not None and cell.url:
            return cell.url
    return None


def _is_http_url(value: str | None) -> bool:
    return bool(value) and re.match(r"^https?://\S+$", value or "", re.IGNORECASE) is not None


def _safe_url(value: str | None) -> str | None:
    return value if _is_http_url(value) else None


def _short_text(value: str, limit: int = 200) -> str:
    """Krótki, czysty tekst komunikatu (bez znaczników i sekcji CDATA) do ostrzeżeń."""
    text = re.sub(r"<!\[CDATA\[|\]\]>|<[^>]*>", " ", value)
    return " ".join(text.split())[:limit]
