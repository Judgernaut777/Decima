"use strict";
/*
 * Capability inspector — inspect each agent's authority envelope (objective, budgets,
 * lineage) and, for a chosen capability, propose a revocation. Revocation is a GATED
 * command: submitting it does NOT revoke immediately — the backend defers it to the
 * Approval inbox, where a human decides. Reads GET /api/v1/agents; proposes via
 * POST /api/v1/capabilities/revoke and POST /api/v1/agents/terminate.
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var ui = D.ui;

  function agentCard(agent, ctx) {
    var capInput = el("input", {
      type: "text", class: "input", placeholder: "capability id to revoke"
    });
    return ui.card([
      el("div", { class: "row-head" }, [
        el("strong", { text: agent.objective || agent.id }),
        ui.statusPill(agent.status)
      ]),
      ui.fields([
        ["Agent", agent.id],
        ["Principal", agent.principal],
        ["Parent", agent.parent_agent_id],
        ["Children", (agent.child_ids || []).length],
        ["Token budget", agent.token_budget],
        ["Monetary budget", agent.monetary_budget],
        ["Deadline", agent.deadline]
      ]),
      el("div", { class: "actions inspector-actions" }, [
        capInput,
        el("button", {
          type: "button", class: "btn btn-danger", text: "Propose revoke",
          on: { click: async function () {
            var capId = capInput.value.trim();
            if (!capId) {
              ctx.toast("Enter a capability id", "warn");
              return;
            }
            var r = await ctx.api.commands.revokeCapability({ id: capId });
            handleGated(r, ctx, "Revocation");
          } }
        }),
        el("button", {
          type: "button", class: "btn", text: "Propose terminate",
          on: { click: async function () {
            var r = await ctx.api.commands.terminateAgent({ id: agent.id });
            handleGated(r, ctx, "Termination");
          } }
        })
      ])
    ]);
  }

  function handleGated(r, ctx, label) {
    if (r.status === 202 || (r.data && r.data.required_approval)) {
      ctx.toast(label + " sent to the Approval inbox", "warn");
      ctx.refreshBadges();
    } else if (r.ok) {
      ctx.toast(label + " applied", "ok");
      ctx.refreshActive();
    } else {
      ctx.toast(label + " failed: " + ((r.data && r.data.reason_code) || r.status), "bad");
    }
  }

  D.registerScreen({
    id: "capabilities",
    title: "Capability inspector",
    icon: "🔑",
    endpoints: [
      "GET /api/v1/agents", "POST /api/v1/capabilities/revoke", "POST /api/v1/agents/terminate"
    ],
    render: function (container, ctx) {
      return ui.withData(container, ctx.api.reads.agents, function (agents) {
        container.appendChild(el("p", { class: "hint",
          text: "Revoking a capability or terminating an agent is gated — it is proposed " +
            "here and decided in the Approval inbox." }));
        container.appendChild(ui.sectionTitle("Agents", agents.length + ""));
        if (!agents.length) {
          container.appendChild(D.dom.empty("No agents."));
        }
        agents.forEach(function (a) { container.appendChild(agentCard(a, ctx)); });
      });
    }
  });
})(typeof window !== "undefined" ? window : this);
