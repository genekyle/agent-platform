"""FastAPI routers extracted from main.py — one module per domain.

See docs/PLAN_main-split.md. Each module exposes `router = APIRouter()`; main.py
includes them. Routers import shared helpers from deps.py / models / schemas —
never from main (that would be circular, since main includes the routers).
"""
