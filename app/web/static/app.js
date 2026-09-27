(function () {
  const main = document.querySelector(".chat-main");
  if (!main) return;

  const conversationId = main.dataset.conversationId;
  const statusEl = document.getElementById("run-status");
  const toast = document.getElementById("toast");
  const form = document.getElementById("composer");
  const input = document.getElementById("message-input");
  const sendButton = document.getElementById("send-button");

  const STATUS_LABELS = {
    running: "Çalışıyor…",
    waiting_approval: "Onay bekliyor",
    done: "Hazır",
    error: "Hata",
  };

  const TOOL_LABELS = {
    list_campaigns: "Kampanyaları listele",
    get_campaign: "Kampanya detayı",
    get_campaign_performance: "Kampanya performansı",
    portfolio_summary: "Portföy özeti",
    category_product_performance: "Kategori ürün performansı",
    propose_campaign: "Kampanya öner",
    update_campaign_budget: "Bütçe güncelle",
    pause_campaign: "Kampanyayı durdur",
    resume_campaign: "Kampanyayı başlat",
    save_learning: "Öğrenim kaydet",
    write_journal: "Journal yaz",
    read_journal: "Journal oku",
    get_recent_audit: "Audit oku",
    list_skills: "Skill listesi",
    read_skill: "Skill oku",
    get_policy: "Politika oku",
    bigquery_query: "BigQuery sorgusu",
  };

  let live = null;
  let running = false;
  const toolTimers = new Map();

  function toolLabel(name) {
    return TOOL_LABELS[name] || name;
  }

  function toolBrief(args) {
    if (!args || typeof args !== "object") return "";
    const parts = [];
    for (const [key, value] of Object.entries(args)) {
      let text;
      if (Array.isArray(value)) text = `${value.length} kayıt`;
      else if (value && typeof value === "object") text = "…";
      else text = String(value);
      if (text.length > 42) text = text.slice(0, 42) + "…";
      parts.push(`${key}: ${text}`);
      if (parts.length === 2) break;
    }
    return parts.join(" · ");
  }

  function formatDuration(ms) {
    const seconds = ms / 1000;
    const text = seconds >= 10 ? seconds.toFixed(0) : seconds.toFixed(1);
    return text.replace(".", ",") + " sn";
  }

  function setStatus(status, label) {
    if (!statusEl) return;
    statusEl.className = "status status-" + status;
    statusEl.textContent = label || STATUS_LABELS[status] || status;
    running = status === "running" || status === "waiting_approval";
    if (sendButton) sendButton.disabled = running;
  }

  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.remove("hidden");
    setTimeout(() => toast.classList.add("hidden"), 7000);
  }

  function scrollToBottom() {
    const nearBottom =
      window.innerHeight + window.scrollY >= document.body.scrollHeight - 220;
    if (nearBottom) window.scrollTo({ top: document.body.scrollHeight });
  }

  function transcriptEl() {
    return document.getElementById("transcript");
  }

  function refreshFeed() {
    if (window.htmx) {
      htmx.ajax("GET", `/partials/transcript/${conversationId}`, {
        target: "#chat-feed",
        swap: "innerHTML",
      });
    }
  }

  function saveOpenState() {
    const openTools = Array.from(
      document.querySelectorAll("#transcript details.tool-card[data-tool-id][open]")
    ).map((element) => element.dataset.toolId);
    const thinkingOpen = !!document.querySelector("#transcript details.thinking[open]");
    sessionStorage.setItem(
      `feed-state:${conversationId}`,
      JSON.stringify({ openTools, thinkingOpen })
    );
  }

  function restoreOpenState() {
    let state = { openTools: [], thinkingOpen: false };
    try {
      state = JSON.parse(sessionStorage.getItem(`feed-state:${conversationId}`) || "{}");
    } catch (error) {
      console.debug("feed state okunamadi", error);
    }
    (state.openTools || []).forEach((toolId) => {
      const card = document.querySelector(`#transcript details.tool-card[data-tool-id="${toolId}"]`);
      if (card) card.open = true;
    });
    if (state.thinkingOpen) {
      const thinking = document.querySelector("#transcript details.thinking");
      if (thinking) thinking.open = true;
    }
    document
      .querySelectorAll("#transcript details.tool-card.status-retry")
      .forEach((card) => (card.open = true));
  }

  function ensureLive() {
    if (live) return live;
    const wrapper = document.createElement("div");
    wrapper.className = "msg msg-assistant live";

    const thinking = document.createElement("details");
    thinking.className = "thinking";
    thinking.hidden = true;
    thinking.innerHTML = "<summary>Düşünüyor…</summary><pre></pre>";

    const content = document.createElement("div");
    content.className = "msg-content markdown streaming";

    const tools = document.createElement("div");
    tools.className = "tool-stack";

    wrapper.append(thinking, content, tools);
    transcriptEl()?.append(wrapper);
    live = {
      wrapper,
      thinking,
      thinkingPre: thinking.querySelector("pre"),
      thinkingStartedAt: null,
      content,
      tools,
    };
    return live;
  }

  function collapseThinking(context) {
    if (!context.thinking || context.thinking.hidden || !context.thinking.open) return;
    context.thinking.open = false;
    const summary = context.thinking.querySelector("summary");
    if (summary) {
      const elapsed = context.thinkingStartedAt ? performance.now() - context.thinkingStartedAt : 0;
      summary.textContent = elapsed ? `Düşünme (${formatDuration(elapsed)})` : "Düşünme";
    }
  }

  function appendUserMessage(text) {
    const empty = transcriptEl()?.querySelector(".empty");
    if (empty) empty.remove();
    const item = document.createElement("div");
    item.className = "msg msg-user";
    const content = document.createElement("div");
    content.className = "msg-content";
    content.textContent = text;
    item.append(content);
    transcriptEl()?.append(item);
    live = null;
    scrollToBottom();
  }

  function renderToolCall(event) {
    const context = ensureLive();
    context.content.classList.remove("streaming");
    collapseThinking(context);
    toolTimers.set(event.tool_call_id, performance.now());

    const card = document.createElement("details");
    card.className = "tool-card status-pending";
    card.dataset.toolId = event.tool_call_id;
    card.innerHTML =
      '<summary><span class="tool-dot"></span><span class="tool-label"></span>' +
      '<span class="tool-brief"></span><span class="tool-status">çalışıyor…</span></summary>' +
      '<div class="tool-args"><span class="label">argümanlar</span><pre></pre></div>' +
      '<div class="tool-result" hidden><span class="label">sonuç</span><pre></pre></div>';
    card.querySelector(".tool-label").textContent = toolLabel(event.name);
    card.querySelector(".tool-brief").textContent = toolBrief(event.args);
    card.querySelector(".tool-args pre").textContent = JSON.stringify(event.args, null, 2);
    context.tools.append(card);
    scrollToBottom();
  }

  function updateToolResult(event) {
    const card = document.querySelector(`[data-tool-id="${event.tool_call_id}"]`);
    if (!card) return;
    const startedAt = toolTimers.get(event.tool_call_id);
    toolTimers.delete(event.tool_call_id);

    card.classList.remove("status-pending");
    card.classList.add(event.status === "ok" ? "status-ok" : "status-retry");
    const result = card.querySelector(".tool-result");
    if (result) {
      result.hidden = false;
      result.querySelector("pre").textContent = event.result;
    }
    const statusNode = card.querySelector(".tool-status");
    if (statusNode) {
      const duration = startedAt ? ` (${formatDuration(performance.now() - startedAt)})` : "";
      statusNode.textContent = (event.status === "ok" ? "tamamlandı" : "yeniden deneme") + duration;
    }
    if (event.status !== "ok") card.open = true;
  }

  function handleEvent(event) {
    switch (event.type) {
      case "user_message":
        appendUserMessage(event.text);
        break;
      case "run_started":
        live = null;
        setStatus("running");
        break;
      case "text_delta": {
        const context = ensureLive();
        collapseThinking(context);
        context.content.textContent += event.content;
        scrollToBottom();
        break;
      }
      case "thinking_delta": {
        const context = ensureLive();
        if (context.thinking.hidden) {
          context.thinking.hidden = false;
          context.thinking.open = true;
          context.thinkingStartedAt = performance.now();
        }
        context.thinkingPre.textContent += event.content;
        break;
      }
      case "tool_call":
        renderToolCall(event);
        break;
      case "tool_result":
        updateToolResult(event);
        break;
      case "approval_required":
        setStatus("waiting_approval");
        refreshFeed();
        break;
      case "approval_resolved":
        refreshFeed();
        break;
      case "run_finished":
        setStatus(event.status, STATUS_LABELS[event.status]);
        refreshFeed();
        break;
      case "error":
        showToast("Hata: " + event.message);
        setStatus("error");
        break;
      default:
        break;
    }
  }

  document.addEventListener("htmx:beforeSwap", (event) => {
    if (event.target?.id === "chat-feed") saveOpenState();
  });
  document.addEventListener("htmx:afterSwap", (event) => {
    if (event.target?.id === "chat-feed") restoreOpenState();
  });
  restoreOpenState();

  const source = new EventSource(`/api/conversations/${conversationId}/events`);
  source.onmessage = (message) => {
    try {
      handleEvent(JSON.parse(message.data));
    } catch (error) {
      console.error("Olay islenemedi", error);
    }
  };

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || running) return;
    input.value = "";
    autoResize();
    setStatus("running");
    const response = await fetch(`/api/conversations/${conversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      showToast(data.detail || "Mesaj gönderilemedi");
      setStatus("done");
    }
  });

  document.addEventListener("click", (event) => {
    const suggestion = event.target.closest(".suggestion");
    if (!suggestion || !input || !form) return;
    input.value = suggestion.dataset.prompt || suggestion.textContent.trim();
    autoResize();
    input.focus();
    form.requestSubmit();
  });

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-approval-action]");
    if (!button) return;
    const card = button.closest("[data-approval-id]");
    if (!card) return;
    const approvalId = card.dataset.approvalId;
    const approved = button.dataset.approvalAction === "approve";
    const note = card.querySelector('input[name="note"]')?.value || null;
    card.querySelectorAll("button").forEach((item) => (item.disabled = true));
    const response = await fetch(`/api/approvals/${approvalId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved, note }),
    });
    if (!response.ok) {
      showToast("Onay kaydedilemedi");
      card.querySelectorAll("button").forEach((item) => (item.disabled = false));
    } else {
      refreshFeed();
    }
  });

  input?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  function autoResize() {
    if (!input) return;
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 240) + "px";
  }
  input?.addEventListener("input", autoResize);
  autoResize();

  document.getElementById("new-conversation")?.addEventListener("click", async () => {
    const response = await fetch("/api/conversations", { method: "POST" });
    const data = await response.json();
    window.location.href = `/c/${data.id}`;
  });

  setStatus(main.dataset.status, statusEl?.textContent?.trim());
})();
