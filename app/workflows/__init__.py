"""
Workflow Package - Explicit workflow patterns for common operations.

This module provides standardized workflows that chain together multiple tools
to accomplish common tasks like processing meeting notes, analyzing documents,
and enriching commits.
"""

from .base import WorkflowBase, WorkflowResult
from .commit_enricher import CommitEnricherWorkflow
from .document_analyzer import DocumentAnalyzerWorkflow

__all__ = [
    "WorkflowBase",
    "WorkflowResult",
    "DocumentAnalyzerWorkflow",
    "CommitEnricherWorkflow",
]
