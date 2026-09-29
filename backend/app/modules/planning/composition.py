"""Root kompozycji modułu planowania: reguły, kafle POG, inspektor i agregaty."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.orm import Session

from app.core.settings import settings
from app.modules.planning.application.pog_release_queries import PogReleaseQueryService
from app.modules.planning.application.pog_tiles import PogTileLimits, PogTileService
from app.modules.planning.application.service import PlanningRuleService
from app.modules.planning.infrastructure.mvt import (
    InMemoryPogTileCache,
    SqlAlchemyPogTileRepository,
)
from app.modules.planning.infrastructure.pog_release_queries import (
    SqlAlchemyPogReleaseQueryRepository,
)
from app.modules.planning.infrastructure.repository import (
    SqlAlchemyPlanningRuleRepository,
)

PARSER_VERSION = "mpzp-rules/1.0"


def build_planning_rule_service(session: Session) -> PlanningRuleService:
    return PlanningRuleService(
        SqlAlchemyPlanningRuleRepository(session),
        parser_version=PARSER_VERSION,
    )


@lru_cache(maxsize=1)
def pog_tile_cache() -> InMemoryPogTileCache:
    """Cache procesu API; kafle są niezmienne dla ``release_id`` i schematu."""
    return InMemoryPogTileCache(settings.pog_tile_cache_max_bytes)


def build_pog_tile_service(session: Session) -> PogTileService:
    return PogTileService(
        SqlAlchemyPogTileRepository(
            session, statement_timeout_ms=settings.pog_tile_statement_timeout_ms
        ),
        pog_tile_cache(),
        PogTileLimits(
            min_zoom=settings.pog_tile_min_zoom,
            max_zoom=settings.pog_tile_max_zoom,
            max_features=settings.pog_tile_max_features,
            max_bytes=settings.pog_tile_max_bytes,
        ),
        source_id=settings.pog_tile_source_id,
    )


def build_pog_release_query_service(session: Session) -> PogReleaseQueryService:
    """Inspektor obiektu (BK-404) i agregaty stref (BK-405) przypięte do wydania."""
    return PogReleaseQueryService(SqlAlchemyPogReleaseQueryRepository(session))
