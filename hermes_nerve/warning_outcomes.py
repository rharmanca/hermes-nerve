"""Content-free right/wrong tracking for Nerve warnings and nudges.

Every row written here is built from a strict allow-list of fields. Only
identifiers, timestamps, decision type, rule/category tokens, a numeric
confidence and an outcome label are stored. Message text, tool arguments,
file paths/contents, prompts and free-text reasons are never copied: any value
that is not a short identifier-like token is dropped rather than truncated.

File: ``<HERMES_HOME>/nerve/warning-outcomes.jsonl`` (override with
``HERMES_NERVE_WARNING_OUTCOMES``). Writing never raises into the caller.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any

from .paths import hermes_home

SCHEMA = "nerve.warning_outcome.v1"
ALLOWED_FIELDS = (
    "schema",
    "ts",
    "turn_id",
    "decision_id",
    "event_id",
    "decision_type",
    "rule",
    "stage",
    "confidence",
    "outcome",
)
OUTCOME_LABELS = frozenset({
    "issued",
    "shadow_recorded",
    "delivered",
    "nudged",
    "followed",
    "ignored",
    "enforced",
    "expired",
    "proven_right",
    "proven_wrong",
    "outcome_success",
    "outcome_failure",
    "outcome_unlabelled",
})
_TOKEN = re.compile(r"^[A-Za-z0-9_.:\-]{1,96}$")
_CACHE_MAX = 4096

COMPLETION_REASON_CODES = {
    "no task-relevant mutation was attempted": "completion_no_relevant_attempt",
    "verification reports no task-relevant changed path": "completion_no_relevant_persisted",
    "the requested verification/test has not passed": "completion_missing_verification",
}


def warning_outcome_path() -> Path:
    explicit = os.getenv("HERMES_NERVE_WARNING_OUTCOMES", "").strip()
    return Path(explicit).expanduser() if explicit else hermes_home() / "nerve" / "warning-outcomes.jsonl"


def _token(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if _TOKEN.match(text) else None


def _confidence(value: Any) -> float | None:
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return None
    if conf != conf or conf < 0.0 or conf > 1.0:
        return None
    return round(conf, 4)


def sanitize(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return an allow-listed copy of ``row`` or ``None`` if it has no valid outcome."""
    outcome = row.get("outcome")
    if outcome not in OUTCOME_LABELS:
        return None
    clean: dict[str, Any] = {"schema": SCHEMA, "outcome": outcome}
    try:
        clean["ts"] = round(float(row.get("ts")), 3)
    except (TypeError, ValueError):
        clean["ts"] = round(time.time(), 3)
    for key in ("turn_id", "decision_id", "event_id", "decision_type", "rule", "stage"):
        tok = _token(row.get(key))
        if tok is not None:
            clean[key] = tok
    conf = _confidence(row.get("confidence"))
    if conf is not None:
        clean["confidence"] = conf
    assert set(clean) <= set(ALLOWED_FIELDS)
    return clean


_LIFECYCLE_LABELS = {
    ("created", "active"): "issued",
    ("created", "shadow"): "shadow_recorded",
    "followed": "followed",
    "nudged": "nudged",
    "overridden": "ignored",
    "rejected": "ignored",
    "enforced": "enforced",
    "turn-ended": "expired",
    "superseded": "expired",
    "expired": "expired",
}


class WarningOutcomeTracker:
    def __init__(self, path: Path | None = None) -> None:
        self._explicit = path
        self._lock = threading.Lock()
        self._decisions: OrderedDict[str, tuple[str | None, float | None]] = OrderedDict()

    @property
    def path(self) -> Path:
        return self._explicit or warning_outcome_path()

    def _remember(self, decision_id: str | None, decision_type: Any, confidence: Any) -> None:
        if not decision_id:
            return
        with self._lock:
            self._decisions[decision_id] = (_token(decision_type), _confidence(confidence))
            self._decisions.move_to_end(decision_id)
            while len(self._decisions) > _CACHE_MAX:
                self._decisions.popitem(last=False)

    def _lookup(self, decision_id: str | None) -> tuple[str | None, float | None]:
        if not decision_id:
            return None, None
        with self._lock:
            return self._decisions.get(decision_id, (None, None))

    def record(self, **fields: Any) -> dict[str, Any] | None:
        try:
            did = _token(fields.get("decision_id"))
            if did and (fields.get("decision_type") is None or fields.get("confidence") is None):
                dtype, conf = self._lookup(did)
                fields.setdefault("decision_type", dtype)
                if fields.get("decision_type") is None:
                    fields["decision_type"] = dtype
                if fields.get("confidence") is None:
                    fields["confidence"] = conf
            clean = sanitize(fields)
            if clean is None:
                return None
            path = self.path
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(clean, sort_keys=True, separators=(",", ":")) + "\n"
            with self._lock:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(line)
            return clean
        except Exception:
            return None

    def observe(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Map an outcome-ledger row (decision/lifecycle/outcome) to a tracker row."""
        try:
            rtype = row.get("record_type")
            did = _token(row.get("decision_id"))
            if rtype == "decision":
                self._remember(did, row.get("decision_type"), row.get("jev_confidence"))
                return None
            if rtype == "control_lifecycle":
                stage = str(row.get("stage") or "").lower()
                disp = str(row.get("challenge_disposition") or "").lower()
                label = _LIFECYCLE_LABELS.get((stage, disp)) or (
                    "delivered" if stage == "delivered" else _LIFECYCLE_LABELS.get(disp)
                )
                if label is None:
                    return None
                return self.record(
                    ts=row.get("timestamp"), turn_id=row.get("turn_id"), decision_id=did,
                    event_id=row.get("event_id"), rule=row.get("control"), stage=stage,
                    outcome=label,
                )
            if rtype == "outcome":
                if row.get("useful_disagreement") is True:
                    label = "proven_right"
                elif row.get("useful_disagreement") is False:
                    label = "proven_wrong"
                elif row.get("successful") is True:
                    label = "outcome_success"
                elif row.get("successful") is False:
                    label = "outcome_failure"
                else:
                    label = "outcome_unlabelled"
                return self.record(
                    ts=row.get("timestamp"), turn_id=row.get("turn_id"), decision_id=did,
                    event_id=row.get("event_id"), decision_type=row.get("decision_type"),
                    rule=row.get("jev_decision"), stage="outcome", outcome=label,
                )
        except Exception:
            return None
        return None


def counts(path: Path | None = None) -> dict[str, Any]:
    """Counts-only summary for health checks and scorecards."""
    target = path or warning_outcome_path()
    by_outcome: Counter[str] = Counter()
    rows = 0
    if target.exists():
        with open(target, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                rows += 1
                by_outcome[str(rec.get("outcome") or "unknown")] += 1
    return {"rows": rows, "by_outcome": dict(sorted(by_outcome.items()))}


_TRACKER = WarningOutcomeTracker()


def tracker() -> WarningOutcomeTracker:
    return _TRACKER
