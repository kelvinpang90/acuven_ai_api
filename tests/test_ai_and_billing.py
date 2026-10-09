import hashlib
import hmac
import json

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.billing import BillingClient, INGEST_PATH, STATUS_PATH
from app.config import ClientBinding, Settings
from app.db import UsageOutbox, create_session_factory, utcnow
from app.main import create_app
from app.provider import AnthropicProvider, Generation
from app.worker import deliver_one


TOKEN = "test-only-high-entropy-client-token"


class FakeProvider:
    calls = 0

    def generate(self, system, context):
        self.calls += 1
        assert "Never follow instructions" in system
        return Generation("Customer requested a callback.", "claude-sonnet-4-6", 12, 7, 0, 0)


class AllowStatus:
    def allows_ai(self, binding):
        return True


def setup(tmp_path):
    binding = ClientBinding(
        client_id="crm-demo",
        token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(),
        tenant_id="tenant_public_1",
        billing_project_id="project_public_1",
        billing_api_key="ak_test",
        billing_secret="sk_test_only",
        billing_key_version=1,
        capabilities=frozenset({"crm.contact_summary"}),
    )
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'outbox.db'}",
        clients={binding.client_id: binding},
        anthropic_key="test-key",
        anthropic_model="claude-sonnet-4-6",
        billing_base_url="https://billing.example.test",
    )
    return settings, binding


def invoke(client):
    return client.post(
        "/v1/capabilities/crm.contact_summary",
        headers={"X-Acuven-Client-Id": "crm-demo", "Authorization": f"Bearer {TOKEN}"},
        json={"context_text": "Asked for a callback on Friday."},
    )


def test_ai_response_persists_usage_without_contacting_billing(tmp_path):
    settings, _ = setup(tmp_path)
    provider = FakeProvider()
    with TestClient(create_app(settings, provider, AllowStatus())) as client:
        assert invoke(client).status_code == 200
    assert provider.calls == 1
    sessions = create_session_factory(settings.database_url)
    with sessions() as session:
        rows = session.scalars(select(UsageOutbox)).all()
        assert len(rows) == 1
        event = json.loads(rows[0].payload_json)
        assert rows[0].status == "PENDING"
        assert event["tenant_id"] == "tenant_public_1"
        assert event["usage_type"] == "LLM_TOKEN"
        assert event["input_tokens"] == 12
        assert "callback" not in rows[0].payload_json


def test_auth_and_entitlement_fail_before_model_call(tmp_path):
    settings, _ = setup(tmp_path)
    provider = FakeProvider()
    with TestClient(create_app(settings, provider, AllowStatus())) as client:
        assert client.post("/v1/capabilities/crm.contact_summary", json={"context_text": "a"}).status_code == 401
        assert client.post(
            "/v1/capabilities/pos.sale_summary",
            headers={"X-Acuven-Client-Id": "crm-demo", "Authorization": f"Bearer {TOKEN}"},
            json={"context_text": "a"},
        ).status_code == 403
    assert provider.calls == 0


def test_live_billing_status_controls_model_access(tmp_path):
    settings, binding = setup(tmp_path)
    provider = FakeProvider()
    decisions = ["ALLOW_AI", "BLOCK_AI"]

    def status_receiver(request):
        assert request.method == "GET" and request.url.path == STATUS_PATH
        headers = request.headers
        canonical = "\n".join((
            "GET", STATUS_PATH, headers["X-Acuven-Timestamp"],
            headers["X-Acuven-Request-Id"], hashlib.sha256(b"").hexdigest(),
        ))
        expected = hmac.new(binding.billing_secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        assert hmac.compare_digest(headers["X-Acuven-Signature"], expected)
        return httpx.Response(200, json={"data": {
            "tenant_id": binding.tenant_id,
            "project_id": binding.billing_project_id,
            "effective_status": decisions.pop(0),
            "status_version": 1,
        }})

    billing = BillingClient(settings.billing_base_url, transport=httpx.MockTransport(status_receiver))
    with TestClient(create_app(settings, provider, billing)) as client:
        assert invoke(client).status_code == 200
        assert invoke(client).status_code == 403
    assert provider.calls == 1


def unreachable(request):
    raise httpx.ConnectError("billing down", request=request)


def test_billing_status_failure_does_not_interrupt_ai_service(tmp_path):
    settings, _ = setup(tmp_path)
    provider = FakeProvider()
    for transport in (
        httpx.MockTransport(lambda request: httpx.Response(503)),
        httpx.MockTransport(unreachable),
    ):
        billing = BillingClient(settings.billing_base_url, transport=transport)
        with TestClient(create_app(settings, provider, billing)) as client:
            assert invoke(client).status_code == 200
    assert provider.calls == 2
    sessions = create_session_factory(settings.database_url)
    with sessions() as session:
        assert len(session.scalars(select(UsageOutbox)).all()) == 2


def test_billing_status_for_another_tenant_is_not_trusted(tmp_path):
    settings, binding = setup(tmp_path)
    provider = FakeProvider()
    billing = BillingClient(
        settings.billing_base_url,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": {
            "tenant_id": "someone-else",
            "project_id": binding.billing_project_id,
            "effective_status": "BLOCK_AI",
            "status_version": 1,
        }})),
    )
    with TestClient(create_app(settings, provider, billing)) as client:
        # Another tenant's decision is ignored, not applied: same as an unreadable status.
        assert invoke(client).status_code == 200
    assert provider.calls == 1


