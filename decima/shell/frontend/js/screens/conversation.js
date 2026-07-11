"use strict";
/*
 * Conversation — the live transcript. Streams assistant / plan / step / approval / error
 * events from GET /api/v1/stream and renders each in its trust zone. Assistant text is
 * MODEL-generated content (zone-model, escaped); plan/step are trusted SYSTEM decisions
 * (zone-system); approval events are a SYSTEM notice that points to the trusted Approval
 * inbox — there are NO action buttons here (invariant 5). No event content is ever
 * executed or rendered as markup.
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var zone = D.dom.zone;

  function renderEvent(frame) {
    var kind = frame.event || "message";
    var data = frame.data && frame.data.data ? frame.data.data : (frame.data || {});
    var seq = frame.id != null ? "#" + frame.id : "";
    if (kind === "assistant") {
      var body = data.text || data.message || data.content || JSON.stringify(data);
      return zone("model", "Model message " + seq, el("p", { class: "msg", text: body }));
    }
    if (kind === "plan" || kind === "step") {
      return zone("system", "System · " + kind + " " + seq,
        el("pre", { class: "sys-data", text: prettyData(data) }));
    }
    if (kind === "approval") {
      return zone("system", "System · approval requested " + seq, [
        el("p", { text: "A gated effect needs your decision: " +
          (data.command || data.item || "") }),
        el("p", { class: "hint", text: "Review and decide it in the Approval inbox." })
      ]);
    }
    if (kind === "error") {
      return zone("system", "System · error " + seq,
        el("p", { class: "err", text: prettyData(data) }));
    }
    return zone("system", "System · " + kind + " " + seq,
      el("pre", { class: "sys-data", text: prettyData(data) }));
  }

  function prettyData(data) {
    try {
      return typeof data === "string" ? data : JSON.stringify(data, null, 2);
    } catch (e) {
      return String(data);
    }
  }

  D.registerScreen({
    id: "conversation",
    title: "Conversation",
    icon: "💬",
    endpoints: ["GET /api/v1/stream"],
    render: function (container, ctx) {
      var cursor = 0;
      var live = true;
      var stream = el("div", { class: "transcript", id: "transcript" });

      var toolbar = el("div", { class: "toolbar" }, [
        el("button", {
          type: "button", class: "btn", id: "live-toggle", text: "Pause stream",
          on: { click: function () {
            live = !live;
            this.textContent = live ? "Pause stream" : "Resume stream";
          } }
        }),
        el("span", { class: "hint", text: "Read-only transcript of model, plan, and step events." })
      ]);

      container.appendChild(toolbar);
      container.appendChild(stream);

      var stopped = false;
      async function poll() {
        if (stopped || !live) {
          return;
        }
        try {
          var res = await ctx.api.stream(cursor);
          (res.frames || []).forEach(function (frame) {
            if (frame.id != null && frame.id > cursor) {
              cursor = frame.id;
            }
            stream.appendChild(renderEvent(frame));
          });
          if (res.frames && res.frames.length) {
            stream.scrollTop = stream.scrollHeight;
          }
        } catch (e) {
          /* transient; next tick retries */
        }
      }

      poll();
      var timer = setInterval(poll, 1500);
      return function cleanup() {
        stopped = true;
        clearInterval(timer);
      };
    }
  });
})(typeof window !== "undefined" ? window : this);
