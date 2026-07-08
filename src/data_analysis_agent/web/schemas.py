"""Pydantic 请求/响应模型(Wave 8 web workbench)。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class NeedRequest(BaseModel):
    raw_request: str


class ContextRequest(BaseModel):
    profile: dict[str, Any]
    events: list[dict[str, Any]] | None = None
    sensitive_mode: bool = False


class ContractRequest(BaseModel):
    question: str
    user_need: dict[str, Any] | None = None
    data_context: dict[str, Any] | None = None
    process_context: dict[str, Any] | None = None
    report_type: str | None = None
    audience: str | None = None
    language: str | None = None


class QARequest(BaseModel):
    document: dict[str, Any]
    artifact_exists: bool = False
    n_points_by_chart: dict[str, int] | None = None
    n_observations_by_chart: dict[str, int] | None = None


class QAFinding(BaseModel):
    severity: Literal["blocker", "high", "medium", "info"]
    code: str
    message: str
    block_id: str | None = None
    suggested_fix: str | None = None


class QAResponse(BaseModel):
    readiness: Literal["draft", "needs_review", "ready"]
    artifact_exists: bool
    findings: list[QAFinding]


class FeedbackRequest(BaseModel):
    tags: list[str] = []
    comment: str = ""
    readiness: str = ""
