// App JS: audit polling + streaming chat UI. No framework; no dependencies.
(function () {
  const REFRESH_MS = 3000;

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function renderEventRow(e) {
    const ts = escapeHtml(e.ts);
    const agent = escapeHtml(e.agent_id);
    const action = escapeHtml(e.action);
    const decision = escapeHtml(e.decision);
    const reason = escapeHtml(e.reason || "");
    const rid = escapeHtml((e.request_id || "").slice(0, 12) + "\u2026");
    const hash = escapeHtml((e.this_hash || "").slice(0, 12) + "\u2026");
    return (
      "<tr>" +
      '<td class="ts">' + ts + "</td>" +
      "<td>" + agent + "</td>" +
      '<td><code>' + action + "</code></td>" +
      '<td><span class="badge badge-' + decision + '">' + decision + "</span></td>" +
      '<td class="reason">' + reason + "</td>" +
      '<td class="rid"><code>' + rid + "</code></td>" +
      '<td class="hash"><code>' + hash + "</code></td>" +
      "</tr>"
    );
  }

  function initAuditPolling() {
    const tableBody = document.querySelector(".audit-table tbody");
    const banner = document.querySelector(".banner");
    if (!tableBody || !banner) return;

    function refresh() {
      fetch("/api/audit", { headers: { "Accept": "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (body) {
          const events = (body.events || []).slice().reverse();
          if (events.length === 0) {
            tableBody.innerHTML = "";
          } else {
            tableBody.innerHTML = events.map(renderEventRow).join("");
          }
          if (body.chain_valid) {
            banner.className = "banner banner-ok";
            banner.textContent = "Chain valid \u00b7 " + events.length + " event(s)";
          } else {
            banner.className = "banner banner-error";
            banner.textContent = "CHAIN TAMPERED \u2014 investigate immediately";
          }
        })
        .catch(function () { /* ignore transient errors */ });
    }

    refresh();
    setInterval(refresh, REFRESH_MS);
  }

  function parseSseBlock(block) {
    let event = null;
    let data = null;
    const lines = block.split("\n");
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.indexOf("event: ") === 0) {
        event = line.slice(7);
      } else if (line.indexOf("data: ") === 0) {
        data = line.slice(6);
      }
    }
    if (!event || !data) return null;
    try {
      return { event: event, data: JSON.parse(data) };
    } catch (e) {
      return null;
    }
  }

  function initChatStream() {
    const form = document.getElementById("chat-form");
    const input = document.getElementById("chat-input");
    const submitBtn = document.getElementById("chat-submit");
    const messagesEl = document.getElementById("chat-messages");
    const traceListEl = document.getElementById("chat-trace-list");
    if (!form || !input || !messagesEl) return;

    function appendMessage(role, content) {
      const div = document.createElement("div");
      div.className = "message message-" + role;
      div.innerHTML = '<div class="role">' + role + '</div>' +
        '<div class="content">' + escapeHtml(content) + '</div>';
      messagesEl.appendChild(div);
      return div.querySelector(".content");
    }

    function appendTraceEntry(t) {
      if (!traceListEl) return;
      const div = document.createElement("div");
      div.className = "trace-entry trace-" + t.decision;
      div.innerHTML =
        '<span class="badge badge-' + t.decision + '">' + t.decision + "</span> " +
        '<code class="tool">' + escapeHtml(t.tool) + "</code> " +
        '<span class="reason">' + escapeHtml(t.reason || "") + "</span>";
      traceListEl.appendChild(div);
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      const message = input.value.trim();
      if (!message) return;
      input.value = "";
      appendMessage("user", message);
      const assistantContentEl = appendMessage("assistant", "");
      assistantContentEl.classList.add("streaming");
      submitBtn.disabled = true;

      fetch("/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: [{ role: "user", content: message }] })
      })
        .then(function (resp) {
          if (!resp.ok) {
            throw new Error("HTTP " + resp.status);
          }
          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";

          function readChunk() {
            return reader.read().then(function (result) {
              if (result.done) {
                assistantContentEl.classList.remove("streaming");
                submitBtn.disabled = false;
                return;
              }
              buffer += decoder.decode(result.value, { stream: true });
              const parts = buffer.split("\n\n");
              buffer = parts.pop();
              for (let i = 0; i < parts.length; i++) {
                const ev = parseSseBlock(parts[i]);
                if (!ev) continue;
                if (ev.event === "token") {
                  assistantContentEl.textContent += ev.data.content;
                } else if (ev.event === "trace") {
                  appendTraceEntry(ev.data);
                } else if (ev.event === "error") {
                  assistantContentEl.textContent += "\n[error: " + escapeHtml(ev.data.message || "unknown") + "]";
                }
              }
              return readChunk();
            });
          }
          return readChunk();
        })
        .catch(function (err) {
          assistantContentEl.classList.remove("streaming");
          submitBtn.disabled = false;
          assistantContentEl.textContent += "\n[error: " + escapeHtml(err.message) + "]";
        });
    });
  }

  function start() {
    initAuditPolling();
    initChatStream();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
