"""Cienka warstwa HTTP dla generowania raportu PDF analizy."""

import io

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.access_control import require_analysis_token
from app.core.rate_limit import rate_limit
from app.core.settings import settings
from app.db.session import get_db
from app.schemas.analyze import ErrorResponse
from app.services.report import (
    AnalysisReportNotFoundError,
    AnalysisReportRenderError,
    generate_analysis_report_pdf,
)

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
