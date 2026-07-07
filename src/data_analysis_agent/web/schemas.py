"""Pydantic 请求模型(Wave 8 web workbench)。响应用 reporting 的 to_dict(dict[str, Any])。"""

from __future__ import annotations

from typing import Any

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
