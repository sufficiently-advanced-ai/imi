#!/usr/bin/env bash
# Strip heavyweight ML/media/viz packages that `semantica` declares as core
# dependencies but that this application never imports.
#
# WHY: `semantica` is a kitchen-sink knowledge engine. Its core deps include
# computer-vision (opencv), audio (librosa), plotting (matplotlib/seaborn/plotly),
# notebook UI (ipywidgets), topic modeling (gensim) and embedding-visualization
# (umap-learn -> numba -> llvmlite/pynndescent) stacks. We only use semantica's
# embeddings / vector_store / graph_store / kg / deduplication / context /
# semantic_extract submodules — none of which import these at runtime.
#
# Notably `umap` is imported lazily inside semantica/visualization/
# embedding_visualizer.py only; nothing in our import graph touches it. Removing
# it lets us also drop numba + llvmlite (~196MB), its only other consumers here.
#
# MUST run chained in the SAME Docker RUN as `pip install` — a separate layer
# would leave the bytes in the lower layer and not shrink the image.
#
# Verified: after stripping, all app-used semantica submodules import and
# FastEmbed still produces correct 384-dim normalized vectors. Saves ~560MB.
set -euo pipefail

STRIP_PACKAGES=(
  opencv-python   # computer vision (cv2)
  librosa         # audio analysis
  matplotlib      # plotting
  seaborn         # plotting
  plotly          # plotting
  ipywidgets      # jupyter notebook widgets
  gensim          # topic modeling
  umap-learn      # embedding-visualization dimensionality reduction (viz only)
  pynndescent     # umap nearest-neighbor backend
  numba           # JIT used only by umap/librosa (both removed)
  llvmlite        # numba's LLVM backend
)

echo "Stripping unused ML/viz packages: ${STRIP_PACKAGES[*]}"
# --no-input keeps it non-interactive. `pip uninstall -y` already exits 0 for a
# package that is not installed (it just prints "Skipping ..."), so we do NOT
# swallow errors with `|| true`: under `set -e` a genuine uninstall failure must
# fail the build loudly rather than silently ship the heavyweight packages.
pip uninstall -y --no-input "${STRIP_PACKAGES[@]}"

# Post-strip smoke test (fails the build under `set -e` if a strip broke an
# import). The strip list is hardcoded but semantica floats (>=0.3.0,<0.4.0), so
# a future patch could start importing a removed package from a path we use —
# that would still BUILD but fail at runtime. This gate catches it at build time.
# NOTE: app/ is not in the image at this layer, so we can't import the app
# entrypoints (initialize_semantica / get_semantica_knowledge); we import the
# exact semantica submodules + classes those entrypoints depend on instead.
echo "Smoke test: verifying app-used semantica import surface survived the strip..."
python - <<'PY'
import importlib
for m in (
    "semantica.embeddings", "semantica.vector_store", "semantica.graph_store",
    "semantica.kg", "semantica.deduplication", "semantica.context",
    "semantica.semantic_extract", "semantica.utils.exceptions",
):
    importlib.import_module(m)
from semantica.embeddings import EmbeddingGenerator          # noqa: F401
from semantica.semantic_extract import NERExtractor          # noqa: F401
from semantica.kg import GraphBuilder                        # noqa: F401
from semantica.vector_store import VectorStore, MetadataFilter  # noqa: F401
from semantica.deduplication import DuplicateDetector        # noqa: F401
from semantica.context import ContextGraph                   # noqa: F401
from semantica.graph_store import GraphStore                 # noqa: F401
from semantica.utils.exceptions import ProcessingError       # noqa: F401
print("OK: semantica import surface intact after strip")
PY
echo "Strip complete."
