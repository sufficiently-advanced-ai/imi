"""
Memory Agent - Autonomous organizational memory with dynamic decision-making.

Converted from service to agent architecture, this provides context-aware
memory queries, entity tracking, and intelligent knowledge discovery.
"""

from datetime import datetime
from typing import Any

from ..git_ops import GitOperations
from ..services.claude_client import ClaudeClient
from ..services.file_cache import FileCache
from ..services.knowledge_graph import get_knowledge_graph
from .base import AgentBase, DecisionContext, DecisionOutcome


class MemoryAgent(AgentBase):
    """Autonomous agent for organizational memory queries and context discovery."""

    def __init__(
        self, claude_client: ClaudeClient, git_ops: GitOperations, file_cache: FileCache
    ):
        super().__init__(claude_client, git_ops, file_cache)

    @property
    def name(self) -> str:
        return "memory_agent"

    @property
    def description(self) -> str:
        return "Autonomous organizational memory agent providing context-aware queries, entity tracking, and knowledge discovery"

    @property
    def capabilities(self) -> list[str]:
        return [
            "query",
            "context_discovery",
            "entity_tracking",
            "topic_search",
            "relationship_analysis",
            "temporal_tracking",
            "work_suggestions",
        ]

    async def make_decision(self, context: DecisionContext) -> DecisionOutcome:
        """Make context-aware decisions about memory operations."""
        query_type = context.inputs.get("query_type", "unknown")

        try:
            if query_type == "memory_query":
                return await self._handle_memory_query(context)
            elif query_type == "surface_context":
                return await self._handle_surface_context(context)
            elif query_type == "find_by_topic":
                return await self._handle_topic_search(context)
            elif query_type == "track_entity_evolution":
                return await self._handle_entity_evolution(context)
            else:
                return DecisionOutcome(
                    decision="unsupported_query_type",
                    confidence=0.1,
                    reasoning=f"Query type '{query_type}' is not supported by the memory agent",
                    actions=["log_unsupported_query", "suggest_alternatives"],
                    metadata={
                        "supported_types": [
                            "memory_query",
                            "surface_context",
                            "find_by_topic",
                            "track_entity_evolution",
                        ]
                    },
                )

        except Exception as e:
            return DecisionOutcome(
                decision="error_occurred",
                confidence=0.2,
                reasoning=f"Error processing memory operation: {str(e)}",
                actions=["error_recovery", "fallback_response"],
                metadata={"error": str(e), "query_type": query_type},
            )

    async def _handle_memory_query(self, context: DecisionContext) -> DecisionOutcome:
        """Handle memory query decision making."""
        question = context.inputs.get("question", "")
        context_hint = context.inputs.get("context_hint")
        max_documents = context.constraints.get("max_documents", 10)

        try:
            # Extract entities from the question
            entities = await self._extract_question_entities(question)

            # Find related entities through the knowledge graph
            expanded_entities = await self._expand_entity_context(entities)

            # Find contextually relevant documents
            relevant_docs = await self._find_contextual_documents(
                expanded_entities, question, context_hint, max_documents
            )

            # Generate intelligent response using Claude
            answer = await self._generate_contextual_answer(question, relevant_docs)
        except Exception as e:
            # Re-raise to be caught by the main error handler
            raise e

        # Calculate confidence based on available data
        confidence = self._calculate_confidence(
            entities_found=len(entities),
            documents_found=len(relevant_docs),
            claude_response_length=len(answer.get("answer", "")),
            has_context_hint=bool(context_hint),
        )

        # Plan actions based on results
        actions = self._plan_actions(
            "memory_query",
            {
                "entities": entities,
                "documents": relevant_docs,
                "answer_generated": True,  # Always true when we reach this point
            },
        )

        return DecisionOutcome(
            decision="provide_memory_response",
            confidence=confidence,
            reasoning=f"Processed memory query with {len(entities)} entities, {len(relevant_docs)} relevant documents",
            actions=actions,
            metadata={
                "answer": answer["answer"],
                "confidence": answer.get("confidence", "medium"),
                "sources": [doc["path"] for doc in relevant_docs],
                "related_entities": expanded_entities,
                "connection_analysis": await self._analyze_connections(entities),
                "query_metadata": {
                    "entities_found": len(entities),
                    "documents_analyzed": len(relevant_docs),
                    "response_time_ms": answer.get("response_time_ms", 0),
                    "timestamp": datetime.utcnow().isoformat(),
                },
            },
        )

    async def _handle_surface_context(
        self, context: DecisionContext
    ) -> DecisionOutcome:
        """Handle context surfacing decision making."""
        current_document = context.inputs.get("current_document", "")
        work_context = context.inputs.get("work_context")

        # Build knowledge graph if needed
        await get_knowledge_graph().build_graph()

        # Get entities from current document
        doc_entities = getattr(get_knowledge_graph(), "document_entities", {}).get(
            current_document, set()
        )

        if not doc_entities:
            return DecisionOutcome(
                decision="no_context_available",
                confidence=0.3,
                reasoning="No entities found in current document for context surfacing",
                actions=[
                    "suggest_manual_entity_extraction",
                    "recommend_document_enrichment",
                ],
                metadata={
                    "related_documents": [],
                    "related_entities": [],
                    "suggestions": [],
                    "context_analysis": "No entities found in current document",
                },
            )

        # Find related documents and entities
        entity_list = list(doc_entities)
        related_docs = await get_knowledge_graph().find_contextual_documents(
            entity_list, max_results=8
        )

        related_entities = []
        for entity_id in entity_list:
            if entity_id.startswith("doc:"):
                continue
            related = await get_knowledge_graph().find_related_entities(
                entity_id, max_results=3
            )
            related_entities.extend(related)

        # Generate work suggestions
        suggestions = await self._generate_work_suggestions(
            current_document, related_docs, related_entities, work_context
        )

        confidence = self._calculate_confidence(
            entities_found=len(entity_list),
            documents_found=len(related_docs),
            claude_response_length=len(" ".join(suggestions)),
            has_context_hint=bool(work_context),
        )

        actions = self._plan_actions(
            "surface_context",
            {
                "current_document": current_document,
                "related_entities": related_entities,
                "suggestions_generated": len(suggestions) > 0,
            },
        )

        return DecisionOutcome(
            decision="context_surfaced",
            confidence=confidence,
            reasoning=f"Surfaced context with {len(related_docs)} related documents and {len(related_entities)} connected entities",
            actions=actions,
            metadata={
                "related_documents": related_docs[:5],
                "related_entities": related_entities[:10],
                "suggestions": suggestions,
                "context_analysis": f"Found {len(related_docs)} related documents and {len(related_entities)} connected entities",
                "document_entities": [
                    {
                        "id": eid,
                        "name": get_knowledge_graph().nodes[eid].name
                        if eid in get_knowledge_graph().nodes
                        else eid,
                        "type": get_knowledge_graph().nodes[eid].type
                        if eid in get_knowledge_graph().nodes
                        else "unknown",
                    }
                    for eid in entity_list
                    if not eid.startswith("doc:")
                ],
            },
        )

    async def _handle_topic_search(self, context: DecisionContext) -> DecisionOutcome:
        """Handle topic search decision making."""
        topic = context.inputs.get("topic", "")
        max_results = context.inputs.get("max_results", 10)

        await get_knowledge_graph().build_graph()

        # Search by topic in knowledge graph
        results = await get_knowledge_graph().search_by_topic(topic, max_results)

        # Find topic-related entities
        topic_entities = []
        for _entity_id, node in get_knowledge_graph().nodes.items():
            if topic.lower() in node.name.lower() or any(
                topic.lower() in str(v).lower() for v in node.metadata.values()
            ):
                topic_entities.append(
                    {
                        "id": node.id,
                        "name": node.name,
                        "type": node.type,
                        "document_count": len(node.documents),
                        "connection_count": len(node.connections),
                    }
                )

        confidence = self._calculate_confidence(
            entities_found=len(topic_entities),
            documents_found=len(results),
            claude_response_length=100,  # Topic searches are generally reliable
            has_context_hint=bool(topic),
        )

        return DecisionOutcome(
            decision="topic_search_completed",
            confidence=confidence,
            reasoning=f"Topic search for '{topic}' found {len(results)} documents and {len(topic_entities)} related entities",
            actions=["return_search_results", "suggest_related_topics"],
            metadata={
                "documents": results,
                "related_entities": topic_entities,
                "topic_analysis": {
                    "total_documents": len(results),
                    "total_entities": len(topic_entities),
                    "entity_types": list(set(e["type"] for e in topic_entities)),
                },
            },
        )

    async def _handle_entity_evolution(
        self, context: DecisionContext
    ) -> DecisionOutcome:
        """Handle entity evolution tracking decision making."""
        entity_name = context.inputs.get("entity_name", "")
        entity_type = context.inputs.get("entity_type")

        await get_knowledge_graph().build_graph()

        # Find the entity
        entity = get_knowledge_graph().get_entity_by_name(entity_name, entity_type)
        if not entity:
            return DecisionOutcome(
                decision="entity_not_found",
                confidence=0.2,
                reasoning=f"Entity '{entity_name}' not found in knowledge graph",
                actions=["suggest_similar_entities", "recommend_entity_creation"],
                metadata={
                    "entity": None,
                    "evolution": [],
                    "error": f"Entity '{entity_name}' not found",
                },
            )

        entity_id = entity["id"]
        node = get_knowledge_graph().nodes[entity_id]

        # Get documents containing this entity, sorted by creation date
        document_timeline = []
        for doc_path in node.documents:
            try:
                content = await self.git_ops.read_file(doc_path)
                if content:
                    from ..services.frontmatter import frontmatter

                    metadata = frontmatter.extract(content)
                    created_date = metadata.get("created") if metadata else None

                    document_timeline.append(
                        {
                            "path": doc_path,
                            "created": created_date,
                            "type": metadata.get("type", "document")
                            if metadata
                            else "document",
                        }
                    )
            except Exception:
                continue

        # Sort by creation date
        document_timeline.sort(key=lambda x: x["created"] or "1900-01-01")

        # Analyze evolution patterns
        evolution_analysis = {
            "first_mention": document_timeline[0]["created"]
            if document_timeline
            else None,
            "latest_mention": document_timeline[-1]["created"]
            if document_timeline
            else None,
            "total_mentions": len(document_timeline),
            "document_types": list(set(doc["type"] for doc in document_timeline)),
            "mention_frequency": self._calculate_mention_frequency(document_timeline),
        }

        confidence = self._calculate_confidence(
            entities_found=1,
            documents_found=len(document_timeline),
            claude_response_length=200,
            has_context_hint=bool(entity_type),
        )

        return DecisionOutcome(
            decision="entity_evolution_tracked",
            confidence=confidence,
            reasoning=f"Tracked evolution of '{entity_name}' across {len(document_timeline)} documents",
            actions=["return_evolution_data", "suggest_trend_analysis"],
            metadata={
                "entity": entity,
                "timeline": document_timeline,
                "evolution_analysis": evolution_analysis,
                "current_connections": len(node.connections),
                "related_entities": await get_knowledge_graph().find_related_entities(
                    entity_id, max_results=5
                ),
            },
        )

    def _calculate_confidence(
        self,
        entities_found: int,
        documents_found: int,
        claude_response_length: int,
        has_context_hint: bool,
    ) -> float:
        """Calculate confidence score based on available data quality."""
        confidence = 0.3  # Base confidence

        # Entity factor (0-0.25)
        if entities_found >= 5:
            confidence += 0.25
        elif entities_found >= 3:
            confidence += 0.2
        elif entities_found >= 1:
            confidence += 0.1

        # Document factor (0-0.25)
        if documents_found >= 8:
            confidence += 0.25
        elif documents_found >= 5:
            confidence += 0.2
        elif documents_found >= 2:
            confidence += 0.1
        elif documents_found >= 1:
            confidence += 0.05

        # Response quality factor (0-0.15)
        if claude_response_length >= 500:
            confidence += 0.15
        elif claude_response_length >= 300:
            confidence += 0.1
        elif claude_response_length >= 100:
            confidence += 0.05

        # Context hint bonus (0-0.05)
        if has_context_hint:
            confidence += 0.05

        return min(1.0, confidence)

    def _plan_actions(self, query_type: str, context_data: dict[str, Any]) -> list[str]:
        """Plan appropriate actions based on query type and context."""
        if query_type == "memory_query":
            actions = ["extract_entities", "find_related_docs"]
            if context_data.get("answer_generated"):
                actions.append("generate_response")
            if len(context_data.get("entities", [])) > 0:
                actions.append("analyze_relationships")
            return actions

        elif query_type == "surface_context":
            actions = ["analyze_current_doc", "find_related_docs"]
            if context_data.get("suggestions_generated"):
                actions.append("generate_suggestions")
            return actions

        elif query_type == "find_by_topic":
            return [
                "search_by_topic",
                "find_related_entities",
                "analyze_topic_coverage",
            ]

        elif query_type == "track_entity_evolution":
            return [
                "find_entity_timeline",
                "analyze_evolution_patterns",
                "suggest_trend_analysis",
            ]

        else:
            return ["log_unknown_query", "provide_help"]

    # Legacy interface methods for backward compatibility
    async def query_memory(
        self, question: str, context_hint: str | None = None, max_documents: int = 10
    ) -> dict[str, Any]:
        """Legacy interface for memory queries."""
        context = DecisionContext(
            inputs={
                "query_type": "memory_query",
                "question": question,
                "context_hint": context_hint,
            },
            constraints={"max_documents": max_documents},
            goals=["accuracy", "comprehensive_context"],
        )

        result = await self.execute_with_tracking(context)
        if result.success and result.decision_outcome:
            return result.decision_outcome.metadata
        else:
            return {
                "answer": "Unable to process query",
                "confidence": "low",
                "sources": [],
                "error": result.error,
            }

    async def surface_context(
        self, current_document: str, work_context: str | None = None
    ) -> dict[str, Any]:
        """Legacy interface for context surfacing."""
        context = DecisionContext(
            inputs={
                "query_type": "surface_context",
                "current_document": current_document,
                "work_context": work_context,
            },
            goals=["relevant_context", "actionable_suggestions"],
        )

        result = await self.execute_with_tracking(context)
        if result.success and result.decision_outcome:
            return result.decision_outcome.metadata
        else:
            return {
                "related_documents": [],
                "related_entities": [],
                "suggestions": [],
                "error": result.error,
            }

    async def find_by_topic(self, topic: str, max_results: int = 10) -> dict[str, Any]:
        """Legacy interface for topic search."""
        context = DecisionContext(
            inputs={
                "query_type": "find_by_topic",
                "topic": topic,
                "max_results": max_results,
            },
            goals=["comprehensive_coverage", "relevance"],
        )

        result = await self.execute_with_tracking(context)
        if result.success and result.decision_outcome:
            return result.decision_outcome.metadata
        else:
            return {"documents": [], "related_entities": [], "error": result.error}

    async def track_entity_evolution(
        self, entity_name: str, entity_type: str | None = None
    ) -> dict[str, Any]:
        """Legacy interface for entity evolution tracking."""
        context = DecisionContext(
            inputs={
                "query_type": "track_entity_evolution",
                "entity_name": entity_name,
                "entity_type": entity_type,
            },
            goals=["temporal_analysis", "evolution_patterns"],
        )

        result = await self.execute_with_tracking(context)
        if result.success and result.decision_outcome:
            return result.decision_outcome.metadata
        else:
            return {"entity": None, "timeline": [], "error": result.error}

    # Private helper methods (keeping the original implementation)
    async def _extract_question_entities(self, question: str) -> list[str]:
        """Extract potential entities from a question using simple pattern matching."""
        entities = []

        # Look for quoted terms (explicit entities)
        import re

        quoted_terms = re.findall(r'"([^"]+)"', question)
        entities.extend(quoted_terms)

        # Look for capitalized terms (potential proper nouns)
        capitalized_terms = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", question)
        entities.extend(capitalized_terms)

        # Look for common organizational terms
        org_terms = ["project", "team", "department", "initiative", "program"]
        for term in org_terms:
            if term in question.lower():
                # Try to find the specific project/team name
                pattern = rf"\b{term}\s+([A-Z][a-zA-Z\s]+)\b"
                matches = re.findall(pattern, question, re.IGNORECASE)
                entities.extend(matches)

        # Remove duplicates and empty strings
        return list(set(entity.strip() for entity in entities if entity.strip()))

    async def _expand_entity_context(self, entities: list[str]) -> list[dict[str, Any]]:
        """Expand the initial entities to include related entities from the knowledge graph."""
        await get_knowledge_graph().build_graph()

        expanded = []
        processed_entities = set()

        for entity_name in entities:
            # Try to find this entity in the knowledge graph
            entity = get_knowledge_graph().get_entity_by_name(entity_name)

            if entity and entity["id"] not in processed_entities:
                expanded.append(entity)
                processed_entities.add(entity["id"])

                # Add directly related entities
                related = await get_knowledge_graph().find_related_entities(
                    entity["id"], max_results=3
                )
                for rel in related:
                    if rel["entity"]["id"] not in processed_entities:
                        expanded.append(rel["entity"])
                        processed_entities.add(rel["entity"]["id"])

        return expanded

    async def _find_contextual_documents(
        self,
        entities: list[dict[str, Any]],
        question: str,
        context_hint: str | None,
        max_documents: int,
    ) -> list[dict[str, Any]]:
        """Find documents that are contextually relevant to the entities and question."""
        if not entities:
            # Fallback: search by topic extraction from question
            topic_results = await get_knowledge_graph().search_by_topic(
                question, max_documents
            )
            return topic_results

        entity_ids = [entity["id"] for entity in entities]
        contextual_docs = await get_knowledge_graph().find_contextual_documents(
            entity_ids, max_documents
        )

        # If we have a context hint, try to boost relevant documents
        if context_hint:
            for doc in contextual_docs:
                if any(
                    term in doc["path"].lower() for term in context_hint.lower().split()
                ):
                    doc["relevance_score"] *= 1.2

            # Re-sort by relevance
            contextual_docs.sort(key=lambda x: x["relevance_score"], reverse=True)

        return contextual_docs

    async def _generate_contextual_answer(
        self, question: str, relevant_docs: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Generate an answer using Claude with the contextual documents."""
        if not relevant_docs:
            return {
                "answer": "I couldn't find any relevant documents in the knowledge base for your question.",
                "confidence": "low",
                "response_time_ms": 0,
            }

        # Build context from relevant documents
        context_text = ""
        for i, doc in enumerate(relevant_docs[:5]):  # Limit to top 5 documents
            doc_path = doc["path"]
            try:
                content = await self.git_ops.read_file(doc_path)
                if content:
                    # Limit content length
                    truncated_content = (
                        content[:1000] + "..." if len(content) > 1000 else content
                    )
                    context_text += (
                        f"\n--- Document {i+1}: {doc_path} ---\n{truncated_content}\n"
                    )
            except Exception:
                continue

        # Generate response using Claude
        prompt = f"""Based on the following documents from the organizational knowledge base, please answer this question:

Question: {question}

Relevant Documents:
{context_text}

Please provide a comprehensive answer that:
1. Directly addresses the question
2. References specific information from the documents
3. Connects related information from different sources
4. Indicates confidence level in your response

Answer:"""

        try:
            start_time = datetime.now()

            response = await self.claude_client.generate_message(
                [{"role": "user", "content": prompt}],
                operation="memory_agent_answer",
            )

            end_time = datetime.now()
            response_time_ms = int((end_time - start_time).total_seconds() * 1000)

            # Extract response text
            if hasattr(response, "content") and response.content:
                answer_text = (
                    response.content[0].text
                    if hasattr(response.content[0], "text")
                    else str(response.content[0])
                )
            else:
                answer_text = str(response)

            # Simple confidence estimation based on answer length and document relevance
            confidence = (
                "high"
                if len(answer_text) > 200 and len(relevant_docs) >= 3
                else "medium"
            )
            if len(relevant_docs) < 2:
                confidence = "low"

            return {
                "answer": answer_text,
                "confidence": confidence,
                "response_time_ms": response_time_ms,
            }

        except Exception as e:
            return {
                "answer": f"I encountered an error while processing your question: {str(e)}",
                "confidence": "low",
                "response_time_ms": 0,
            }

    async def _analyze_connections(self, entities: list[str]) -> dict[str, Any]:
        """Analyze how the entities in the question are connected."""
        await get_knowledge_graph().build_graph()

        connections = []
        entity_nodes = []

        # Find entity nodes
        for entity_name in entities:
            entity = get_knowledge_graph().get_entity_by_name(entity_name)
            if entity:
                entity_nodes.append(entity)

        # Find connections between entity pairs
        for i, entity1 in enumerate(entity_nodes):
            for entity2 in entity_nodes[i + 1 :]:
                related = await get_knowledge_graph().find_related_entities(
                    entity1["id"], max_results=20
                )

                for relation in related:
                    if relation["entity"]["id"] == entity2["id"]:
                        connections.append(
                            {
                                "source": entity1["name"],
                                "target": entity2["name"],
                                "relationship": relation["relationship"]["type"],
                                "strength": relation["relationship"]["strength"],
                                "shared_documents": relation["relationship"][
                                    "shared_documents"
                                ],
                            }
                        )
                        break

        return {
            "direct_connections": connections,
            "connection_strength": sum(c["strength"] for c in connections)
            / max(1, len(connections)),
            "total_entities": len(entity_nodes),
            "graph_density": len(connections)
            / max(1, len(entity_nodes) * (len(entity_nodes) - 1) / 2),
        }

    async def _generate_work_suggestions(
        self,
        current_document: str,
        related_docs: list[dict[str, Any]],
        related_entities: list[dict[str, Any]],
        work_context: str | None,
    ) -> list[str]:
        """Generate intelligent work suggestions based on context."""
        suggestions = []

        # Document-based suggestions
        if related_docs:
            suggestions.append(
                f"Review {len(related_docs)} related documents that share entities with your current work"
            )

            # Check for recent documents
            recent_docs = [doc for doc in related_docs if doc["relevance_score"] > 0.5]
            if recent_docs:
                suggestions.append(
                    f"Check {len(recent_docs)} highly relevant documents that might contain updates"
                )

        # Entity-based suggestions
        if related_entities:
            entity_types = set(entity["entity"]["type"] for entity in related_entities)
            if "person" in entity_types:
                suggestions.append(
                    "Consider reaching out to connected team members for collaboration"
                )
            if "project" in entity_types:
                suggestions.append(
                    "Review related projects that might have dependencies or learnings"
                )

        # Context-based suggestions
        if work_context:
            if "meeting" in work_context.lower():
                suggestions.append(
                    "Prepare agenda items based on related documents and entity connections"
                )
            elif "planning" in work_context.lower():
                suggestions.append(
                    "Consider historical context and entity relationships in your planning"
                )

        return suggestions[:5]  # Limit to top 5 suggestions

    def _calculate_mention_frequency(
        self, timeline: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Calculate mention frequency patterns over time."""
        if not timeline:
            return {"pattern": "no_data", "frequency": 0}

        # Simple frequency calculation
        total_days = 1  # Default to avoid division by zero
        if len(timeline) > 1:
            try:
                first_date = datetime.fromisoformat(
                    timeline[0]["created"].replace("Z", "+00:00")
                )
                last_date = datetime.fromisoformat(
                    timeline[-1]["created"].replace("Z", "+00:00")
                )
                total_days = max(1, (last_date - first_date).days)
            except Exception:
                pass

        frequency = len(timeline) / total_days

        if frequency > 0.1:
            pattern = "high_frequency"
        elif frequency > 0.05:
            pattern = "moderate_frequency"
        else:
            pattern = "low_frequency"

        return {
            "pattern": pattern,
            "frequency": frequency,
            "mentions_per_day": frequency,
            "total_mentions": len(timeline),
            "total_days": total_days,
        }


# Create global singleton instance
from ..git_ops import git_ops  # noqa: E402 — singleton wiring at module bottom
from ..services.claude_client import get_claude_client  # noqa: E402
from ..services.file_cache import FileCache  # noqa: E402

# Initialize with default dependencies
_claude_client = get_claude_client()
_file_cache = FileCache()

# Global memory agent instance
memory_agent = MemoryAgent(_claude_client, git_ops, _file_cache)
