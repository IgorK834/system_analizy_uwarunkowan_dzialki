"""FIXTURE (poprawny): application definiuje port i importuje domain + shared."""

from typing import Protocol

from app.modules.parcels.domain.models import ParcelIdentifier  # noqa: F401
from app.shared.provenance import Provenance  # noqa: F401


class SamplePort(Protocol):
    def do(self, identifier: ParcelIdentifier) -> Provenance: ...
