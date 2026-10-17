"""Derived verification evidence only; no geometry or access tokens."""
import json
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.analysis import Analysis
from app.models.parcel import Parcel
from app.models.source_record import SourceRecord
from app.services.persistence import _source_metadata_from_record
from app.schemas.source import SectionQualityMatrix

manifest = json.loads(Path('/repo/backend/tests/fixtures/reference_corpus/manifest.json').read_text())
identifiers = {item['parcel_identifier'] for item in manifest['cases']}
source_counts = defaultdict(Counter)
source_examples = {}
known_source_examples = {}
section_counts = defaultdict(Counter)
missing_dates = []
legacy_source_records = Counter()
cases = []
with SessionLocal() as session:
    rows = session.execute(select(Analysis, Parcel.parcel_identifier).join(Parcel).order_by(Analysis.id)).all()
    for analysis, identifier in rows:
        if identifier not in identifiers:
            continue
        terrain = analysis.terrain or {}
        quality = analysis.section_quality or {}
        for section in quality.get('sections', []):
            section_counts[section['section']][section['status']] += 1
            if not section.get('data_as_of') and not section.get('data_as_of_reason'):
                missing_dates.append({'analysis_id': analysis.id, 'section': section['section']})
        crosscheck = terrain.get('crosscheck') or {}
        cases.append({
            'analysis_id': analysis.id,
            'parcel_identifier': identifier,
            'status': analysis.status,
            'terrain_status': terrain.get('status'),
            'elevation_method': terrain.get('elevation_method'),
            'min_height_m': terrain.get('min_height_m'),
            'max_height_m': terrain.get('max_height_m'),
            'crosscheck_difference_m': crosscheck.get('max_difference_m'),
            'crosscheck_warning_code': crosscheck.get('warning_code'),
            'quality_schema_version': quality.get('schema_version'),
            'quality_integrity_ok': SectionQualityMatrix.model_validate(quality).integrity_ok(),
            'primary_equals_raster': (
                terrain.get('min_height_m') == (terrain.get('relief') or {}).get('min_height_m')
                and terrain.get('max_height_m') == (terrain.get('relief') or {}).get('max_height_m')
            ),
        })
        for source in session.scalars(select(SourceRecord).where(SourceRecord.analysis_id == analysis.id)):
            key = source.source_id or source.source_name
            if not source.dataset_metadata:
                legacy_source_records[key] += 1
            metadata = _source_metadata_from_record(source).model_dump(mode='json', include={'data_as_of', 'data_as_of_basis', 'data_as_of_reason'})
            state = 'known' if metadata.get('data_as_of') else 'unknown_with_reason' if metadata.get('data_as_of_reason') else 'missing_reason'
            source_counts[key][state] += 1
            source_examples.setdefault(key, metadata)
            if metadata.get('data_as_of'):
                known_source_examples.setdefault(key, metadata)
result = {
    'case_count': len(cases),
    'terrain_methods': dict(Counter(item['elevation_method'] for item in cases)),
    'terrain_statuses': dict(Counter(item['terrain_status'] for item in cases)),
    'crosscheck_warnings': sum(bool(item['crosscheck_warning_code']) for item in cases),
    'sections': dict(section_counts),
    'source_dates': dict(source_counts),
    'source_date_examples': source_examples,
    'known_source_date_examples': known_source_examples,
    'legacy_records_with_reason_supplied_on_read': dict(legacy_source_records),
    'sections_without_date_or_reason': missing_dates,
    'cases': cases,
}
assert len(cases) == 30, 'Niepełny korpus'
assert not missing_dates, 'Sekcja bez daty i bez powodu'
assert all(item['quality_integrity_ok'] for item in cases), 'Uszkodzony odcisk macierzy jakości'
assert all(item['primary_equals_raster'] for item in cases), 'Różne zakresy nadrzędny i raster'
assert all(bool(item['crosscheck_warning_code']) == (item['crosscheck_difference_m'] > 0.5)
           for item in cases if item['crosscheck_difference_m'] is not None), 'Błędny próg kontroli'
Path('/tmp/au108-live-details.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({key: value for key, value in result.items() if key != 'cases'}, ensure_ascii=False, indent=2))
