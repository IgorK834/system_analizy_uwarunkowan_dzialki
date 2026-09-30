"""Składanie pakietu audytowego zapisanej analizy (BK-505, ADR-011).

Przypadek użycia przyjmuje gotowe, JSON-owe dane analizy (adapter w
``app.services.audit_package`` odczytuje je z zapisanego snapshotu — bez ponownego
odpytywania źródeł) i zwraca treść pakietu: ``analysis.json``, ``sources.json``,
``parcel.geojson``, dozwolone warstwy pochodne, ``README.md`` oraz
``manifest.json``.

Reguła redystrybucji jest w tym miejscu jedyną bramką dołączania treści:

- warstwa pochodna trafia do pakietu, gdy **każde** jej źródło ma w katalogu
  ``allowed`` albo ``derived_only``;
- surowe dane źródła (np. atrybuty rekordu) — tylko przy ``allowed``;
- ``forbidden`` i ``unconfirmed`` (także źródło spoza katalogu) oznaczają
  pominięcie: w pakiecie zostaje wyłącznie referencja, SHA-256 i powód.

Nic w tym module nie zależy od czasu eksportu ani od środowiska: te same dane
wejściowe dają te same bajty każdego pliku.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Final, Literal

from app.modules.reporting.domain.audit_package import (
    AUDIT_EXPORTER_VERSION,
    AUDIT_PACKAGE_SCHEMA_VERSION,
    MANIFEST_NAME,
    README_NAME,
    REASON_RAW_NOT_ALLOWED,
    REASON_REDISTRIBUTION_FORBIDDEN,
    REASON_REDISTRIBUTION_UNCONFIRMED,
    AuditLimits,
    AuditPackage,
    OmittedArtifact,
    PackageFile,
    build_manifest,
    canonical_bytes,
    render_json,
    sha256_hex,
)
from app.shared.data_quality import (
    FRESHNESS_DESCRIPTIONS_PL,
    FRESHNESS_LABELS_PL,
    QUALITY_STATUS_DESCRIPTIONS_PL,
    QUALITY_STATUS_LABELS_PL,
    REDISTRIBUTION_LABELS_PL,
    allows_derived,
    allows_raw,
    reason_label_pl,
)

ANALYSIS_SCHEMA_VERSION: Final[str] = "audit-analysis/1"
SOURCES_SCHEMA_VERSION: Final[str] = "audit-sources/1"
PARCEL_FILE: Final[str] = "parcel.geojson"
ANALYSIS_FILE: Final[str] = "analysis.json"
SOURCES_FILE: Final[str] = "sources.json"
GEOJSON_MEDIA: Final[str] = "application/geo+json"
UNRESOLVED_SOURCE: Final[str] = "(nieustalone)"

LayerKind = Literal["parcel", "derived"]


@dataclass(frozen=True)
class AuditLayer:
    """Warstwa GeoJSON (EPSG:4326) wraz ze źródłami, z których pochodzi."""

    name: str
    title: str
    description: str
    features: tuple[Mapping[str, Any], ...]
    source_ids: tuple[str | None, ...]
    kind: LayerKind = "derived"

    @property
    def path(self) -> str:
        return PARCEL_FILE if self.kind == "parcel" else f"layers/{self.name}.geojson"


@dataclass(frozen=True)
class AuditRawBlock:
    """Blok surowych danych źródła wewnątrz ``analysis`` (ścieżka klucz/indeks)."""

    path: tuple[str | int, ...]
    source_id: str | None
    label: str

    @property
    def pointer(self) -> str:
        """Wskaźnik JSON do bloku w ``analysis.json`` (wynik leży pod ``result``)."""
        return "/result/" + "/".join(str(part) for part in self.path)


@dataclass(frozen=True)
class AuditInput:
    """Dane analizy gotowe do pakowania (bez zależności od ORM i Pydantic)."""

    analysis_id: int
    analyzed_at: datetime
    parcel_identifier: str | None
    status: str
    analysis: Mapping[str, Any]
    raw_blocks: tuple[AuditRawBlock, ...]
    sources: tuple[Mapping[str, Any], ...]
    layers: tuple[AuditLayer, ...]
    redistribution: Mapping[str, str]
    quality: Mapping[str, Any] | None
    computation_parcel_2180: Mapping[str, Any] | None
    parcel_source_ids: tuple[str | None, ...] = ()
    extra_context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Decision:
    include: bool
    reason: str | None
    policy: dict[str, str]
    source_ids: tuple[str, ...]


def _policy_of(source_id: str | None, redistribution: Mapping[str, str]) -> str:
    return redistribution.get(source_id, "unconfirmed") if source_id else "unconfirmed"


def _reason_for(policies: Sequence[str], *, raw: bool) -> str | None:
    """Powód pominięcia albo ``None``, gdy wszystkie źródła zezwalają."""
    permits = allows_raw if raw else allows_derived
    if all(permits(policy) for policy in policies):
        return None
    if "forbidden" in policies:
        return REASON_REDISTRIBUTION_FORBIDDEN
    if raw and "derived_only" in policies and "unconfirmed" not in policies:
        return REASON_RAW_NOT_ALLOWED
    return REASON_REDISTRIBUTION_UNCONFIRMED


def _decide(
    source_ids: Sequence[str | None], redistribution: Mapping[str, str], *, raw: bool
) -> _Decision:
    ids = sorted({sid or UNRESOLVED_SOURCE for sid in source_ids}) or [UNRESOLVED_SOURCE]
    policy = {
        sid: _policy_of(None if sid == UNRESOLVED_SOURCE else sid, redistribution) for sid in ids
    }
    reason = _reason_for(list(policy.values()), raw=raw)
    return _Decision(reason is None, reason, policy, tuple(ids))


def _iso(value: datetime) -> str:
    utc = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# --- Warstwy ------------------------------------------------------------------------


def _layer_document(layer: AuditLayer, source: AuditInput) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "name": layer.name,
        "metadata": {
            "title": layer.title,
            "derivation": layer.description,
            "coordinate_reference_system": "EPSG:4326 (WGS 84, kolejność: długość, szerokość; RFC 7946)",
            "computation_crs": "EPSG:2180",
            "computation_note": (
                "Współrzędne służą prezentacji i wymianie danych. Pola, udziały i "
                "odległości zostały obliczone w EPSG:2180 (opis w analysis.json)."
            ),
            "analysis_id": source.analysis_id,
            "source_ids": sorted({sid for sid in layer.source_ids if sid}),
            "feature_count": len(layer.features),
        },
        "features": [dict(feature) for feature in layer.features],
    }


def _layer_files(
    source: AuditInput,
) -> tuple[list[PackageFile], list[OmittedArtifact], list[dict[str, Any]]]:
    files: list[PackageFile] = []
    omitted: list[OmittedArtifact] = []
    index: list[dict[str, Any]] = []
    for layer in sorted(source.layers, key=lambda item: item.path):
        if not layer.features:
            continue
        document = _layer_document(layer, source)
        decision = _decide(layer.source_ids, source.redistribution, raw=False)
        if decision.include:
            file = PackageFile(layer.path, render_json(document), GEOJSON_MEDIA)
            files.append(file)
            index.append(
                {"name": layer.name, "path": layer.path, "title": layer.title,
                 "features": len(layer.features), "included": True}
            )
            continue
        payload = canonical_bytes(document)
        assert decision.reason is not None
        omitted.append(
            OmittedArtifact(
                name=layer.path,
                kind="derived_layer",
                source_ids=decision.source_ids,
                reason=decision.reason,
                policy=decision.policy,
                sha256=sha256_hex(payload),
                bytes=len(payload),
            )
        )
        index.append(
            {"name": layer.name, "path": layer.path, "title": layer.title,
             "features": len(layer.features), "included": False, "reason": decision.reason}
        )
    return files, omitted, index


# --- analysis.json ------------------------------------------------------------------------


def _get_path(payload: Any, path: Sequence[str | int]) -> Any:
    current = payload
    for part in path:
        try:
            current = current[part]
        except (KeyError, IndexError, TypeError):
            return None
    return current


def _set_path(payload: Any, path: Sequence[str | int], value: Any) -> None:
    parent = _get_path(payload, path[:-1])
    if isinstance(parent, (dict, list)):
        parent[path[-1]] = value  # type: ignore[index]


def _redact_raw_blocks(
    analysis: dict[str, Any], source: AuditInput
) -> tuple[list[OmittedArtifact], list[dict[str, Any]]]:
    omitted: list[OmittedArtifact] = []
    redactions: list[dict[str, Any]] = []
    for block in source.raw_blocks:
        value = _get_path(analysis, block.path)
        if value in (None, {}, []):
            continue
        decision = _decide([block.source_id], source.redistribution, raw=True)
        if decision.include:
            continue
        payload = canonical_bytes(value)
        assert decision.reason is not None
        artifact = OmittedArtifact(
            name=f"{ANALYSIS_FILE}#{block.pointer}",
            kind="raw_attributes",
            source_ids=decision.source_ids,
            reason=decision.reason,
            policy=decision.policy,
            sha256=sha256_hex(payload),
            bytes=len(payload),
        )
        omitted.append(artifact)
        redactions.append(
            {"pointer": block.pointer, "label": block.label, "reason": artifact.reason,
             "source_ids": list(artifact.source_ids), "sha256": artifact.sha256,
             "bytes": artifact.bytes}
        )
        _set_path(analysis, block.path, None)
    return omitted, redactions


def _analysis_document(
    source: AuditInput,
    layer_index: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[OmittedArtifact]]:
    result = copy.deepcopy(dict(source.analysis))
    omitted, redactions = _redact_raw_blocks(result, source)

    computation: dict[str, Any] = {
        "crs": "EPSG:2180",
        "crs_name": "ETRF2000-PL / CS92 (PUWG 1992), jednostka: metr",
        "note": (
            "Pola, udziały i odległości w wyniku zostały obliczone na geometrii w EPSG:2180. "
            "GeoJSON w pakiecie (EPSG:4326) służy prezentacji i wymianie."
        ),
        "parcel_geometry_epsg2180": None,
    }
    if source.computation_parcel_2180 is not None:
        decision = _decide(source.parcel_source_ids, source.redistribution, raw=False)
        if decision.include:
            computation["parcel_geometry_epsg2180"] = dict(source.computation_parcel_2180)
        else:
            payload = canonical_bytes(source.computation_parcel_2180)
            assert decision.reason is not None
            artifact = OmittedArtifact(
                name=f"{ANALYSIS_FILE}#/computation/parcel_geometry_epsg2180",
                kind="derived_geometry",
                source_ids=decision.source_ids,
                reason=decision.reason,
                policy=decision.policy,
                sha256=sha256_hex(payload),
                bytes=len(payload),
            )
            omitted.append(artifact)
            redactions.append(
                {"pointer": "/computation/parcel_geometry_epsg2180",
                 "label": "geometria działki w EPSG:2180", "reason": artifact.reason,
                 "source_ids": list(artifact.source_ids), "sha256": artifact.sha256,
                 "bytes": artifact.bytes}
            )

    document = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis": {
            "analysis_id": source.analysis_id,
            "analyzed_at": _iso(source.analyzed_at),
            "status": source.status,
            "parcel_identifier": source.parcel_identifier,
            **dict(source.extra_context),
        },
        "crs": {
            "computation": "EPSG:2180",
            "geojson_files": "EPSG:4326",
        },
        "computation": computation,
        "result": result,
        "section_quality": dict(source.quality) if source.quality is not None else None,
        "geometry_files": layer_index,
        "redactions": sorted(redactions, key=lambda item: item["pointer"]),
        "sources_file": SOURCES_FILE,
    }
    return document, omitted


# --- sources.json ------------------------------------------------------------------------


def _sources_document(source: AuditInput) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for item in source.sources:
        entry = dict(item)
        source_id = entry.get("source_id")
        policy = _policy_of(source_id, source.redistribution)
        entry["redistribution"] = policy
        entry["redistribution_label"] = REDISTRIBUTION_LABELS_PL[policy]
        entry["package_content"] = {
            "derived_layers": allows_derived(policy),
            "raw_data": allows_raw(policy),
        }
        entries.append(entry)
    entries.sort(
        key=lambda item: (
            str(item.get("source_id") or ""),
            str(item.get("source_name") or ""),
            str(item.get("fetched_at") or ""),
            str(item.get("source_url") or ""),
        )
    )
    return {
        "schema_version": SOURCES_SCHEMA_VERSION,
        "analysis_id": source.analysis_id,
        "note": (
            "Rejestr źródeł jest kompletny niezależnie od redystrybucji: dla źródeł, których "
            "treści nie dołączono, zachowano identyfikator, adres, czas pobrania i SHA-256."
        ),
        "sources": entries,
    }


# --- README --------------------------------------------------------------------------------


def _readme(
    source: AuditInput,
    files: Sequence[PackageFile],
    omitted: Sequence[OmittedArtifact],
    layer_index: Sequence[Mapping[str, Any]],
) -> str:
    lines: list[str] = [
        f"# Pakiet audytowy analizy #{source.analysis_id}",
        "",
        "Pakiet jest samowystarczalnym zapisem wyniku zapisanej analizy działki. Powstał "
        "wyłącznie z zapisanego snapshotu — bez ponownego odpytywania źródeł. Umożliwia "
        "niezależne sprawdzenie wyniku, geometrii i pochodzenia danych.",
        "",
        "## Identyfikacja",
        "",
        f"- Analiza: #{source.analysis_id}; działka: {source.parcel_identifier or 'nie ustalono'}.",
        f"- Data analizy (UTC): {_iso(source.analyzed_at)} — chwila wykonania analizy. "
        "Pakiet nie zawiera czasu eksportu, dzięki czemu ten sam snapshot daje identyczny pakiet.",
        f"- Status analizy: {source.status}.",
        f"- Format: {AUDIT_PACKAGE_SCHEMA_VERSION}; eksporter: {AUDIT_EXPORTER_VERSION}.",
    ]
    if source.quality is not None:
        lines.append(
            f"- Polityka jakości: {source.quality.get('policy_version')}; suma kontrolna macierzy "
            f"jakości (SHA-256): {source.quality.get('matrix_sha256')}."
        )
    lines += [
        "",
        "## Układy współrzędnych",
        "",
        "- **Obliczenia** (pola, udziały, odległości): EPSG:2180 (PUWG 1992, metry). "
        "Geometria działki w EPSG:2180 jest w `analysis.json`, w polu "
        "`computation.parcel_geometry_epsg2180`.",
        "- **GeoJSON** (`parcel.geojson`, `layers/*.geojson`): EPSG:4326 (WGS 84, kolejność: "
        "długość, szerokość; RFC 7946) — do prezentacji i wymiany. Nie licz pól ani odległości "
        "na tych współrzędnych.",
        "",
        "## Zawartość",
        "",
        "| Plik | Opis |",
        "| --- | --- |",
        f"| `{ANALYSIS_FILE}` | wynik analizy (sekcje, parametry, statusy, macierz jakości, "
        "ostrzeżenia); geometrie są w plikach GeoJSON |",
        f"| `{SOURCES_FILE}` | rejestr źródeł: identyfikator z katalogu, adres, czas pobrania, wydanie, "
        "SHA-256 artefaktu, licencja i zgoda na redystrybucję |",
        f"| `{PARCEL_FILE}` | obrys działki (EPSG:4326) |",
    ]
    for entry in layer_index:
        if entry["included"] and entry["path"] != PARCEL_FILE:
            lines.append(
                f"| `{entry['path']}` | {entry['title']} — {entry['features']} obiekt(ów), EPSG:4326 |"
            )
    lines += [
        f"| `{MANIFEST_NAME}` | lista plików z rozmiarem i SHA-256 (bez samego manifestu) oraz "
        "pominięte artefakty |",
        f"| `{README_NAME}` | ten opis |",
        "",
        "Brak pliku warstwy nie dowodzi braku ograniczeń: oznacza brak obiektów w wyniku albo "
        "pominięcie warstwy (patrz „Pominięte artefakty”). Brak danych to nie brak ograniczenia, "
        "a `null` to nie zero.",
        "",
        "## Znaczenie statusów sekcji",
        "",
        "Status opisuje wynik sprawdzenia według kontraktu źródła, a nie świeżość danych.",
        "",
        "| Status | Znaczenie |",
        "| --- | --- |",
    ]
    for key, label in QUALITY_STATUS_LABELS_PL.items():
        lines.append(f"| `{key}` — {label} | {QUALITY_STATUS_DESCRIPTIONS_PL[key]} |")
    lines += [
        "",
        "Świeżość jest oceniana osobno, według jawnej reguły wieku przypisanej do źródła i "
        "punktu odniesienia (chwila analizy). Źródło bez reguły ma świeżość `unknown` — system "
        "nie zgaduje terminu ważności.",
        "",
        "| Świeżość | Znaczenie |",
        "| --- | --- |",
    ]
    for key, label in FRESHNESS_LABELS_PL.items():
        lines.append(f"| `{key}` — {label} | {FRESHNESS_DESCRIPTIONS_PL[key]} |")
    lines += [
        "",
        "Flaga `manual_review_required` oznacza, że wynik sekcji wymaga ręcznej weryfikacji "
        "w materiale źródłowym; nie zmienia statusu sekcji.",
    ]

    if source.quality is not None:
        lines += [
            "",
            "## Macierz jakości tej analizy",
            "",
            "| Sekcja | Status | Źródło | Pobrano (UTC) | Świeżość | Weryfikacja | Powody |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for section in source.quality.get("sections", []):
            freshness = section.get("freshness", {})
            reasons = "; ".join(reason_label_pl(code) for code in section.get("reason_codes", []))
            lines.append(
                "| {section} | {status} | {src} | {fetched} | {fresh} | {manual} | {reasons} |".format(
                    section=section["section"],
                    status=section["status"],
                    src=section.get("source_id") or section.get("source_name") or "brak",
                    fetched=section.get("fetched_at") or "brak danych",
                    fresh=freshness.get("state", "unknown"),
                    manual="tak" if section.get("manual_review_required") else "nie",
                    reasons=reasons or "—",
                )
            )

    lines += ["", "## Pominięte artefakty (redystrybucja)", ""]
    if omitted:
        lines += [
            "Katalog źródeł steruje dołączaniem treści. Poniższych artefaktów nie skopiowano — "
            "pozostała referencja, SHA-256 i powód (szczegóły w `manifest.json`).",
            "",
            "| Artefakt | Źródła | Powód | SHA-256 |",
            "| --- | --- | --- | --- |",
        ]
        for item in sorted(omitted, key=lambda entry: entry.name):
            lines.append(
                f"| `{item.name}` | {', '.join(item.source_ids)} | "
                f"{item.reason} | `{item.sha256}` |"
            )
    else:
        lines.append("Wszystkie artefakty dozwolone przez katalog źródeł zostały dołączone.")

    lines += [
        "",
        "## Weryfikacja integralności",
        "",
        f"1. Rozpakuj archiwum. `{MANIFEST_NAME}` zawiera nazwę, rozmiar i SHA-256 każdego pliku "
        "(poza samym manifestem).",
        "2. Sprawdź każdy plik, np. `sha256sum analysis.json` i porównaj z manifestem. Zmiana "
        "jednego bajtu zmienia sumę.",
        "3. Skrypt `backend/scripts/verify_audit_package.py <plik.zip|katalog>` sprawdza wszystko "
        "offline (tylko biblioteka standardowa Pythona).",
        "4. Hash całej paczki ZIP nie jest w archiwum; podano go w nagłówku odpowiedzi "
        "`X-Audit-Package-SHA256`.",
        "",
        "## Zastrzeżenia",
        "",
        "Pakiet nie jest opinią prawną. Podgląd WMS służy wyłącznie prezentacji i nie jest źródłem "
        "obliczeń. Wynik zależy od stanu źródeł w chwili analizy.",
        "",
    ]
    return "\n".join(lines)


# --- Przypadek użycia ---------------------------------------------------------------------


def build_audit_package(source: AuditInput, limits: AuditLimits) -> AuditPackage:
    """Składa pakiet audytowy albo zgłasza ``AuditExportLimitError``."""
    layer_files, layer_omitted, layer_index = _layer_files(source)
    analysis_document, analysis_omitted = _analysis_document(source, layer_index)
    omitted = [*layer_omitted, *analysis_omitted]

    files = [
        PackageFile(ANALYSIS_FILE, render_json(analysis_document), "application/json"),
        PackageFile(SOURCES_FILE, render_json(_sources_document(source)), "application/json"),
        *layer_files,
    ]
    files.append(
        PackageFile(
            README_NAME,
            _readme(source, files, omitted, layer_index).encode("utf-8"),
            "text/markdown; charset=utf-8",
        )
    )

    context = {
        "analysis_id": source.analysis_id,
        "analyzed_at": _iso(source.analyzed_at),
        "parcel_identifier": source.parcel_identifier,
        "quality_matrix_sha256": (source.quality or {}).get("matrix_sha256"),
        "quality_policy_version": (source.quality or {}).get("policy_version"),
        "crs": {"computation": "EPSG:2180", "geojson_files": "EPSG:4326"},
    }
    manifest = build_manifest(files, omitted, context, limits)
    all_files = sorted([*files, manifest], key=lambda item: item.name)
    limits.enforce(all_files)
    return AuditPackage(tuple(all_files), tuple(sorted(omitted, key=lambda item: item.name)))
