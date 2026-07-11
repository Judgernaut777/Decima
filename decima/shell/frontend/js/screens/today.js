"use strict";
/*
 * Today — the operator's runnable focus: tasks that are ready now, plus a quick complete
 * action. Reads GET /api/v1/tasks; completes via POST /api/v1/tasks/complete.
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var ui = D.ui;

  function taskRow(task, ctx) {
    var done = task.status === "succeeded" || task.status === "completed";
    return ui.card([
      el("div", { class: "row-head" }, [
        el("strong", { class: "task-desc", text: task.description || task.id }),
        ui.statusPill(task.status)
      ]),
      ui.fields([
        ["Task", task.id],
        ["Plan", task.plan_id],
        ["Assigned", task.assigned_agent_id],
        ["Ready", task.ready ? "yes" : "no"],
        ["Deadline", task.deadline]
      ]),
      el("div", { class: "actions" }, [
        done ? null : el("button", {
          type: "button", class: "btn btn-primary", text: "Mark complete",
          on: { click: async function () {
            var r = await ctx.api.commands.completeTask({ id: task.id });
            if (r.ok) {
              ctx.toast("Task completed", "ok");
              ctx.refreshActive();
            } else {
              ctx.toast("Complete failed: " + reason(r), "bad");
            }
          } }
        })
      ])
    ], task.ready && !done ? "card-ready" : null);
  }

  function reason(r) {
    return (r.data && (r.data.reason_code || r.data.error)) || r.status;
  }

  D.registerScreen({
    id: "today",
    title: "Today",
    icon: "📅",
    endpoints: ["GET /api/v1/tasks", "POST /api/v1/tasks/complete"],
    render: function (container, ctx) {
      return ui.withData(container, ctx.api.reads.tasks, function (tasks) {
        var ready = tasks.filter(function (t) {
          return t.ready && t.status !== "succeeded" && t.status !== "completed";
        });
        var other = tasks.filter(function (t) {
          return !(t.ready && t.status !== "succeeded" && t.status !== "completed");
        });
        container.appendChild(ui.sectionTitle("Ready now", ready.length + " task(s)"));
        if (!ready.length) {
          container.appendChild(D.dom.empty("Nothing is ready to work on right now."));
        }
        ready.forEach(function (t) { container.appendChild(taskRow(t, ctx)); });
        if (other.length) {
          container.appendChild(ui.sectionTitle("Everything else", other.length + " task(s)"));
          other.forEach(function (t) { container.appendChild(taskRow(t, ctx)); });
        }
      });
    }
  });
})(typeof window !== "undefined" ? window : this);
