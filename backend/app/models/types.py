"""Typy kolumn współdzielone przez modele ORM."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.shared.text import clip_text

logger = logging.getLogger(__name__)


class ClippedString(TypeDecorator[str]):
    """``VARCHAR(n)``, który przycina za długi tekst z zewnątrz zamiast przerywać zapis.

    Schemat bazy jest taki sam jak dla ``String(n)`` (migracje nie wymagają zmian).
    Przycięcie następuje przy wiązaniu parametru, więc obejmuje każdą ścieżkę zapisu —
    ORM i Core — i jest obroną w głębi dla pól zasilanych tekstem z zewnątrz (nazwy źródeł,
    numery uchwał, etykiety). Wartości nadawane przez aplikację (skróty, statusy, klucze
    tożsamości) zostają zwykłym ``String(n)``: ich nadmiar jest błędem programisty i ma
    kończyć się jawnym błędem zapisu, nie cichą zmianą wartości.
    """

    impl = String
    # Klucz cache zapytań SQLAlchemy jest budowany z argumentów ``__init__`` obecnych w ``__dict__`` —
    # jawny ``length`` odróżnia ``ClippedString(120)`` od ``ClippedString(255)``.
    cache_ok = True

    def __init__(self, length: int) -> None:
        super().__init__(length)
        self.length = length

    @property
    def max_length(self) -> int:
        return self.length

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        limit = self.max_length
        if not isinstance(value, str) or len(value) <= limit:
            return value
        # Do logu trafia wyłącznie długość: wartość może zawierać adres z geometrią działki.
        logger.warning("text_clipped max_len=%d original_len=%d", limit, len(value))
        return clip_text(value, limit)
