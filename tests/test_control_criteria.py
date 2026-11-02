"""The five-way control question must carry explicit, non-empty criteria."""

from __future__ import annotations

from typing import Any

from hermes_nerve import nervous
from hermes_nerve.nervous import CONTROL_CHOICES, CONTROL_CRITERIA, CONTROL_INSTRUCTIONS, NervousSystem


def test_criteria_cover_every_control_choice() -> None:
    assert set(CONTROL_CRITERIA) == set(CONTROL_CHOICES)
    assert all(isinstance(v, str) and len(v) > 20 for v in CONTROL_CRITERIA.values())
    assert "CONTINUE" in CONTROL_INSTRUCTIONS


class _CapturingEngine:
    def __init__(self, sink: list[dict[str, Any]]) -> None:
        self.sink = sink

    def assess(self, *, state: Any, questions: dict[str, Any], **_: Any) -> dict[str, Any]:
        self.sink.append(questions)
        return {
            "answers": {
                "intervention": {"value": False, "confidence": 0.9},
                "next_control": {"choice": "CONTINUE", "confidence": 0.9, "probabilities": {"CONTINUE": 0.9}},
            },
            "latency_ms": 1.0,
            "usage": {},
            "request_id": "",
        }

    def decide(self, **_: Any) -> Any:  # pragma: no cover - not used by tool observations
        raise AssertionError("decide path not expected")


def test_assess_sends_criteria_to_judge(tmp_path, monkeypatch) -> None:
    for name in ("NERVOUS_EVENTS", "OUTCOMES", "RECEIPTS", "GATE_EVENTS", "CONTEXT_LEDGER"):
        monkeypatch.setenv(f"HERMES_NERVE_{name}", str(tmp_path / f"{name.lower()}.jsonl"))
    monkeypatch.setenv("HERMES_NERVE_ASSISTANT_DIR", str(tmp_path / "assistant"))
    sent: list[dict[str, Any]] = []
    system = NervousSystem(engine_factory=lambda: _CapturingEngine(sent))
    system.configure(enabled=True, admission_enabled=False, mode="shadow", local_learning=False, call_threshold=0.0)
    turn = system.start_turn(user_message="Summarize the settings file.", session_id="s", turn_id="t")
    system.observe_tool_call(tool_name="read_file", args={"path": "a.yaml"}, status="ok", result="read",
                             turn_id=turn, session_id="s")
    system.drain(timeout=5.0)
    system.finish_turn(turn_id=turn, session_id="s", assistant_response="ok")
    system.configure(enabled=False)
    assert sent, "judge was not called"
    control = sent[-1]["next_control"]
    assert control["instructions"] == nervous.CONTROL_INSTRUCTIONS
    assert control["criteria"] == nervous.CONTROL_CRITERIA
    assert all(control["criteria"][label] for label in CONTROL_CHOICES)
