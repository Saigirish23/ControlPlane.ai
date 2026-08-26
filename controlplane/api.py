"""
ControlPlane.AI — FastAPI Application

Exposes the ControlPlane decision engine via HTTP:

  POST /control          — Evaluate an AI request
  POST /execution-rail   — Evaluate an AI tool call
  GET  /health           — Health check

All responses are structured, explainable, and auditable.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from controlplane.models import (
    ControlRequest,
    ControlResponse,
    ExecutionRailResult,
    ToolCallRequest,
)
from controlplane.pipeline import ControlPlanePipeline

# Load environment variables from .env
load_dotenv()

app = FastAPI(
    title="ControlPlane.AI",
    description=(
        "Consequence-aware runtime governance layer for enterprise AI. "
        "Matches the depth of oversight to the consequence of getting it wrong."
    ),
    version="0.1.0",
)

# CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Singleton pipeline instance
pipeline = ControlPlanePipeline()


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "healthy", "service": "controlplane.ai"}


@app.post("/control", response_model=ControlResponse)
async def control(request: ControlRequest) -> ControlResponse:
    """
    Evaluate an AI request through the ControlPlane.

    Determines consequence tier, evaluation depth, runs appropriate
    checks, and returns a structured decision.
    """
    return await pipeline.evaluate(request)


@app.post("/execution-rail", response_model=ExecutionRailResult)
async def execution_rail(tool_call: ToolCallRequest) -> ExecutionRailResult:
    """
    Evaluate an AI-generated tool call through the execution rail.

    Intercepts external actions and applies governance controls before
    allowing execution.
    """
    return await pipeline.evaluate_tool_call(tool_call)
