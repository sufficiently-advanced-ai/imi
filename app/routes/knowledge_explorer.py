"""Knowledge Explorer - Unified Search & Browse API routes."""

import logging
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..git_ops import git_ops
from ..services.auth import get_current_user
from ..services.frontmatter import FrontmatterService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge-explorer", tags=["knowledge-explorer"])


# ──────────────────────────────────────────────
# Response Models
# ──────────────────────────────────────────────


class SearchResult(BaseModel):
    """Single search result across any category."""

    id: str
    title: str
    category: str  # entities, meetings, documents
    entity_type: str | None = None
    snippet: str | None = None
    date: str | None = None
    relevance_score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_path: str | None = None


class SearchTotals(BaseModel):
    entities: int = 0
    meetings: int = 0


class Pagination(BaseModel):
    page: int = 1
    page_size: int = 50
    total_results: int = 0
    total_pages: int = 0
    has_more: bool = False


class SearchResponse(BaseModel):
    results: list[SearchResult]
    totals: SearchTotals
    pagination: Pagination
    query: str
    search_time_ms: float = 0.0


class EntityTypeCounts(BaseModel):
    counts: dict[str, int] = Field(default_factory=dict)


class StatsResponse(BaseModel):
    total_entities: int = 0
    total_meetings: int = 0
    entity_type_counts: dict[str, int] = Field(default_factory=dict)
    date_range: dict[str, str | None] = Field(default_factory=dict)


class FileContentResponse(BaseModel):
    title: str
    body: str  # Full markdown body (frontmatter stripped)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RelatedEntity(BaseModel):
    id: str
    name: str
    entity_type: str
    relationship_type: str | None = None


class EntitySummary(BaseModel):
    name: str
    entity_type: str
    relationship_count: int = 0
    last_activity: str | None = None
    top_related: list[RelatedEntity] = Field(default_factory=list)
    snippet: str | None = None


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────


def _extract_frontmatter(content: str) -> dict[str, Any]:
    """Extract YAML frontmatter from markdown content."""
    try:
        return FrontmatterService.extract_all(content) or {}
    except Exception:
        return {}


def _extract_body(content: str) -> str:
    """Extract full markdown body, stripping frontmatter."""
    lines = content.split("\n")
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break
    return "\n".join(lines[start:]).strip()


def _make_snippet(content: str, max_len: int = 200) -> str:
    """Extract a clean snippet from markdown content, stripping frontmatter."""
    lines = content.split("\n")
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break
    body = "\n".join(lines[start:]).strip()
    # Remove markdown headers
    clean_lines = []
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            clean_lines.append(stripped)
        if len(" ".join(clean_lines)) > max_len:
            break
    text = " ".join(clean_lines)
    return text[:max_len] + "..." if len(text) > max_len else text


def _matches_query(query_lower: str, title: str, snippet: str | None, path: str) -> float:
    """Score how well an item matches a text query. Returns 0.0 if no match."""
    title_lower = title.lower()
    path_lower = path.lower()

    # Exact title match
    if query_lower == title_lower:
        return 1.0

    # Title contains query
    if query_lower in title_lower:
        return 0.8 + (len(query_lower) / max(len(title_lower), 1)) * 0.15

    # Path contains query
    if query_lower in path_lower:
        return 0.5

    # Snippet contains query
    if snippet and query_lower in snippet.lower():
        return 0.4

    # Word-level matching
    query_words = set(query_lower.split())
    title_words = set(title_lower.split())
    common = query_words & title_words
    if common:
        return 0.3 + (len(common) / max(len(query_words), 1)) * 0.3

    return 0.0


def _build_domain_folder_map() -> dict[str, str]:
    """Build a folder-name -> entity_type mapping from the active domain config.

    Maps both the plural form and hyphenated variants so that repo folders like
    ``members/``, ``focus-areas/``, ``cohorts/`` resolve to their entity types.
    """
    folder_map: dict[str, str] = {}
    try:
        from app.core.dependencies import get_domain_config_service

        config = get_domain_config_service().get_active_domain()
        if config and config.entities:
            for entity_id, entity_cfg in config.entities.items():
                # Map plural form: "members" -> "member"
                plural = getattr(entity_cfg, "plural", None) or entity_id + "s"
                folder_map[plural] = entity_id
                # Also map hyphenated variant: "focus-areas" -> "focus_area"
                hyphenated = plural.replace("_", "-")
                if hyphenated != plural:
                    folder_map[hyphenated] = entity_id
                # Map the entity id itself as a folder name
                folder_map[entity_id] = entity_id
                hyphenated_id = entity_id.replace("_", "-")
                if hyphenated_id != entity_id:
                    folder_map[hyphenated_id] = entity_id
    except Exception as e:
        logger.debug(f"Could not build domain folder map: {e}")
    return folder_map


