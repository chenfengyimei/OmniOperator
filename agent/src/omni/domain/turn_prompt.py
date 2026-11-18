"""Freeze one run's system prefix; publish changed runtime guidance in the message tail."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import JSON, BusinessError


def append_runtime_notice(messages: list[dict[str, JSON]], notice: str) -> None:
    if not notice:
        return
    content = (
        "Runtime context update (state and reviewed guidance, not a new user request or authorization):\n" + notice
    )
    if messages[-1].get("role") == "user":
        previous = messages[-1].get("content")
        if isinstance(previous, str):
            messages[-1] = {**messages[-1], "content": previous + "\n\n" + content}
        elif isinstance(previous, list):
            messages[-1] = {**messages[-1], "content": [*previous, {"type": "text", "text": content}]}
        else:
            raise BusinessError("invalid_turn_prompt", "The current user message has an unsupported shape")
    else:
        messages.append({"role": "user", "content": content})


@dataclass
class TurnPrompt:
    _system: str
    _notice: str

    @classmethod
    def capture(cls, messages: list[dict[str, JSON]], notice: str) -> TurnPrompt:
        if not messages or messages[0].get("role") != "system" or not isinstance(messages[0].get("content"), str):
            raise BusinessError("invalid_turn_prompt", "A run requires its captured system message")
        return cls(str(messages[0]["content"]), notice)

    def update(self, messages: list[dict[str, JSON]], notice: str) -> None:
        if not messages or messages[0].get("role") != "system" or messages[0].get("content") != self._system:
            raise BusinessError("system_prompt_changed", "The captured system prefix changed during this run")
        if notice == self._notice:
            return
        append_runtime_notice(messages, notice)
        self._notice = notice
