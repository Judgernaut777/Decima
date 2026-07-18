"""serve_mcp — the LAUNCHER that actually runs Decima as an MCP stdio process.

Batch U (MCP-SERVE LAUNCHER). `decima/mcp_server.py` has a proven `serve_stdio(k,
agent_cell, ...)` loop that reads newline-delimited JSON-RPC 2.0 from a stream, routes
EVERY frame through `handle(k, agent_cell, request)` (authorize + Morta + the
inputSchema gate all still run — check 492 proves this end to end), and writes one
response line per request — but nothing BOOTED a Kernel, bound a consumer, and pointed
that loop at a real process's stdin/stdout. This module is that missing launcher. It is
pure composition over `mcp_server.bind_consumer` + `mcp_server.serve_stdio`: no new
gate, no new authority, no core edit.

THE LAW THIS PRESERVES (repeated, because a launcher is exactly the seam where a gate
gets quietly bypassed): serving NEVER weakens the gate. `serve()` below dispatches
every request PAST `bind_consumer`'s attenuated principal and THROUGH `serve_stdio` →
`handle` — it answers no method itself. A served `tools/call` still crosses authorize +
Morta + the inputSchema gate exactly as it would in-process; a Morta-gated tool is
refused/queued over the wire, never auto-run.

DEFAULT-DENY. `bind_consumer(k, consumer_name, tools or [])` is called with `tools=[]`
unless the caller passes an explicit list: the launched consumer starts with NO ambient
authority — it may discover (tools/list, resources/list, prompts/list) and read
(resources/read, prompts/get are pure data folds), but it can INVOKE NOTHING until a
real deployment grants specific tools by name. Default-deny is the fail-closed posture
for anything reachable from outside the process boundary.

FAIL-CLOSED, INTS-NOT-FLOATS. This module records no content of its own beyond what
`bind_consumer` already records (a `mcp_consumer` Cell + admission edge); it does not
introduce any new event shape, so the ints-not-floats law is inherited unchanged from
`mcp_server`. Pure stdlib, no pip deps.
"""

DEFAULT_DB_PATH = "weft.db"                          # the same warm-start path run.py uses


def serve(k, *, consumer_name="mcp-stdio", tools=None, stdin=None, stdout=None,
          stop=None) -> dict:
    """Bind ONE MCP consumer and serve it over newline-delimited JSON-RPC.

    Binds `consumer_name` as its OWN attenuated principal via
    `mcp_server.bind_consumer(k, consumer_name, tools or [])` — DEFAULT-DENY: with no
    `tools` argument the consumer holds NOTHING ambient, only what a caller explicitly
    grants by name. Resolves the bound consumer's agent cell
    (`k.weave().get(admission["consumer"])`) and hands it to
    `mcp_server.serve_stdio(k, agent_cell, stdin=stdin, stdout=stdout, stop=stop)` — the
    ONLY place a request is dispatched. This function answers no JSON-RPC method
    itself; every method the stream carries is served BY `serve_stdio` THROUGH
    `handle`, so authorize + Morta + the inputSchema gate run on every served call
    exactly as they do in-process. Returns `serve_stdio`'s summary dict
    (requests/responses/notifications/malformed/eof/stopped — all ints)."""
    from decima import mcp_server

    admission = mcp_server.bind_consumer(k, consumer_name, tools or [])
    agent_cell = k.weave().get(admission["consumer"])
    return mcp_server.serve_stdio(k, agent_cell, stdin=stdin, stdout=stdout, stop=stop)


def main(argv=None) -> dict:
    """Boot a real warm Kernel (the same way `heartbeat/run.py` does — `--fresh` in
    argv starts from genesis, otherwise the warm `weft.db` is reused) and serve it as
    an MCP stdio server over the REAL `sys.stdin`/`sys.stdout`. This is the actual
    process entry point (`python3 -m decima.serve_mcp` or `python3 decima/serve_mcp.py`
    from the `heartbeat/` directory): with no streams injected, `serve()` drives the
    real stdio streams, so every line an external MCP client writes to this process's
    stdin is served through the SAME gated `handle` path a check exercises offline."""
    import sys
    from decima.kernel import Kernel

    argv = sys.argv[1:] if argv is None else list(argv)
    k = Kernel(DEFAULT_DB_PATH, fresh="--fresh" in argv)
    return serve(k)


if __name__ == "__main__":
    main()
