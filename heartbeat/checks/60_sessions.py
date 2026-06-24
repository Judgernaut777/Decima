"""D2 — Session / process Cells: streams fold; attach/detach/replay (and
time-travel), all reconstructed from the Weft rather than a RAM scrollback."""
from decima import session
from decima.weave import Weave


def run(k, line):
    line("\n== SESSION / PROCESS CELLS (streams fold · attach/detach · replay) ==")
    author = k.decima_agent_id

    sid = session.open_session(k.weft, author, "build-1", kind="pty")
    session.write(k.weft, author, sid, "make: entering 'src'\n")
    session.attach(k.weft, author, sid)                 # a viewer connects
    session.write(k.weft, author, sid, "cc -c main.c\n")
    frontier = k.weft.count()                           # checkpoint: mid-build
    session.write(k.weft, author, sid, "warning: unused var 'x'\n", stream="stderr")
    session.detach(k.weft, author, sid)                 # viewer disconnects
    attached_at_frontier = session.attached(k.weave(upto_seq=frontier), sid)
    session.write(k.weft, author, sid, "build ok\n")
    session.close(k.weft, author, sid, status="exited")

    w = k.weave()

    # 1) Replay: the transcript is the FOLD of the stream events — nothing buffered.
    full = session.replay(w, sid)
    assert "make: entering" in full and "build ok" in full, full
    assert full.index("entering") < full.index("build ok"), "stream order lost"
    streams = {s for s, _ in session.transcript(w, sid)}
    assert streams == {"stdout", "stderr"}, streams
    data_chunks = session.events_of(w, sid, streams=("stdout", "stderr"))
    line(f"  replay reconstructs {len(data_chunks)} output chunks "
         f"({len(full)} bytes) from the fold — no scrollback buffer")

    # 2) Lifecycle folds: status is an LWW register; attach/detach fold to a bit.
    assert session.status(w, sid) == "exited"
    assert session.attached(w, sid) is False, "detach not folded"
    assert attached_at_frontier is True, "attach state wrong at the frontier"
    line(f"  lifecycle: status={session.status(w, sid)} · attached(now)="
         f"{session.attached(w, sid)} · attached@frontier={attached_at_frontier}")

    # 3) Time-travel: fold upto the mid-build frontier → a PREFIX of the transcript,
    #    proving the scrollback is recomputed, not stored.
    past = session.replay(k.weave(upto_seq=frontier), sid)
    assert "build ok" not in past and "cc -c main.c" in past, past
    assert full.startswith(past), "history is not a prefix of the present"
    line(f"  time-travel @e{frontier}: replay is a {len(past)}-byte prefix "
         f"(no 'build ok' yet) — the fold IS scrollback")

    # 4) Arrival-order independence (FOLD §11): replay from a re-fold in a DIFFERENT
    #    delivery order is byte-identical, because events sort by their `seq` key.
    w2 = Weave()
    for ev in sorted(reversed(list(k.weft.events())), key=lambda e: (e.lamport, e.id)):
        w2._apply(ev)
    assert session.replay(w2, sid) == full, "replay depends on arrival order"
    line("  arrival-order independent: reorder all events → identical transcript")
