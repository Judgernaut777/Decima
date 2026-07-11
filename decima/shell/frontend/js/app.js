"use strict";
/*
 * app.js — the trusted application shell: screen registry, navigation, view switching,
 * login gate, and a small toast channel. This is the top-level chrome the user trusts;
 * it renders trusted decisions and hosts the trusted approval component. Nothing here is
 * agent-generated at runtime.
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var clear = D.dom.clear;

  var screens = [];
  var byId = {};
  var active = null;
  var activeCleanup = null;
  var refreshTimer = null;

  function registerScreen(screen) {
    screens.push(screen);
    byId[screen.id] = screen;
  }

  function screenList() {
    return screens.slice();
  }

  // -- toast (transient, trusted status) ---------------------------------
  function toast(message, kind) {
    var host = document.getElementById("toasts");
    if (!host) {
      return;
    }
    var node = el("div", { class: "toast toast-" + (kind || "info"), text: message });
    host.appendChild(node);
    setTimeout(function () {
      if (node.parentNode) {
        node.parentNode.removeChild(node);
      }
    }, 4000);
  }

  // -- navigation --------------------------------------------------------
  function buildNav() {
    var nav = document.getElementById("nav");
    clear(nav);
    screens.forEach(function (screen) {
      var btn = el("button", {
        class: "nav-item",
        type: "button",
        dataset: { screen: screen.id },
        on: {
          click: function () {
            show(screen.id);
          }
        }
      }, [
        el("span", { class: "nav-icon", "aria-hidden": "true", text: screen.icon || "•" }),
        el("span", { class: "nav-text", text: screen.title }),
        el("span", { class: "nav-badge", dataset: { badge: screen.id } })
      ]);
      nav.appendChild(btn);
    });
  }

  function setBadge(screenId, count) {
    var badge = document.querySelector('[data-badge="' + screenId + '"]');
    if (!badge) {
      return;
    }
    if (count && count > 0) {
      badge.textContent = String(count);
      badge.classList.add("has-count");
    } else {
      badge.textContent = "";
      badge.classList.remove("has-count");
    }
  }

  function markActive(screenId) {
    var items = document.querySelectorAll(".nav-item");
    items.forEach(function (item) {
      if (item.dataset.screen === screenId) {
        item.classList.add("active");
        item.setAttribute("aria-current", "page");
      } else {
        item.classList.remove("active");
        item.removeAttribute("aria-current");
      }
    });
  }

  async function show(screenId) {
    var screen = byId[screenId];
    if (!screen) {
      return;
    }
    if (typeof activeCleanup === "function") {
      try {
        activeCleanup();
      } catch (e) {
        /* cleanup is best-effort */
      }
      activeCleanup = null;
    }
    active = screenId;
    markActive(screenId);
    var container = document.getElementById("view");
    clear(container);
    document.getElementById("view-title").textContent = screen.title;
    document.getElementById("view-endpoints").textContent =
      (screen.endpoints || []).join("  ");
    var body = el("div", { class: "screen screen-" + screenId });
    container.appendChild(body);
    try {
      var cleanup = await screen.render(body, ctx);
      activeCleanup = typeof cleanup === "function" ? cleanup : null;
    } catch (e) {
      clear(body);
      body.appendChild(D.dom.zone("system", "Shell error",
        el("p", { text: "Could not render this screen: " + (e && e.message) })));
    }
  }

  function refreshActive() {
    if (active) {
      show(active);
    }
  }

  // -- badges from live data (approvals count) ---------------------------
  async function refreshBadges() {
    if (!D.api.state.authenticated) {
      return;
    }
    try {
      var approvals = await D.api.reads.approvals();
      var pending = approvals.filter(function (a) {
        return a.state === "pending";
      }).length;
      setBadge("approvals", pending);
    } catch (e) {
      /* non-fatal: badges are cosmetic */
    }
  }

  // -- login gate --------------------------------------------------------
  function showGate(showIt) {
    var gate = document.getElementById("gate");
    var appEl = document.getElementById("app");
    gate.hidden = !showIt;
    appEl.hidden = showIt;
  }

  function wireGate() {
    var form = document.getElementById("gate-form");
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      var input = document.getElementById("gate-secret");
      var err = document.getElementById("gate-error");
      err.textContent = "";
      var r = await D.api.login(input.value);
      input.value = "";
      if (r.ok) {
        onAuthenticated();
      } else {
        err.textContent = "Pairing failed (" +
          ((r.data && r.data.reason_code) || r.status) + ").";
      }
    });
  }

  function wireLogout() {
    document.getElementById("logout-btn").addEventListener("click", async function () {
      await D.api.logout();
      onLoggedOut();
    });
  }

  function onAuthenticated() {
    showGate(false);
    document.getElementById("principal").textContent = D.api.state.principal || "operator";
    buildNav();
    show(screens[0].id);
    refreshBadges();
    if (refreshTimer) {
      clearInterval(refreshTimer);
    }
    refreshTimer = setInterval(refreshBadges, 5000);
  }

  function onLoggedOut() {
    if (refreshTimer) {
      clearInterval(refreshTimer);
      refreshTimer = null;
    }
    showGate(true);
  }

  var ctx = {
    api: D.api,
    dom: D.dom,
    toast: toast,
    refreshActive: refreshActive,
    refreshBadges: refreshBadges,
    show: show
  };

  async function boot() {
    wireGate();
    wireLogout();
    // If a session cookie already exists, resume; otherwise show the pairing gate.
    var r = await D.api.refreshSession();
    if (r.ok) {
      onAuthenticated();
    } else {
      showGate(true);
    }
  }

  D.registerScreen = registerScreen;
  D.screenList = screenList;
  D.setBadge = setBadge;
  D.app = { boot: boot, show: show, toast: toast, ctx: ctx };

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", boot);
    } else {
      boot();
    }
  }
})(typeof window !== "undefined" ? window : this);
