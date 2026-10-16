from app.models.analysis import Analysis
from app.models.analysis_pending_document import AnalysisPendingDocument
from app.models.bdot10k import BdotBuilding, BdotRelease, BdotRoad
from app.models.infrastructure import Infrastructure
from app.models.mpzp_llm_extraction import MpzpLlmExtraction
from app.models.mpzp_llm_usage import MpzpLlmUsage
from app.models.mpzp_parameter import MpzpParameter
from app.models.mpzp_zone import MpzpZone
from app.models.parcel import Parcel
from app.models.pog_data import PogData
from app.models.pog_registry import (
    ImportJob,
    PogMapAct,
    PogMapOverlay,
    PogMapZone,
    PogRegistryEntry,
    PogRegistryState,
    PogRuCatalogEntry,
)
from app.models.risk import Risk
from app.models.source_record import SourceRecord
from app.models.versioned import (
    DataRelease,
    DataSource,
    DocumentPage,
    DocumentVersion,
    ImportRun,
    LandUseArea,
    LegalUnit,
    ManualReview,
    ParcelVersion,
    PlanBoundary,
    PlanningAct,
    PlanningActVersion,
    PlanningFeature,
    PogActMetadataRecord,
    PogAreaSummary,
    PogAreaSummaryZone,
    PogFormalDocument,
    PlanningRule,
    PlanningSymbol,
    RasterAsset,
    SourceArtifact,
    SourceDocument,
    SymbolLegalUnit,
)
from app.modules.location.infrastructure.models import AddressSearchEntry

__all__ = [
    "Analysis",
    "AnalysisPendingDocument",
    "BdotBuilding",
    "BdotRelease",
    "BdotRoad",
    "Infrastructure",
    "MpzpLlmExtraction",
    "MpzpLlmUsage",
    "MpzpParameter",
    "MpzpZone",
    "Parcel",
    "PogData",
    "ImportJob",
    "PogMapAct",
    "PogMapOverlay",
    "PogMapZone",
    "PogRegistryEntry",
    "PogRegistryState",
    "PogRuCatalogEntry",
    "Risk",
    "SourceRecord",
    # Wersjonowany model danych źródłowych (Faza 10.3)
    "DataSource",
    "SourceArtifact",
    "DataRelease",
    "ImportRun",
    "ParcelVersion",
    "PlanningAct",
    "PlanningActVersion",
    "PlanBoundary",
    "LandUseArea",
    "PlanningSymbol",
    "PlanningFeature",
    "PogActMetadataRecord",
    "PogAreaSummary",
    "PogAreaSummaryZone",
    "PogFormalDocument",
    "PlanningRule",
    "RasterAsset",
    "SourceDocument",
    "DocumentVersion",
    "DocumentPage",
    "LegalUnit",
    "SymbolLegalUnit",
    "ManualReview",
    "AddressSearchEntry",
]
