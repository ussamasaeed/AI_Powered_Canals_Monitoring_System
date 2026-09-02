// Floating chatbot widget: toggles the panel, persists the conversation in
// localStorage (so it survives page reloads/navigation between Home/Map),
// and talks to /api/chat, which runs the RAG pipeline (Chroma similarity
// search + HF chat model) server-side in chatbot.py.
(function () {
  const STORAGE_KEY = "canal_chatbot_history";
  const GREETING = "Assalam-o-Alaikum! How can I help you today?";

  const fab = document.getElementById("chatbotFab");
  const panel = document.getElementById("chatbotPanel");
  const closeBtn = document.getElementById("chatbotCloseBtn");
  const newBtn = document.getElementById("chatbotNewBtn");
  const deleteBtn = document.getElementById("chatbotDeleteBtn");
  const form = document.getElementById("chatbotForm");
  const input = document.getElementById("chatbotInput");
  const messages = document.getElementById("chatbotMessages");

  if (!fab || !panel || !form) return;

  function loadHistory() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function saveHistory(history) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(history));
    } catch (e) {
      /* storage full or unavailable - conversation just won't persist */
    }
  }

  let history = loadHistory() || [{ who: "bot", text: GREETING }];

  function render() {
    messages.innerHTML = "";
    history.forEach((m) => {
      const div = document.createElement("div");
      div.className = "chatbot-msg " + (m.who === "user" ? "chatbot-msg-user" : "chatbot-msg-bot");
      div.textContent = m.text;
      messages.appendChild(div);
    });
    messages.scrollTop = messages.scrollHeight;
  }

  function addMessage(text, who) {
    history.push({ who, text });
    saveHistory(history);
    const div = document.createElement("div");
    div.className = "chatbot-msg " + (who === "user" ? "chatbot-msg-user" : "chatbot-msg-bot");
    div.textContent = text;
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
  }

  function startNewChat() {
    history = [{ who: "bot", text: GREETING }];
    saveHistory(history);
    render();
    input.focus();
  }

  function deleteChat() {
    if (!confirm("Delete this chat history? This can't be undone.")) return;
    localStorage.removeItem(STORAGE_KEY);
    history = [{ who: "bot", text: GREETING }];
    render();
  }

  function togglePanel() {
    const isOpen = panel.classList.toggle("open");
    fab.classList.toggle("open", isOpen);
    if (isOpen) input.focus();
  }

  function closePanel() {
    panel.classList.remove("open");
    fab.classList.remove("open");
  }

  async function sendMessage(text) {
    addMessage(text, "user");
    const loading = addMessage("Thinking...", "bot");
    loading.classList.add("chatbot-msg-loading");
    // the "Thinking..." placeholder shouldn't be persisted - drop it, the
    // real answer gets pushed once it arrives
    history.pop();
    saveHistory(history);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      const data = await res.json();
      loading.classList.remove("chatbot-msg-loading");
      const finalText = !res.ok
        ? "Error: " + (data.detail || "something went wrong.")
        : (data.answer || "No answer returned.");
      loading.textContent = finalText;
      history.push({ who: "bot", text: finalText });
      saveHistory(history);
    } catch (err) {
      loading.classList.remove("chatbot-msg-loading");
      const finalText = "Network error - is the server running?";
      loading.textContent = finalText;
      history.push({ who: "bot", text: finalText });
      saveHistory(history);
    }
  }

  render();

  fab.addEventListener("click", togglePanel);
  closeBtn.addEventListener("click", closePanel);
  newBtn.addEventListener("click", startNewChat);
  deleteBtn.addEventListener("click", deleteChat);

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    sendMessage(text);
  });
})();
