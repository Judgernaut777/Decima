"use strict";
/*
 * Workspace — the isolated coding workspace: select an explicitly granted repository
 * root, request a bounded change (declared edits + a declared check), run it inside a
 * jailed worker, review the unified diff + test output, keep durable artifacts.
 *
 * OWNER: workspace lane (Path A). This file is the workspace lane's frontend surface;
 * no other lane edits it, and the workspace lane edits no shared frontend file
 * (app.js/api.js/dom.js/sanitize.js/index.html are pre-wired).
 *
 * Trust: diff text, test output, changed-file names, and any worker output are
 * UNTRUSTED content — rendered ONLY as text (dom.el `text` / textContent) inside
 * labelled untrusted zones, never as markup, never as a handler. There is NO push,
 * deploy, or network affordance anywhere on this screen: a workspace has no outward
 * path (the policy is structurally networkless).
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var ui = D.ui;

  var TERMINAL = { SUCCEEDED: 1, FAILED: 1, CANCELLED: 1, UNKNOWN: 1 };

  // Stage 2: a run's bounded change moves proposed → authorized → executed. The change
  // is either operator-DECLARED (literal edits) or model-PROPOSED (an objective). All
  // three markers are pure structural chrome (never untrusted content); the proposal
  // summary / diffs / test output stay untrusted display text elsewhere.
  function lifecycleNode(run) {
    var authorized = true;                       // CREATED means it already validated + mounted
    var executed = !!TERMINAL[run.status];       // reached a terminal (executed) outcome
    function stage(label, active, cls) {
      return el("span", {
        class: "ws-stage " + cls + (active ? " ws-stage-active" : " ws-stage-idle"),
        text: label
      });
    }
    return el("div", { class: "ws-lifecycle", "aria-label": "Change lifecycle" }, [
      stage("Proposed", true, "ws-stage-proposed"),
      el("span", { class: "ws-stage-sep", text: "→" }),
      stage("Authorized", authorized, "ws-stage-authorized"),
      el("span", { class: "ws-stage-sep", text: "→" }),
      stage("Executed", executed, "ws-stage-executed")
    ]);
  }

  function changeSourceLabel(run) {
    return run.edit_source === "model"
      ? "model-proposed (from objective)"
      : "operator-declared (literal edits)";
  }

  function restrictionsSummary(restrictions) {
    var denied = [];
    Object.keys(restrictions || {}).forEach(function (key) {
      if (restrictions[key] === false) {
        denied.push("no " + key.replace(/_/g, " "));
      }
    });
    var scope = restrictions && restrictions.scope ? String(restrictions.scope) : "";
    return denied.join(", ") + (scope ? " — " + scope : "");
  }

  D.registerScreen({
    id: "workspace",
    title: "Workspace",
    icon: "🧰",
    endpoints: [
      "GET /api/v1/workspaces", "GET /api/v1/workspaces/detail",
      "POST /api/v1/workspaces", "POST /api/v1/workspaces/start",
      "POST /api/v1/workspaces/cancel"
    ],
    render: function (container, ctx) {
      var pollTimer = null;
      var openDetailId = null;
      var disposed = false;
      var listHost = null;     // stable region: the runs list re-renders here
      var detailHost = null;   // stable region: the open run's detail renders here

      function stopPoll() {
        if (pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
      }

      async function drive(runId) {
        // Re-driving Start reconciles a finished worker into durable artifacts;
        // on a terminal run it replays the recorded outcome with no new effect.
        return ctx.api.commands.startWorkspaceRun({ id: runId });
      }

      // Poll running runs WITHOUT tearing down the DOM: each tick advances any RUNNING
      // run server-side (re-driving Start reconciles a finished worker), updates that
      // card's status pill IN PLACE, and only does ONE full re-render when every run
      // has reached a terminal/interrupted state (so cards + buttons stay stable while
      // the operator interacts).
      function updatePillInPlace(run) {
        var host = container.querySelector('.ws-run-status[data-run="' + run.id + '"]');
        if (host) {
          D.dom.clear(host);
          host.appendChild(ui.statusPill(
            run.status + (run.interrupted ? " (interrupted)" : "")));
        }
      }

      function ensurePolling(runs) {
        var anyRunning = runs.some(function (r) {
          return r.status === "RUNNING" && !r.interrupted;
        });
        if (!anyRunning) {
          stopPoll();
          return;
        }
        if (pollTimer) {
          return;
        }
        pollTimer = setInterval(async function () {
          try {
            var listing = await ctx.api.get("/workspaces");
            var items = (listing.data && listing.data.items) || [];
            var stillRunning = false;
            for (var i = 0; i < items.length; i++) {
              if (items[i].status === "RUNNING" && !items[i].interrupted) {
                var after = await drive(items[i].id);
                var run = (after.data && after.data.data) || items[i];
                if (run.status === "RUNNING" && !run.interrupted) {
                  stillRunning = true;
                }
                updatePillInPlace(run);
              }
            }
            if (!stillRunning && !disposed) {
              stopPoll();
              await renderRuns();       // re-render the list once everything terminal
              if (openDetailId) {
                await renderDetailRegion();  // refresh an open detail's artifacts
              }
            }
          } catch (e) {
            /* best-effort; the next tick retries */
          }
        }, 1000);
      }

      // ---- detail panel (fold-derived; all content rendered as text) ----------
      // `data` is the already-fetched {run, artifacts, receipt, grant} payload.
      function renderDetail(host, data) {
        var run = data.run;
        var grant = data.grant;
        var receipt = data.receipt;

        var rows = [
          ["Run id", run.id],
          ["Status", ui.statusPill(run.status)],
          ["Change source", changeSourceLabel(run)],
          ["Objective", run.objective || "—"],
          ["Repository root", run.repo_root],
          ["Declared check", run.check],
          ["Timeout (s)", String((run.policy || {}).timeout_seconds || "")],
          ["Worker profile", (run.policy || {}).profile || ""],
          ["Restrictions", restrictionsSummary(run.restrictions)],
          ["Mounted scope", (run.mounted_files || []).length + " file(s): " +
            (run.mounted_files || []).join(", ")],
          ["Tests", "passed " + (run.passed || 0) + " / failed " + (run.failed || 0)]
        ];
        if (run.edit_source === "model") {
          // Model-PROPOSED provenance: a model proposed the change; deterministic code
          // validated + authorized it (proposal summary is untrusted display text).
          rows.push(["Proposed by model", run.proposal_model || "—"]);
          rows.push(["Proposal summary", run.proposal_summary || "—"]);
          rows.push(["Proposal record", run.proposal_id || "—"]);
          rows.push(["Routing decision", run.routing_cell || "—"]);
          rows.push(["Proposed edits", String(run.proposed_edit_count || 0)]);
        }
        if (grant) {
          rows.push(["Grant record", grant.id]);
        }
        if (receipt) {
          rows.push(["Worker receipt", receipt.id + " (" + receipt.status + ")"]);
        }
        if (run.interrupted) {
          rows.push(["Interrupted", "run was RUNNING when the service stopped"]);
        }
        host.appendChild(ui.card([
          ui.sectionTitle("Run detail", run.name),
          lifecycleNode(run),
          ui.fields(rows)
        ], "ws-detail-card"));

        // Changed files — untrusted names, text only.
        var changed = run.changed_files || [];
        host.appendChild(D.dom.zone("untrusted", "Changed files (untrusted)",
          changed.length
            ? el("ul", { class: "ws-changed-files" }, changed.map(function (p) {
                return el("li", { class: "ws-changed-file", text: p });
              }))
            : D.dom.empty("No changed files.")));

        // Artifacts: unified diff + test output — untrusted, rendered as <pre> text.
        var artifacts = data.artifacts || [];
        artifacts.forEach(function (art) {
          if (art.kind === "diff_artifact") {
            host.appendChild(D.dom.zone("untrusted",
              "Unified diff (untrusted) — artifact " + art.id.slice(0, 12),
              el("pre", { class: "ws-diff", text: art.diff || "(empty diff)" })));
          } else if (art.kind === "test_artifact") {
            host.appendChild(D.dom.zone("untrusted",
              "Test output (untrusted) — artifact " + art.id.slice(0, 12) +
                " [" + art.status + "]",
              el("pre", { class: "ws-test-output", text: art.output || "(no output)" })));
          }
        });
        if (!artifacts.length) {
          host.appendChild(D.dom.empty("No artifacts yet — start the run."));
        }
      }

      // ---- one-time skeleton (grant zone + form stay STABLE; only the runs list
      //      and the detail region re-render, so form input + open buttons never
      //      get torn out from under the operator or a Playwright interaction) -----
      function buildForm(grants, checks, defaults) {
        var repoSelect = el("select", { id: "ws-repo", "aria-label": "Repository root" },
          grants.map(function (g) {
            return el("option", { value: g.root, text: g.root });
          }));
        var checkSelect = el("select", { id: "ws-check", "aria-label": "Declared check" },
          checks.map(function (name) {
            return el("option", { value: name, text: name });
          }));
        var nameInput = el("input", { id: "ws-name", type: "text", required: "required",
          placeholder: "run name", "aria-label": "Run name" });
        var objectiveInput = el("input", { id: "ws-objective", type: "text",
          placeholder: "objective — describe the bounded change (model proposes the edits)",
          "aria-label": "Objective" });
        var timeoutInput = el("input", { id: "ws-timeout", type: "number", min: "1",
          max: "120", value: String(defaults.timeout_seconds || 10),
          "aria-label": "Timeout seconds" });
        var editPath = el("input", { id: "ws-edit-path", type: "text",
          placeholder: "file to edit (relative path)",
          "aria-label": "Edit path" });
        var editContent = el("textarea", { id: "ws-edit-content", rows: "6",
          placeholder: "new file content", "aria-label": "Edit content" });
        var formError = el("p", { class: "form-error", id: "ws-form-error", text: "" });

        async function onCreate(event) {
          event.preventDefault();
          formError.textContent = "";
          var objective = (objectiveInput.value || "").trim();
          var body = {
            name: nameInput.value,
            repo_root: repoSelect.value,
            check: checkSelect.value,
            policy: { timeout_seconds: parseInt(timeoutInput.value, 10) || 10 }
          };
          // Two MUTUALLY EXCLUSIVE ways to declare the bounded change: a literal edit
          // (operator-declared) OR an objective the model proposes edits for. A literal
          // edit takes precedence; when only an objective is given we OMIT the `edits`
          // field so the backend routes a model proposal (proposal → validation → auth).
          if (editPath.value) {
            body.objective = objective;                       // metadata alongside literal edits
            body.edits = [{ path: editPath.value, content: editContent.value }];
          } else if (objective) {
            body.objective = objective;                       // model-proposed (no `edits` field)
          } else {
            body.objective = "";
            body.edits = [];
          }
          var resp = await ctx.api.commands.createWorkspaceRun(body);
          if (resp.ok) {
            ctx.toast("Workspace run created", "ok");
            nameInput.value = "";
            objectiveInput.value = "";
            editPath.value = "";
            editContent.value = "";
            await renderRuns();
          } else {
            formError.textContent = "Refused (" +
              ((resp.data && resp.data.reason_code) || resp.status) + "): " +
              ((resp.data && resp.data.error) || "");
          }
        }

        return el("form", { class: "stacked-form ws-form", on: { submit: onCreate } }, [
          el("label", { text: "Granted repository" }), repoSelect,
          el("label", { text: "Run name" }), nameInput,
          el("label", { text: "Objective" }), objectiveInput,
          el("label", { text: "Declared check" }), checkSelect,
          el("label", { text: "Timeout (seconds)" }), timeoutInput,
          el("label", { text: "Bounded edit — path" }), editPath,
          el("label", { text: "Bounded edit — content" }), editContent,
          el("button", { type: "submit", id: "ws-create", text: "Create workspace run" }),
          formError
        ]);
      }

      function runCardNode(run) {
        var buttons = [];
        if (run.status === "CREATED") {
          buttons.push(el("button", {
            type: "button", class: "ws-start", dataset: { run: run.id },
            on: { click: async function () {
              var resp = await drive(run.id);
              if (!resp.ok) {
                ctx.toast("Start refused (" +
                  ((resp.data && resp.data.reason_code) || resp.status) + ")", "warn");
              }
              await renderRuns();
            } },
            text: "Start"
          }));
        }
        if (run.status === "CREATED" || run.status === "RUNNING") {
          buttons.push(el("button", {
            type: "button", class: "ws-cancel", dataset: { run: run.id },
            on: { click: async function () {
              var resp = await ctx.api.commands.cancelWorkspaceRun({ id: run.id });
              if (!resp.ok) {
                ctx.toast("Cancel refused (" +
                  ((resp.data && resp.data.reason_code) || resp.status) + ")", "warn");
              }
              await renderRuns();
            } },
            text: "Cancel"
          }));
        }
        buttons.push(el("button", {
          type: "button", class: "ws-open", dataset: { run: run.id },
          on: { click: function () {
            openDetailId = openDetailId === run.id ? null : run.id;
            renderDetailRegion();
            // Reflect the button label without a full re-render.
            renderRuns();
          } },
          text: openDetailId === run.id ? "Close detail" : "Open detail"
        }));

        var card = ui.card([
          el("div", { class: "ws-run-head" }, [
            el("strong", { class: "ws-run-name", text: run.name || run.id.slice(0, 12) }),
            el("span", { class: "ws-run-status", dataset: { run: run.id } },
              [ui.statusPill(run.status + (run.interrupted ? " (interrupted)" : ""))])
          ]),
          ui.fields([
            ["Repository", run.repo_root],
            ["Check", run.check],
            ["Changed files", String((run.changed_files || []).length)],
            ["Artifacts", String((run.artifact_ids || []).length)]
          ]),
          el("div", { class: "ws-run-actions" }, buttons)
        ], "ws-run");
        card.dataset.runId = run.id;
        return card;
      }

      // Re-render ONLY the runs list (guarded so a stale async render bails).
      var runsGen = 0;
      async function renderRuns() {
        var gen = ++runsGen;
        var r = await ctx.api.get("/workspaces");
        if (disposed || gen !== runsGen || !listHost) {
          return;
        }
        var runs = (r.data && r.data.items) || [];
        D.dom.clear(listHost);
        if (!runs.length) {
          listHost.appendChild(D.dom.empty("No workspace runs yet."));
        }
        runs.forEach(function (run) {
          listHost.appendChild(runCardNode(run));
        });
        ensurePolling(runs);
      }

      // Re-render ONLY the detail region (guarded independently).
      var detailGen = 0;
      async function renderDetailRegion() {
        if (!detailHost) {
          return;
        }
        var gen = ++detailGen;
        if (!openDetailId) {
          D.dom.clear(detailHost);
          return;
        }
        var r = await ctx.api.reads.workspaceDetail(openDetailId);
        if (disposed || gen !== detailGen) {
          return;
        }
        D.dom.clear(detailHost);
        if (!r.ok || !r.data || !r.data.run) {
          detailHost.appendChild(D.dom.empty("Could not load run detail."));
          return;
        }
        renderDetail(detailHost, r.data);
      }

      async function refresh() {
        var r = await ctx.api.get("/workspaces");
        D.dom.clear(container);
        container.appendChild(ui.sectionTitle("Coding workspace",
          "isolated, networkless, credential-free"));

        if (r.status === 501) {
          container.appendChild(ui.card([
            el("p", { text: "The workspace lane is not enabled on this service." }),
            el("p", {
              class: "muted",
              text: "No repository root is granted. Grant explicit roots via " +
                "DECIMA_WORKSPACE_ROOTS on the service; nothing can be mounted " +
                "until the operator does."
            })
          ]));
          return;
        }
        if (!r.ok || !r.data) {
          container.appendChild(D.dom.empty("Could not load workspaces."));
          return;
        }
        var grants = r.data.grants || [];
        var checks = r.data.checks || [];
        var defaults = r.data.policy_defaults || {};

        container.appendChild(D.dom.zone("system", "Granted repository roots",
          grants.length
            ? el("ul", { class: "ws-grants" }, grants.map(function (g) {
                return el("li", { class: "ws-grant" }, [
                  el("code", { class: "ws-grant-root", text: g.root }),
                  el("span", {
                    class: "muted ws-grant-restrictions",
                    text: " " + restrictionsSummary(g.restrictions)
                  })
                ]);
              }))
            : D.dom.empty("No granted roots.")));

        container.appendChild(ui.card([
          ui.sectionTitle("Request a bounded change"),
          buildForm(grants, checks, defaults)
        ]));

        listHost = el("div", { class: "ws-runs" });
        container.appendChild(ui.card([
          ui.sectionTitle("Recorded runs"),
          listHost
        ]));
        detailHost = el("div", { class: "ws-detail", id: "ws-detail" });
        container.appendChild(detailHost);

        await renderRuns();
        await renderDetailRegion();
      }

      refresh();
      return function cleanup() {
        disposed = true;
        stopPoll();
      };
    }
  });
})(typeof window !== "undefined" ? window : this);
