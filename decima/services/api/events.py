"""The stream bus — a disposable, in-process fan-out of UI events (Phase 8).

The API streams assistant / plan / step / approval / error events to a connected
browser over a chunked, SSE-shaped response. That stream is a PROJECTION of things
that happened on the Weft, never a second store: the bus holds a bounded, append-only
buffer of already-recorded facts and can be dropped and rebuilt from the log at any
time (invariant 2). It mints no authority and executes nothing — it only relays.

Determinism: an event carries a monotonically increasing integer ``seq`` (a logical
cursor, never wall-clock — invariant 6) so a client can resume with ``since`` and a
test can assert an exact frame sequence. The buffer is bounded so a long-lived
process cannot grow it without limit.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass

# The event kinds (FAMILIES) the UI stream carries. Kept small and explicit — the
# stream is a view surface, not an arbitrary event channel. The Path-A feature lanes
# (Q&A / planning / workspace) emit ONLY within these families; adding a family is a
# shared-contract change, never a lane change.
ASSISTANT = "assistant"
PLAN = "plan"
STEP = "step"
APPROVAL = "approval"
ERROR = "error"
QUESTION = "question"
WORKSPACE = "workspace"
AGENT = "agent"
ARTIFACT = "artifact"
KINDS = frozenset({
    ASSISTANT, PLAN, STEP, APPROVAL, ERROR,
    QUESTION, WORKSPACE, AGENT, ARTIFACT,
})

# Dotted event names per family — the vocabulary the command/service handlers emit via
# ``EventBus.emit``. An event's ``data`` carries STABLE IDS + REFS into the Weft only:
# it never duplicates canonical state (the reader routes serve that from projections).
QUESTION_EVENTS = (
    "question.asked", "question.answered", "question.failed",
)
WORKSPACE_EVENTS = (
    "workspace.created", "workspace.run_started", "workspace.run_succeeded",
    "workspace.run_failed", "workspace.run_cancelled",
)
PLAN_EVENTS = (
    "plan.proposal_requested", "plan.proposal_ready", "plan.proposal_rejected",
    "plan.accepted", "plan.execution_started", "plan.paused", "plan.resumed",
    "plan.cancelled",
)
STEP_EVENTS = (
    "step.started", "step.succeeded", "step.failed", "step.cancelled",
)
AGENT_EVENTS = (
    "agent.spawned", "agent.status_changed", "agent.terminated",
)
APPROVAL_EVENTS = (
    "approval.requested", "approval.approved", "approval.denied",
)
ARTIFACT_EVENTS = (
    "artifact.produced", "artifact.imported", "artifact.exported",
)
FAMILY_EVENTS: dict[str, tuple[str, ...]] = {
    QUESTION: QUESTION_EVENTS,
    WORKSPACE: WORKSPACE_EVENTS,
    PLAN: PLAN_EVENTS,
    STEP: STEP_EVENTS,
    AGENT: AGENT_EVENTS,
    APPROVAL: APPROVAL_EVENTS,
    ARTIFACT: ARTIFACT_EVENTS,
}


@dataclass(frozen=True)
class StreamEvent:
    """One UI event: a logical ``seq``, a ``kind`` from ``KINDS``, and a JSON-safe
    ``data`` payload. ``data`` is DATA, not instruction — it may quote untrusted
    content, so a renderer must treat it as display text (invariant 5)."""

    seq: int
    kind: str
    data: dict

    def as_dict(self) -> dict:
        return {"seq": self.seq, "kind": self.kind, "data": self.data}

    def as_sse(self) -> bytes:
        """Encode as one SSE frame: an ``id:`` cursor, an ``event:`` kind, and a
        single-line ``data:`` JSON payload, terminated by a blank line."""
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        frame = f"id: {self.seq}\nevent: {self.kind}\ndata: {payload}\n\n"
        return frame.encode("utf-8")


class EventBus:
    """A bounded, thread-safe buffer of ``StreamEvent`` for the streaming endpoint.

    The bus is disposable: it retains at most ``maxlen`` recent events and holds no
    canonical state. ``publish`` stamps the next logical ``seq``; ``since`` returns
    the tail a client has not seen. It never blocks a mutation — publishing is a
    pure in-memory append under a short lock."""

    def __init__(self, maxlen: int = 1024) -> None:
        self._events: deque[StreamEvent] = deque(maxlen=maxlen)
        self._seq = 0
        self._lock = threading.Lock()

    def publish(self, kind: str, data: dict) -> StreamEvent:
        if kind not in KINDS:
            raise ValueError(f"unknown stream kind {kind!r}")
        with self._lock:
            self._seq += 1
            ev = StreamEvent(seq=self._seq, kind=kind, data=dict(data))
            self._events.append(ev)
            return ev

    def emit(self, name: str, **refs: object) -> StreamEvent:
        """The emit seam for dotted family events: ``emit("plan.accepted", id=...)``
        publishes to the ``plan`` family with ``data={"event": "plan.accepted", ...}``.

        ``refs`` must be stable ids/refs (JSON-safe), never canonical state. The
        family must be a declared KIND — an undeclared family is refused, so a lane
        cannot invent an event channel outside the shared contract."""
        family, _, leaf = name.partition(".")
        if not leaf or family not in KINDS:
            raise ValueError(f"unknown event family in {name!r}")
        data: dict = {"event": name}
        data.update(refs)
        return self.publish(family, data)

    def since(self, cursor: int = 0) -> list[StreamEvent]:
        """Every buffered event with ``seq > cursor``, in order."""
        with self._lock:
            return [e for e in self._events if e.seq > cursor]

    def frontier(self) -> int:
        with self._lock:
            return self._seq

    def sse_stream(self, cursor: int = 0) -> list[bytes]:
        """A snapshot of the tail as SSE frames. Finite by design: it drains the
        current buffer and ends, so a driven (in-process) response terminates
        deterministically rather than blocking a test on an open socket."""
        return [e.as_sse() for e in self.since(cursor)]
