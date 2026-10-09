"""ai_billing_hub's signed single-event ingestion contract."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from app.config import ClientBinding
from app.ids import new_ulid
from app.provider import Generation

INGEST_PATH = "/api/v1/integration/usage-events"
STATUS_PATH = "/api/v1/integration/effective-status"


class BillingStatusUnavailable(Exception):
    pass


def usage_payload(binding: ClientBinding, result: Generation, *, request_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": new_ulid(),
        "request_id": request_id,
        "tenant_id": binding.tenant_id,
        "project_id": binding.billing_project_id,
        "provider": "anthropic",
        "model": result.model,
        "usage_type": "LLM_TOKEN",
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cache_creation_input_tokens": result.cache_creation_input_tokens,
        "cache_read_input_tokens": result.cache_read_input_tokens,
        "occurred_at": datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
    }


def signed_headers(
    binding: ClientBinding,
    body: bytes,
    *,
    timestamp: str,
    attempt_id: str,
    method: str = "POST",
    path: str = INGEST_PATH,
) -> dict[str, str]:
    canonical = "\n".join(
        (method, path, timestamp, attempt_id, hashlib.sha256(body).hexdigest())
    )
    signature = hmac.new(
        binding.billing_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {
        "X-Acuven-Api-Key": binding.billing_api_key,
        "X-Acuven-Key-Version": str(binding.billing_key_version),
        "X-Acuven-Timestamp": timestamp,
        "X-Acuven-Request-Id": attempt_id,
        "X-Acuven-Signature": signature,
        "Content-Type": "application/json",
    }


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    retryable: bool
    error_code: str | None = None


class BillingClient:
    def __init__(self, base_url: str, *, transport: httpx.BaseTransport | None = None) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path.rstrip("/") or parsed.query or parsed.fragment:
            raise ValueError("billing base URL must be an origin without a path")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("billing API requires HTTPS outside local development")
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    def allows_ai(self, binding: ClientBinding) -> bool:
        """Read live status; raise BillingStatusUnavailable so the caller can fail open per invariant 1."""
        headers = signed_headers(
            binding,
            b"",
            timestamp=str(int(datetime.now(UTC).timestamp())),
            attempt_id=new_ulid(),
            method="GET",
            path=STATUS_PATH,
        )
        try:
            with httpx.Client(timeout=5, transport=self.transport) as client:
                response = client.get(self.base_url + STATUS_PATH, headers=headers)
            if response.status_code != 200:
                raise BillingStatusUnavailable
            data = response.json()["data"]
            if (
                data["tenant_id"] != binding.tenant_id
                or data["project_id"] != binding.billing_project_id
                or data["effective_status"] not in {"ALLOW_AI", "BLOCK_AI"}
                or not isinstance(data["status_version"], int)
            ):
                raise BillingStatusUnavailable
            return data["effective_status"] == "ALLOW_AI"
        except (httpx.RequestError, ValueError, TypeError, KeyError) as error:
            raise BillingStatusUnavailable from error

    def send(self, binding: ClientBinding, payload_json: str) -> DeliveryResult:
        body = payload_json.encode("utf-8")
        try:
            event_id = json.loads(payload_json)["event_id"]
            if not isinstance(event_id, str):
                raise ValueError("invalid event_id")
        except (ValueError, KeyError, TypeError):
            return DeliveryResult(False, False, "INVALID_LOCAL_EVENT")
        headers = signed_headers(
            binding,
            body,
            timestamp=str(int(datetime.now(UTC).timestamp())),
            attempt_id=new_ulid(),  # Fresh on every retry; event_id and body never change.
        )
        try:
            with httpx.Client(timeout=10, transport=self.transport) as client:
                response = client.post(self.base_url + INGEST_PATH, headers=headers, content=body)
        except httpx.RequestError:
            return DeliveryResult(False, True, "NETWORK_ERROR")
        if response.status_code in {200, 202}:
            try:
                receipt = response.json()["data"]
                expected_statuses = {202: {"accepted"}, 200: {"already_received", "already_processed"}}
                if receipt["event_id"] == event_id and receipt["status"] in expected_statuses[response.status_code]:
                    return DeliveryResult(True, False)
            except (ValueError, KeyError, TypeError):
                pass
            return DeliveryResult(False, True, "INVALID_BILLING_RECEIPT")
        try:
            data = response.json()
            code = data.get("error", {}).get("code", "BILLING_ERROR")
            retryable = data.get("retryable")
        except (ValueError, AttributeError, TypeError):
            code, retryable = "INVALID_BILLING_RESPONSE", None
        if not isinstance(code, str):
            code = "BILLING_ERROR"
        # Only the billing server's explicit retryable flag authorizes a retry.
        return DeliveryResult(False, retryable is True, code[:64])


def payload_json(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
