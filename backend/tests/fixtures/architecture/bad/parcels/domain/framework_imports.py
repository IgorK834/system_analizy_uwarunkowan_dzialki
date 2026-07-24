"""FIXTURE (celowo błędny): domain z zabronionymi importami frameworków.

Ten plik NIE jest importowany — służy wyłącznie do statycznej analizy AST.
"""

import fastapi
import sqlalchemy
import geoalchemy2
import httpx
