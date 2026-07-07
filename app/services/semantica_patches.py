"""
Runtime patches applied to the upstream `semantica` package.

We pin `semantica==0.3.x` in requirements.txt and don't control its release
cadence, but a few specific bugs would otherwise force us into ugly
workarounds at every call site. Apply targeted monkey-patches here at app
startup, before any Semantica object is constructed.

Each patch documents:
- Which upstream version it targets
- What the bug is (symptom + root cause)
- A pointer to where this should eventually be fixed upstream

If/when upstream ships a fixed release, drop the patch and bump the pin.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PATCHES_APPLIED = False


def apply_semantica_patches() -> None:
    """Apply all runtime patches to the semantica package. Idempotent."""
    global _PATCHES_APPLIED
    if _PATCHES_APPLIED:
        return

    _patch_neo4j_store_execute_query()

    _PATCHES_APPLIED = True
    logger.info("Semantica runtime patches applied")


def _patch_neo4j_store_execute_query() -> None:
    """Fix Node/Relationship coercion in Neo4jStore.execute_query.

    Targets: semantica==0.3.0 (semantica/graph_store/neo4j_store.py:773-839)

    Bug: the upstream type-check order is inverted. A Neo4j ``Node`` is
    iterable (yields property names) AND has ``.items()`` returning
    (key, value) tuples. The upstream code checks ``__iter__`` first:

        if hasattr(value, "__iter__") and not isinstance(value, (str, dict)):
            row[key] = list(value)         # ← Node hits this first
        elif hasattr(value, "items"):
            row[key] = dict(value)         # ← never reached for Nodes

    Result: ``RETURN n`` returns rows like ``{"n": ["id", "name", ...]}`` —
    just the property names, not the values. Downstream code that does
    ``row["n"].get("id")`` blows up with ``'list' object has no attribute
    'get'`` (or ``'str'`` if iterated wrong).

    Fix: try the dict coercion path first for any object that exposes
    ``.items()``. Falls through to the list path only for genuine
    sequences (Cypher COLLECT() results, ``properties()`` of relationship
    types, etc.).
    """
    try:
        from semantica.graph_store import neo4j_store as _ns
    except ImportError:
        logger.warning("semantica.graph_store.neo4j_store not importable; skipping patch")
        return

    Neo4jStore = getattr(_ns, "Neo4jStore", None)
    if Neo4jStore is None:
        logger.warning("Neo4jStore class not found; skipping patch")
        return

    original_execute_query = Neo4jStore.execute_query

    # Marker so we don't double-patch on hot reload.
    if getattr(original_execute_query, "_imi_patched", False):
        return

    def execute_query(self, query, parameters=None, **options):
        """Patched execute_query — see semantica_patches._patch_neo4j_store_execute_query."""
        from semantica.utils.exceptions import ProcessingError

        tracking_id = self.progress_tracker.start_tracking(
            module="graph_store",
            submodule="Neo4jStore",
            message="Executing Cypher query",
        )

        try:
            with self.get_session() as session:
                result = session.run(query, parameters or {})

                records = []
                keys: list[str] = []

                for record in result:
                    if not keys:
                        keys = list(record.keys())

                    row = {}
                    for key in keys:
                        value = record[key]
                        # Prefer dict coercion (Node/Relationship) over list
                        # coercion. Strings are explicitly excluded so
                        # they're returned as-is.
                        if isinstance(value, str):
                            row[key] = value
                        elif hasattr(value, "items"):
                            # Neo4j Node or Relationship — has .items()
                            row[key] = dict(value)
                        elif hasattr(value, "__iter__") and not isinstance(value, dict):
                            row[key] = list(value)
                        else:
                            row[key] = value
                    records.append(row)

                self.progress_tracker.stop_tracking(
                    tracking_id,
                    status="completed",
                    message=f"Query returned {len(records)} records",
                )

                return {
                    "success": True,
                    "records": records,
                    "keys": keys,
                    "metadata": {"query": query},
                }

        except Exception as e:
            self.progress_tracker.stop_tracking(
                tracking_id, status="failed", message=str(e)
            )
            raise ProcessingError(f"Query execution failed: {str(e)}")

    execute_query._imi_patched = True  # type: ignore[attr-defined]
    Neo4jStore.execute_query = execute_query
    logger.info("Patched semantica.Neo4jStore.execute_query (Node coercion fix)")
