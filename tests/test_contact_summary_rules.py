"""crm.contact_summary v1 rules, standard library only (commands.yaml: tests.rules).

Do not import httpx, FastAPI or SQLAlchemy here: the Worker sandbox has no ssl and no loopback.
"""

import json
import unittest

from app.contact_summary import (
    NEXT_STEP_MAX_CHARS,
    OUTPUT_SCHEMA,
    SUMMARY_MAX_CHARS,
    InvalidOutput,
    model_input,
    output_instructions,
    parse_output,
    snapshot_size,
)


def answer(summary="客户要求周五回电。", next_step="周五回电。"):
    return json.dumps({"summary": summary, "suggested_next_step": next_step}, ensure_ascii=False)


class SnapshotSizeTests(unittest.TestCase):
    def test_size_is_compact_utf8_bytes(self):
        # Pretty-printing must not count against CRM, but multibyte text does.
        self.assertEqual(snapshot_size({"a": 1}), len(b'{"a":1}'))
        self.assertEqual(snapshot_size({"n": "陈"}), len('{"n":"陈"}'.encode("utf-8")))


class ModelInputTests(unittest.TestCase):
    def test_contact_ref_never_reaches_the_model(self):
        sent = json.loads(model_input("2026-10-10T09:30:00+08:00", "crm-contact-1", {"stage": "won"}))
        self.assertEqual(set(sent), {"as_of", "contact_schema_version", "contact_snapshot"})
        self.assertEqual(sent["contact_snapshot"], {"stage": "won"})

    def test_instructions_name_the_language_and_limits(self):
        for code, language in (("zh-CN", "Simplified Chinese"), ("en", "English"), ("ms", "Malay")):
            with self.subTest(code=code):
                text = output_instructions(code)
                self.assertIn(language, text)
                self.assertIn(str(SUMMARY_MAX_CHARS), text)
                self.assertIn(str(NEXT_STEP_MAX_CHARS), text)

    def test_output_schema_is_closed(self):
        # Structured outputs require additionalProperties false; both fields are always present.
        self.assertIs(OUTPUT_SCHEMA["additionalProperties"], False)
        self.assertEqual(set(OUTPUT_SCHEMA["required"]), set(OUTPUT_SCHEMA["properties"]))


class ParseOutputTests(unittest.TestCase):
    def test_valid_answer_is_trimmed(self):
        self.assertEqual(parse_output(answer(" 摘要 ", " 下一步 "), "end_turn"), ("摘要", "下一步"))

    def test_empty_next_step_is_allowed(self):
        self.assertEqual(parse_output(answer(next_step=""), "end_turn")[1], "")

    def test_limits_are_inclusive_and_counted_in_characters(self):
        # 600 Chinese characters are 1800 UTF-8 bytes; the limit is on characters.
        self.assertEqual(len(parse_output(answer("字" * SUMMARY_MAX_CHARS), "end_turn")[0]), SUMMARY_MAX_CHARS)
        self.assertEqual(len(parse_output(answer(next_step="步" * NEXT_STEP_MAX_CHARS), "end_turn")[1]), NEXT_STEP_MAX_CHARS)

    def test_only_a_completed_turn_is_usable(self):
        # A refusal or a truncated answer may not match the schema even when it parses.
        for stop_reason in ("refusal", "max_tokens", None):
            with self.subTest(stop_reason=stop_reason), self.assertRaises(InvalidOutput):
                parse_output(answer(), stop_reason)

    def test_unusable_answers_raise(self):
        for text in (
            "not json",
            "[]",
            json.dumps({"summary": "x"}),
            json.dumps({"summary": "x", "suggested_next_step": "", "extra": 1}),
            json.dumps({"summary": 1, "suggested_next_step": ""}),
            answer(summary="   "),
            answer(summary="字" * (SUMMARY_MAX_CHARS + 1)),
            answer(next_step="步" * (NEXT_STEP_MAX_CHARS + 1)),
        ):
            with self.subTest(text=text[:40]), self.assertRaises(InvalidOutput):
                parse_output(text, "end_turn")

    def test_error_messages_do_not_echo_model_text(self):
        # Errors are logged; model text may carry customer data.
        with self.assertRaises(InvalidOutput) as caught:
            parse_output("客户电话 0123456789", "end_turn")
        self.assertNotIn("0123456789", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
