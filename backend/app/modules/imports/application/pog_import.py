"""Przypadek użycia importu pełnych snapshotów aktów POG (Plan Ogólny Gminy).

Analogiczny do ``mpzp_import`` (raw → QA → atomowa publikacja wersji), ale
publikuje odrębny rodzaj aktu (``kind='pog'``) z czterema logicznymi warstwami
i własnym statusem prawnym. Reguła nadrzędna: akt o statusie ``project`` lub
``in_progress`` nigdy nie jest publikowany jako obowiązujący — status jest
przenoszony bez zmian do wersji aktu, a wynik importu jawnie go raportuje.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Protocol

from app.modules.imports.application.common import (
    ImportOutcome,
    ImportRelease,
    ImportSourceNotRunnable,
    MutableImportStats,
    SourceBatch,
    artifact_sha256,
)
from app.modules.imports.application.parcels_import import RepairedGeometry
from app.modules.imports.domain.pog import (
    PogActRecord,
    PogFeatureRecord,
    PogValidationError,
    parse_app_reference,
    pog_record_to_dict,
)
from app.shared.geometry import GeometryPayload


@dataclass(frozen=True)
class PogSourceBatch(SourceBatch):
    acts: tuple[PogActRecord, ...] = ()
    complete: bool = True
    strict_identifiers: bool = False
    parser_config_id: str = "app3-v2"
    # Ostrzeżenia czytnika (np. niedostępne CSW, dokument nierozstrzygnięty);
    # nie blokują publikacji, ale trafiają do wyniku importu i ImportRun.
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PogPublicationResult:
    new: int
    changed: int
    unchanged: int
    import_run_id: int
    data_release_id: int


class PogSourceReader(Protocol):
    def read(self) -> PogSourceBatch: ...


class PogImportRepository(Protocol):
    def repair_geometry(self, geometry: GeometryPayload) -> RepairedGeometry | None: ...

    def publish_pog(
        self,
        *,
        release: ImportRelease,
        batch: PogSourceBatch,
        artifact_hash: str,
        acts: tuple[tuple[PogActRecord, str], ...],
        stats: MutableImportStats,
        warnings: tuple[str, ...],
    ) -> PogPublicationResult: ...

    def previous_pog_feature_count(self) -> int | None: ...


def _stable_json_value(value: Any) -> Any:
    """Sprowadza atrybuty OGR do deterministycznej, hashowalnej postaci JSON."""
    if isinstance(value, dict):
        return {
            str(key): _stable_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_json_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _stable_json_value(item())
        except (TypeError, ValueError):
            pass
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def _snapshot_hash(act: PogActRecord, geometry_hashes: list[str]) -> str:
    """Liczy hash całego snapshotu aktu, niezależny od kolejności warstw.

    Pierwszy element ``geometry_hashes`` to hash granicy aktu (albo None), a
    kolejne odpowiadają obiektom ``act.features`` w tej samej kolejności.
    """
    boundary_hash = geometry_hashes[0] if geometry_hashes else None
    feature_hashes = geometry_hashes[1:]
    if len(feature_hashes) != len(act.features):
        raise ValueError("Liczba hashy geometrii warstw jest niespójna z aktem.")
    features = [
        {
            "feature_type": feature.feature_type,
            "object_id": (
                feature.object_id.versioned_id if feature.object_id else None
            ),
            "act_reference": (
                feature.act_reference.href if feature.act_reference else None
            ),
            "source_reference": feature.source_reference,
            "raw_legal_status": feature.raw_legal_status,
            "symbol": feature.symbol,
            "label": feature.label,
            "parameters": _stable_json_value(
                feature.parameters.values() if feature.parameters else None
            ),
            "primary_profiles": _stable_json_value(feature.primary_profiles),
            "additional_profiles": _stable_json_value(feature.additional_profiles),
            "raw_attributes": _stable_json_value(dict(feature.raw_attributes)),
            "geometry_hash": geometry_hash,
        }
        for feature, geometry_hash in zip(act.features, feature_hashes, strict=True)
    ]
    features.sort(
        key=lambda item: json.dumps(
            item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    )
    payload = {
        "identifier": act.act_identifier,
        "resolution_number": act.resolution_number,
        "resolution_date": (
            act.resolution_date.isoformat() if act.resolution_date else None
        ),
        "teryt": act.teryt,
        "name": act.name,
        "legal_status": act.legal_status,
        "raw_legal_status": act.raw_legal_status,
        "object_id": act.object_id.versioned_id if act.object_id else None,
        "relations": _stable_json_value(pog_record_to_dict(act).get("feature_references", [])),
        "documents": _stable_json_value(pog_record_to_dict(act).get("documents", [])),
        "publication_id": act.publication_id,
        "version_started_at": (
            act.version_started_at.isoformat() if act.version_started_at else None
        ),
        "valid_from": act.valid_from.isoformat() if act.valid_from else None,
        "valid_to": act.valid_to.isoformat() if act.valid_to else None,
        "metadata": _stable_json_value(pog_record_to_dict(act).get("metadata", [])),
        "boundary_hash": boundary_hash,
        "features": features,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _references_act(href: str, act: PogActRecord, act_stable_id: str) -> bool:
    """Relacja cecha → akt: identyfikator i wersja idIIP, a bez idIIP — sufiks."""
    reference = parse_app_reference(href)
    if act.object_id is not None and reference is not None:
        return reference.matches(act.object_id)
    return href.rstrip("/").endswith(act_stable_id.rstrip("/"))


def run_pog_import(
    reader: PogSourceReader,
    gmina_source_id: str,
    db: PogImportRepository,
    *,
    release: ImportRelease,
) -> ImportOutcome:
    """Naprawia i waliduje cały akt POG, a następnie publikuje go atomowo."""
    if gmina_source_id != release.source_id:
        raise ValueError("source_id czytnika i wydania muszą być zgodne.")
    if not release.publication_allowed and not release.dry_run:
        raise ImportSourceNotRunnable(
            "DRY-RUN ONLY: źródło POG nie jest production_ready."
        )

    batch = reader.read()
    if not batch.complete:
        raise PogValidationError(
            "Źródło POG nie potwierdziło kompletności wszystkich stron; publikacja przerwana."
        )
    stats = MutableImportStats(input=len(batch.acts))
    warnings: list[str] = list(batch.warnings)
    prepared: list[tuple[PogActRecord, str]] = []
    critical_errors: list[str] = []
    seen_ids: set[str] = set()

    for act in batch.acts:
        try:
            act.validate()
        except PogValidationError as exc:
            stats.rejected += 1
            critical_errors.append(str(exc))
            continue

        act_stable_id = act.object_id.stable_id if act.object_id else act.act_identifier
        for feature in act.features:
            if batch.strict_identifiers and feature.object_id is None:
                critical_errors.append(
                    f"missing_feature_id:{act.act_identifier}:{feature.feature_type}"
                )
            if feature.object_id:
                stable_id = feature.object_id.stable_id
                if stable_id in seen_ids:
                    critical_errors.append(f"duplicate_feature_id:{stable_id}")
                seen_ids.add(stable_id)
            if feature.act_reference and not _references_act(
                feature.act_reference.href, act, act_stable_id
            ):
                critical_errors.append(
                    f"invalid_act_reference:{feature.stable_id or feature.feature_type}"
                )

        for document in act.documents:
            if document.resolution_status != "resolved":
                warnings.append(
                    f"pog_document_{document.resolution_status}:"
                    f"{document.object_id.versioned_id}"
                )

        # Status niewiążący jest przenoszony bez zmian, ale jawnie oznaczony w
        # statystykach i ostrzeżeniach — nie może wyglądać jak plan obowiązujący.
        if not act.is_binding:
            warnings.append(f"pog_not_binding:{act.act_identifier}:{act.legal_status}")
        status_key = f"legal_status:{act.legal_status}"
        stats.extra[status_key] = int(stats.extra.get(status_key, 0)) + 1

        boundary_payload: GeometryPayload | None = None
        geometry_hashes: list[str] = []
        if act.boundary is not None:
            boundary = db.repair_geometry(act.boundary)
            if boundary is None:
                stats.rejected += 1
                critical_errors.append(f"empty_boundary:{act.act_identifier}")
                continue
            if boundary.repaired:
                stats.repaired += 1
            boundary_payload = boundary.geometry
            geometry_hashes.append(boundary.content_hash)
        else:
            geometry_hashes.append("")

        repaired_features: list[PogFeatureRecord] = []
        act_has_geometry_error = False
        for feature in act.features:
            repaired = db.repair_geometry(feature.geometry)
            if repaired is None:
                stats.rejected += 1
                critical_errors.append(
                    f"empty_feature:{act.act_identifier}:{feature.feature_type}"
                )
                act_has_geometry_error = True
                continue
            if repaired.repaired:
                stats.repaired += 1
            repaired_features.append(feature.with_geometry(repaired.geometry))
            geometry_hashes.append(repaired.content_hash)

        if act_has_geometry_error:
            continue

        prepared_act = replace(
            act,
            boundary=boundary_payload,
            features=tuple(repaired_features),
        )
        for feature_type, count in prepared_act.feature_counts().items():
            key = f"feature:{feature_type}"
            stats.extra[key] = int(stats.extra.get(key, 0)) + count
        prepared.append((prepared_act, _snapshot_hash(prepared_act, geometry_hashes)))

    if critical_errors:
        raise PogValidationError(
            "Krytyczne QA POG odrzuciło cały import: "
            + "; ".join(dict.fromkeys(critical_errors))
        )

    previous_count_method = getattr(db, "previous_pog_feature_count", None)
    previous_count = previous_count_method() if callable(previous_count_method) else None
    current_count = sum(len(act.features) for act, _hash in prepared)
    stats.extra["feature_count"] = current_count
    if previous_count is not None:
        stats.extra["previous_feature_count"] = previous_count
        if previous_count > 0 and current_count < previous_count * 0.5:
            raise PogValidationError(
                f"Krytyczne QA POG: liczba cech spadła z {previous_count} do {current_count} (>50%)."
            )

    if release.dry_run or not release.publication_allowed:
        stats.extra["publishable"] = len(prepared)
        return ImportOutcome(
            status="dry_run_only",
            stats=stats.as_dict(),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    result = db.publish_pog(
        release=release,
        batch=batch,
        artifact_hash=artifact_sha256(batch.content),
        acts=tuple(prepared),
        stats=stats,
        warnings=tuple(dict.fromkeys(warnings)),
    )
    stats.new, stats.changed, stats.unchanged = (
        result.new,
        result.changed,
        result.unchanged,
    )
    return ImportOutcome(
        status="succeeded",
        stats=stats.as_dict(),
        warnings=tuple(dict.fromkeys(warnings)),
        import_run_id=result.import_run_id,
        data_release_id=result.data_release_id,
    )
