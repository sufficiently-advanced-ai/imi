"""
Semantica Decisions — Decision intelligence backed by Semantica ContextGraph.

Replaces:
- signal_promoter.py (decision recording)
- Parts of signal_store.py (decision queries)

Provides:
- Decision recording with context capture
- Precedent search (find similar past decisions)
- Decision chain tracing (causal analysis)
- Decision statistics and insights
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SemanticaDecisions:
    """Decision intelligence backed by Semantica ContextGraph."""

    def __init__(self, context_graph: Any):
        self.context = context_graph

    async def record_decision(
        self,
        category: str,
        content: str,
        reasoning: str = "",
        outcome: str = "",
        confidence: float = 0.8,
        decision_maker: str = "",
        entities: list[str] | None = None,
        meeting_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record a decision in the ContextGraph.

        Args:
            category: Decision category (e.g. 'strategy', 'technical', 'hiring').
            content: Decision content/description.
            reasoning: Reasoning behind the decision.
            outcome: Decision outcome.
            confidence: Confidence score (0.0-1.0).
            decision_maker: Who made the decision.
            entities: Related entity IDs.
            meeting_id: Source meeting ID if from a meeting.
            metadata: Additional metadata.

        Returns:
            Decision ID string.
        """
        try:
            extra_meta = metadata or {}
            if meeting_id:
                extra_meta["meeting_id"] = meeting_id

            decision_id = self.context.record_decision(
                category=category,
                scenario=content,
                reasoning=reasoning or content,
                outcome=outcome or "recorded",
                confidence=confidence,
                entities=entities,
                decision_maker=decision_maker,
                metadata=extra_meta,
            )

            logger.info(f"Recorded decision {decision_id} (category={category})")
            return decision_id

        except Exception as e:
            logger.error(f"Failed to record decision: {e}")
            raise

    async def find_precedents(
        self,
        query: str,
        category: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Find similar past decisions as precedents.

        Args:
            query: Search query describing the decision context.
            category: Optional category filter.
            limit: Maximum number of precedents to return.

        Returns:
            List of precedent decisions with similarity scores.
        """
        try:
            precedents = self.context.find_precedents(
                category=category or query,
                limit=limit,
            )

            return [
                {
                    "decision_id": p.get("decision_id", ""),
                    "category": p.get("category", ""),
                    "scenario": p.get("scenario", ""),
                    "reasoning": p.get("reasoning", ""),
                    "outcome": p.get("outcome", ""),
                    "confidence": p.get("confidence", 0.0),
                    "similarity_score": p.get("similarity_score", 0.0),
                    "timestamp": str(p.get("timestamp", "")),
                    "decision_maker": p.get("decision_maker", ""),
                }
                for p in precedents
            ]

        except Exception as e:
            logger.error(f"Failed to find precedents: {e}", exc_info=True)
            raise

    async def trace_decision_chain(
        self,
        decision_id: str,
    ) -> list[dict[str, Any]]:
        """Trace the causal chain of a decision.

        Args:
            decision_id: ID of the decision to trace.

        Returns:
            List of decisions in the causal chain, ordered by time.
        """
        try:
            result = self.context.trace_decision_causality(decision_id)

            chain = result.get("chain", result.get("decisions", []))
            return [
                {
                    "decision_id": d.get("decision_id", ""),
                    "category": d.get("category", ""),
                    "scenario": d.get("scenario", ""),
                    "reasoning": d.get("reasoning", ""),
                    "outcome": d.get("outcome", ""),
                    "confidence": d.get("confidence", 0.0),
                    "timestamp": str(d.get("timestamp", "")),
                    "relationship": d.get("relationship", "causal"),
                }
                for d in chain
            ]

        except Exception as e:
            logger.error(f"Failed to trace decision chain for {decision_id}: {e}", exc_info=True)
            raise

    async def get_decision_stats(self) -> dict[str, Any]:
        """Get decision statistics and insights."""
        try:
            return self.context.get_decision_insights()
        except Exception as e:
            logger.error(f"Failed to get decision stats: {e}")
            return {"total_decisions": 0, "categories": {}, "error": str(e)}

    async def analyze_influence(
        self,
        decision_id: str,
    ) -> dict[str, Any]:
        """Analyze the influence/impact of a specific decision."""
        try:
            return self.context.analyze_decision_influence(decision_id)
        except Exception as e:
            logger.error(f"Failed to analyze decision influence: {e}")
            return {"decision_id": decision_id, "influence": [], "error": str(e)}
