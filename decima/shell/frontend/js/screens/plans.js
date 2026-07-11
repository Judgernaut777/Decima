"use strict";
/*
 * Plans — the plan lifecycle controller. Lists projects (plans) with their steps and lets
 * the operator start or pause a plan. Reads GET /api/v1/projects and GET /api/v1/tasks;
 * acts via POST /api/v1/plans/start and /api/v1/plans/pause.
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var ui = D.ui;

  function planCard(plan, tasks, ctx) {
    var steps = tasks.filter(function (t) { return t.plan_id === plan.id; });
    var stepList = el("ul", { class: "step-list" });
    if (!steps.length) {
      stepList.appendChild(el("li", { class: "muted", text: "no steps" }));
    }
    steps.forEach(function (s) {
      stepList.appendChild(el("li", {}, [
        ui.statusPill(s.status),
        el("span", { class: "step-desc", text: " " + (s.description || s.id) })
      ]));
    });
    return ui.card([
      el("div", { class: "row-head" }, [
        el("strong", { text: plan.objective || plan.id }),
        ui.statusPill(plan.status)
      ]),
      stepList,
      el("div", { class: "actions" }, [
        el("button", {
          type: "button", class: "btn btn-primary", text: "Start",
          on: { click: function () { act(ctx, "startPlan", plan.id, "started"); } }
        }),
        el("button", {
          type: "button", class: "btn", text: "Pause",
          on: { click: function () { act(ctx, "pausePlan", plan.id, "paused"); } }
        })
      ])
    ]);
  }

  async function act(ctx, command, id, verb) {
    var r = await ctx.api.commands[command]({ id: id });
    if (r.ok) {
      ctx.toast("Plan " + verb, "ok");
      ctx.refreshActive();
    } else {
      ctx.toast("Action failed: " + ((r.data && r.data.reason_code) || r.status), "bad");
    }
  }

  D.registerScreen({
    id: "plans",
    title: "Plans",
    icon: "🗺️",
    endpoints: [
      "GET /api/v1/projects", "GET /api/v1/tasks",
      "POST /api/v1/plans/start", "POST /api/v1/plans/pause"
    ],
    render: function (container, ctx) {
      return ui.withData(container, async function () {
        var projects = await ctx.api.reads.projects();
        var tasks = await ctx.api.reads.tasks();
        return { projects: projects, tasks: tasks };
      }, function (data) {
        container.appendChild(ui.sectionTitle("Plans", data.projects.length + ""));
        if (!data.projects.length) {
          container.appendChild(D.dom.empty("No plans yet."));
        }
        data.projects.forEach(function (p) {
          container.appendChild(planCard(p, data.tasks, ctx));
        });
      });
    }
  });
})(typeof window !== "undefined" ? window : this);
