"""
Api models package.

This package provides backward compatibility for all API models that were
previously in core.py. Models are now organized into domain-specific modules.
"""

# Knowledge & Document Operations
# Also export enums for backward compatibility
from ..types import (
    CorrelationType,
    DomainEntityRelationship,
    EntityType,
    ExtractionPriority,
    MetricType,
    ObjectiveStatus,
    PatternTrigger,
    PatternType,
    ProcessingStatus,
    SuccessMetric,
)

# Agent & Workflow Operations
from .agents import (
    ChatMessageRequest,
    ObjectiveExecution,
    ObjectivePerformanceReport,
    ObjectiveTemplate,
    PatternAnalysis,
    ToolExecution,
    WorkflowExecution,
)

# Diff models (separate file)
from .diff import DiffExplanationCache

# Entity Management
from .entities import (
    BulkEnrichmentRequest,
    BulkEnrichmentResponse,
    BulkMergeOperation,
    BulkMergeResponse,
    BulkValidationRequest,
    BulkValidationResponse,
    CanonicalPerson,
    CanonicalProject,
    CanonicalTeam,
    EnrichedEntity,
    EnrichedEntityRelationship,
    EnrichedEntityWithContext,
    EnrichmentOptions,
    EntityActivity,
    EntityAliasRequest,
    EntityAliasResponse,
    EntityArchiveResponse,
    EntityAutocompleteResponse,
    EntityDocumentsResponse,
    EntityExtraction,
    EntityInsight,
    EntityMergeRequest,
    EntityMergeResponse,
    EntityProfileResponse,
    EntitySearchResult,
    EntityStatistics,
    EntityUpdateRequest,
    EntityUpdateResponse,
    ExportRequest,
    ExportResponse,
    ImportResponse,
    ProfileEntityRelationship,
)
from .knowledge import (
    DiffExplanationRequest,
    DiffExplanationResponse,
    DigestRequest,
    DigestResponse,
    DocumentMetadata,
    EnhancedDocumentMetadata,
    File,
    KnowledgeResponse,
    MetadataResponse,
    ProcessingResult,
    QueryRequest,
    QueryResponse,
    UploadResponse,
)

# System Utilities
from .system import HealthResponse, ModeSettings, PaginationInfo, ReinitializeResponse

__all__ = [
    # Knowledge models
    "File",
    "KnowledgeResponse",
    "QueryRequest",
    "QueryResponse",
    "HealthResponse",
    "ReinitializeResponse",
    "DocumentMetadata",
    "DigestRequest",
    "DigestResponse",
    "ProcessingStatus",
    "ProcessingResult",
    "UploadResponse",
    "DiffExplanationRequest",
    "DiffExplanationResponse",
    "ObjectiveStatus",
    "EntityType",
    "EntityExtraction",
    "EnhancedDocumentMetadata",
    "MetadataResponse",
    # Entity models
    "CanonicalPerson",
    "CanonicalProject",
    "CanonicalTeam",
    "EntitySearchResult",
    "EntityAutocompleteResponse",
    "EntityActivity",
    "ProfileEntityRelationship",
    "EntityInsight",
    "EntityStatistics",
    "EnrichedEntity",
    "EntityProfileResponse",
    "EntityDocumentsResponse",
    "EntityMergeRequest",
    "EntityMergeResponse",
    "EntityAliasRequest",
    "EntityAliasResponse",
    "EntityUpdateRequest",
    "EntityUpdateResponse",
    "EntityArchiveResponse",
    "BulkMergeOperation",
    "BulkMergeResponse",
    "BulkValidationRequest",
    "BulkValidationResponse",
    "EnrichmentOptions",
    "BulkEnrichmentRequest",
    "BulkEnrichmentResponse",
    "ImportResponse",
    "ExportRequest",
    "ExportResponse",
    # Agent models
    "ObjectiveExecution",
    "ObjectiveTemplate",
    "ObjectivePerformanceReport",
    "ToolExecution",
    "WorkflowExecution",
    "ChatMessageRequest",
    "EnrichedEntityRelationship",
    "EnrichedEntityWithContext",
    "ModeSettings",
    "PatternAnalysis",
    "PatternType",
    "MetricType",
    "DomainEntityRelationship",
    "PatternTrigger",
    "ExtractionPriority",
    "SuccessMetric",
    "CorrelationType",
    "PaginationInfo",
    # Diff models
    "DiffExplanationCache",
]
