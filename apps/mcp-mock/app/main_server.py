from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.artifacts import write_observation_artifact
from app.main import observe_live_capture

app = FastAPI(title="MCP Mock Capture Server", version="0.0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CaptureRequest(BaseModel):
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    scenario: str = "live_capture"
    task_context: Optional[dict] = None
    training_metadata: Optional[dict] = None
    browser_url: str = "http://127.0.0.1:9222"


@app.get("/health")
def health():
    return {"ok": True, "service": "mcp-mock-capture-server"}


@app.post("/capture")
async def trigger_capture(body: CaptureRequest):
    artifact = await observe_live_capture(
        scenario=body.scenario,
        tab_id=body.tab_id,
        tab_url=body.tab_url,
        browser_url=body.browser_url,
        task_context=body.task_context,
        training_metadata=body.training_metadata,
    )
    path = write_observation_artifact(artifact)
    candidate_count = len(artifact.get("ranked_candidates", []))
    return {"filename": path.name, "candidate_count": candidate_count}
