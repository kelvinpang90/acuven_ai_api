"""crm.contact_summary v1 rules (docs/CONTACT_SUMMARY_V1.md).

Standard library only, so the OpenClaw Worker sandbox can test them.
"""

from __future__ import annotations

import json

SCHEMA_VERSION = "1.0"
SNAPSHOT_MAX_BYTES = 16384
REQUEST_MAX_BYTES = 20480
SUMMARY_MAX_CHARS = 600
NEXT_STEP_MAX_CHARS = 240
LANGUAGES = {"zh-CN": "Simplified Chinese", "en": "English", "ms": "Malay"}

# Structured outputs cannot bound string length; the system prompt states it and parse_output enforces it.
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "suggested_next_step": {"type": "string"},
    },
    "required": ["summary", "suggested_next_step"],
    "additionalProperties": False,
}


class InvalidOutput(ValueError):
    """The model answered (tokens were spent) but the answer cannot be returned."""


def snapshot_size(snapshot) -> int:
    """Bytes of the compact UTF-8 serialization, the size the contract limits."""
    return len(json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def output_instructions(output_language: str) -> str:
    return (
        f"Write in {LANGUAGES[output_language]}. "
        f"Return `summary` in at most {SUMMARY_MAX_CHARS} characters; if facts are missing, say which. "
        f"Return `suggested_next_step` in at most {NEXT_STEP_MAX_CHARS} characters, "
        "or an empty string when the facts support no next step."
    )


def model_input(as_of: str, contact_schema_version: str, contact_snapshot: dict) -> str:
    """The facts sent to the model. contact_ref is deliberately absent: it is only for the caller."""
    return json.dumps(
        {"as_of": as_of, "contact_schema_version": contact_schema_version, "contact_snapshot": contact_snapshot},
        ensure_ascii=False,
    )


def parse_output(text: str, stop_reason: str | None) -> tuple[str, str]:
    """Return (summary, suggested_next_step) or raise InvalidOutput. Error messages never echo model text."""
    if stop_reason != "end_turn":
        # refusal or max_tokens: the JSON may be absent, partial or off-schema.
        raise InvalidOutput(f"stop_reason={stop_reason}")
    try:
        data = json.loads(text)
    except ValueError:
        raise InvalidOutput("not JSON") from None
    if not isinstance(data, dict) or set(data) != {"summary", "suggested_next_step"}:
        raise InvalidOutput("unexpected fields")
    summary, next_step = data["summary"], data["suggested_next_step"]
    if not isinstance(summary, str) or not isinstance(next_step, str):
        raise InvalidOutput("non-string field")
    summary, next_step = summary.strip(), next_step.strip()
    if not 1 <= len(summary) <= SUMMARY_MAX_CHARS:
        raise InvalidOutput("summary length")
    if len(next_step) > NEXT_STEP_MAX_CHARS:
        raise InvalidOutput("suggested_next_step length")
    return summary, next_step