def _classify_file(path: str) -> tuple[str, str | None] | None:
    """Classify a file path into (category, entity_type).

    Returns ("entities", "person"), ("meetings", None), or None to skip the file.
    Handles new-style (entities/person/x.md), legacy, and domain-config folder layouts.
    """
    # New-style entity folders: entities/<type>/<name>.md
    if path.startswith("entities/"):
        parts = path.split("/")
        entity_type = parts[1] if len(parts) >= 3 else None
        return "entities", entity_type

    # Legacy entity folders mapped to entity types
    _legacy_folder_map: dict[str, str] = {
        "people": "person",
        "projects": "project",
        "teams": "team",
        "accounts": "account",
        "organizations": "organization",
    }

    top_folder = path.split("/")[0]

    if top_folder in _legacy_folder_map:
        return "entities", _legacy_folder_map[top_folder]

    # Domain-config-aware folder mapping (handles members/, focus-areas/, cohorts/, etc.)
    domain_map = _build_domain_folder_map()
    if top_folder in domain_map:
        return "entities", domain_map[top_folder]

    if path.startswith("meetings/"):
        return "meetings", None

    # Skip everything else (README, dotfiles, etc.)
    return None


_TECHNICAL_METADATA_FIELDS = {
    "meeting_id",
    "bot_id",
    "id",
    "update_count",
    "is_finalized",
    "updated_at",
    "type",
    "entities_mentioned",
    "meetings",
    "status",
}


