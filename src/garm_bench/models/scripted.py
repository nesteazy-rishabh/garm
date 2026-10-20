"""Scripted models for validating episodes and graders without API calls.

`ScriptedModel` replays a per-job list of tool calls (an episode's oracle). Arguments can reference earlier results
with "$name.field", where `name` was given as "save" on an earlier step in the same episode. `NullModel` does
nothing, so a correct grader should fail it on any episode that needs action.
"""

from __future__ import annotations

import json
from typing import Any

from .base import ModelTurn, ToolCall


class NullModel:
    model_id = "null"

    async def generate(self, system, tools, messages, max_tokens) -> ModelTurn:
        return ModelTurn(text="Nothing to do.", tool_calls=[], stop_reason="end_turn")


class ScriptedModel:
    model_id = "scripted-oracle"

    def __init__(self, script: dict[str, list[list[dict]]]):
        self.script = script
        self.runs_seen: dict[str, int] = {}
        self.saved: dict[str, Any] = {}
        self._steps: list[dict] = []
        self._pending_save: str | None = None
        self._n = 0

    def on_job_start(self, job: str) -> None:
        idx = self.runs_seen.get(job, 0)
        self.runs_seen[job] = idx + 1
        runs = self.script.get(job, [])
        self._steps = list(runs[idx]) if idx < len(runs) else []
        self._pending_save = None

    def _resolve(self, value: Any) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            name, _, path = value[1:].partition(".")
            obj = self.saved[name]
            for part in filter(None, path.split(".")):
                obj = obj[part]
            return obj
        if isinstance(value, dict):
            return {k: self._resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve(v) for v in value]
        return value

    async def generate(self, system, tools, messages, max_tokens) -> ModelTurn:
        last = messages[-1]
        if self._pending_save and last["role"] == "tool_results":
            result = last["results"][0]
            if result["is_error"]:
                raise RuntimeError(f"oracle step failed: {result['content']}")
            self.saved[self._pending_save] = json.loads(result["content"])
            self._pending_save = None
        elif last["role"] == "tool_results" and last["results"][0]["is_error"]:
            raise RuntimeError(f"oracle step failed: {last['results'][0]['content']}")
        if not self._steps:
            return ModelTurn(text="Done.", tool_calls=[], stop_reason="end_turn")
        step = self._steps.pop(0)
        self._pending_save = step.get("save")
        self._n += 1
        call = ToolCall(id=f"oracle-{self._n}", name=step["tool"], input=self._resolve(step.get("args", {})))
        return ModelTurn(text="", tool_calls=[call], stop_reason="tool_use")
