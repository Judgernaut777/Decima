"use strict";
/*
 * Workspace — the isolated coding workspace: mount a repo, edit, run checks in a
 * jailed worker, review a diff, keep durable diff/test artifacts.
 *
 * OWNER: workspace lane (Path A). This file is the workspace lane's frontend surface;
 * no other lane edits it, and the workspace lane edits no shared frontend file
 * (app.js/api.js/dom.js/sanitize.js/index.html are pre-wired).
 *
 * TODO(lane-workspace): replace the stub panel with the real screen — create runs via
 * ctx.api.commands.createWorkspaceRun / startWorkspaceRun / cancelWorkspaceRun, list
 * runs via ctx.api.reads.workspaces, show a run's artifacts via
 * ctx.api.reads.workspaceDetail. Diff text, test output, and any worker output are
 * UNTRUSTED content: render as text only (dom.el `text`), never as markup, and never
 * offer a push/deploy/network affordance — a workspace has no outward path.
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var ui = D.ui;

  D.registerScreen({
    id: "workspace",
    title: "Workspace",
    icon: "🧰",
    endpoints: [
      "GET /api/v1/workspaces", "GET /api/v1/workspaces/detail",
      "POST /api/v1/workspaces", "POST /api/v1/workspaces/start",
      "POST /api/v1/workspaces/cancel"
    ],
    render: function (container) {
      container.appendChild(ui.sectionTitle("Coding workspace"));
      container.appendChild(ui.card([
        el("p", { text: "Not yet implemented." }),
        el("p", {
          class: "muted",
          text: "This is the reserved surface for the isolated coding workspace " +
            "(workspace lane): mount, edit, run checks in a jailed worker, review " +
            "diffs, keep durable artifacts. The backend contract is frozen; workspace " +
            "commands currently return NOT_IMPLEMENTED (501)."
        })
      ]));
    }
  });
})(typeof window !== "undefined" ? window : this);
