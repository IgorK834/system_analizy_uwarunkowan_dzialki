"""Derived acceptance evidence: no tokens, geometry or full source bodies."""
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.analysis import Analysis
from app.models.mpzp_zone import MpzpZone
from app.models.parcel import Parcel
from app.models.versioned import DataSource, DocumentPage, DocumentVersion, LegalUnit, SourceDocument
from app.schemas.source import SectionQualityMatrix

cases = []
citations = []
documents = []
warning_counts = Counter()
date_errors = []
with SessionLocal() as db:
    source_count = db.scalar(select(func.count()).select_from(DataSource).where(DataSource.source_id == 'kimpzp'))
    for analysis, parcel in db.execute(select(Analysis, Parcel.parcel_identifier).join(Parcel).order_by(Analysis.id)):
        warnings = [item['code'] for item in analysis.warnings or []]
        warning_counts.update(warnings)
        terrain = analysis.terrain or {}
        crosscheck = terrain.get('crosscheck') or {}
        quality = SectionQualityMatrix.model_validate(analysis.section_quality)
        for section in quality.sections:
            if not section.data_as_of and not section.data_as_of_reason:
                date_errors.append({'analysis_id': analysis.id, 'section': section.section})
        cited, missing = 0, []
        for zone in db.scalars(select(MpzpZone).where(MpzpZone.analysis_id == analysis.id)):
            for parameter in (zone.result_snapshot or {}).get('parameters', []):
                identifier = parameter.get('legal_unit_id')
                if identifier is None:
                    continue
                cited += 1
                unit = db.get(LegalUnit, identifier)
                if unit is None or unit.document_version_id != parameter.get('document_version_id'):
                    missing.append(identifier)
        citations.append({'analysis_id': analysis.id, 'parcel_identifier': parcel, 'cited_parameters': cited,
                         'missing_or_wrong_version_units': missing})
        cases.append({'analysis_id': analysis.id, 'parcel_identifier': parcel, 'status': analysis.status,
                      'warning_codes': warnings, 'terrain_status': terrain.get('status'),
                      'elevation_method': terrain.get('elevation_method'), 'quality_integrity_ok': quality.integrity_ok(),
                      'primary_equals_raster': (terrain.get('min_height_m') == (terrain.get('relief') or {}).get('min_height_m')
                                               and terrain.get('max_height_m') == (terrain.get('relief') or {}).get('max_height_m'))
                                              if terrain.get('elevation_method') == 'wcs_raster' else None,
                      'crosscheck_threshold_ok': bool(crosscheck.get('warning_code')) == (crosscheck['max_difference_m'] > 0.5)
                                                if crosscheck else None})
    for version, act in db.execute(select(DocumentVersion, SourceDocument.planning_act_id).join(SourceDocument)):
        documents.append({'document_version_id': version.id, 'planning_act_id': act, 'content_hash': version.content_hash,
                          'source_artifact_id': version.source_artifact_id, 'data_release_id': version.data_release_id,
                          'page_count': db.scalar(select(func.count()).select_from(DocumentPage).where(DocumentPage.document_version_id == version.id)),
                          'legal_unit_count': db.scalar(select(func.count()).select_from(LegalUnit).where(LegalUnit.document_version_id == version.id))})
result = {'case_count': len(cases), 'kimpzp_source_count': source_count, 'document_versions': documents,
          'warning_occurrences': dict(warning_counts), 'citations': citations,
          'cited_parameter_count': sum(item['cited_parameters'] for item in citations),
          'missing_or_wrong_version_unit_count': sum(len(item['missing_or_wrong_version_units']) for item in citations),
          'sections_without_date_or_reason': date_errors, 'cases': cases}
Path('/tmp/au108-race-acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
assert len(cases) == 30 and source_count == 1
assert warning_counts['MPZP_DOCUMENT_PERSISTENCE_FAILED'] == 0
assert not result['missing_or_wrong_version_unit_count'] and result['cited_parameter_count'] > 0
assert not date_errors
assert all(item['quality_integrity_ok'] and item['primary_equals_raster'] is not False
           and item['crosscheck_threshold_ok'] is not False for item in cases)
audit_parcel = next(item for item in citations if item['parcel_identifier'] == '126105_9.0001.580/4')
assert audit_parcel['cited_parameters'] > 0
print(json.dumps({key: value for key, value in result.items() if key not in {'cases', 'citations'}}, ensure_ascii=False, indent=2))
