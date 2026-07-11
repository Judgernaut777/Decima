# Known issue for the kernel/API lane — threaded server + single-threaded sqlite Weft

**Surfaced by:** WS1 browser qualification (first authenticated read over the real socket).
**Severity:** was release-blocking for any daemon serving concurrent/threaded requests.
**Status from WS1:** mitigated for the Shell daemon in-lane; **root fix belongs to the kernel lane.**

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
