"""
Entity Activity Tracker Service - Extract and track entity activities from documents.

This service analyzes documents to extract entity activities (meetings, commits, documents)
and provides timeline and statistics for entity profiles.
"""

import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ..models import EntityActivity, EntityStatistics
from .file_cache import file_cache
from .frontmatter import frontmatter
from .knowledge_graph import KnowledgeGraph

logger = logging.getLogger(__name__)


class EntityActivityTracker:
    """Track and analyze entity activities across documents."""

    def __init__(self, knowledge_graph: KnowledgeGraph | None = None):
        self.knowledge_graph = knowledge_graph or KnowledgeGraph()
        self.activity_cache: dict[str, list[EntityActivity]] = {}
        self.statistics_cache: dict[str, EntityStatistics] = {}
        self._cache_ttl = timedelta(minutes=15)
        self._last_cache_time = datetime.utcnow()

    async def get_entity_activities(
        self,
        entity_id: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        activity_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[EntityActivity]:
        """Get activities for an entity from documents."""

        # Check cache
        if self._is_cache_valid() and entity_id in self.activity_cache:
            activities = self.activity_cache[entity_id]
        else:
            activities = await self._extract_entity_activities(entity_id)
            self.activity_cache[entity_id] = activities

        # Filter by date range
        if start_date:
            activities = [a for a in activities if a.activity_date >= start_date]
        if end_date:
            activities = [a for a in activities if a.activity_date <= end_date]

        # Filter by activity type
        if activity_types:
            activities = [a for a in activities if a.activity_type in activity_types]

        # Sort by date (newest first) and limit
        activities.sort(key=lambda a: a.activity_date, reverse=True)
        return activities[:limit]

    async def get_entity_statistics(self, entity_id: str) -> EntityStatistics:
        """Calculate statistics for an entity."""

        # Check cache
        if self._is_cache_valid() and entity_id in self.statistics_cache:
            return self.statistics_cache[entity_id]

        # Signal-derived stats (ingest world): the ingest pipeline writes
        # Signal->Entity edges but no Document nodes, so the legacy
        # entity_documents path returns all-zero. Prefer signals when present,
        # falling back to the document-based path below for file/git-corpus KBs.
        signal_stats = None
        if hasattr(self.knowledge_graph, "get_entity_signal_stats"):
            # Timezone-aware so the ISO string carries a +00:00 offset, matching
            # the offset-aware signal source_timestamps. A naive cutoff would
            # mis-sort at the lexicographic boundary in get_entity_signal_stats.
            recent_cutoff_iso = (
                datetime.now(UTC) - timedelta(days=30)
            ).isoformat()
            try:
                signal_stats = await self.knowledge_graph.get_entity_signal_stats(
                    entity_id, recent_cutoff_iso
                )
            except Exception as e:
                logger.warning("Signal stats failed for %s: %s", entity_id, e)
                signal_stats = None

        if signal_stats and signal_stats.get("mention_count", 0) > 0:
            node = self.knowledge_graph.nodes.get(entity_id)
            relationship_count = len(node.connections) if node else 0
            last_activity = self._parse_date(signal_stats.get("last_ts"))
            stats = EntityStatistics(
                total_mentions=signal_stats["mention_count"],
                recent_mentions=signal_stats["recent_count"],
                document_count=signal_stats["document_count"],
                activity_count=signal_stats["mention_count"],
                relationship_count=relationship_count,
                last_activity=last_activity,
                quality_score=self._calculate_quality_score(
                    entity_id, signal_stats["mention_count"], relationship_count
                ),
                completeness_score=self._calculate_completeness_score(entity_id),
            )
            self.statistics_cache[entity_id] = stats
            return stats

        # Get all documents mentioning the entity
        doc_paths = self.knowledge_graph.entity_documents.get(entity_id, set())

        # Calculate mentions
        total_mentions = 0
        recent_mentions = 0
        recent_cutoff = datetime.utcnow() - timedelta(days=30)

        for doc_path in doc_paths:
            try:
                file_obj = await file_cache.get_file(doc_path)
                if not file_obj:
                    continue
                # file_cache.get_file returns a File model — extract the
                # raw markdown content. Treating the File as a string was a
                # latent bug: re.findall raised TypeError, caught silently,
                # and total_mentions stayed at 0 for every entity.
                content = file_obj.content if hasattr(file_obj, "content") else file_obj
                if not content:
                    continue

                # Count mentions (case-insensitive)
                entity_name = self._get_entity_name(entity_id)
                mentions = len(
                    re.findall(rf"\b{re.escape(entity_name)}\b", content, re.IGNORECASE)
                )
                total_mentions += mentions

                # Check if recent
                metadata = frontmatter.extract_all(content)
                if metadata:
                    doc_date = self._parse_date(
                        metadata.get("created") or metadata.get("date")
                    )
                    if doc_date and doc_date >= recent_cutoff:
                        recent_mentions += mentions

            except Exception as e:
                logger.error(f"Error processing document {doc_path}: {e}")
                continue

        # Get activities
        activities = await self._extract_entity_activities(entity_id)
        activity_count = len(activities)

        # Get last activity date
        last_activity = None
        if activities:
            last_activity = max(a.activity_date for a in activities)

        # Get relationships from knowledge graph
        node = self.knowledge_graph.nodes.get(entity_id)
        relationship_count = len(node.connections) if node else 0

        # Calculate quality scores
        quality_score = self._calculate_quality_score(
            entity_id, total_mentions, relationship_count
        )
        completeness_score = self._calculate_completeness_score(entity_id)

        stats = EntityStatistics(
            total_mentions=total_mentions,
            recent_mentions=recent_mentions,
            document_count=len(doc_paths),
            activity_count=activity_count,
            relationship_count=relationship_count,
            last_activity=last_activity,
            quality_score=quality_score,
            completeness_score=completeness_score,
        )

        self.statistics_cache[entity_id] = stats
        return stats

    async def get_entity_documents(
        self,
        entity_id: str,
        document_types: list[str] | None = None,
        sort_by: str = "relevance",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get documents associated with an entity."""

        doc_paths = self.knowledge_graph.entity_documents.get(entity_id, set())
        documents = []

        for doc_path in doc_paths:
            try:
                content = await file_cache.get_file(doc_path)
                if not content:
                    continue

                metadata = frontmatter.extract_all(content)
                if not metadata:
                    continue

                # Determine document type
                doc_type = self._determine_document_type(doc_path, metadata)

                # Filter by type if specified
                if document_types and doc_type not in document_types:
                    continue

                # Calculate relevance
                entity_name = self._get_entity_name(entity_id)
                mentions = len(
                    re.findall(rf"\b{re.escape(entity_name)}\b", content, re.IGNORECASE)
                )
                relevance = min(1.0, mentions * 0.1)  # Cap at 1.0

                # Get modification date
                last_modified = self._parse_date(
                    metadata.get("modified") or metadata.get("created")
                )
                if not last_modified:
                    last_modified = datetime.utcnow() - timedelta(days=30)  # Default

                documents.append(
                    {
                        "path": doc_path,
                        "type": doc_type,
                        "relevance": relevance,
                        "last_modified": last_modified,
                        "mentions": mentions,
                        "title": metadata.get("title", os.path.basename(doc_path)),
                    }
                )

            except Exception as e:
                logger.error(f"Error processing document {doc_path}: {e}")
                continue

        # Sort documents
        if sort_by == "relevance":
            documents.sort(key=lambda d: d["relevance"], reverse=True)
        elif sort_by == "date":
            documents.sort(key=lambda d: d["last_modified"], reverse=True)

        return documents[:limit]

    async def _extract_entity_activities(self, entity_id: str) -> list[EntityActivity]:
        """Extract activities for an entity from all documents."""

        activities = []
        doc_paths = self.knowledge_graph.entity_documents.get(entity_id, set())

        # Ingest world: no Document nodes — build activities from Signal edges.
        if not doc_paths and hasattr(self.knowledge_graph, "find_signals_for_entity"):
            return await self._extract_entity_activities_from_signals(entity_id)

        for doc_path in doc_paths:
            try:
                file_obj = await file_cache.get_file(doc_path)
                if not file_obj:
                    continue
                content = file_obj.content if hasattr(file_obj, "content") else file_obj
                if not content:
                    continue

                metadata = frontmatter.extract_all(content)
                if not metadata:
                    continue

                # Determine activity type based on document
                activity_type = self._determine_activity_type(doc_path, metadata)

                # Extract activity details
                activity = await self._create_activity_from_document(
                    entity_id, doc_path, content, metadata, activity_type
                )

                if activity:
                    activities.append(activity)

            except Exception as e:
                logger.error(f"Error extracting activity from {doc_path}: {e}")
                continue

        return activities

    async def _extract_entity_activities_from_signals(
        self, entity_id: str
    ) -> list[EntityActivity]:
        """Build activity records from Signal->Entity edges (ingest world).

        The document-based activity path needs Document nodes / markdown files,
        which the ingest pipeline never creates. Signals carry the same
        information (type, timestamp, content, source meeting), so we map each
        signal that mentions the entity to an EntityActivity.
        """
        try:
            signals = await self.knowledge_graph.find_signals_for_entity(
                entity_id, limit=100
            )
        except Exception as e:
            logger.error(f"Signal activity fetch failed for {entity_id}: {e}")
            return []

        activities: list[EntityActivity] = []
        for sig in signals:
            activity_date = self._parse_date(sig.get("source_timestamp"))
            if not activity_date:
                continue
            # Defensive: confidence is normally a float, but a malformed value
            # (e.g. "high") must not abort the whole activity list — fall back.
            try:
                relevance_score = float(sig.get("confidence", 0.8))
            except (TypeError, ValueError):
                relevance_score = 0.8
            activities.append(
                EntityActivity(
                    entity_id=entity_id,
                    activity_type=sig.get("type") or "signal",
                    activity_date=activity_date,
                    description=sig.get("content", ""),
                    document_path=sig.get("source_meeting_id")
                    or f"signal:{sig.get('id', '')}",
                    relevance_score=relevance_score,
                    metadata={
                        "signal_id": sig.get("id", ""),
                        "meeting_title": sig.get("source_meeting_title", ""),
                    },
                )
            )
        return activities

    async def _create_activity_from_document(
        self,
        entity_id: str,
        doc_path: str,
        content: str,
        metadata: dict[str, Any],
        activity_type: str,
    ) -> EntityActivity | None:
        """Create an activity record from a document."""

        # Get activity date
        activity_date = self._parse_date(
            metadata.get("date")
            or metadata.get("created")
            or metadata.get("meeting_date")
        )

        if not activity_date:
            return None

        # Generate description based on activity type
        description = self._generate_activity_description(
            entity_id, doc_path, content, metadata, activity_type
        )

        # Calculate relevance
        entity_name = self._get_entity_name(entity_id)
        mentions = len(
            re.findall(rf"\b{re.escape(entity_name)}\b", content, re.IGNORECASE)
        )
        relevance_score = min(1.0, mentions * 0.15)

        # Extract activity-specific metadata
        activity_metadata = self._extract_activity_metadata(
            content, metadata, activity_type
        )

        return EntityActivity(
            entity_id=entity_id,
            activity_type=activity_type,
            activity_date=activity_date,
            description=description,
            document_path=doc_path,
            relevance_score=relevance_score,
            metadata=activity_metadata,
        )

    def _determine_activity_type(self, doc_path: str, metadata: dict[str, Any]) -> str:
        """Determine the activity type from document path and metadata."""

        # Check metadata type field
        doc_type = metadata.get("type", "").lower()

        if "meeting" in doc_type or "meeting" in doc_path.lower():
            return "meeting"
        elif "commit" in doc_type or "commits/" in doc_path:
            return "commit"
        elif "project" in doc_type:
            return "project"
        elif "decision" in doc_type:
            return "decision"
        else:
            return "document"

    def _determine_document_type(self, doc_path: str, metadata: dict[str, Any]) -> str:
        """Determine document type for filtering."""

        doc_type = metadata.get("type", "").lower()

        if "meeting" in doc_type or "meeting" in doc_path.lower():
            return "meeting"
        elif "commit" in doc_type or "commits/" in doc_path:
            return "commit"
        elif "project" in doc_type:
            return "project"
        elif "analysis" in doc_type:
            return "analysis"
        else:
            return "document"

    def _generate_activity_description(
        self,
        entity_id: str,
        doc_path: str,
        content: str,
        metadata: dict[str, Any],
        activity_type: str,
    ) -> str:
        """Generate a description for the activity."""

        entity_name = self._get_entity_name(entity_id)

        if activity_type == "meeting":
            # Extract meeting context
            attendees = metadata.get("attendees", [])
            if entity_name in str(attendees):
                if metadata.get("organizer") == entity_name:
                    return f"Led {metadata.get('title', 'meeting')}"
                else:
                    return f"Participated in {metadata.get('title', 'meeting')}"
            else:
                return f"Mentioned in {metadata.get('title', 'meeting')}"

        elif activity_type == "commit":
            # Extract commit message
            commit_msg = metadata.get("message", "")
            if commit_msg:
                return f"Commit: {commit_msg[:100]}"
            else:
                return "Made code changes"

        elif activity_type == "project":
            return f"Involved in {metadata.get('title', 'project work')}"

        elif activity_type == "decision":
            return f"Decision: {metadata.get('title', 'Made decision')}"

        else:
            return f"Mentioned in {metadata.get('title', os.path.basename(doc_path))}"

    def _extract_activity_metadata(
        self, content: str, metadata: dict[str, Any], activity_type: str
    ) -> dict[str, Any]:
        """Extract activity-specific metadata."""

        activity_metadata = {}

        if activity_type == "meeting":
            activity_metadata["attendees"] = len(metadata.get("attendees", []))
            activity_metadata["duration"] = metadata.get("duration", 60)
            activity_metadata["organizer"] = metadata.get("organizer")

        elif activity_type == "commit":
            activity_metadata["files_changed"] = metadata.get("files_changed", 0)
            activity_metadata["lines_added"] = metadata.get("additions", 0)
            activity_metadata["lines_deleted"] = metadata.get("deletions", 0)

        elif activity_type == "project":
            activity_metadata["project_name"] = metadata.get("project")
            activity_metadata["role"] = metadata.get("role")

        return activity_metadata

    def _get_entity_name(self, entity_id: str) -> str:
        """Get entity name from ID."""

        # Try to get from knowledge graph
        node = self.knowledge_graph.nodes.get(entity_id)
        if node:
            return node.name

        # Extract from ID (format: type:name)
        parts = entity_id.split(":", 1)
        if len(parts) == 2:
            return parts[1].replace("-", " ").title()

        return entity_id

    def _parse_date(self, date_str: str | None) -> datetime | None:
        """Parse date string to datetime."""

        if not date_str:
            return None

        # Try common formats
        formats = [
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%fZ",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue

        # Try parsing as ISO format
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            return None

    def _calculate_quality_score(
        self, entity_id: str, total_mentions: int, relationship_count: int
    ) -> float:
        """Calculate quality score for an entity."""

        # Base score on mentions and relationships
        mention_score = min(1.0, total_mentions / 100.0)
        relationship_score = min(1.0, relationship_count / 20.0)

        # Weight the scores
        quality_score = (mention_score * 0.6) + (relationship_score * 0.4)

        return round(quality_score, 2)

    def _calculate_completeness_score(self, entity_id: str) -> float:
        """Calculate completeness score for an entity."""

        # Check what fields are populated
        # This is simplified - in reality would check entity attributes
        node = self.knowledge_graph.nodes.get(entity_id)
        if not node:
            return 0.0

        # Check metadata completeness
        metadata = node.metadata
        required_fields = ["name", "type", "created"]
        optional_fields = ["description", "email", "role", "department"]

        required_complete = sum(1 for f in required_fields if metadata.get(f))
        optional_complete = sum(1 for f in optional_fields if metadata.get(f))

        # Calculate score
        required_score = (
            required_complete / len(required_fields) if required_fields else 1.0
        )
        optional_score = (
            optional_complete / len(optional_fields) if optional_fields else 0.5
        )

        completeness_score = (required_score * 0.7) + (optional_score * 0.3)

        return round(completeness_score, 2)

    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid."""
        return (datetime.utcnow() - self._last_cache_time) < self._cache_ttl

    def clear_cache(self):
        """Clear all caches."""
        self.activity_cache.clear()
        self.statistics_cache.clear()
        self._last_cache_time = datetime.utcnow()
