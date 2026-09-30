"""Cienka warstwa HTTP dla generowania raportu PDF analizy."""

import io
import logging

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.access_control import require_analysis_token
from app.core.rate_limit import rate_limit
from app.core.settings import settings
from app.db.session import get_db
from app.modules.reporting.domain.audit_package import (
    AUDIT_EXPORTER_VERSION,
    AuditExportLimitError,
)
from app.modules.reporting.infrastructure.archive import iter_archive
from app.schemas.analyze import ErrorResponse
from app.services.audit_package import AuditPackageNotFoundError, generate_audit_package
from app.services.report import (
    AnalysisReportNotFoundError,
    AnalysisReportRenderError,
    generate_analysis_report_pdf,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/report", tags=["report"])

# Generowanie PDF (WeasyPrint) jest kosztowne CPU-wo.
_report_limit = rate_limit(settings.rate_limit_report_per_minute)


@router.get(
    "/{analysis_id}",
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Raport PDF zapisanej analizy.",
        },
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    dependencies=[Depends(_report_limit), Depends(require_analysis_token)],
    response_class=StreamingResponse,
    description=(
        "Generuje raport PDF dla zapisanej analizy. Raport jest budowany "
        "wyłącznie z zapisanego snapshotu — nie uruchamia ponownie analizy "
        "ani nie odpytuje usług zewnętrznych. Zwraca 404, gdy analiza o podanym "
        "identyfikatorze nie istnieje. Wymaga tokenu ``access_token`` z odpowiedzi analizy."
    ),
)
def get_analysis_report(
    analysis_id: int = Path(
        gt=0,
        description="Identyfikator zapisanej analizy.",
        examples=[123],
    ),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    try:
        pdf_bytes = generate_analysis_report_pdf(analysis_id, db)
    except AnalysisReportNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Analiza o podanym identyfikatorze nie istnieje.",
        ) from exc
    except AnalysisReportRenderError as exc:
        # Nie ujawniamy szczegółów WeasyPrint, ścieżek plikowych ani stack trace.
        raise HTTPException(
            status_code=500,
            detail="Nie udało się wygenerować raportu PDF.",
        ) from exc

    # Nazwa pliku zawiera tylko identyfikator liczbowy z walidowanej ścieżki,
    # więc Content-Disposition jest bezpieczny (brak wstrzyknięcia nagłówka).
    filename = f"raport_analizy_{analysis_id}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{analysis_id}/audit.zip",
    responses={
        200: {
            "content": {"application/zip": {}},
            "description": (
                "Pakiet audytowy zapisanej analizy (ZIP): analysis.json, sources.json, "
                "parcel.geojson, dozwolone warstwy pochodne, README.md i manifest.json "
                "z SHA-256 każdego pliku. Hash całej paczki w nagłówku "
                "X-Audit-Package-SHA256."
            ),
            "headers": {
                "X-Audit-Package-SHA256": {
                    "description": "SHA-256 całego archiwum ZIP (poza archiwum).",
                    "schema": {"type": "string"},
                },
                "X-Audit-Exporter-Version": {
                    "description": "Wersja eksportera; determinizm dotyczy tej samej wersji.",
                    "schema": {"type": "string"},
                },
            },
        },
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    dependencies=[Depends(_report_limit), Depends(require_analysis_token)],
    response_class=StreamingResponse,
    description=(
        "Udostępnia samowystarczalny pakiet audytowy zapisanej analizy, budowany "
        "wyłącznie z zapisanego snapshotu (bez ponownej analizy i bez odpytywania "
        "źródeł). Dostęp jak do raportu PDF: wymaga tokenu ``access_token`` z "
        "odpowiedzi analizy. Zwraca 404, gdy analiza nie istnieje, i 413, gdy pakiet "
        "przekroczyłby limity rozmiaru. Warstwy i dane źródeł, dla których katalog "
        "nie zezwala na redystrybucję, nie są kopiowane — w manifeście zostaje "
        "referencja, SHA-256 i powód pominięcia."
    ),
)
def get_analysis_audit_package(
    analysis_id: int = Path(
        gt=0,
        description="Identyfikator zapisanej analizy.",
        examples=[123],
    ),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    try:
        archive = generate_audit_package(analysis_id, db)
    except AuditPackageNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Analiza o podanym identyfikatorze nie istnieje.",
        ) from exc
    except AuditExportLimitError as exc:
        raise HTTPException(
            status_code=413,
            detail="Pakiet audytowy przekracza dopuszczalny rozmiar.",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - granica HTTP: bez szczegółów wewnętrznych
        logger.exception("Nie udało się zbudować pakietu audytowego analizy %s", analysis_id)
        raise HTTPException(
            status_code=500,
            detail="Nie udało się przygotować pakietu audytowego.",
        ) from exc

    filename = f"analiza_{analysis_id}_pakiet_audytowy.zip"
    return StreamingResponse(
        iter_archive(archive),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(archive.size),
            "X-Audit-Package-SHA256": archive.sha256,
            "X-Audit-Exporter-Version": AUDIT_EXPORTER_VERSION,
            "Cache-Control": "private, no-store",
        },
    )
