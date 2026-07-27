# Known issue for the kernel/API lane — threaded server + single-threaded sqlite Weft

**Surfaced by:** WS1 browser qualification (first authenticated read over the real socket).
**Severity:** was release-blocking for any daemon serving concurrent/threaded requests.
**Status from WS1:** mitigated for the Shell daemon in-lane; **root fix belongs to the kernel lane.**
**Status now (2026-07-27): RESOLVED — the recommended root fix landed.** See
[the resolution note](#resolution--031-t13-root-fix-landed) at the foot of this file. The
sections below are kept as the original filing, not rewritten.

## Symptom

Over the shipped `decima.services.api.server.make_http_server` (a per-connection-threaded
`ThreadingWSGIServer`), the FIRST authenticated read or mutation returns **HTTP 500**:

```
sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same
thread. The object was created in thread id ... and this is thread id ...
```

Trace: `ShellApp.__call__` → backend `dispatch` → `_read` → `driver.update()` →
`weft.events(from_seq=…)` → `self.db.execute(...)` on a connection opened on the build thread but
now touched from a per-request worker thread.

## Why the existing suite did not catch it

`tests/api/test_loopback_server.py` drives only `health` and `login` over a real socket — both are
`SPECIAL` routes that never call `driver.update()`. Every other test drives `Application.dispatch`
**in-process on one thread**, so the cross-thread access never happened. No test served an
authenticated **reader** or **command** over a real threaded socket.

## Reproduce

```bash
DB=$(mktemp -d)/weft.db
PYTHONPATH="$TESTENV:$PWD" python3 - "$DB" <<'PY'
import json, threading
from wsgiref.simple_server import make_server
from decima.services.api.server import build_application, ThreadingWSGIServer
import sys, urllib.request
db = sys.argv[1]
app, ident = build_application(db, seed=bytes(32), secure_cookie=False)
srv = make_server("127.0.0.1", 0, app, server_class=ThreadingWSGIServer)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}/api/v1"
# login (works)
r = urllib.request.urlopen(urllib.request.Request(base+"/session/login",
    data=json.dumps({"pairing_secret": ident.pairing_secret}).encode(),
    headers={"Content-Type":"application/json"}, method="POST"))
cookie = r.getheader("Set-Cookie").split(";")[0]
# any authenticated read → 500 (cross-thread sqlite)
try:
    urllib.request.urlopen(urllib.request.Request(base+"/tasks", headers={"Cookie": cookie}))
    print("NO REPRO (fixed?)")
except urllib.error.HTTPError as e:
    print("REPRO: HTTP", e.code)
PY
```

## WS1 in-lane mitigation (Shell only)

`decima/shell/serve.py` now serves the Shell via `make_loopback_server`, a **single-threaded**
loopback WSGI server: the backend is built and every request is served on the same thread, so all
Weft access is single-threaded and correct. For a single-user local daemon this is invisible —
projection reads are in-memory and `/stream` frames are drained finitely, not held open. The WS1
browser suite (`tests/browser/`) exercises this path end-to-end.

## Recommended root fix (kernel lane — OUT OF WS1 SCOPE)

`decima/kernel/weft.py` opens `sqlite3.connect(db_path)` (implicitly `check_same_thread=True`).
Either:

1. open with `check_same_thread=False` and serialize all Weft access with a `threading.Lock`
   (so the shipped `ThreadingWSGIServer` can safely serve concurrent requests), **or**
2. give each request thread its own connection.

Option 1 is minimal and would let both the API daemon and the Shell keep the threaded server.
Until then, the Shell's single-threaded server is the safe default. `decima/kernel/` is off-limits
to WS1, so this is filed for the kernel-owning lane, not applied here.

---

## Resolution — 0.3.1 T1.3, root fix landed

**Option 1 was taken, in the kernel lane, and it is in the tree.** `decima/kernel/weft.py` now
opens the store with `sqlite3.connect(db_path, check_same_thread=False)` and holds a re-entrant
per-store `threading.RLock` on **every** path that touches the connection or the in-memory
head / lamport / rotation state. `append` reads `head`, derives `parents`/`lamport`, signs,
INSERTs and moves `head` as one critical section, so concurrent appends cannot interleave into a
forked chain or a duplicated lamport: the log a thread-mixed run writes is byte-identical to the
log a single-threaded run would have written. Canonical bytes, durability and the fold are
unchanged. The reproducer above now prints `NO REPRO (fixed?)`.

The Shell **still** serves single-threaded (`decima/shell/serve.py` → `make_loopback_server`),
but that is now a deployment choice for a single-user local daemon rather than a mitigation the
store forces. Serving threaded would need its own qualification; nothing in the kernel forbids
it any more.

_Verified against the tree on 2026-07-27 while reconciling this evidence pack; recorded here
because a known-issues file that still lists a fixed defect as open is worse than no file._
