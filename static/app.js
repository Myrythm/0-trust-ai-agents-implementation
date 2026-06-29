// App JS: theme, audit polling, and a modern multi-turn streaming chat UI.
// No framework; no dependencies. Markdown is rendered with a small built-in
// renderer that always escapes HTML before formatting.
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

  // ---------- Theme ----------

  function initTheme() {
    const btn = document.getElementById("theme-toggle");
    if (!btn) return;
    btn.addEventListener("click", function () {
      const cur = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
      const next = cur === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("zta-theme", next); } catch (e) { /* ignore */ }
    });
  }

  // ---------- Minimal Markdown renderer (escape-first) ----------

  function renderInline(text) {
    // text is already HTML-escaped. Split on inline-code spans so their
    // contents are not touched by the bold/italic/link passes.
    const parts = text.split(/(`[^`]+`)/g);
    return parts
      .map(function (part) {
        if (part.length >= 2 && part.charAt(0) === "`" && part.charAt(part.length - 1) === "`") {
          return "<code>" + part.slice(1, -1) + "</code>";
        }
        return part
          .replace(/\[([^\]]+)\]\((https?:[^\s)]+)\)/g,
            '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
          .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
          .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
      })
      .join("");
  }

  function renderMarkdown(src) {
    const lines = escapeHtml(src).split("\n");
    let html = "";
    let i = 0;

    function isListItem(line) {
      return /^\s*([-*+]|\d+\.)\s+/.test(line);
    }

    // Build nested <ul>/<ol> from a flat list of {indent, ordered, content}.
    function buildNested(items) {
      let pos = 0;
      function build() {
        const indent = items[pos].indent;
        const ordered = items[pos].ordered;
        let out = ordered ? "<ol>" : "<ul>";
        while (pos < items.length && items[pos].indent === indent) {
          let li = "<li>" + renderInline(items[pos].content).replace(/\n/g, "<br>");
          pos++;
          if (pos < items.length && items[pos].indent > indent) {
            li += build();
          }
          li += "</li>";
          out += li;
        }
        out += ordered ? "</ol>" : "</ul>";
        return out;
      }
      return items.length ? build() : "";
    }

    // Consume a whole list block: consecutive list items (any nesting),
    // tolerating blank lines between items and indented continuation lines.
    function renderListBlock() {
      const items = [];
      while (i < lines.length) {
        const line = lines[i];
        if (line.trim() === "") {
          let j = i + 1;
          while (j < lines.length && lines[j].trim() === "") j++;
          if (j < lines.length && isListItem(lines[j])) { i = j; continue; }
          break;
        }
        const m = line.match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
        if (m) {
          const indent = m[1].replace(/\t/g, "    ").length;
          const ordered = /\d+\./.test(m[2]);
          items.push({ indent: indent, ordered: ordered, content: m[3] });
          i++;
          continue;
        }
        // Indented continuation text belongs to the previous item.
        if (items.length && /^\s+\S/.test(line)) {
          items[items.length - 1].content += "\n" + line.trim();
          i++;
          continue;
        }
        break;
      }
      return buildNested(items);
    }

    while (i < lines.length) {
      const line = lines[i];

      // Fenced code block
      const fence = line.match(/^```(.*)$/);
      if (fence) {
        const body = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) { body.push(lines[i]); i++; }
        i++; // skip closing fence
        html += "<pre><code>" + body.join("\n") + "</code></pre>";
        continue;
      }

      // Headings
      const h = line.match(/^(#{1,3})\s+(.*)$/);
      if (h) {
        const level = h[1].length;
        html += "<h" + level + ">" + renderInline(h[2]) + "</h" + level + ">";
        i++;
        continue;
      }

      // Blockquote
      if (/^>\s?/.test(line)) {
        const quote = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          quote.push(renderInline(lines[i].replace(/^>\s?/, "")));
          i++;
        }
        html += "<blockquote>" + quote.join("<br>") + "</blockquote>";
        continue;
      }

      // Lists (supports nesting + blank lines between items)
      if (isListItem(line)) {
        html += renderListBlock();
        continue;
      }

      // Blank line
      if (line.trim() === "") { i++; continue; }

      // Paragraph (gather consecutive non-special lines)
      const para = [renderInline(line)];
      i++;
      while (
        i < lines.length &&
        lines[i].trim() !== "" &&
        !/^```/.test(lines[i]) &&
        !/^(#{1,3})\s+/.test(lines[i]) &&
        !/^>\s?/.test(lines[i]) &&
        !isListItem(lines[i])
      ) {
        para.push(renderInline(lines[i]));
        i++;
      }
      html += "<p>" + para.join("<br>") + "</p>";
    }
    return html;
  }

  // ---------- Audit polling ----------

  function renderEventRow(e) {
    const ts = escapeHtml(e.ts);
    const agent = escapeHtml(e.agent_id);
    const action = escapeHtml(e.action);
    const decision = escapeHtml(e.decision);
    const reason = escapeHtml(e.reason || "");
    const rid = escapeHtml((e.request_id || "").slice(0, 12) + "…");
    const hash = escapeHtml((e.this_hash || "").slice(0, 12) + "…");
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
          tableBody.innerHTML = events.length === 0 ? "" : events.map(renderEventRow).join("");
          if (body.chain_valid) {
            banner.className = "banner banner-ok";
            banner.innerHTML =
              '<span class="banner-ico">✓</span> Chain valid · ' + events.length + " event(s)";
          } else {
            banner.className = "banner banner-error";
            banner.innerHTML =
              '<span class="banner-ico">!</span> CHAIN TAMPERED — investigate immediately';
          }
        })
        .catch(function () { /* ignore transient errors */ });
    }

    refresh();
    setInterval(refresh, REFRESH_MS);
  }

  // ---------- SSE parsing ----------

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

  // ---------- Chat ----------

  function initChat() {
    const form = document.getElementById("chat-form");
    const input = document.getElementById("chat-input");
    const submitBtn = document.getElementById("chat-submit");
    const messagesEl = document.getElementById("chat-messages");
    const scrollEl = document.getElementById("chat-scroll");
    const emptyEl = document.getElementById("chat-empty");
    const newChatBtn = document.getElementById("new-chat");
    if (!form || !input || !messagesEl) return;

    // Full conversation history sent to the backend each turn.
    const conversation = [];
    let streaming = false;

    function scrollToBottom() {
      if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight;
    }

    function hideEmpty() {
      if (emptyEl) emptyEl.style.display = "none";
    }

    function autoSize() {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 200) + "px";
    }

    function appendUserMessage(text) {
      const div = document.createElement("div");
      div.className = "msg msg-user";
      div.innerHTML = '<div class="bubble">' + escapeHtml(text) + "</div>";
      messagesEl.appendChild(div);
    }

    function appendAssistantMessage() {
      const div = document.createElement("div");
      div.className = "msg msg-assistant";
      div.innerHTML = '<div class="avatar">🛡️</div><div class="md streaming"></div>';
      messagesEl.appendChild(div);
      return div.querySelector(".md");
    }

    function appendToolCard(t) {
      const decision = t.decision || "error";
      const div = document.createElement("div");
      div.className = "tool-card tool-" + decision;
      let html =
        '<div class="tool-card-head">' +
        '<span class="badge badge-' + escapeHtml(decision) + '">' + escapeHtml(decision) + "</span>" +
        '<code class="tool">' + escapeHtml(t.tool || "") + "</code>";
      const hasArgs = t.args && Object.keys(t.args).length > 0;
      if (hasArgs) html += '<button type="button" class="tool-toggle">args</button>';
      html += "</div>";
      if (t.reason) html += '<div class="tool-reason">' + escapeHtml(t.reason) + "</div>";
      if (hasArgs) {
        html += '<pre class="tool-args" hidden>' + escapeHtml(JSON.stringify(t.args, null, 2)) + "</pre>";
      }
      div.innerHTML = html;
      const toggle = div.querySelector(".tool-toggle");
      const args = div.querySelector(".tool-args");
      if (toggle && args) {
        toggle.addEventListener("click", function () { args.hidden = !args.hidden; });
      }
      messagesEl.appendChild(div);
    }

    function send(message) {
      if (streaming) return;
      message = message.trim();
      if (!message) return;

      hideEmpty();
      input.value = "";
      autoSize();
      appendUserMessage(message);
      conversation.push({ role: "user", content: message });

      const mdEl = appendAssistantMessage();
      let raw = "";
      streaming = true;
      submitBtn.disabled = true;
      scrollToBottom();

      fetch("/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: conversation })
      })
        .then(function (resp) {
          if (!resp.ok) throw new Error("HTTP " + resp.status);
          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";

          function finish() {
            mdEl.classList.remove("streaming");
            mdEl.innerHTML = renderMarkdown(raw);
            conversation.push({ role: "assistant", content: raw });
            streaming = false;
            submitBtn.disabled = false;
            scrollToBottom();
          }

          function readChunk() {
            return reader.read().then(function (result) {
              if (result.done) { finish(); return; }
              buffer += decoder.decode(result.value, { stream: true });
              const parts = buffer.split("\n\n");
              buffer = parts.pop();
              for (let i = 0; i < parts.length; i++) {
                const ev = parseSseBlock(parts[i]);
                if (!ev) continue;
                if (ev.event === "token") {
                  raw += ev.data.content;
                  mdEl.textContent = raw;
                } else if (ev.event === "trace") {
                  appendToolCard(ev.data);
                } else if (ev.event === "error") {
                  raw += "\n\n_[error: " + (ev.data.message || "unknown") + "]_";
                  mdEl.textContent = raw;
                }
                scrollToBottom();
              }
              return readChunk();
            });
          }
          return readChunk();
        })
        .catch(function (err) {
          mdEl.classList.remove("streaming");
          raw += "\n\n_[error: " + err.message + "]_";
          mdEl.innerHTML = renderMarkdown(raw);
          conversation.push({ role: "assistant", content: raw });
          streaming = false;
          submitBtn.disabled = false;
          scrollToBottom();
        });
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      send(input.value);
    });

    // Enter sends; Shift+Enter inserts a newline.
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send(input.value);
      }
    });

    input.addEventListener("input", autoSize);

    // Suggestion chips fill the composer.
    const suggestions = document.querySelectorAll(".suggestion");
    for (let i = 0; i < suggestions.length; i++) {
      suggestions[i].addEventListener("click", function () {
        input.value = this.textContent;
        autoSize();
        input.focus();
      });
    }

    // New chat resets the conversation.
    if (newChatBtn) {
      newChatBtn.addEventListener("click", function () {
        if (streaming) return;
        conversation.length = 0;
        messagesEl.innerHTML = "";
        if (emptyEl) emptyEl.style.display = "";
        input.value = "";
        autoSize();
        input.focus();
      });
    }

    autoSize();
  }

  function start() {
    initTheme();
    initAuditPolling();
    initChat();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
