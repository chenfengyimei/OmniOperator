"""Pure rules for structured clarification: question normalization, answer cleaning and outcomes.

The model asks 1-5 independent questions in one call; the current run pauses for the owner and
resumes with structured responses. This module only validates and shapes — it never waits, dispatches
or touches storage. Presentation (the recommended label, multi-select JSON) never leaks into answers.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from typing import cast

from .contracts import JSON, BusinessError, ToolSpec

MAX_QUESTIONS = 5
MAX_CHOICES = 4
MAX_CHOICE_CHARS = 8000
MAX_QUESTION_CHARS = 4000
CLARIFY_TIMEOUT_S = 300.0
RECOMMENDED_LABEL = "(Recommended)"
OUTCOMES = frozenset({"submitted", "cancelled", "timed_out", "undelivered"})
RESPONSE_STATUSES = frozenset({"answered", "skipped", "unanswered"})

CLARIFY = ToolSpec(
    "clarify",
    "Ask the owner 1-5 structured questions and pause this run until they respond, cancel or it times out. "
    "Pass questions=[{question, choices?, multi_select?}]; put the recommended choice FIRST (it is marked "
    "'(Recommended)'); omit choices for free-text. Options go only in choices, never enumerated in the question "
    "text. Returns {responses:[{question, choices_offered, status, user_response}], outcome} in question order; "
    "status is answered/skipped/unanswered and outcome is submitted/cancelled/timed_out/undelivered. Prefer deciding "
    "low-stakes questions yourself; this is not for dangerous-command confirmation (write approval handles that).",
    {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_QUESTIONS,
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "minLength": 1, "maxLength": MAX_QUESTION_CHARS},
                        "choices": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_CHOICES},
                        "multi_select": {"type": "boolean"},
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["questions"],
        "additionalProperties": False,
    },
)


def _invalid(message: str) -> BusinessError:
    return BusinessError("invalid_clarify", message, status=422)


def _text(value: object, max_chars: int, field: str) -> str:
    if not isinstance(value, str):
        raise _invalid(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise _invalid(f"{field} must be non-empty")
    if len(text) > max_chars:
        raise _invalid(f"{field} exceeds {max_chars} characters")
    if any(unicodedata.category(ch) == "Cc" and ch not in "\n\r\t" for ch in text):
        raise _invalid(f"{field} contains control characters")
    return text


def strip_recommended(text: str) -> str:
    stripped = text.strip()
    if stripped.casefold().endswith(RECOMMENDED_LABEL.casefold()):
        return stripped[: -len(RECOMMENDED_LABEL)].strip()
    return stripped


def mark_recommended(choices: list[str]) -> list[str]:
    first = choices[0].strip() if choices else ""
    if len(choices) < 2 or first != strip_recommended(first):
        return choices
    return [f"{first} {RECOMMENDED_LABEL}", *choices[1:]]


def normalize_questions(questions: object) -> list[dict[str, JSON]]:
    if not isinstance(questions, list) or not questions:
        raise _invalid("questions must be a non-empty array of {question, choices?, multi_select?}")
    items = cast(list[object], questions)
    if len(items) > MAX_QUESTIONS:
        raise _invalid(f"questions supports at most {MAX_QUESTIONS} items")
    normalized: list[dict[str, JSON]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise _invalid(f"questions[{index}] must be an object")
        entry = cast(dict[str, object], item)
        text = _text(entry.get("question"), MAX_QUESTION_CHARS, f"questions[{index}].question")
        raw_choices = entry.get("choices")
        choices: list[str] | None = None
        if raw_choices is not None:
            if not isinstance(raw_choices, list) or not all(
                isinstance(c, str) for c in cast(list[object], raw_choices)
            ):
                raise _invalid(f"questions[{index}].choices must be a list of strings")
            cleaned: list[str] = []
            for pos, choice in enumerate(cast(list[str], raw_choices)):
                label = _text(choice, MAX_CHOICE_CHARS, f"questions[{index}].choices[{pos}]")
                if label not in cleaned:
                    cleaned.append(label)
            choices = cleaned[:MAX_CHOICES] or None
            if raw_choices and choices is None:
                raise _invalid(f"questions[{index}].choices are all blank; send real labels or omit choices")
        multi = bool(entry.get("multi_select")) and choices is not None
        normalized.append(
            cast(
                dict[str, JSON],
                {
                    "qid": f"q{index}",
                    "question": text,
                    "choices": mark_recommended(list(choices)) if choices else None,
                    "choices_offered": list(choices) if choices else None,
                    "multi_select": multi,
                },
            )
        )
    return normalized


def _clean_answer(raw: object, multi: bool) -> str | list[str] | None:
    if not multi:
        cleaned = strip_recommended(raw) if isinstance(raw, str) else ""
        return cleaned or None
    values: list[object]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        values = cast(list[object], parsed) if isinstance(parsed, list) else [raw]
    elif isinstance(raw, list):
        values = cast(list[object], raw)
    else:
        return None
    out = [strip_recommended(item) for item in values if isinstance(item, str) and item.strip()]
    return out or None


def response_status(qid: str, answers: Mapping[str, JSON], multi: bool) -> tuple[str, str | list[str] | None]:
    raw = answers.get(qid)
    cleaned = _clean_answer(raw, multi) if raw not in (None, "") else None
    if cleaned:
        return "answered", cleaned
    return ("skipped" if qid in answers else "unanswered"), None


def build_result(
    normalized: list[dict[str, JSON]], answers: Mapping[str, JSON], outcome: str, notice: str = ""
) -> dict[str, JSON]:
    if outcome not in OUTCOMES:
        raise _invalid(f"unknown clarify outcome: {outcome}")
    responses: list[dict[str, JSON]] = []
    for entry in normalized:
        multi = bool(entry.get("multi_select"))
        status, value = response_status(str(entry["qid"]), answers, multi)
        responses.append(
            cast(
                dict[str, JSON],
                {
                    "question": entry.get("question"),
                    "choices_offered": entry.get("choices_offered"),
                    "status": status,
                    "user_response": value,
                },
            )
        )
    result: dict[str, JSON] = cast(dict[str, JSON], {"responses": responses, "outcome": outcome})
    if notice:
        result["notice"] = notice
    return result


def render_result(result: Mapping[str, JSON]) -> str:
    outcome = str(result.get("outcome"))
    responses = result.get("responses")
    items = responses if isinstance(responses, list) else []
    lines = [f"Clarify {outcome}."]
    for entry in items:
        if isinstance(entry, dict):
            value = entry.get("user_response")
            answer = json.dumps(value, ensure_ascii=False) if isinstance(value, list) else str(value)
            lines.append(f"- {entry.get('question')} [{entry.get('status')}]: {answer if value else '(no answer)'}")
    notice = result.get("notice")
    if notice:
        lines.append(str(notice))
    return "\n".join(lines)
