"""
Nova Reconciliation Service — entrypoint.

Standalone FastAPI app (docs/NOVA_RECONCILIATION_LOGIC.md §1): its own process,
own DB pool, own CORS policy. Run with:
    uvicorn app.main:app --port 8100 --reload
from services/nova_reconciliation/.
"""
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config, orchestrator
from .routes import router

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Nova Reconciliation Service",
    version="1.1.0",
    description=(
        "Certifies each line of a Nova vendor usage bill against lendix/nova_vendor_mapping.json "
        "and checks the billed rate against the certified one. See docs/NOVA_RECONCILIATION_LOGIC.md."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOW_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(router)

_orchestrator_task: asyncio.Task | None = None


@app.on_event("startup")
async def _start_orchestrator():
    global _orchestrator_task
    _orchestrator_task = asyncio.create_task(orchestrator.run_forever())


@app.get("/health")
def health():
    return {"status": "ok"}
