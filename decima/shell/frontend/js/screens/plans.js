"use strict";
/*
 * Plans — model-planned durable agents (planning lane).
 *
 * Flow: enter an objective → a MODEL proposes a plan (rendered inside the untrusted
 * model trust-zone; every value is text, never markup) → the operator ACCEPTS or
 * REJECTS (the human decision, in trusted chrome) → acceptance mints durable Plan/
 * Step/Agent Cells → execution advances through the budget-gated runtime one bounded
 * pass per click; Pause/Resume/Cancel are server-enforced. The step legend makes the
 * trust chain visible: model proposal (untrusted) → your acceptance → deterministic
 * authorization (READY) → dispatch (RUNNING) → receipt-confirmed completion.
 *
 * Reads  GET /api/v1/plans/proposals, /api/v1/projects, /api/v1/tasks, /api/v1/agents/runs
 * Acts   POST /api/v1/plans/propose|accept|execute|pause|resume|cancel,
 *        POST /api/v1/agents/terminate (gated → approval inbox)
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var ui = D.ui;

  // -- shared action helper -------------------------------------------------
  async function act(ctx, fn, args, okMsg) {
    var r = await fn(args);
    if (r.ok) {
      if (okMsg) { ctx.toast(okMsg, "ok"); }
      ctx.refreshActive();
    } else if (r.status === 202 || (r.data && r.data.required_approval)) {
      ctx.toast("Approval required — see the Approvals inbox", "warn");
      ctx.refreshBadges();
      ctx.refreshActive();
    } else {
      ctx.toast("Refused: " + ((r.data && (r.data.reason_code || r.data.error)) || r.status), "bad");
      ctx.refreshActive();
    }
    return r;
  }

  // -- objective form ---------------------------------------------------------
  function objectiveForm(ctx) {
    var input = el("input", {
      type: "text", class: "input", id: "plan-objective",
      placeholder: "What should be planned?", maxlength: "400"
    });
    var form = el("form", { class: "inline-form" }, [
      input,
      el("button", {
        type: "submit", class: "btn btn-primary",
        dataset: { action: "propose" }, text: "Request plan proposal"
      })
    ]);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      var objective = input.value.trim();
      if (!objective) { return; }
      var r = await act(ctx, ctx.api.commands.requestPlanProposal,
        { objective: objective }, "Model proposal recorded");
      if (r.ok) { input.value = ""; }
    });
    return form;
  }

  // -- proposal rendering (model output = untrusted DATA, distinct zone) ------
  function proposalSteps(p) {
    var list = el("ol", { class: "prop-steps" });
    (p.steps || []).forEach(function (s) {
      var deps = (s.depends_on_ids || []).length
        ? "needs " + s.depends_on_ids.join(", ") : "no dependencies";
      list.appendChild(el("li", { class: "prop-step" }, [
        el("strong", { text: s.id + " " }),
        el("span", { text: s.description }),
        el("div", { class: "muted", text:
          deps + " · capability " + s.capability +
          " · agent " + s.agent +
          (s.expected_output ? " · expects: " + s.expected_output : "") })
      ]));
    });
    return list;
  }

  function proposalCard(p, ctx) {
    var routing = p.routing || {};
    var body = [
      el("div", { class: "row-head" }, [
        el("strong", { text: p.objective || p.id }),
        ui.statusPill(p.status)
      ]),
      el("p", { class: "prop-summary", text: p.summary || "" }),
      proposalSteps(p),
      ui.fields([
        ["Model", p.model || routing.selected_model || "—"],
        ["Routing policy", "v" + (routing.policy_version || "?") + " · " +
          ((routing.reason_codes || []).join(", ") || "—")],
        ["Risk", p.risk],
        ["Model budget", p.model_budget + " tokens"],
        ["Execution budget", p.execution_budget + " µ¢"],
        ["Expected approvals", (p.expected_approvals || []).length
          ? p.expected_approvals.join(", ") : "none"]
      ])
    ];
    var zone = D.dom.zone("model", "Model proposal (untrusted)", body);
    var card = ui.card([zone], "proposal-card");
    card.appendChild(el("div", { class: "muted", text:
      "Deterministic validation: PASSED — structure, dependencies, capabilities and " +
      "budgets were checked by policy code before this proposal was recorded." }));
    if (p.status === "PROPOSED") {
      card.appendChild(D.dom.zone("human", "Your decision", [
        el("div", { class: "actions" }, [
          el("button", {
            type: "button", class: "btn btn-primary",
            dataset: { action: "accept" }, text: "Accept",
            on: { click: function () {
              act(ctx, ctx.api.commands.acceptPlanProposal,
                { proposal_id: p.id }, "Plan accepted — durable plan minted");
            } }
          }),
          el("button", {
            type: "button", class: "btn",
            dataset: { action: "reject" }, text: "Reject",
            on: { click: function () {
              act(ctx, ctx.api.commands.acceptPlanProposal,
                { proposal_id: p.id, decision: "reject" }, "Proposal rejected");
            } }
          })
        ])
      ]));
    } else if (p.plan_id) {
      card.appendChild(el("div", { class: "muted", text:
        "Accepted by you → durable plan " + p.plan_id.slice(0, 12) + "…" }));
    }
    return card;
  }

  // -- durable plan rendering -------------------------------------------------
  function stepLine(s) {
    var receipt = s.status === "SUCCEEDED";
    return el("li", {}, [
      ui.statusPill(s.status),
      el("span", { class: "step-desc", text: " " + (s.description || s.id) }),
      receipt
        ? el("span", { class: "muted step-receipt", text: " · receipt-confirmed" })
        : null,
      (s.dependency_ids || []).length
        ? el("span", { class: "muted", text: " · " + s.dependency_ids.length + " dep(s)" })
        : null
    ]);
  }

  function agentLine(a, ctx) {
    return el("div", { class: "agent-card" + (a.parent_agent_id ? " agent-child" : "") }, [
      el("div", { class: "row-head" }, [
        el("strong", { text: a.objective || a.agent_id }),
        ui.statusPill(a.status)
      ]),
      ui.fields([
        ["Agent", a.agent_id.slice(0, 12) + "…"],
        ["Model", a.model || "—"],
        ["Token budget", a.token_budget === null ? "unlimited" : a.token_budget],
        ["Monetary budget", a.monetary_budget === null ? "unlimited" : a.monetary_budget],
        ["Capabilities", (a.capabilities || []).join(", ") || "none (coordinator)"],
        ["Steps", a.steps_succeeded + " ok / " + a.steps_failed + " failed / " +
          a.steps_total + " total"],
        a.budget_block_reason ? ["Blocked", a.budget_block_reason] : null
      ]),
      el("div", { class: "actions" }, [
        el("button", {
          type: "button", class: "btn btn-danger",
          dataset: { action: "terminate", agent: a.agent_id },
          text: "Terminate (needs approval)",
          on: { click: function () {
            act(ctx, ctx.api.commands.terminateAgent, { id: a.agent_id });
          } }
        })
      ])
    ]);
  }

  function planCard(plan, tasks, agents, proposals, ctx) {
    var steps = tasks.filter(function (t) { return t.plan_id === plan.id; });
    var planAgents = agents.filter(function (a) { return a.plan_id === plan.id; });
    var fromProposal = proposals.filter(function (p) { return p.plan_id === plan.id; })[0];

    var stepList = el("ul", { class: "step-list" });
    if (!steps.length) {
      stepList.appendChild(el("li", { class: "muted", text: "no steps" }));
    }
    steps.forEach(function (s) { stepList.appendChild(stepLine(s)); });

    var controls = el("div", { class: "actions" });
    function btn(label, action, handler, primary) {
      controls.appendChild(el("button", {
        type: "button", class: "btn" + (primary ? " btn-primary" : ""),
        dataset: { action: action }, text: label,
        on: { click: handler }
      }));
    }
    var status = String(plan.status || "");
    if (status === "DRAFT") {
      btn("Start execution", "start", function () {
        act(ctx, ctx.api.commands.startPlanExecution, { id: plan.id }, "Execution advanced");
      }, true);
    }
    if (status === "ACTIVE") {
      btn("Advance", "advance", function () {
        act(ctx, ctx.api.commands.startPlanExecution, { id: plan.id }, "Execution advanced");
      }, true);
      btn("Pause", "pause", function () {
        act(ctx, ctx.api.commands.pausePlan, { id: plan.id }, "Plan paused");
      });
    }
    if (status === "PAUSED") {
      btn("Resume", "resume", function () {
        act(ctx, ctx.api.commands.resumePlan, { id: plan.id }, "Plan resumed");
      }, true);
      btn("Advance", "advance", function () {
        act(ctx, ctx.api.commands.startPlanExecution, { id: plan.id }, "No new work: paused");
      });
    }
    if (status !== "COMPLETED" && status !== "CANCELLED") {
      btn("Cancel", "cancel", function () {
        act(ctx, ctx.api.commands.cancelPlan, { id: plan.id }, "Plan cancelled");
      });
    }

    var children = [
      el("div", { class: "row-head" }, [
        el("strong", { text: plan.objective || plan.id }),
        ui.statusPill(plan.status)
      ]),
      fromProposal ? el("div", { class: "muted", text:
        "From model proposal " + fromProposal.id.slice(0, 12) + "… (model " +
        (fromProposal.model || "?") + ") — accepted by you; steps run only after " +
        "deterministic authorization and complete only on a recorded receipt." }) : null,
      stepList,
      controls
    ];
    if (planAgents.length) {
      children.push(el("div", { class: "section-title" }, [
        el("h3", { text: "Agents" }),
        el("span", { class: "section-sub", text: planAgents.length + "" })
      ]));
      planAgents.forEach(function (a) { children.push(agentLine(a, ctx)); });
    }
    return ui.card(children, "plan-card");
  }

  D.registerScreen({
    id: "plans",
    title: "Plans",
    icon: "🗺️",
    endpoints: [
      "GET /api/v1/plans/proposals", "GET /api/v1/projects", "GET /api/v1/tasks",
      "GET /api/v1/agents/runs",
      "POST /api/v1/plans/propose", "POST /api/v1/plans/accept",
      "POST /api/v1/plans/execute", "POST /api/v1/plans/pause",
      "POST /api/v1/plans/resume", "POST /api/v1/plans/cancel"
    ],
    render: function (container, ctx) {
      return ui.withData(container, async function () {
        var proposals = await ctx.api.reads.planProposals();
        var projects = await ctx.api.reads.projects();
        var tasks = await ctx.api.reads.tasks();
        var agents = await ctx.api.reads.agentRuns();
        return { proposals: proposals, projects: projects, tasks: tasks, agents: agents };
      }, function (data) {
        container.appendChild(ui.sectionTitle("New plan"));
        container.appendChild(objectiveForm(ctx));

        container.appendChild(ui.sectionTitle("Proposals", data.proposals.length + ""));
        if (!data.proposals.length) {
          container.appendChild(D.dom.empty("No proposals yet — ask for one above."));
        }
        data.proposals.forEach(function (p) {
          container.appendChild(proposalCard(p, ctx));
        });

        container.appendChild(ui.sectionTitle("Plans", data.projects.length + ""));
        if (!data.projects.length) {
          container.appendChild(D.dom.empty("No plans yet."));
        }
        data.projects.forEach(function (p) {
          container.appendChild(planCard(p, data.tasks, data.agents, data.proposals, ctx));
        });
      });
    }
  });
})(typeof window !== "undefined" ? window : this);
