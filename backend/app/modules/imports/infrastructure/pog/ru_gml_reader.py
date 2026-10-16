"""Czytnik pliku GML aktu POG pobranego z Rejestru Urbanistycznego (ADR-026).

Plik zawiera jeden akt (``AktPlanowaniaPrzestrzennego``), jego strefy, obszary OUZ/OZS/OSDIS i dokumenty formalne, w układzie
PL-2000 strefy gminy (EPSG:2176–2179; ``posList`` w kolejności północ,wschód). Czytnik:

* odrzuca plik, który nie jest dokładnie jednym aktem tej gminy (TERC z wykazu musi zgadzać się z przestrzenią nazw aktu);
* parsuje bezpiecznie (``parse_xml_root`` odrzuca DTD i encje, limit węzłów) i przelicza geometrię do EPSG:2180;
* opcjonalnie dołącza rekordy CSW (provenance ADR-003); awaria CSW daje ostrzeżenie, nie blokuje importu;
* jako artefakt źródłowy zachowuje **bajty pliku GML bez zmian** (SHA-256 artefaktu = SHA-256 pobranego pliku).

Nie zna HTTP aktu ani bazy — dostaje gotowe bajty (``RuDownload``) i token wersji z wykazu.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from app.modules.imports.application.pog_import import PogSourceBatch
from app.modules.imports.domain.pog import PogActRecord, PogValidationError
from app.modules.imports.infrastructure.pog.csw_metadata import attach_metadata, fetch_act_metadata
from app.modules.imports.infrastructure.pog.reader import (
    assemble_ru_pog_acts,
    parse_ru_app_feature_collection,
)

PARSER_CONFIG_ID = "ru-app-3.0-gml-v1"
PARSER_CONFIG_ID_WITH_CSW = "ru-app-3.0-gml-csw-v1"


class RuGmlError(PogValidationError):
    """Plik GML aktu nie spełnia kontraktu importu; ``code`` jest stałym kodem przyczyny."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class RuGmlSource:
    """Wejście czytnika: bajty pliku i to, co o nim wiemy z wykazu RU."""

    content: bytes
    filename: str
    published_act_id: int
    terc: str
    source_reference: str | None
    gml_url: str | None = None


class RuGmlPogReader:
    def __init__(
        self,
        source: RuGmlSource,
        *,
        csw_url: str | None = None,
        csw_client: Any = None,
        csw_gate: Any = None,
        max_nodes: int = 8_000_000,
    ) -> None:
        self._source = source
        self._csw_url = csw_url
        self._csw_client = csw_client
        self._csw_gate = csw_gate
        self._max_nodes = max_nodes

    def read(self) -> PogSourceBatch:
        source = self._source
        objects = parse_ru_app_feature_collection(
            source.content,
            source_reference=source.source_reference,
            reproject=True,
            max_nodes=self._max_nodes,
        )
        if len(objects.acts) != 1:
            code = "gml_no_act" if not objects.acts else "gml_multiple_acts"
            raise RuGmlError(code, f"plik {source.filename} zawiera {len(objects.acts)} aktów (oczekiwano 1).")
        acts = assemble_ru_pog_acts(objects)
        act = acts[0]
        if act.teryt[:6] != source.terc[:6]:
            raise RuGmlError(
                "gml_terc_mismatch",
                f"TERC z wykazu RU ({source.terc}) różni się od przestrzeni nazw aktu ({act.teryt}).",
            )
        orphan_features = len(objects.features) - len(act.features)
        warnings: list[str] = []
        if orphan_features:
            warnings.append(f"gml_orphan_features:{act.act_identifier}:{orphan_features}")
        parser_config = PARSER_CONFIG_ID
        if self._csw_url and self._csw_client is not None:
            parser_config = PARSER_CONFIG_ID_WITH_CSW
            act, csw_warnings = self._with_catalog_metadata(act)
            warnings.extend(csw_warnings)
        return PogSourceBatch(
            content=source.content,
            filename=source.filename,
            media_type="application/gml+xml",
            acts=(act,),
            complete=True,
            strict_identifiers=True,
            parser_config_id=parser_config,
            warnings=tuple(warnings),
            source_tokens={act.act_identifier: source.published_act_id},
        )

    def _with_catalog_metadata(self, act: PogActRecord) -> tuple[PogActRecord, list[str]]:
        assert self._csw_url is not None
        warnings: list[str] = []
        gate = self._csw_gate
        try:
            if gate is not None:
                gate.wait()
            result = fetch_act_metadata(self._csw_client, self._csw_url, act.teryt)
        except Exception as exc:  # noqa: BLE001 - awaria CSW nie blokuje importu (ADR-003 pkt 4)
            return act, [f"csw_unavailable:{act.teryt}:{type(exc).__name__}"]
        if result.error:
            warnings.append(f"{result.error}:{act.teryt}")
        linked, link_warnings = attach_metadata((act,), result.records)
        warnings.extend(link_warnings)
        return replace(linked[0]), warnings
