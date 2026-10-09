"""Server-to-server AI capability API; caller identity comes from configured credentials."""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from app.billing import BillingClient, BillingStatusUnavailable
from app.config import CAPABILITIES, Settings
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

    @api.post("/v1/capabilities/{capability}", response_model=CapabilityResponse)
    def invoke(
        capability: str,
        request: CapabilityRequest,
        authorization: str | None = Header(default=None),
        x_acuven_client_id: str | None = Header(default=None),
    ) -> CapabilityResponse:
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
        try:
            request_id, result = run_capability(
                sessions, provider, binding, capability, request.context_text
            )
        except (httpx.HTTPError, ValueError, RuntimeError, KeyError, TypeError):
            logger.exception("AI provider request failed for capability %s", capability)
            raise HTTPException(status_code=502, detail="AI provider unavailable") from None
        except (SQLAlchemyError, OSError):
            logger.exception("AI usage outbox write failed")
            raise HTTPException(status_code=503, detail="AI usage persistence unavailable") from None
        return CapabilityResponse(request_id=request_id, text=result.text, model=result.model)

    return api


app = create_app()
