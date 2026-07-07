"""API routes for accessing stored meeting analysis data"""

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..config import settings
from ..services.analysis_storage import AnalysisStorage

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


class AnalysisListResponse(BaseModel):
    """Response for paginated analysis list"""

    items: list[dict[str, Any]]
    total: int
    page: int
    limit: int


class CommitmentListResponse(BaseModel):
    """Response for commitment list"""

    month: str
    commitments: list[dict[str, Any]]


class PatternEvolutionResponse(BaseModel):
    """Response for pattern evolution"""

    pattern: str
    evolution: list[dict[str, Any]]
    trend: str | None = None


class DecisionListResponse(BaseModel):
    """Response for decision list"""

    month: str
    decisions: list[dict[str, Any]]


class AnalysisSummaryResponse(BaseModel):
    """Response for analysis summary statistics"""

    total_meetings: int
    total_commitments: int
    commitment_completion_rate: float
    average_quality_score: float
    detected_patterns: int


class BulkExportRequest(BaseModel):
    """Request for bulk export"""

    meeting_ids: list[str]
    format: str = "json"


class CommitmentUpdateRequest(BaseModel):
    """Request to update commitment status"""

    meeting_id: str
    commitment_text: str
    new_status: str


def get_storage() -> AnalysisStorage:
    """Get analysis storage instance"""
    return AnalysisStorage(base_path=settings.REPO_PATH)


@router.get("/meetings/{meeting_id}")
async def get_meeting_analysis(
    meeting_id: str, storage: AnalysisStorage = Depends(get_storage)
) -> dict[str, Any]:
    """Retrieve analysis for a specific meeting"""
    analysis = await storage.get_meeting_analysis(meeting_id)

    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    return analysis


@router.get("/meetings")
async def list_analyses(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    storage: AnalysisStorage = Depends(get_storage),
) -> AnalysisListResponse:
    """List meeting analyses with pagination"""
    # Get all analysis files
    analysis_dir = storage.base_path / "analysis" / "meetings"

    if not analysis_dir.exists():
        return AnalysisListResponse(items=[], total=0, page=page, limit=limit)

    # Get all JSON files
    all_files = sorted(
        analysis_dir.glob("*-analysis.json"),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )

    total = len(all_files)
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit

    # Load analyses for current page
    items = []
    for file_path in all_files[start_idx:end_idx]:
        try:
            with open(file_path) as f:
                analysis = json.load(f)
                # Add summary fields
                items.append(
                    {
                        "meeting_id": analysis["meeting_id"],
                        "timestamp": analysis["timestamp"],
                        "quality_score": analysis["quality_score"],
                        "commitment_count": len(analysis.get("commitments", [])),
                        "decision_count": len(analysis.get("decisions", [])),
                        "pattern_count": len(analysis.get("patterns", [])),
                    }
                )
        except Exception:
            continue

    return AnalysisListResponse(items=items, total=total, page=page, limit=limit)


@router.get("/commitments")
async def get_commitments(
    month: str = Query(..., regex="^\\d{4}-\\d{2}$"),
    person: str | None = None,
    status: str | None = None,
    storage: AnalysisStorage = Depends(get_storage),
) -> CommitmentListResponse:
    """Get commitments for a specific month with optional filters"""
    # Parse commitments from monthly file
    commitment_file = (
        storage.base_path / "analysis" / "commitments" / f"{month}-commitments.md"
    )

    if not commitment_file.exists():
        return CommitmentListResponse(month=month, commitments=[])

    # Parse markdown file to extract commitments
    commitments = []
    with open(commitment_file) as f:
        content = f.read()

    # Simple parsing logic - in production would use proper markdown parser
    current_meeting = None
    for line in content.split("\n"):
        if line.startswith("## meeting-"):
            current_meeting = line.replace("## ", "").strip()
        elif line.startswith("- **") and current_meeting:
            # Extract commitment details
            commitment_text = line.split("**")[1]

            # Parse details from subsequent lines
            commitment = {"text": commitment_text, "meeting_id": current_meeting}

            # Apply filters
            if person and person.lower() not in line.lower():
                continue
            if status and f"Status: {status}" not in content:
                continue

            commitments.append(commitment)

    return CommitmentListResponse(month=month, commitments=commitments)


@router.get("/patterns/evolution")
async def get_pattern_evolution(
    pattern: str = Query(...),
    start_month: str | None = None,
    end_month: str | None = None,
    storage: AnalysisStorage = Depends(get_storage),
) -> PatternEvolutionResponse:
    """Track pattern evolution over time"""
    # For now, return from current month
    current_month = datetime.utcnow().strftime("%Y-%m")
    evolution = await storage.get_pattern_evolution(pattern, current_month)

    # Calculate trend
    trend = None
    if len(evolution) >= 2:
        confidence_delta = evolution[-1]["confidence"] - evolution[0]["confidence"]
        if confidence_delta > 0.05:
            trend = "improving"
        elif confidence_delta < -0.05:
            trend = "declining"
        else:
            trend = "stable"

    return PatternEvolutionResponse(pattern=pattern, evolution=evolution, trend=trend)


