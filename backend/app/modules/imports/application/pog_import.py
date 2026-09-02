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
    POG_FEATURE_TYPES,
    PogActRecord,
    PogFeatureRecord,
    PogValidationError,
)
from app.shared.geometry import GeometryPayload


@dataclass(frozen=True)
class PogSourceBatch(SourceBatch):
    acts: tuple[PogActRecord, ...] = ()


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
        "boundary_hash": boundary_hash,
        "features": features,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    stats = MutableImportStats(input=len(batch.acts))
    warnings: list[str] = []
    prepared: list[tuple[PogActRecord, str]] = []

    for act in batch.acts:
        try:
            act.validate()
        except PogValidationError as exc:
            stats.rejected += 1
            warnings.append(str(exc))
            continue

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
                warnings.append(f"empty_boundary:{act.act_identifier}")
                continue
            if boundary.repaired:
                stats.repaired += 1
            boundary_payload = boundary.geometry
            geometry_hashes.append(boundary.content_hash)
        else:
            geometry_hashes.append("")

        repaired_features: list[PogFeatureRecord] = []
        for feature in act.features:
            repaired = db.repair_geometry(feature.geometry)
            if repaired is None:
                stats.rejected += 1
                warnings.append(
                    f"empty_feature:{act.act_identifier}:{feature.feature_type}"
                )
                continue
            if repaired.repaired:
                stats.repaired += 1
            repaired_features.append(feature.with_geometry(repaired.geometry))
            geometry_hashes.append(repaired.content_hash)

        prepared_act = replace(
            act,
            boundary=boundary_payload,
            features=tuple(repaired_features),
        )
        for feature_type, count in prepared_act.feature_counts().items():
            key = f"feature:{feature_type}"
            stats.extra[key] = int(stats.extra.get(key, 0)) + count
        prepared.append((prepared_act, _snapshot_hash(prepared_act, geometry_hashes)))

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
