"""Oznaczenie odczytu automatycznego (model językowy) wspólne dla API, raportu PDF i pakietu audytowego (PV3-18).

Wartość parametru MPZP z modelu językowego trafia do wyniku wyłącznie po deterministycznej weryfikacji
względem tekstu uchwały (bramki G1–G8) i ma status kandydata ``ai_candidate`` do ręcznej weryfikacji —
nigdy „verified”. Ten moduł ustala JEDEN sposób rozpoznania takiej wartości i JEDNO brzmienie
oznaczenia, żeby interfejs (``frontend/lib/mpzpProvenance.ts``, tekst pilnowany testem), PDF i pakiet
audytowy nie rozjechały się. Wartość deterministyczna nigdy nie jest oznaczana jako odczyt modelu:
rozpoznanie opiera się wyłącznie na statusie i metodzie nadawanych przez weryfikator, a nie na
wyglądzie wartości czy pewności.

Moduł nie zależy od frameworków (może być importowany z każdej warstwy).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

REVIEW_STATUS_AI_CANDIDATE: Final[str] = "ai_candidate"
EXTRACTION_METHOD_LLM_VERIFIED: Final[str] = "llm_verified"

MODEL_READING_MARK: Final[str] = (
    "odczyt automatyczny (model językowy), zweryfikowany z cytatem — wymaga potwierdzenia"
)
MODEL_READING_SHORT: Final[str] = "odczyt automatyczny (model językowy)"
MODEL_READING_DISCLAIMER: Final[str] = (
    "Odczyt automatyczny nie jest interpretacją prawną: model językowy zaproponował wartość, a program "
    "potwierdził jedynie, że cytat i liczba występują w tekście uchwały na wskazanej stronie. Treść, "
    "zakres i warunki obowiązywania ustalenia należy potwierdzić w uchwale."
)
NO_DATA_NOT_NO_RESTRICTION: Final[str] = (
    "Brak danych nie oznacza braku ograniczenia: brak wartości parametru znaczy, że nie ustalono jej z "
    "uchwały, a nie że uchwała niczego nie ogranicza."
)
NULL_NOT_ZERO: Final[str] = "Brak wartości (null) nie jest zerem."


def is_model_reading(parameter: Any) -> bool:
    """Czy parametr jest odczytem modelu językowego (kandydatem ``ai_candidate`` po bramkach).

    Rozpoznaje po statusie ``ai_candidate`` albo metodzie ``llm_verified`` (weryfikator nadaje oba
    jednocześnie; więzy bazy ``ck_mpzp_parameters_llm_candidate`` nie pozwalają na metodę bez statusu).
    """
    if isinstance(parameter, Mapping):  # JSON pakietu audytowego i snapshotu
        status, method = parameter.get("review_status"), parameter.get("extraction_method")
    else:
        status, method = getattr(parameter, "review_status", None), getattr(parameter, "extraction_method", None)
    return status == REVIEW_STATUS_AI_CANDIDATE or method == EXTRACTION_METHOD_LLM_VERIFIED