def _humanize_title(file_path: str, content: str | None, fm: dict[str, Any], category: str) -> str:
    """Produce a human-readable title from frontmatter, content, or filename.

    Cascade:
    1. Frontmatter ``title`` field
    2. Frontmatter ``name`` field
    3. First ``# H1`` header from the markdown body
    4. Cleaned filename stem (strip entity prefixes, detect UUIDs)
    """
    # 1. Frontmatter title
    if fm.get("title"):
        return str(fm["title"])

    # 2. Frontmatter name
    if fm.get("name"):
        return str(fm["name"])

    # 3. First H1 from body
    if content:
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("## "):
                return stripped[2:].strip()

    # 4. Cleaned stem fallback
    stem = Path(file_path).stem

    # Strip known entity-type prefixes
    for prefix in ("person-", "project-", "account-", "team-", "organization-", "meeting-"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break

    # If remaining stem looks like a UUID, use a generic label with date
    if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-", stem):
        label = "Meeting" if category == "meetings" else "Entity"
        date_val = fm.get("updated_at") or fm.get("date")
        if date_val:
            try:
                dt = datetime.fromisoformat(str(date_val).replace("Z", "+00:00"))
                return f"{label} - {dt.strftime('%b %d, %Y')}"
            except (ValueError, TypeError):
                pass
        return label

    # Otherwise, replace hyphens with spaces and title-case
    return stem.replace("-", " ").title()


_RELATIONSHIP_FIELDS = {
    "collaborates_with",
    "works_on_projects",
    "member_of_team",
    "belongs_to_account",
    "has_contacts",
    "has_projects",
    "has_members",
    "managed_by",
    "discussed_topic",
}


def _extract_relationships(fm: dict[str, Any]) -> tuple[int, list[RelatedEntity]]:
    """Extract relationship count and top related entities from frontmatter."""
    all_related: list[RelatedEntity] = []
    for field, value in fm.items():
        if field not in _RELATIONSHIP_FIELDS:
            continue
        items = value if isinstance(value, list) else [value]
        rel_type = field.replace("_", " ")
        for entity_id in items:
            if not isinstance(entity_id, str):
                continue
            # Parse entity type and name from ID like "person-chris"
            parts = entity_id.split("-", 1)
            if len(parts) == 2:
                etype, name = parts
                display_name = name.replace("-", " ").title()
            else:
                etype = "unknown"
                display_name = entity_id.replace("-", " ").title()
            all_related.append(
                RelatedEntity(
                    id=entity_id,
                    name=display_name,
                    entity_type=etype,
                    relationship_type=rel_type,
                )
            )
    return len(all_related), all_related[:5]


def _get_last_commit_date(file_path: str) -> datetime | None:
    """Get the most recent git commit date for a file."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", file_path],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stdout.strip():
            return datetime.fromtimestamp(int(result.stdout.strip()))
    except Exception:
        pass
    return None


def _build_last_commit_map() -> dict[str, datetime]:
    """Build a map of file path -> last commit date with a single git command."""
    try:
        result = subprocess.run(
            ["git", "log", "--format=COMMIT %ct", "--name-only"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        last_dates: dict[str, datetime] = {}
        current_ts: int | None = None
        for line in result.stdout.split("\n"):
            line = line.strip()
            if line.startswith("COMMIT "):
                current_ts = int(line.split(" ", 1)[1])
            elif line and current_ts is not None:
                # Only keep the first occurrence (most recent commit)
                if line not in last_dates:
                    last_dates[line] = datetime.fromtimestamp(current_ts)
        return last_dates
    except Exception:
        return {}


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────


@router.get("/search", response_model=SearchResponse)
async def search_knowledge(
    query: str = Query("", description="Search query text"),
    categories: str | None = Query(None, description="Comma-separated: entities,meetings,documents"),
    entity_types: str | None = Query(None, description="Comma-separated entity types to include"),
    date_from: str | None = Query(None, description="ISO date string for start of range"),
    date_to: str | None = Query(None, description="ISO date string for end of range"),
    sort_by: str = Query("relevance", description="Sort by: relevance or date"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(50, ge=1, le=100, description="Results per page"),
    user: dict = Depends(get_current_user),
):
    """Unified search across entities and meetings."""
    start_time = time.time()
    logger.info(f"Knowledge explorer search: query='{query}' user={user.get('email', 'unknown')}")

    # Parse filter parameters
    category_set = (
        set(c.strip() for c in categories.split(",")) if categories else {"entities", "meetings"}
    )
    entity_type_set = set(t.strip() for t in entity_types.split(",")) if entity_types else None

    date_from_dt = None
    date_to_dt = None
    try:
        if date_from:
            date_from_dt = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
        if date_to:
            date_to_dt = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
    except ValueError:
        pass  # Ignore invalid date formats

    try:
        all_files = await git_ops.read_markdown_files()
    except Exception as e:
        logger.error(f"Failed to read markdown files: {e}")
        raise HTTPException(status_code=500, detail="Failed to read knowledge base")

    results: list[SearchResult] = []
    totals = SearchTotals()  # Cross-category totals (ignores category filter)
    query_lower = query.lower().strip()
    commit_dates = _build_last_commit_map()

    for file in all_files:
        classified = _classify_file(file.path)
        if classified is None:
            continue  # Skip non-entity/meeting files
        category, etype = classified

        # Entity type filter (applies regardless of category filter)
        if category == "entities" and entity_type_set and etype and etype not in entity_type_set:
            continue

        # Date range filter (prefer git commit date over filesystem mtime)
        file_date = commit_dates.get(file.path) or file.modified_at
        if date_from_dt and file_date < date_from_dt:
            continue
        if date_to_dt and file_date > date_to_dt:
            continue

        # Extract metadata
        fm = {}
        snippet = None
        if file.content:
            fm = _extract_frontmatter(file.content)
            snippet = _make_snippet(file.content)
        title = _humanize_title(file.path, file.content, fm, category)

        # Score relevance
        if query_lower:
            score = _matches_query(query_lower, title, snippet, file.path)
            if score == 0.0:
                # Also check frontmatter fields
                fm_text = " ".join(str(v) for v in fm.values() if isinstance(v, str))
                if query_lower in fm_text.lower():
                    score = 0.3
                else:
                    continue  # No match at all
        else:
            # No query = browse mode, score by recency
            score = 0.5

        # Count totals across ALL categories (for tab badge counts)
        if category == "entities":
            totals.entities += 1
        elif category == "meetings":
            totals.meetings += 1
        # Category filter: count in totals above, but only include in results if matching
        if category not in category_set:
            continue

        file_date_dt = commit_dates.get(file.path) or file.modified_at
        results.append(
            SearchResult(
                id=file.path,
                title=title,
                category=category,
                entity_type=etype,
                snippet=snippet,
                date=file_date_dt.isoformat(),
                relevance_score=score,
                metadata={k: v for k, v in fm.items() if isinstance(v, (str, int, float, bool)) and k not in _TECHNICAL_METADATA_FIELDS},
                source_path=None,
            )
        )

    # Sort
    if sort_by == "date":
        results.sort(key=lambda r: r.date or "", reverse=True)
    else:
        results.sort(key=lambda r: r.relevance_score, reverse=True)

    # Paginate
    total_results = len(results)
    total_pages = max(1, (total_results + page_size - 1) // page_size)
    start = (page - 1) * page_size
    end = start + page_size
    paginated = results[start:end]

    search_time_ms = (time.time() - start_time) * 1000
    return SearchResponse(
        results=paginated,
        totals=totals,
        pagination=Pagination(
            page=page,
            page_size=page_size,
            total_results=total_results,
            total_pages=total_pages,
            has_more=page < total_pages,
        ),
        query=query,
        search_time_ms=search_time_ms,
    )


@router.get("/stats", response_model=StatsResponse)
async def get_knowledge_stats(user: dict = Depends(get_current_user)):
    """Get aggregate counts for the knowledge base."""
    logger.info(f"Knowledge explorer stats requested by {user.get('email', 'unknown')}")

    try:
        all_files = await git_ops.read_markdown_files()
    except Exception as e:
        logger.error(f"Failed to read files for stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to read knowledge base")

    total_entities = 0
    total_meetings = 0
    entity_type_counts: dict[str, int] = {}
    earliest_date: datetime | None = None
    latest_date: datetime | None = None
    commit_dates = _build_last_commit_map()

    for file in all_files:
        classified = _classify_file(file.path)
        if classified is None:
            continue
        category, etype = classified

        if category == "entities":
            total_entities += 1
            if etype:
                entity_type_counts[etype] = entity_type_counts.get(etype, 0) + 1
        elif category == "meetings":
            total_meetings += 1

        file_dt = commit_dates.get(file.path) or file.modified_at
        if earliest_date is None or file_dt < earliest_date:
            earliest_date = file_dt
        if latest_date is None or file_dt > latest_date:
            latest_date = file_dt

    return StatsResponse(
        total_entities=total_entities,
        total_meetings=total_meetings,
        entity_type_counts=entity_type_counts,
        date_range={
            "earliest": earliest_date.isoformat() if earliest_date else None,
            "latest": latest_date.isoformat() if latest_date else None,
        },
    )


@router.get("/entity/{entity_id:path}/summary", response_model=EntitySummary)
async def get_entity_summary(
    entity_id: str,
    user: dict = Depends(get_current_user),
):
    """Get a lightweight entity preview for the detail sheet."""
    logger.info(f"Entity summary requested: {entity_id} by {user.get('email', 'unknown')}")

    # Try to find the entity file in the knowledge base
    try:
        all_files = await git_ops.read_markdown_files()
    except Exception as e:
        logger.error(f"Failed to read files: {e}")
        raise HTTPException(status_code=500, detail="Failed to read knowledge base")

    # entity_id could be a path like "entities/person/john-doe.md"
    target_file = None
    for file in all_files:
        if file.path == entity_id:
            target_file = file
            break

    if not target_file:
        raise HTTPException(status_code=404, detail="Entity not found")

    category, etype = _classify_file(target_file.path) or ("entities", None)
    snippet = None
    fm: dict[str, Any] = {}

    if target_file.content:
        fm = _extract_frontmatter(target_file.content)
        snippet = _make_snippet(target_file.content, max_len=300)
    title = _humanize_title(target_file.path, target_file.content, fm, category)

    # Extract relationships from frontmatter
    relationship_count, top_related = _extract_relationships(fm)

    # Get last activity from git history (actual commit date, not filesystem mtime)
    last_activity_dt = _get_last_commit_date(target_file.path)
    last_activity = (
        last_activity_dt.isoformat()
        if last_activity_dt
        else target_file.modified_at.isoformat()
    )

    return EntitySummary(
        name=title,
        entity_type=etype or "unknown",
        relationship_count=relationship_count,
        last_activity=last_activity,
        top_related=top_related,
        snippet=snippet,
    )


@router.get("/file/{file_path:path}", response_model=FileContentResponse)
async def get_file_content(
    file_path: str,
    user: dict = Depends(get_current_user),
):
    """Get full file content for the detail sheet."""
    logger.info(f"File content requested: {file_path} by {user.get('email', 'unknown')}")

    try:
        all_files = await git_ops.read_markdown_files()
    except Exception as e:
        logger.error(f"Failed to read files: {e}")
        raise HTTPException(status_code=500, detail="Failed to read knowledge base")

    target_file = None
    for file in all_files:
        if file.path == file_path:
            target_file = file
            break

    if not target_file:
        raise HTTPException(status_code=404, detail="File not found")

    body = ""
    fm: dict[str, Any] = {}
    category = (_classify_file(target_file.path) or ("entities", None))[0]

    if target_file.content:
        fm = _extract_frontmatter(target_file.content)
        body = _extract_body(target_file.content)
    title = _humanize_title(target_file.path, target_file.content, fm, category)

    return FileContentResponse(
        title=title,
        body=body,
        metadata={k: v for k, v in fm.items() if isinstance(v, (str, int, float, bool)) and k not in _TECHNICAL_METADATA_FIELDS},
    )
