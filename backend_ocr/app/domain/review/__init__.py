"""Review Hub — the API-side half: visibility, entitlement filtering, serialisation.

Detection, scoring and the single write path live in `backend_ocr/app/review/`, which
this imports (the same arrangement as utils/ocr_engine — see
backend_ocr/app/integrations/ocr.py for the sys.path anchor).
"""

import sys

from ...core.storage import PROJECT_ROOT

# Same anchor as integrations/ocr.py: the scoring and type definitions are
# pipeline-owned but shared, and uvicorn runs from backend_ocr/.
_ROOT = str(PROJECT_ROOT)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
