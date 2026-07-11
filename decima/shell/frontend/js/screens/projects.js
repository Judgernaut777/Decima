"use strict";
/*
 * Projects — the plans/objectives overview with progress, plus a create-project form.
 * Reads GET /api/v1/projects; creates via POST /api/v1/projects.
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var ui = D.ui;

  function projectCard(project) {
    var total = project.task_count || 0;
    var done = project.completed_count || 0;
    var pct = total ? Math.round((done / total) * 100) : 0;
    // Set the fill width via the CSSOM (element.style.width), NOT a `style` ATTRIBUTE.
    // The Shell's strict CSP is `style-src 'self'` (no 'unsafe-inline'), which BLOCKS a
    // style attribute (setAttribute('style', …)); a CSSOM property assignment is not
    // governed by style-src, so the bar fills without a CSP violation.
    var fill = el("div", { class: "progress-fill" });
    fill.style.width = pct + "%";
    return ui.card([
      el("div", { class: "row-head" }, [
        el("strong", { text: project.objective || project.id }),
        ui.statusPill(project.status)
      ]),
      el("div", { class: "progress", title: done + " / " + total }, [fill]),
      ui.fields([
        ["Project", project.id],
        ["Objective", project.objective],
        ["Creator", project.creator_principal],
        ["Steps", total],
        ["Completed", done + " (" + pct + "%)"],
        ["Members", (project.member_agent_ids || []).length]
      ])
    ]);
  }

  function createForm(ctx) {
    var input = el("input", {
      type: "text", class: "input", id: "new-project-objective",
      placeholder: "New project objective", maxlength: "400"
    });
    var form = el("form", { class: "inline-form" }, [
      input,
      el("button", { type: "submit", class: "btn btn-primary", text: "Create project" })
    ]);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      var objective = input.value.trim();
      if (!objective) {
        return;
      }
      var r = await ctx.api.commands.createProject({ objective: objective });
      if (r.ok) {
        input.value = "";
        ctx.toast("Project created", "ok");
        ctx.refreshActive();
      } else {
        ctx.toast("Create failed: " + ((r.data && r.data.reason_code) || r.status), "bad");
      }
    });
    return form;
  }

  D.registerScreen({
    id: "projects",
    title: "Projects",
    icon: "📁",
    endpoints: ["GET /api/v1/projects", "POST /api/v1/projects"],
    render: function (container, ctx) {
      return ui.withData(container, ctx.api.reads.projects, function (projects) {
        container.appendChild(createForm(ctx));
        container.appendChild(ui.sectionTitle("All projects", projects.length + ""));
        if (!projects.length) {
          container.appendChild(D.dom.empty("No projects yet."));
        }
        projects.forEach(function (p) { container.appendChild(projectCard(p)); });
      });
    }
  });
})(typeof window !== "undefined" ? window : this);
