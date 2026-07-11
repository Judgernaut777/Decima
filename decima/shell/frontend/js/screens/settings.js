"use strict";
/*
 * Settings — session and Shell posture. Shows the authenticated principal, the API health,
 * the security properties the Shell relies on, and a sign-out. Reads GET /api/v1/session
 * and GET /api/v1/health; signs out via POST /api/v1/session/logout.
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var ui = D.ui;

  var GUARANTEES = [
    "All content from models and imports is rendered as escaped text — never as markup or code.",
    "Approval action buttons exist only in the trusted Approval inbox, never inside a message.",
    "Mutations carry the CSRF token; approvals additionally require a fresh reauth.",
    "A strict same-origin CSP blocks remote scripts, styles, and fonts.",
    "There is no standing \"approve everything from this agent\" control — every gated " +
      "effect is decided one at a time."
  ];

  D.registerScreen({
    id: "settings",
    title: "Settings",
    icon: "⚙️",
    endpoints: ["GET /api/v1/session", "GET /api/v1/health", "POST /api/v1/session/logout"],
    render: function (container, ctx) {
      return ui.withData(container, async function () {
        var session = await ctx.api.get("/session");
        var health = await ctx.api.reads.health();
        return { session: session, health: health };
      }, function (data) {
        var s = data.session.data || {};
        var h = data.health.data || {};
        container.appendChild(ui.sectionTitle("Session"));
        container.appendChild(ui.card(ui.fields([
          ["Principal", s.principal],
          ["CSRF token present", ctx.api.state.csrf ? "yes" : "no"],
          ["API app", h.app],
          ["API version", h.version],
          ["API status", h.status]
        ])));
        container.appendChild(ui.sectionTitle("Security guarantees"));
        var list = el("ul", { class: "guarantees" });
        GUARANTEES.forEach(function (g) { list.appendChild(el("li", { text: g })); });
        container.appendChild(ui.card(list));
        container.appendChild(el("div", { class: "actions" }, [
          el("button", {
            type: "button", class: "btn btn-danger", text: "Sign out",
            on: { click: async function () {
              await ctx.api.logout();
              window.location.reload();
            } }
          })
        ]));
      });
    }
  });
})(typeof window !== "undefined" ? window : this);
