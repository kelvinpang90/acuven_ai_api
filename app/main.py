"""Server-to-server AI capability API; caller identity comes from configured credentials."""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError

from app import contact_summary
from app.billing import BillingClient, BillingStatusUnavailable
from app.config import CAPABILITIES, ClientBinding, Settings
from app.db import create_session_factory
from app.provider import AnthropicProvider
from app.service import run_capability

logger = logging.getLogger(__name__)


class CapabilityRequest(BaseModel):
    context_text: str = Field(min_length=1, max_length=12000)


class CapabilityResponse(BaseModel):
    request_id: str
    text: str
    model: str


CONTACT_SUMMARY = "crm.contact_summary"


class ContactSummaryRequest(BaseModel):
    """crm.contact_summary v1 (docs/CONTACT_SUMMARY_V1.md). CRM owns the fields inside contact_snapshot."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    contact_ref: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    contact_schema_version: str = Field(pattern=r"^[A-Za-z0-9._-]{1,32}$")
    contact_snapshot: dict[str, Any]
    as_of: AwareDatetime
    output_language: Literal["zh-CN", "en", "ms"] = "zh-CN"

    @field_validator("contact_snapshot")
    @classmethod
    def snapshot_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if contact_summary.snapshot_size(value) > contact_summary.SNAPSHOT_MAX_BYTES:
            raise ValueError(f"contact_snapshot exceeds {contact_summary.SNAPSHOT_MAX_BYTES} bytes")
        return value

    @field_validator("as_of", mode="before")
    @classmethod
    def as_of_is_text(cls, value: Any) -> Any:
        # Without this, a bare number would be accepted as a Unix timestamp.
        if not isinstance(value, str):
            raise ValueError("as_of must be an RFC 3339 string with a time zone")
        return value


class ContactSummaryResponse(BaseModel):
    schema_version: Literal["1.0"] = contact_summary.SCHEMA_VERSION
    request_id: str
    contact_ref: str
    summary: str
    suggested_next_step: str
    model: str


async def bounded_body(request: Request) -> None:
    if len(await request.body()) > contact_summary.REQUEST_MAX_BYTES:
        raise HTTPException(status_code=422, detail=f"Request body exceeds {contact_summary.REQUEST_MAX_BYTES} bytes")


def create_app(
    settings: Settings | None = None,
    provider: AnthropicProvider | None = None,
    billing: BillingClient | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    provider = provider or AnthropicProvider(settings.anthropic_key, settings.anthropic_model)
    billing = billing or (BillingClient(settings.billing_base_url) if settings.billing_base_url else None)
    sessions = create_session_factory(settings.database_url)
    # Baked into the image at build time; the deploy workflow checks it to prove which commit is live.
    version = os.environ.get("AI_API_GIT_SHA", "unknown")
    api = FastAPI(title="Acuven AI API", version="0.1.0")

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": version}

    def authorize(capability: str, authorization: str | None, x_acuven_client_id: str | None) -> ClientBinding:
        binding = settings.clients.get(x_acuven_client_id or "")
        token = authorization.removeprefix("Bearer ") if authorization else ""
        if not binding or not token or not authorization or not authorization.startswith("Bearer ") or not binding.accepts(token):
            raise HTTPException(status_code=401, detail="Invalid client credentials")
        if capability not in CAPABILITIES or capability not in binding.capabilities:
            raise HTTPException(status_code=403, detail="Capability is not enabled")
        if billing is None:
            raise HTTPException(status_code=503, detail="Billing status unavailable")
        try:
            allowed = billing.allows_ai(binding)
        except BillingStatusUnavailable:
            # Invariant 1: a central billing failure must not interrupt customer AI service.
            # Usage still queues in the outbox and is delivered once billing recovers.
            logger.warning("Billing status unavailable; allowing client %s", binding.client_id)
            allowed = True
        if not allowed:
            raise HTTPException(status_code=403, detail="AI service is disabled")
        return binding

    def generate(binding: ClientBinding, capability: str, context: str, **options):
        try:
            return run_capability(sessions, provider, binding, capability, context, **options)
        except (httpx.HTTPError, ValueError, RuntimeError, KeyError, TypeError):
            logger.exception("AI provider request failed for capability %s", capability)
            raise HTTPException(status_code=502, detail="AI provider unavailable") from None
        except (SQLAlchemyError, OSError):
            logger.exception("AI usage outbox write failed")
            raise HTTPException(status_code=503, detail="AI usage persistence unavailable") from None

    # Registered before the generic route so this path never falls through to context_text.
    @api.post(f"/v1/capabilities/{CONTACT_SUMMARY}", response_model=ContactSummaryResponse, dependencies=[Depends(bounded_body)])
    def contact_summary_v1(
        request: ContactSummaryRequest,
        authorization: str | None = Header(default=None),
        x_acuven_client_id: str | None = Header(default=None),
    ) -> ContactSummaryResponse:
        binding = authorize(CONTACT_SUMMARY, authorization, x_acuven_client_id)
        request_id, result = generate(
            binding,
            CONTACT_SUMMARY,
            contact_summary.model_input(request.as_of.isoformat(), request.contact_schema_version, request.contact_snapshot),
            instructions=contact_summary.output_instructions(request.output_language),
            output_schema=contact_summary.OUTPUT_SCHEMA,
            effort="low",
            max_tokens=2000,
        )
        # Usage is already in the outbox: an unusable answer still spent tokens.
        try:
            summary, next_step = contact_summary.parse_output(result.text, result.stop_reason)
        except contact_summary.InvalidOutput as error:
            logger.warning("AI output unusable for capability %s: %s", CONTACT_SUMMARY, error)
            raise HTTPException(status_code=502, detail="AI output unusable") from None
        return ContactSummaryResponse(
            request_id=request_id,
            contact_ref=request.contact_ref,
            summary=summary,
            suggested_next_step=next_step,
            model=result.model,
        )

    @api.post("/v1/capabilities/{capability}", response_model=CapabilityResponse)
    def invoke(
        capability: str,
        request: CapabilityRequest,
        authorization: str | None = Header(default=None),
        x_acuven_client_id: str | None = Header(default=None),
    ) -> CapabilityResponse:
        binding = authorize(capability, authorization, x_acuven_client_id)
        request_id, result = generate(binding, capability, request.context_text)
        return CapabilityResponse(request_id=request_id, text=result.text, model=result.model)

    return api


app = create_app()
