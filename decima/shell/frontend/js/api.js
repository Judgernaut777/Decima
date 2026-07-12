"use strict";
/*
 * api.js — the fetch-based client for the loopback backend.
 *
 * All calls are same-origin (the Shell serves the frontend and proxies /api from one
 * endpoint), so credentials:'same-origin' sends the HttpOnly session cookie and the
 * backend's SameSite=Strict cookie policy holds. Mutations echo the CSRF token in the
 * X-CSRF-Token header (double-submit); a high-risk approval additionally presents the
 * pairing secret in X-Reauth for that one call. The client stores the CSRF token in
 * memory only and never persists the pairing secret.
 *
 * Endpoints (all real, from decima.services.api.routes):
 *   GET  /api/v1/health, /api/v1/session, /api/v1/tasks, /api/v1/projects,
 *        /api/v1/agents, /api/v1/notes, /api/v1/approvals, /api/v1/activity, /api/v1/stream
 *   POST /api/v1/session/login, /api/v1/session/logout, /api/v1/notes[/update|/retract],
 *        /api/v1/tasks[/complete], /api/v1/projects, /api/v1/plans/start|pause,
 *        /api/v1/agents/terminate, /api/v1/capabilities/revoke,
 *        /api/v1/artifacts/import|export, /api/v1/approvals/deny|approve
 *   Path-A lanes (contracts frozen; backends 501 until each lane lands):
 *   GET  /api/v1/questions[/detail], /api/v1/workspaces[/detail],
 *        /api/v1/plans/proposals, /api/v1/agents/runs
 *   POST /api/v1/questions/ask, /api/v1/workspaces[/start|/cancel],
 *        /api/v1/plans/propose|accept|execute|resume|cancel
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var BASE = "/api/v1";

  var state = {
    csrf: null,
    principal: null,
    authenticated: false
  };

  function _headers(extra) {
    var h = { "Content-Type": "application/json", Accept: "application/json" };
    return Object.assign(h, extra || {});
  }

  async function _json(resp) {
    var textBody = await resp.text();
    var data = null;
    if (textBody) {
      try {
        data = JSON.parse(textBody);
      } catch (e) {
        data = { error: "non-JSON response" };
      }
    }
    return { status: resp.status, ok: resp.ok, data: data };
  }

  async function get(path) {
    var resp = await fetch(BASE + path, {
      method: "GET",
      credentials: "same-origin",
      headers: _headers()
    });
    return _json(resp);
  }

  async function post(path, body, opts) {
    opts = opts || {};
    var headers = _headers();
    if (state.csrf) {
      headers["X-CSRF-Token"] = state.csrf;
    }
    if (opts.reauth) {
      headers["X-Reauth"] = opts.reauth;
    }
    var resp = await fetch(BASE + path, {
      method: "POST",
      credentials: "same-origin",
      headers: headers,
      body: JSON.stringify(body || {})
    });
    return _json(resp);
  }

  // -- session -----------------------------------------------------------
  async function login(pairingSecret) {
    var r = await post("/session/login", { pairing_secret: pairingSecret });
    if (r.ok && r.data) {
      state.csrf = r.data.csrf;
      state.principal = r.data.principal;
      state.authenticated = true;
    }
    return r;
  }

  async function refreshSession() {
    var r = await get("/session");
    if (r.ok && r.data) {
      state.csrf = r.data.csrf;
      state.principal = r.data.principal;
      state.authenticated = true;
    } else {
      state.authenticated = false;
    }
    return r;
  }

  async function logout() {
    var r = await post("/session/logout", {});
    state.csrf = null;
    state.principal = null;
    state.authenticated = false;
    return r;
  }

  // -- reads (return the items array or []) ------------------------------
  async function _items(path) {
    var r = await get(path);
    if (r.ok && r.data && Array.isArray(r.data.items)) {
      return r.data.items;
    }
    return [];
  }

  // Generic detail reader: GET path with an encoded query string (ids ride in the
  // query, never the path — mirrors the backend route discipline).
  function _detail(path, params) {
    var pairs = [];
    Object.keys(params || {}).forEach(function (k) {
      pairs.push(encodeURIComponent(k) + "=" + encodeURIComponent(params[k]));
    });
    return get(path + (pairs.length ? "?" + pairs.join("&") : ""));
  }

  var reads = {
    health: function () { return get("/health"); },
    tasks: function () { return _items("/tasks"); },
    projects: function () { return _items("/projects"); },
    agents: function () { return _items("/agents"); },
    notes: function () { return _items("/notes"); },
    approvals: function () { return _items("/approvals"); },
    activity: function () { return _items("/activity"); },
    // -- Path-A lane readers (contracts.py; stubs return 501 until a lane lands) --
    questions: function () { return _items("/questions"); },
    questionDetail: function (id) { return _detail("/questions/detail", { id: id }); },
    workspaces: function () { return _items("/workspaces"); },
    workspaceDetail: function (id) { return _detail("/workspaces/detail", { id: id }); },
    planProposals: function () { return _items("/plans/proposals"); },
    agentRuns: function () { return _items("/agents/runs"); }
  };

  // -- SSE-shaped stream (finite frames, poll with a cursor) -------------
  // The backend's stream drains buffered frames and ends, so we fetch, parse the frames,
  // advance the cursor, and re-fetch on an interval — no EventSource (which cannot carry
  // our cursor semantics). Each frame's `data` is JSON DATA, rendered as text downstream.
  async function stream(since) {
    var resp = await fetch(BASE + "/stream?since=" + encodeURIComponent(since || 0), {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "text/event-stream" }
    });
    if (!resp.ok) {
      return { status: resp.status, frames: [] };
    }
    var body = await resp.text();
    return { status: resp.status, frames: parseSse(body) };
  }

  function parseSse(body) {
    var frames = [];
    var blocks = body.split("\n\n");
    for (var i = 0; i < blocks.length; i++) {
      var block = blocks[i];
      if (!block.trim()) {
        continue;
      }
      var frame = { id: null, event: "message", data: null };
      var lines = block.split("\n");
      for (var j = 0; j < lines.length; j++) {
        var line = lines[j];
        if (line.indexOf("id:") === 0) {
          frame.id = parseInt(line.slice(3).trim(), 10);
        } else if (line.indexOf("event:") === 0) {
          frame.event = line.slice(6).trim();
        } else if (line.indexOf("data:") === 0) {
          var raw = line.slice(5).trim();
          try {
            frame.data = JSON.parse(raw);
          } catch (e) {
            frame.data = { raw: raw };
          }
        }
      }
      frames.push(frame);
    }
    return frames;
  }

  // Poll the finite SSE-shaped stream on an interval, advancing the cursor, and hand
  // each frame (untrusted DATA — render as text only) to the handler. Returns a stop
  // function. This is the generic subscribe seam the feature lanes reuse.
  function subscribe(handler, opts) {
    opts = opts || {};
    var cursor = opts.since || 0;
    var timer = setInterval(async function () {
      try {
        var r = await stream(cursor);
        for (var i = 0; i < r.frames.length; i++) {
          var frame = r.frames[i];
          if (frame.id !== null && frame.id > cursor) {
            cursor = frame.id;
          }
          handler(frame);
        }
      } catch (e) {
        /* polling is best-effort; the next tick retries */
      }
    }, opts.intervalMs || 2000);
    return function stop() { clearInterval(timer); };
  }

  // -- mutations (thin, typed wrappers over the command endpoints) -------
  var commands = {
    createNote: function (args) { return post("/notes", args); },
    updateNote: function (args) { return post("/notes/update", args); },
    retractNote: function (args) { return post("/notes/retract", args); },
    createTask: function (args) { return post("/tasks", args); },
    completeTask: function (args) { return post("/tasks/complete", args); },
    createProject: function (args) { return post("/projects", args); },
    startPlan: function (args) { return post("/plans/start", args); },
    pausePlan: function (args) { return post("/plans/pause", args); },
    terminateAgent: function (args) { return post("/agents/terminate", args); },
    revokeCapability: function (args) { return post("/capabilities/revoke", args); },
    importArtifact: function (args) { return post("/artifacts/import", args); },
    exportArtifact: function (args) { return post("/artifacts/export", args); },
    denyInvocation: function (args) { return post("/approvals/deny", args); },
    // Approval is REAUTH-gated: the pairing secret must ride in X-Reauth for this call.
    approveInvocation: function (args, pairingSecret) {
      return post("/approvals/approve", args, { reauth: pairingSecret });
    },
    // -- Path-A lane commands (contracts.py; stubs return 501 until a lane lands) --
    askQuestion: function (args) { return post("/questions/ask", args); },
    createWorkspaceRun: function (args) { return post("/workspaces", args); },
    startWorkspaceRun: function (args) { return post("/workspaces/start", args); },
    cancelWorkspaceRun: function (args) { return post("/workspaces/cancel", args); },
    requestPlanProposal: function (args) { return post("/plans/propose", args); },
    acceptPlanProposal: function (args) { return post("/plans/accept", args); },
    startPlanExecution: function (args) { return post("/plans/execute", args); },
    resumePlan: function (args) { return post("/plans/resume", args); },
    cancelPlan: function (args) { return post("/plans/cancel", args); }
  };

  D.api = {
    state: state,
    get: get,
    post: post,
    login: login,
    logout: logout,
    refreshSession: refreshSession,
    reads: reads,
    stream: stream,
    parseSse: parseSse,
    subscribe: subscribe,
    commands: commands
  };
})(typeof window !== "undefined" ? window : this);