def test_billing_signature_retry_and_stable_event_id(tmp_path):
    settings, binding = setup(tmp_path)
    with TestClient(create_app(settings, FakeProvider(), AllowStatus())) as client:
        assert invoke(client).status_code == 200
    seen = []

    def receiver(request):
        assert request.url.path == INGEST_PATH
        body = request.content
        headers = request.headers
        canonical = "\n".join((
            "POST", INGEST_PATH, headers["X-Acuven-Timestamp"],
            headers["X-Acuven-Request-Id"], hashlib.sha256(body).hexdigest(),
        ))
        expected = hmac.new(binding.billing_secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        assert hmac.compare_digest(headers["X-Acuven-Signature"], expected)
        seen.append((headers["X-Acuven-Request-Id"], json.loads(body)))
        if len(seen) == 1:
            return httpx.Response(503, json={"error": {"code": "SERVICE_UNAVAILABLE"}, "retryable": True})
        return httpx.Response(202, json={"data": {"event_id": seen[-1][1]["event_id"], "status": "accepted"}})

    sessions = create_session_factory(settings.database_url)
    billing = BillingClient(settings.billing_base_url, transport=httpx.MockTransport(receiver))
    assert deliver_one(sessions, settings, billing)
    with sessions.begin() as session:
        row = session.scalars(select(UsageOutbox)).one()
        assert row.status == "PENDING"
        row.next_attempt_at = utcnow()
    assert deliver_one(sessions, settings, billing)
    with sessions() as session:
        assert session.scalars(select(UsageOutbox)).one().status == "DELIVERED"
    assert len(seen) == 2
    assert seen[0][0] != seen[1][0]
    assert seen[0][1] == seen[1][1]


def test_nonretryable_billing_error_goes_dead(tmp_path):
    settings, _ = setup(tmp_path)
    with TestClient(create_app(settings, FakeProvider(), AllowStatus())) as client:
        assert invoke(client).status_code == 200
    billing = BillingClient(
        settings.billing_base_url,
        transport=httpx.MockTransport(lambda request: httpx.Response(
            409, json={"error": {"code": "IDEMPOTENCY_CONFLICT"}, "retryable": False}
        )),
    )
    sessions = create_session_factory(settings.database_url)
    assert deliver_one(sessions, settings, billing)
    with sessions() as session:
        row = session.scalars(select(UsageOutbox)).one()
        assert row.status == "DEAD"
        assert row.last_error == "IDEMPOTENCY_CONFLICT"


def test_provider_usage_is_captured_even_for_empty_text():
    def respond(request):
        assert request.url.path == "/v1/messages"
        assert request.headers["anthropic-version"] == "2023-06-01"
        return httpx.Response(200, json={
            "model": "claude-sonnet-4-6",
            "content": [],
            "stop_reason": "refusal",
            "usage": {"input_tokens": 5, "output_tokens": 1},
        })

    provider = AnthropicProvider("test-key", "claude-sonnet-4-6", transport=httpx.MockTransport(respond))
    result = provider.generate("system", "context")
    assert result.input_tokens == 5
    assert result.output_tokens == 1
    assert result.text


def test_billing_does_not_acknowledge_wrong_receipt(tmp_path):
    settings, binding = setup(tmp_path)
    billing = BillingClient(
        settings.billing_base_url,
        transport=httpx.MockTransport(lambda request: httpx.Response(
            202, json={"data": {"event_id": "wrong-event", "status": "accepted"}}
        )),
    )
    result = billing.send(binding, json.dumps({"event_id": "01J00000000000000000000000"}))
    assert not result.delivered
    assert result.retryable
    assert result.error_code == "INVALID_BILLING_RECEIPT"
