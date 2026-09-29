"""Backendowy adapter wspólnego artefaktu prezentacji POG (BK-403).

``shared/pog-presentation.json`` jest jedynym źródłem kodów stref, etykiet,
kolejności, palety, progów tematów, jednostek oraz stylu „brak wartości”.
Frontend czyta ten sam plik (``lib/pogZones.ts``, ``lib/pogThemes.ts``), a obraz
Docker backendu kopiuje go z kontekstu ``shared`` — obie aplikacje budują się z
jednej wersji. Wersja i SHA-256 trafiają do snapshotu analizy, dzięki czemu
raport starej analizy rysuje się zapisanym stylem, nawet po zmianie palety.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import math
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.settings import settings

PRESENTATION_FILENAME = "pog-presentation.json"
PRESENTATION_SCHEMA = "pog-presentation/1"
_HEX_COLOR = re.compile(r"^#[0-9a-f]{6}$")


class PogPresentationError(RuntimeError):
    """Artefakt prezentacji nie istnieje albo narusza kontrakt."""


def _hex(value: str) -> str:
    if not _HEX_COLOR.match(value):
        raise ValueError(f"Kolor musi mieć postać #rrggbb (małe litery): {value!r}.")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ZoneDictionary(_Strict):
    codelist: str
    url: str
    legal_basis: str
    verified_at: str
    source_last_modified: str
    sha256: str
    fixture: str


class ZoneStyle(_Strict):
    code: str
    codelist_id: str
    label: str
    order: int
    fill: str
    outline: str

    _colors = field_validator("fill", "outline")(_hex)


class PatternStyle(_Strict):
    code: str | None = None
    label: str
    fill: str
    outline: str
    pattern: str
    description: str

    _colors = field_validator("fill", "outline")(_hex)


class ThemeClass(_Strict):
    min: float
    max: float | None
    color: str
    label: str

    _color = field_validator("color")(_hex)


class Theme(_Strict):
    id: Literal["zones", "intensity", "building_coverage", "height", "biologically_active"]
    label: str
    kind: Literal["categorical", "numeric"]
    property: str
    unit: str | None
    unit_label: str
    direction: Literal["categorical", "ascending", "descending"]
    scale_description: str
    interval_closure: Literal["left"] | None = None
    classes: tuple[ThemeClass, ...] = ()

    @model_validator(mode="after")
    def _contiguous_classes(self) -> Theme:
        if self.kind == "categorical":
            if self.classes:
                raise ValueError("Temat kategorialny nie ma klas liczbowych.")
            return self
        if self.interval_closure != "left" or not self.classes:
            raise ValueError(f"Temat {self.id} wymaga klas z domknięciem 'left'.")
        if self.classes[0].min != 0:
            raise ValueError(f"Pierwsza klasa tematu {self.id} musi zaczynać się od 0.")
        for previous, current in zip(self.classes, self.classes[1:]):
            if previous.max is None or previous.max != current.min:
                raise ValueError(f"Klasy tematu {self.id} muszą być ciągłe.")
        if self.classes[-1].max is not None:
            raise ValueError(f"Ostatnia klasa tematu {self.id} musi być otwarta.")
        return self

    # Pole ``property`` tej klasy przesłania wbudowany dekorator w ciele klasy.
    @builtins.property
    def breaks(self) -> tuple[float, ...]:
        """Progi rozpoczynające klasy 2..n (wejście ``step`` MapLibre)."""
        return tuple(item.min for item in self.classes[1:])

    def class_for(self, value: float | None) -> ThemeClass | None:
        """Klasa wartości; ``None`` (brak wartości) nie należy do żadnej klasy."""
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        chosen = self.classes[0]
        for item in self.classes[1:]:
            if value >= item.min:
                chosen = item
        return chosen


class OverlayStyle(_Strict):
    id: Literal["ouz", "downtown", "social_infrastructure_standard", "act_boundary"]
    short_label: str
    label: str
    outline: str
    line_width: float
    line_dasharray: tuple[float, ...] | None
    pattern: str | None
    description: str

    _color = field_validator("outline")(_hex)


class LegalStatusStyle(_Strict):
    status: Literal["binding", "project", "in_progress", "superseded", "unknown"]
    label: str
    fill_opacity: float = Field(ge=0.0, le=1.0)
    line_dasharray: tuple[float, ...] | None
    description: str


class PogPresentation(_Strict):
    schema_: str = Field(alias="schema")
    style_version: str
    description: str
    zone_dictionary: ZoneDictionary
    zones: tuple[ZoneStyle, ...]
    unknown_zone: PatternStyle
    null_style: PatternStyle
    themes: tuple[Theme, ...]
    overlays: tuple[OverlayStyle, ...]
    legal_statuses: tuple[LegalStatusStyle, ...]

    @model_validator(mode="after")
    def _consistent(self) -> PogPresentation:
        if self.schema_ != PRESENTATION_SCHEMA:
            raise ValueError(f"Nieobsługiwany schemat {self.schema_!r}.")
        codes = [zone.code for zone in self.zones]
        if len(set(codes)) != len(codes):
            raise ValueError("Kody stref muszą być unikalne.")
        if sorted(zone.order for zone in self.zones) != list(range(1, len(codes) + 1)):
            raise ValueError("Kolejność stref musi być permutacją 1..n.")
        if len({theme.id for theme in self.themes}) != 5:
            raise ValueError("Wymagane jest pięć tematów POG.")
        return self

    def zone(self, code: str | None) -> ZoneStyle | PatternStyle:
        for zone in self.zones:
            if zone.code == code:
                return zone
        return self.unknown_zone

    def theme(self, theme_id: str) -> Theme:
        for theme in self.themes:
            if theme.id == theme_id:
                return theme
        raise KeyError(theme_id)

    def overlay(self, overlay_id: str) -> OverlayStyle:
        for overlay in self.overlays:
            if overlay.id == overlay_id:
                return overlay
        raise KeyError(overlay_id)


class LoadedPogPresentation(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    presentation: PogPresentation
    sha256: str
    path: str

    @property
    def style_version(self) -> str:
        return self.presentation.style_version


def presentation_path_candidates() -> list[Path]:
    configured = settings.pog_presentation_path or os.environ.get("POG_PRESENTATION_PATH")
    if configured:
        return [Path(configured)]
    here = Path(__file__).resolve()
    # Obraz Docker: /app/app/core → /app/shared; repozytorium: backend/app/core → shared.
    return [here.parents[2] / "shared" / PRESENTATION_FILENAME,
            here.parents[3] / "shared" / PRESENTATION_FILENAME]


def parse_pog_presentation(content: bytes, *, path: str = "<memory>") -> LoadedPogPresentation:
    try:
        data: Any = json.loads(content.decode("utf-8"))
        presentation = PogPresentation.model_validate(data)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PogPresentationError(f"Niepoprawny artefakt prezentacji POG: {exc}") from exc
    return LoadedPogPresentation(
        presentation=presentation,
        sha256=hashlib.sha256(content).hexdigest(),
        path=path,
    )


@lru_cache(maxsize=1)
def load_pog_presentation() -> LoadedPogPresentation:
    for candidate in presentation_path_candidates():
        if candidate.is_file():
            return parse_pog_presentation(candidate.read_bytes(), path=str(candidate))
    searched = ", ".join(str(path) for path in presentation_path_candidates())
    raise PogPresentationError(f"Nie znaleziono {PRESENTATION_FILENAME} (sprawdzono: {searched}).")


def style_snapshot(loaded: LoadedPogPresentation | None = None) -> dict[str, Any]:
    """Zamrożony podzbiór stylu zapisywany w snapshocie analizy POG.

    Snapshot zawiera wszystko, czego raport potrzebuje do narysowania mapy i
    legendy (strefy, nierozpoznany kod, brak wartości, wzory nakładek), więc
    stary raport nie zależy od bieżącej wersji palety.
    """
    active = loaded or load_pog_presentation()
    presentation = active.presentation
    return {
        "style_version": presentation.style_version,
        "style_sha256": active.sha256,
        "zones": {
            zone.code: {"label": zone.label, "fill": zone.fill, "outline": zone.outline}
            for zone in sorted(presentation.zones, key=lambda item: item.order)
        },
        "unknown_zone": presentation.unknown_zone.model_dump(mode="json"),
        "null_style": presentation.null_style.model_dump(mode="json"),
        "overlays": {
            overlay.id: overlay.model_dump(mode="json", exclude={"id"})
            for overlay in presentation.overlays
        },
    }
