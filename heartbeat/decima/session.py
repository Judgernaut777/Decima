"""Session / process Cells — multiplex shells, agents, and logs as the fold, not
as terminal panes (D2; VISION "session Cells … tmux-native, not tmux").

A live process — a shell, a build, a sub-agent's REPL — is naturally a *stream*:
output arrives chunk by chunk over time, viewers attach and detach, and you want
to scroll back. The terminal-multiplexer answer is a pane backed by a scrollback
buffer in RAM. Decima's answer is Law 5: a session is a **Cell**, and every chunk
of output is an **event appended to the Weft**. The scrollback is not stored — it
is the *fold* of those events. That single property gives, for free:

  • replay / scrollback — fold the session's events to reconstruct the transcript;
  • time-travel — fold `upto_seq` to see the session as of any past point;
  • attach / detach — a viewer is just a reader of the fold; attach/detach are
    themselves recorded control events, so "who was watching when" is auditable;
  • durability + tamper-evidence — the transcript inherits the Weft's signed,
    hash-chained guarantees (a RAM scrollback has none of these).

Model (all DATA on the Log — no kernel edit; this module calls the public API):

  session        — a Cell (LWW register for its metadata: name, kind, status).
  session_event  — one Cell per chunk: {session, seq, stream, data}. `stream` is
                   "stdout" | "stderr" | "control". `seq` is a monotonic order key
                   (the Weft lamport at write time) so replay is arrival-order
                   independent (FOLD §11): sort by seq, not by delivery order.
  emits          — an EDGE  session ──emits──▶ session_event, so the fold can
                   gather a session's stream both ways (provenance + projection).

A PTY is stubbed: callers push bytes via `write()`. The Cell/fold model is the
point, not the terminal I/O. This module has NO authority — opening a session and
writing to it are ASSERTs of data; `capability.authorize` still gates any real
effect (e.g. the capability that actually spawns the process).
"""
from decima.weft import ASSERT
from decima.hashing import content_id, nfc

SESSION = "session"
SESSION_EVENT = "session_event"
EMITS = "emits"                      # edge rel: session ──emits──▶ session_event

STDOUT, STDERR, CONTROL = "stdout", "stderr", "control"
# control actions recorded on the stream (last one wins when folding `attached`).
ATTACH, DETACH = "attach", "detach"


# ── write side: append-only stream events ───────────────────────────────────
def open_session(weft, author: str, name: str, *, kind: str = "pty",
                 meta: dict | None = None) -> str:
    """Open a session Cell. Idempotent by (name, kind): re-opening the same named
    session lands on the same id, so a reconnect reuses the stream."""
    sid = content_id({"session": nfc(name), "kind": kind})
    content = {"name": nfc(name), "kind": kind, "status": "open"}
    if meta:
        content["meta"] = meta
    weft.append(author, ASSERT, {
        "cell": sid, "type": SESSION, "kind": "CONTENT", "content": content,
    })
    return sid


def _emit(weft, author: str, session_id: str, stream: str, data: str) -> str:
    """Append one stream event and link it to the session. `seq` is the Weft
    lamport captured at call time — a monotonic total-order key that makes replay
    independent of the order events are later delivered/folded."""
    seq = weft.lamport                                  # monotonic, pre-append
    eid = content_id({"session": session_id, "seq": seq,
                      "stream": stream, "data": data})
    weft.append(author, ASSERT, {
        "cell": eid, "type": SESSION_EVENT, "kind": "CONTENT",
        "content": {"session": session_id, "seq": seq,
                    "stream": stream, "data": data},
    })
    weft.append(author, ASSERT, {"kind": "EDGE",
                                 "src": session_id, "rel": EMITS, "dst": eid})
    return eid


def write(weft, author: str, session_id: str, data: str,
          *, stream: str = STDOUT) -> str:
    """Push a chunk of output onto the session's stream (stdout/stderr)."""
    return _emit(weft, author, session_id, stream, data)


def control(weft, author: str, session_id: str, action: str) -> str:
    """Record a control event (attach/detach/…) on the session's stream."""
    return _emit(weft, author, session_id, CONTROL, action)


def attach(weft, author: str, session_id: str) -> str:
    return control(weft, author, session_id, ATTACH)


def detach(weft, author: str, session_id: str) -> str:
    return control(weft, author, session_id, DETACH)


def close(weft, author: str, session_id: str, *, status: str = "closed") -> str:
    """Mark the session finished. Session metadata is an LWW register, so this is
    a fresh CONTENT version that supersedes the prior status (FOLD §4)."""
    from decima.weave import Weave  # local import: read current content to preserve fields
    cur = Weave.fold(weft).get(session_id)
    base = dict(cur.content) if cur else {}
    base["status"] = status
    weft.append(author, ASSERT, {
        "cell": session_id, "type": SESSION, "kind": "CONTENT", "content": base,
    })
    return session_id


# ── read side: projections of the fold ──────────────────────────────────────
def sessions(weave) -> list:
    """All live session Cells."""
    return weave.of_type(SESSION)


def events_of(weave, session_id: str, *, streams=None) -> list:
    """The session's stream events, in monotonic `seq` order (NOT delivery order).
    `streams` optionally filters to a subset (e.g. just stdout/stderr)."""
    sess = weave.get(session_id)
    if sess is None:
        return []
    out = []
    for edge in sess.edges_out:
        if edge["rel"] != EMITS:
            continue
        cell = weave.get(edge["dst"])
        if cell is None or cell.retracted:
            continue
        if streams is not None and cell.content.get("stream") not in streams:
            continue
        out.append(cell)
    out.sort(key=lambda c: c.content.get("seq", 0))
    return out


def replay(weave, session_id: str, *, streams=(STDOUT, STDERR)) -> str:
    """Reconstruct the session transcript by folding its stream events. This IS the
    scrollback — nothing was buffered; it is recomputed from the Log every time."""
    return "".join(c.content.get("data", "")
                   for c in events_of(weave, session_id, streams=streams))


def transcript(weave, session_id: str, *, streams=(STDOUT, STDERR)) -> list:
    """The transcript as ordered (stream, data) pairs — for a richer projection."""
    return [(c.content.get("stream"), c.content.get("data", ""))
            for c in events_of(weave, session_id, streams=streams)]


def status(weave, session_id: str) -> str | None:
    sess = weave.get(session_id)
    return sess.content.get("status") if sess else None


def attached(weave, session_id: str) -> bool:
    """Is a viewer currently attached? Fold the control events; last action wins."""
    ctrl = events_of(weave, session_id, streams=(CONTROL,))
    state = False
    for c in ctrl:
        action = c.content.get("data")
        if action == ATTACH:
            state = True
        elif action == DETACH:
            state = False
    return state
