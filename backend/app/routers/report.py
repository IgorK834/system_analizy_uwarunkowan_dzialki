"""Cienka warstwa HTTP dla generowania raportu PDF analizy."""

import io

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.analyze import ErrorResponse
from app.services.report import (
    AnalysisReportNotFoundError,
    AnalysisReportRenderError,
    generate_analysis_report_pdf,
)

router = APIRouter(prefix="/report", tags=["report"])


@router.get(
    "/{analysis_id}",
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Raport PDF zapisanej analizy.",
        },
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    response_class=StreamingResponse,
    description=(
        "Generuje raport PDF dla zapisanej analizy. Raport jest budowany "
        "wyłącznie z zapisanego snapshotu — nie uruchamia ponownie analizy "
        "ani nie odpytuje usług zewnętrznych. Zwraca 404, gdy analiza o podanym "
        "identyfikatorze nie istnieje."
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
