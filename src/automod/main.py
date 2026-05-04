"""FastAPI application entrypoint."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .db.database import init_db
from .api import (
    communities_router,
    rules_router,
    checklist_router,
    examples_router,
    alignment_router,
    decisions_router,
    evaluation_router,
    health_router,
    intent_router,
    scenarios_router,
    telemetry_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables on startup
    await init_db()
    yield


app = FastAPI(
    title="AutoMod Agent",
    description="AI-powered community moderation system",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(communities_router, prefix="/api")
app.include_router(rules_router, prefix="/api")
app.include_router(checklist_router, prefix="/api")
app.include_router(examples_router, prefix="/api")
app.include_router(alignment_router, prefix="/api")
app.include_router(decisions_router, prefix="/api")
app.include_router(evaluation_router, prefix="/api")
app.include_router(health_router, prefix="/api")
app.include_router(intent_router, prefix="/api")
app.include_router(scenarios_router, prefix="/api")
app.include_router(telemetry_router, prefix="/api")

# Serve admin frontend if built
admin_dist = os.path.join(os.path.dirname(__file__), "../../admin/dist")
if os.path.exists(admin_dist):
    app.mount("/admin", StaticFiles(directory=admin_dist, html=True), name="admin")


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "automod-agent-v2"}
