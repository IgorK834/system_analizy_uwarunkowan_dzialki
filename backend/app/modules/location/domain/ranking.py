"""Deterministyczny ranking wyników adresowych (czysta logika).

Wynik rankingu łączy dopasowanie tekstowe, pewność dostawcy, bliskość do bias
point oraz przynależność do bbox. Kolejność jest w pełni deterministyczna:
malejąco po wyniku, a przy remisie rosnąco po identyfikatorze.
"""

from __future__ import annotations

import hashlib
import math

from app.modules.location.domain.models import (
    BBox,
    GeoPoint,
    RankedAddress,
    RawAddressCandidate,
)
from app.modules.location.domain.normalization import (
    compute_match_ranges,
    fold_text,
    tokenize,
)

# Wagi składników rankingu. Trzymane jawnie, aby ranking był audytowalny.
_WEIGHT_EXACT_SUBSTRING = 3.0
_WEIGHT_TOKEN_COVERAGE = 2.0
_WEIGHT_PROVIDER_CONFIDENCE = 1.0
_WEIGHT_BIAS = 1.0
_BONUS_BBOX_INSIDE = 0.5
_PENALTY_BBOX_OUTSIDE = 1.0


def stable_result_id(candidate: RawAddressCandidate) -> str:
    """Zwraca jednoznaczny, deterministyczny identyfikator wyniku.

    Preferuje identyfikator źródłowy. Gdy kontrakt go nie daje, liczy stabilny
    skrót SHA-256 z kanonicznych danych (części adresu + zaokrąglone współrzędne).
    """
    if candidate.source_identifier:
        return f"{candidate.source_id or 'uug'}:{candidate.source_identifier}"
    canonical = "|".join(
        [
            candidate.parts.country or "",
            candidate.parts.voivodeship or "",
            candidate.parts.county or "",
            candidate.parts.municipality or "",
            candidate.parts.city or "",
            candidate.parts.street or "",
            candidate.parts.house_number or "",
            f"{candidate.point.lon:.6f}",
            f"{candidate.point.lat:.6f}",
            candidate.result_type.value,
        ]
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"hash:{digest}"


def _text_score(label: str, query: str) -> float:
    folded_label = fold_text(label)
    folded_query = fold_text(query).strip()
    score = 0.0
    if folded_query and folded_query in folded_label:
        score += _WEIGHT_EXACT_SUBSTRING
    query_tokens = [token for token in tokenize(folded_query) if len(token) >= 2]
    if query_tokens:
        matched = sum(1 for token in query_tokens if token in folded_label)
        score += _WEIGHT_TOKEN_COVERAGE * (matched / len(query_tokens))
    return score


def _bias_score(point: GeoPoint, bias: GeoPoint | None) -> float:
    if bias is None:
        return 0.0
    # Odległość euklidesowa w stopniach wystarcza do deterministycznego biasu
    # rankingu (nie jest to pomiar metryczny). Bliżej = wyżej.
    distance = math.hypot(point.lon - bias.lon, point.lat - bias.lat)
    return _WEIGHT_BIAS / (1.0 + distance)


def _bbox_score(point: GeoPoint, bbox: BBox | None) -> float:
    if bbox is None:
        return 0.0
    return _BONUS_BBOX_INSIDE if bbox.contains(point) else -_PENALTY_BBOX_OUTSIDE


def score_candidate(
    candidate: RawAddressCandidate,
    query: str,
    bias: GeoPoint | None,
    bbox: BBox | None,
) -> float:
    """Liczy deterministyczny wynik rankingu pojedynczego kandydata."""
    return (
        _text_score(candidate.label, query)
        + _WEIGHT_PROVIDER_CONFIDENCE * candidate.provider_confidence
        + _bias_score(candidate.point, bias)
        + _bbox_score(candidate.point, bbox)
    )


def rank_candidates(
    candidates: list[RawAddressCandidate],
    query: str,
    limit: int,
    bias: GeoPoint | None = None,
    bbox: BBox | None = None,
) -> list[RankedAddress]:
    """Rankuje kandydatów i zwraca co najwyżej ``limit`` wyników.

    Sortowanie: malejąco po wyniku, a przy remisie rosnąco po identyfikatorze —
    dzięki temu kolejność jest stabilna i powtarzalna.
    """
    ranked: list[RankedAddress] = []
    for candidate in candidates:
        result_id = stable_result_id(candidate)
        ranked.append(
            RankedAddress(
                id=result_id,
                label=candidate.label,
                match_ranges=compute_match_ranges(candidate.label, query),
                point=candidate.point,
                parts=candidate.parts,
                result_type=candidate.result_type,
                confidence=candidate.provider_confidence,
                score=score_candidate(candidate, query, bias, bbox),
                source_id=candidate.source_id,
            )
        )
    ranked.sort(key=lambda item: (-item.score, item.id))
    return ranked[:limit]
