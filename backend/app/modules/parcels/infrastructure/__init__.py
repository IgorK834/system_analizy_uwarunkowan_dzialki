"""Warstwa infrastruktury modułu ``parcels``.

Implementuje porty z warstwy ``application`` przy użyciu bibliotek zewnętrznych
(SQLAlchemy, GeoAlchemy2, httpx). Może zależeć od ``application``, ``domain``,
``app.shared`` oraz bibliotek zewnętrznych.
"""
