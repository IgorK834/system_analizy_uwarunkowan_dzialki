"""FIXTURE (celowo błędny): application zależy od infrastruktury."""

from app.modules.parcels.infrastructure.repository import ParcelRepo  # noqa: F401