@router.get("/decisions")
async def get_decisions(
    month: str = Query(..., regex="^\\d{4}-\\d{2}$"),
    impact: str | None = None,
    storage: AnalysisStorage = Depends(get_storage),
) -> DecisionListResponse:
    """Get decisions for a specific month"""
    decision_file = (
        storage.base_path / "analysis" / "decisions" / f"{month}-decisions.md"
    )

    if not decision_file.exists():
        return DecisionListResponse(month=month, decisions=[])

    # Parse decisions from markdown
    decisions = []
    with open(decision_file) as f:
        content = f.read()

    # Simple parsing - extract decision blocks
    current_meeting = None
    current_decision = None

    for line in content.split("\n"):
        if line.startswith("## meeting-"):
            current_meeting = line.replace("## ", "").strip()
        elif line.startswith("### ") and current_meeting:
            if current_decision:
                decisions.append(current_decision)
            current_decision = {
                "text": line.replace("### ", "").strip(),
                "meeting_id": current_meeting,
            }
        elif current_decision and line.startswith("**"):
            # Parse decision attributes
            if "Rationale:" in line:
                current_decision["rationale"] = line.split("Rationale:")[1].strip()
            elif "Impact:" in line:
                current_decision["impact"] = line.split("Impact:")[1].strip()

    if current_decision:
        decisions.append(current_decision)

    # Apply filters
    if impact:
        decisions = [
            d for d in decisions if d.get("impact", "").lower() == impact.lower()
        ]

    return DecisionListResponse(month=month, decisions=decisions)


@router.get("/summary")
async def get_analysis_summary(
    month: str = Query(..., regex="^\\d{4}-\\d{2}$"),
    storage: AnalysisStorage = Depends(get_storage),
) -> AnalysisSummaryResponse:
    """Get summary statistics for a month"""
    # Count meetings
    analysis_dir = storage.base_path / "analysis" / "meetings"
    month_pattern = month.replace("-", "")

    meeting_files = list(analysis_dir.glob(f"meeting-{month_pattern}*-analysis.json"))
    total_meetings = len(meeting_files)

    # Calculate statistics
    total_commitments = 0
    completed_commitments = 0
    total_quality_score = 0
    detected_patterns = set()

    for file_path in meeting_files:
        try:
            with open(file_path) as f:
                analysis = json.load(f)

            # Count commitments
            commitments = analysis.get("commitments", [])
            total_commitments += len(commitments)
            completed_commitments += sum(
                1 for c in commitments if c.get("status") == "completed"
            )

            # Sum quality scores
            total_quality_score += analysis.get("quality_score", 0)

            # Collect unique patterns
            for pattern in analysis.get("patterns", []):
                detected_patterns.add(pattern.get("pattern"))

        except Exception:
            continue

    completion_rate = (
        completed_commitments / total_commitments if total_commitments > 0 else 0
    )
    avg_quality = total_quality_score / total_meetings if total_meetings > 0 else 0

    return AnalysisSummaryResponse(
        total_meetings=total_meetings,
        total_commitments=total_commitments,
        commitment_completion_rate=completion_rate,
        average_quality_score=avg_quality,
        detected_patterns=len(detected_patterns),
    )


@router.post("/export")
async def bulk_export(
    request: BulkExportRequest, storage: AnalysisStorage = Depends(get_storage)
) -> dict[str, Any]:
    """Export multiple analyses"""
    analyses = []

    for meeting_id in request.meeting_ids:
        analysis = await storage.get_meeting_analysis(meeting_id)
        if analysis:
            analyses.append(analysis)

    return {"analyses": analyses, "format": request.format}


@router.get("/search")
async def search_analyses(
    q: str = Query(..., min_length=1), storage: AnalysisStorage = Depends(get_storage)
) -> dict[str, Any]:
    """Search across all analyses"""
    results = []
    analysis_dir = storage.base_path / "analysis" / "meetings"

    if not analysis_dir.exists():
        return {"results": results, "query": q}

    # Search through all analysis files
    for file_path in analysis_dir.glob("*-analysis.json"):
        try:
            with open(file_path) as f:
                analysis = json.load(f)

            # Search in various fields
            found = False
            match_context = ""
            match_type = ""

            # Search commitments
            for commitment in analysis.get("commitments", []):
                if q.lower() in commitment.get("text", "").lower():
                    found = True
                    match_context = commitment["text"]
                    match_type = "commitment"
                    break

            # Search decisions
            if not found:
                for decision in analysis.get("decisions", []):
                    if q.lower() in decision.get("text", "").lower():
                        found = True
                        match_context = decision["text"]
                        match_type = "decision"
                        break

            # Search patterns
            if not found:
                for pattern in analysis.get("patterns", []):
                    if q.lower() in pattern.get("pattern", "").lower():
                        found = True
                        match_context = pattern["pattern"]
                        match_type = "pattern"
                        break

            if found:
                results.append(
                    {
                        "meeting_id": analysis["meeting_id"],
                        "match_type": match_type,
                        "context": match_context,
                        "timestamp": analysis["timestamp"],
                    }
                )

        except Exception:
            continue

    return {"results": results, "query": q}


@router.patch("/commitments/update")
async def update_commitment_status(
    request: CommitmentUpdateRequest, storage: AnalysisStorage = Depends(get_storage)
) -> dict[str, Any]:
    """Update the status of a commitment"""
    # Load the analysis
    analysis = await storage.get_meeting_analysis(request.meeting_id)

    if not analysis:
        raise HTTPException(status_code=404, detail="Meeting analysis not found")

    # Find and update the commitment
    updated = False
    for commitment in analysis.get("commitments", []):
        if commitment.get("text") == request.commitment_text:
            commitment["status"] = request.new_status
            updated = True
            break

    if not updated:
        raise HTTPException(status_code=404, detail="Commitment not found")

    # Save updated analysis
    json_path = (
        storage.base_path
        / "analysis"
        / "meetings"
        / f"{request.meeting_id}-analysis.json"
    )
    with open(json_path, "w") as f:
        json.dump(analysis, f, indent=2)

    return {
        "success": True,
        "commitment": {"text": request.commitment_text, "status": request.new_status},
    }
