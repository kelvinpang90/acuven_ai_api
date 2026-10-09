"""Configuration and per-client trust bindings. Secrets are never supplied by callers."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass


CAPABILITIES = {
    "crm.contact_summary": "Summarize the CRM contact facts and suggest one next step. Do not invent facts.",
    "erp.document_summary": "Summarize the ERP document facts. Flag missing information. Do not approve or post a document.",
    "shop.order_summary": "Summarize the shop order facts. Do not change the order or promise a refund.",
    "inventory.stock_explanation": "Explain the inventory facts and possible causes. Do not change stock quantities.",
    "pos.sale_summary": "Summarize the POS sale facts. Do not modify prices, payments or refunds.",
}


@dataclass(frozen=True)
class ClientBinding:
    client_id: str
    token_sha256: str
    tenant_id: str
    billing_project_id: str
    billing_api_key: str
    billing_secret: str
    billing_key_version: int
    capabilities: frozenset[str]

    def accepts(self, token: str) -> bool:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return hmac.compare_digest(self.token_sha256, digest)


@dataclass(frozen=True)
class Settings:
    database_url: str
    clients: dict[str, ClientBinding]
    anthropic_key: str
    anthropic_model: str
    billing_base_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        raw = json.loads(os.environ.get("AI_API_CLIENTS_JSON", "[]"))
        if not isinstance(raw, list):
            raise ValueError("AI_API_CLIENTS_JSON must be a list")
        clients: dict[str, ClientBinding] = {}
        for item in raw:
            binding = ClientBinding(
                client_id=item["client_id"],
                token_sha256=item["token_sha256"],
                tenant_id=item["tenant_id"],
                billing_project_id=item["billing_project_id"],
                billing_api_key=item["billing_api_key"],
                billing_secret=item["billing_secret"],
                billing_key_version=int(item["billing_key_version"]),
                capabilities=frozenset(item["capabilities"]),
            )
            if len(binding.token_sha256) != 64 or not all(c in "0123456789abcdef" for c in binding.token_sha256):
                raise ValueError("client token_sha256 must be lowercase SHA-256 hex")
            if binding.client_id in clients or not binding.capabilities <= CAPABILITIES.keys():
                raise ValueError("duplicate client or unknown capability")
            clients[binding.client_id] = binding
        return cls(
            database_url=os.environ.get("AI_API_DATABASE_URL", "sqlite:///./acuven_ai_api.db"),
            clients=clients,
            anthropic_key=os.environ.get("AI_API_ANTHROPIC_KEY", ""),
            anthropic_model=os.environ.get("AI_API_ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            billing_base_url=os.environ.get("AI_API_BILLING_BASE_URL", ""),
        )
