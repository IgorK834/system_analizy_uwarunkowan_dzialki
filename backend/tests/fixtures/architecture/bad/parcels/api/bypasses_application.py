"""FIXTURE (celowo błędny): api sięga wprost do infrastruktury."""

from app.modules.parcels.infrastructure.repository import ParcelRepo  # noqa: F401
