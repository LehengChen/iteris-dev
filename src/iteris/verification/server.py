"""HTTP wrapper around Iteris verification backends."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from iteris.project import require_project
from iteris.verification.agent import verify_agent
from iteris.verification.local import verify_local


class VerifyRequest(BaseModel):
    project_path: str = Field(..., min_length=1)
    mode: str = "source"
    claim: str = Field(..., min_length=1)
    artifacts: list[str] = []
    fact_ids: list[str] = []
    target_artifact: str | None = None
    backend: str = "agent"


app = FastAPI(title="Iteris Verification Service", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/verify")
def verify(request: VerifyRequest) -> dict[str, Any]:
    root = require_project(request.project_path)
    return _run_backend(root, request.mode, request)


@app.post("/verify/fact")
def verify_fact(request: VerifyRequest) -> dict[str, Any]:
    root = require_project(request.project_path)
    return _run_backend(root, "fact", request)


@app.post("/verify/assembly")
def verify_assembly(request: VerifyRequest) -> dict[str, Any]:
    root = require_project(request.project_path)
    target = request.target_artifact or (request.artifacts[-1] if request.artifacts else None)
    request.target_artifact = target
    return _run_backend(root, "assembly", request)


@app.post("/verify/goal-success")
def verify_goal_success(request: VerifyRequest) -> dict[str, Any]:
    root = require_project(request.project_path)
    target = request.target_artifact or (request.artifacts[-1] if request.artifacts else None)
    request.target_artifact = target
    return _run_backend(root, "goal_success", request)


def _run_backend(root: Path, mode: str, request: VerifyRequest) -> dict[str, Any]:
    artifacts = [Path(a) for a in request.artifacts]
    target = Path(request.target_artifact) if request.target_artifact else None
    if request.backend == "agent":
        return verify_agent(root, mode=mode, claim=request.claim, artifacts=artifacts, fact_ids=request.fact_ids, target_artifact=target)
    if request.backend == "structural":
        return verify_local(root, mode=mode, claim=request.claim, artifacts=artifacts, fact_ids=request.fact_ids, target_artifact=target)
    raise ValueError("backend must be 'agent' or 'structural'")
