"""FIXTURE (celowo błędny): import prywatnego elementu innego modułu."""

from app.modules.parcels.domain._secret import Hidden  # noqa: F401
