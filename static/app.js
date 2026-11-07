/**
 * ==============================================================================
 * My Private AI - Frontend Application Logic
 * ==============================================================================
 * 
 * How this works (Beginner's Guide):
 * 1. WORKSPACES: Documents and chat memory are grouped by workspace (e.g. "default", "finance").
 * 2. CHAT HISTORY: Past conversations are stored in SQLite. You can view past threads,
 *    switch between them, start fresh conversations, or delete old ones.
 * 3. HEALTH CHECK: Periodically checks if Ollama (the local AI server) is online and ready.
 * 4. FILE UPLOADS: Sends PDFs, Docs, Sheets to FastAPI, where they are chunked and converted into
 *    mathematical vectors (embeddings) and stored in ChromaDB.
 * 5. CHAT & STREAMING: When you ask a question:
 *    - The backend retrieves the most relevant chunks from ChromaDB (using Hybrid BM25 + Vector Search).
 *    - Ollama generates an answer and streams words back one token at a time via Server-Sent Events (SSE).
 *    - Markdown is parsed into styled HTML with syntax highlighting on the fly!
 */

// Shorthand helper for getting DOM elements by ID
const $ = (id) => document.getElementById(id);

// Configure marked.js for GitHub-flavored Markdown rendering
if (window.marked) {
  marked.setOptions({
    gfm: true,
    breaks: true,
  });
}

// Global reference to the current streaming request (allows clicking "Stop")
let activeAbortController = null;

// ==============================================================================
// 0. User Authentication & Chat History Security State
// ==============================================================================

let currentUser = null; // { id: number, username: string } or null
let authMode = "login"; // "login" | "register"

function getAuthToken() {
  return localStorage.getItem("coworker_auth_token") || "";
}

function setAuthToken(token) {
  if (token) {
    localStorage.setItem("coworker_auth_token", token);
  } else {
    localStorage.removeItem("coworker_auth_token");
  }
}

/**
 * Custom fetch wrapper that automatically appends the Bearer authentication token
 * if a user session is active.
 */
async function authFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = getAuthToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  options.headers = headers;
  return fetch(url, options);
}

function updateAuthUI() {
  const loginBtn = $("headerLoginBtn");
  const profileBadge = $("userProfileBadge");
  const usernameSpan = $("headerUsername");

  if (currentUser) {
    if (loginBtn) loginBtn.style.display = "none";
    if (profileBadge) profileBadge.style.display = "inline-flex";
    if (usernameSpan) usernameSpan.textContent = currentUser.username;
    showChatUnlockedState();
  } else {
    if (loginBtn) loginBtn.style.display = "inline-flex";
    if (profileBadge) profileBadge.style.display = "none";
    showChatLockedState();
  }
}

function showChatLockedState() {
  const lockedState = $("chatAuthLockedState");
  const unlockedView = $("chatUnlockedView");
  const tabBadge = $("tabChatCount");

  if (lockedState) lockedState.style.display = "flex";
  if (unlockedView) unlockedView.style.display = "none";
  if (tabBadge) tabBadge.textContent = "🔒";
}

function showChatUnlockedState() {
  const lockedState = $("chatAuthLockedState");
  const unlockedView = $("chatUnlockedView");

  if (lockedState) lockedState.style.display = "none";
  if (unlockedView) unlockedView.style.display = "block";
}

async function checkAuthStatus() {
  try {
    const res = await authFetch("/api/auth/me");
    if (!res.ok) throw new Error("Auth check failed");
    const data = await res.json();
    if (data.authenticated) {
      currentUser = { id: data.user_id, username: data.username };
    } else {
      currentUser = null;
    }
  } catch (err) {
    currentUser = null;
  }
  updateAuthUI();
  return currentUser;
}

// ==============================================================================
// 1. Session & Workspace Helpers
// ==============================================================================

let cachedWorkspaces = [];

/**
 * Returns the currently active workspace name.
 */
function getWorkspace() {
  const hiddenInput = $("workspace");
  if (hiddenInput && hiddenInput.value.trim()) {
    return hiddenInput.value.trim();
  }
  const select = $("workspaceSelect");
  if (select && select.value) {
    return select.value;
  }
  return "default";
}

/**
 * Set active workspace across select, hidden input, and localStorage
 */
function setWorkspace(name) {
  const ws = (name || "default").trim().toLowerCase();
  if ($("workspace")) $("workspace").value = ws;
  if ($("workspaceSelect")) $("workspaceSelect").value = ws;
  localStorage.setItem("coworker_active_workspace", ws);
  updateWorkspaceUI();
}

/**
 * Update UI elements depending on active workspace (delete button visibility, stats badge)
 */
function updateWorkspaceUI() {
  const activeWs = getWorkspace();
  const deleteBtn = $("deleteWorkspaceBtn");
  if (deleteBtn) {
    deleteBtn.style.display = (activeWs === "default") ? "none" : "inline-flex";
  }

  const wsInfo = cachedWorkspaces.find(w => w.name === activeWs);
  const badge = $("wsStatsBadge");
  if (badge) {
    if (wsInfo) {
      badge.textContent = `📁 ${wsInfo.file_count} files · ${wsInfo.conversation_count} chats`;
    } else {
      badge.textContent = `📁 ${activeWs}`;
    }
  }

  const activeBadge = $("workspaceActiveBadge");
  if (activeBadge) {
    activeBadge.textContent = activeWs;
    activeBadge.title = `Active workspace: ${activeWs}`;
  }
}

/**
 * Fetches all available workspaces from backend and populates the selector dropdown
 */
async function loadWorkspaces(preferredWorkspace = null) {
  try {
    const res = await fetch("/api/workspaces");
    if (!res.ok) throw new Error("Failed to load workspaces");
    const data = await res.json();
    cachedWorkspaces = data.workspaces || [];

    const select = $("workspaceSelect");
    if (!select) return;

    const savedWs = preferredWorkspace 
      || localStorage.getItem("coworker_active_workspace") 
      || $("workspace")?.value 
      || "default";

    select.innerHTML = "";
    let matched = false;

    for (const ws of cachedWorkspaces) {
      const option = document.createElement("option");
      option.value = ws.name;
      const countLabel = `${ws.file_count} files · ${ws.conversation_count} chats`;
      option.textContent = `${ws.name} (${countLabel})`;
      if (ws.name === savedWs) {
        option.selected = true;
        matched = true;
      }
      select.appendChild(option);
    }

    if (!matched && savedWs) {
      const option = document.createElement("option");
      option.value = savedWs;
      option.textContent = `${savedWs} (0 files · 0 chats)`;
      option.selected = true;
      select.appendChild(option);
    }

    const finalActive = select.value || "default";
    if ($("workspace")) $("workspace").value = finalActive;
    localStorage.setItem("coworker_active_workspace", finalActive);
    updateWorkspaceUI();
  } catch (err) {
    console.warn("Could not load workspaces:", err);
  }
}

/**
 * Generates or retrieves the active conversation ID for the current workspace.
 * Stored in sessionStorage so refreshing the browser retains the current conversation.
 */
function getConversationId() {
  const key = "coworkerConversationId:" + getWorkspace();
  let id = sessionStorage.getItem(key);
  if (!id) {
    id = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : "conv-" + Date.now();
    sessionStorage.setItem(key, id);
  }
  return id;
}

/**
 * Explicitly sets the active conversation ID.
 */
function setConversationId(id) {
  const key = "coworkerConversationId:" + getWorkspace();
  sessionStorage.setItem(key, id);
  if (window.innerWidth <= 860) {
    $("appSidebar")?.classList.remove("mobile-open");
    $("sidebarBackdrop")?.classList.remove("active");
  }
}

/**
 * Starts a fresh conversation thread with a new unique ID.
 */
function startNewConversation() {
  clearFailedPrompt();
  const newId = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : "conv-" + Date.now();
  setConversationId(newId);

  const container = $("messagesContainer");
  container.innerHTML = `
    <div id="welcomeHero" class="welcome-hero">
      <div class="welcome-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
        </svg>
      </div>
      <h2>New Conversation</h2>
      <p>Started a fresh chat in workspace '<strong>${escapeHtml(getWorkspace())}</strong>'. Ask a question or select a suggestion below.</p>

      <div class="starter-chips">
        <button class="chip" data-prompt="Summarize the key objectives and conclusions from the uploaded documents.">
          📑 Summarize key objectives
        </button>
        <button class="chip" data-prompt="Extract all dates, milestones, and project deadlines.">
          🗓️ Find deadlines & milestones
        </button>
        <button class="chip" data-prompt="Who are the key people mentioned and what are their designated roles?">
          👤 Identify team & roles
        </button>
        <button class="chip" data-prompt="List all financial numbers, budgets, and cost metrics mentioned.">
          💰 Extract budget & financials
        </button>
      </div>
    </div>
  `;
  $("chatStatusText").textContent = "New conversation ready.";
  $("question").value = "";
  $("question").focus();
  loadConversations();
}

/**
 * Escapes raw strings to prevent HTML injection.
 */
function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

/**
 * Formats byte counts into human-readable strings (e.g., 2.4 MB).
 */
function formatBytes(bytes) {
  if (!bytes || bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

// ==============================================================================
// 2. Markdown & Code Block Post-Processing
// ==============================================================================

/**
 * Converts raw markdown text to safe HTML.
 */
function renderMarkdown(rawText) {
  if (!rawText) return "";
  if (window.marked && typeof marked.parse === "function") {
    return marked.parse(rawText);
  }
  return escapeHtml(rawText).replace(/\n/g, "<br>");
}

/**
 * Inserts the typing cursor *inside* the trailing block tag (e.g. </p>, </li>)
 * so it stays inline with the text and never drops onto an empty line.
 */
function renderMarkdownWithCursor(rawText) {
  const html = renderMarkdown(rawText);
  const cursorHtml = '<span class="typing-cursor"></span>';
  if (!html) return cursorHtml;

  const match = html.match(/(<\/(?:p|li|h[1-6]|blockquote|pre|div)>)\s*$/i);
  if (match) {
    const lastIndex = html.lastIndexOf(match[1]);
    return html.substring(0, lastIndex) + cursorHtml + html.substring(lastIndex);
  }
  return html + cursorHtml;
}

/**
 * Applies syntax highlighting (highlight.js) to code blocks and attaches
 * a "Copy Code" button to each code snippet.
 */
function postProcessAssistantBubble(container) {
  if (window.hljs) {
    container.querySelectorAll("pre code").forEach((block) => {
      hljs.highlightElement(block);
    });
  }

  container.querySelectorAll("pre").forEach((pre) => {
    if (pre.parentElement.classList.contains("code-container")) return;

    const wrapper = document.createElement("div");
    wrapper.className = "code-container";

    const header = document.createElement("div");
    header.className = "code-header";

    const codeEl = pre.querySelector("code");
    let lang = "code";
    if (codeEl) {
      const match = codeEl.className.match(/language-([a-zA-Z0-9_-]+)/);
      if (match) lang = match[1];
    }

    const langSpan = document.createElement("span");
    langSpan.textContent = lang;

    const copyBtn = document.createElement("button");
    copyBtn.className = "code-copy-btn";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(codeEl ? codeEl.textContent : pre.textContent).then(() => {
        copyBtn.textContent = "Copied!";
        setTimeout(() => { copyBtn.textContent = "Copy"; }, 2000);
      });
    });

    header.append(langSpan, copyBtn);
    pre.parentNode.insertBefore(wrapper, pre);
    wrapper.append(header, pre);
  });
}

/**
 * Returns a styled file format pill (PDF, DOC, XLS, IMG, CODE) for file lists.
 */
function getFileBadge(filename) {
  const ext = filename.split(".").pop().toLowerCase();
  let badgeClass = "badge-text";
  let label = ext.toUpperCase().slice(0, 4);

  if (["pdf"].includes(ext)) { badgeClass = "badge-pdf"; label = "PDF"; }
  else if (["doc", "docx"].includes(ext)) { badgeClass = "badge-doc"; label = "DOC"; }
  else if (["xls", "xlsx", "csv", "tsv", "ods"].includes(ext)) { badgeClass = "badge-sheet"; label = "XLS"; }
  else if (["png", "jpg", "jpeg", "webp", "tiff"].includes(ext)) { badgeClass = "badge-image"; label = "IMG"; }
  else if (["py", "js", "ts", "json", "html", "css", "sql"].includes(ext)) { badgeClass = "badge-code"; label = ext.toUpperCase().slice(0, 3); }

  return `<div class="file-badge ${badgeClass}">${label}</div>`;
}

// ==============================================================================
// 3. System Health Check (Ollama & ChromaDB status)
// ==============================================================================

async function loadHealth() {
  const dots = document.querySelectorAll(".status-dot");
  const statusText = $("connectionText");
  const healthLabel = $("healthOllamaLabel");

  try {
    const response = await fetch("/api/health");
    const data = await response.json();

    if (data.ollama_connected) {
      dots.forEach((dot) => {
        dot.className = "status-dot online";
      });
      if (statusText) statusText.textContent = "Ollama Connected";
      if (healthLabel) healthLabel.textContent = "Ollama Online";
    } else {
      dots.forEach((dot) => {
        dot.className = "status-dot offline";
      });
      if (statusText) statusText.textContent = "Ollama Offline";
      if (healthLabel) healthLabel.textContent = "Ollama Offline";
    }

    if ($("healthChatModel")) $("healthChatModel").textContent = data.chat_model || "llama3.2";
    if ($("healthEmbedModel")) $("healthEmbedModel").textContent = data.embedding_model || "nomic-embed-text";
    const chunks = data.chroma_document_chunks || 0;
    if ($("healthChunks")) $("healthChunks").textContent = `${chunks} chunks`;
  } catch (err) {
    dots.forEach((dot) => {
      dot.className = "status-dot offline";
    });
    if (statusText) statusText.textContent = "Service Unreachable";
    if (healthLabel) healthLabel.textContent = "Service Offline";
  }
}

// ==============================================================================
// 4. File Management & Upload Handlers
// ==============================================================================

async function loadFiles() {
  const list = $("fileList");
  list.innerHTML = `<p class="file-size">Refreshing files…</p>`;

  try {
    const response = await fetch("/api/files?workspace=" + encodeURIComponent(getWorkspace()));
    const data = await response.json();

    list.replaceChildren();
    const files = data.files || [];
    $("fileBadgeCount").textContent = files.length;
    if ($("tabFileCount")) $("tabFileCount").textContent = files.length;

    if (!files.length) {
      list.innerHTML = `<p class="file-size" style="padding:10px 0;">No indexed files in this workspace.</p>`;
      return;
    }

    for (const file of files) {
      const item = document.createElement("div");
      item.className = "file-item";

      item.innerHTML = `
        <div class="file-info">
          ${getFileBadge(file.filename)}
          <div class="file-meta">
            <span class="file-name" title="${escapeHtml(file.filename)}">${escapeHtml(file.filename)}</span>
            <span class="file-size">${formatBytes(file.size_bytes)}</span>
          </div>
        </div>
        <button class="btn-delete" title="Delete file and vectors">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"/>
          </svg>
        </button>
      `;

      item.querySelector(".btn-delete").addEventListener("click", async () => {
        if (!confirm(`Delete ${file.filename} and remove from vector index?`)) return;
        try {
          const res = await fetch("/api/files", {
            method: "DELETE",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ file_id: file.file_id, workspace: getWorkspace() })
          });
          if (!res.ok) throw new Error("Delete failed");
          await loadFiles();
          await loadHealth();
          loadWorkspaces(getWorkspace()).catch(() => {});
        } catch (e) {
          alert(e.message);
        }
      });

      list.appendChild(item);
    }
  } catch (err) {
    list.innerHTML = `<p class="file-size" style="color:#ef4444;">Failed to load files: ${err.message}</p>`;
  }
}

// Drag & drop upload events
const dropZone = $("dropZone");
const fileInput = $("fileInput");
const selectedCount = $("selectedCount");

dropZone.addEventListener("click", () => fileInput.click());

["dragenter", "dragover"].forEach(evt => {
  dropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
  });
});

["dragleave", "drop"].forEach(evt => {
  dropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
  });
});

dropZone.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    updateSelectedCount();
  }
});

fileInput.addEventListener("change", updateSelectedCount);

function updateSelectedCount() {
  const count = fileInput.files.length;
  selectedCount.textContent = count > 0 ? `${count} file(s) selected` : "";
}

$("uploadBtn").addEventListener("click", async () => {
  const files = fileInput.files;
  if (!files.length) {
    $("uploadStatus").textContent = "Please select or drop files first.";
    return;
  }

  const form = new FormData();
  form.append("workspace", getWorkspace());
  for (const file of files) {
    form.append("files", file);
  }

  $("uploadBtn").disabled = true;
  $("uploadStatus").textContent = "Extracting text and building vector embeddings…";

  try {
    const response = await fetch("/api/upload", { method: "POST", body: form });
    const data = await response.json();

    if (!response.ok) throw new Error(data.detail || "Upload failed");

    const summary = [];
    (data.uploaded || []).forEach(f => summary.push(`✓ Indexed: ${f.filename} (${f.chunks_indexed} chunks)`));
    (data.errors || []).forEach(f => summary.push(`✗ Failed: ${f.filename} (${f.error})`));

    $("uploadStatus").textContent = summary.join("\n") || "Upload finished.";
    fileInput.value = "";
    selectedCount.textContent = "";

    await loadFiles();
    await loadHealth();
    loadWorkspaces(getWorkspace()).catch(() => {});
  } catch (err) {
    $("uploadStatus").textContent = "Upload error: " + err.message;
  } finally {
    $("uploadBtn").disabled = false;
  }
});

// ==============================================================================
// 5. Conversation History Threads & New Chat Management
// ==============================================================================

/**
 * Loads and displays all saved conversation threads for the current workspace.
 * Requires user authentication; shows privacy locked state when unauthenticated.
 */
async function loadConversations() {
  const list = $("conversationList");
  if (!list) return;

  if (!currentUser) {
    showChatLockedState();
    return;
  }
  showChatUnlockedState();

  try {
    const workspace = encodeURIComponent(getWorkspace());
    const res = await authFetch(`/api/chat/conversations?workspace=${workspace}`);
    if (res.status === 401) {
      currentUser = null;
      setAuthToken(null);
      updateAuthUI();
      return;
    }
    const data = await res.json();
    const conversations = data.conversations || [];
    const activeId = getConversationId();

    if ($("tabChatCount")) $("tabChatCount").textContent = conversations.length;

    list.replaceChildren();

    if (!conversations.length) {
      list.innerHTML = `<p class="file-size" style="padding: 6px 0;">No saved conversations yet.</p>`;
      return;
    }

    for (const conv of conversations) {
      const item = document.createElement("div");
      const isActive = conv.conversation_id === activeId;
      item.className = `conversation-item ${isActive ? "active" : ""}`;
      item.dataset.convId = conv.conversation_id;

      item.innerHTML = `
        <div class="conversation-info">
          <div class="conversation-icon">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
          </div>
          <div class="conversation-meta">
            <span class="conversation-title" title="${escapeHtml(conv.title)}">${escapeHtml(conv.title)}</span>
            <div class="conversation-sub">
              <span class="conv-badge">${conv.message_count} msgs</span>
            </div>
          </div>
        </div>
        <button class="btn-delete" title="Delete conversation thread">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"/>
          </svg>
        </button>
      `;

      // Click to switch active conversation
      item.addEventListener("click", (e) => {
        if (e.target.closest(".btn-delete")) return;
        if (conv.conversation_id === getConversationId()) return;
        setConversationId(conv.conversation_id);
        loadChatHistory(conv.conversation_id);
        loadConversations();
      });

      // Click to delete conversation
      item.querySelector(".btn-delete").addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete conversation "${conv.title}"?`)) return;
        try {
          const delRes = await authFetch("/api/chat/conversations", {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              workspace: getWorkspace(),
              conversation_id: conv.conversation_id,
            }),
          });
          if (!delRes.ok) throw new Error("Delete failed");

          if (conv.conversation_id === getConversationId()) {
            startNewConversation();
          }
          await loadConversations();
          loadWorkspaces(getWorkspace()).catch(() => {});
        } catch (err) {
          alert("Could not delete conversation: " + err.message);
        }
      });

      list.appendChild(item);
    }
    applyChatFilter();
  } catch (err) {
    list.innerHTML = `<p class="file-size" style="color:#ef4444;">Failed to load conversations: ${err.message}</p>`;
  }
}

function applyChatFilter() {
  const query = ($("chatSearchInput")?.value || "").toLowerCase().trim();
  const items = document.querySelectorAll("#conversationList .conversation-item");
  items.forEach((item) => {
    const title = item.querySelector(".conversation-title")?.textContent.toLowerCase() || "";
    item.style.display = title.includes(query) ? "flex" : "none";
  });
}

// ==============================================================================
// 6. Chat & Streaming Token Engine (Server-Sent Events)
// ==============================================================================

function scrollToBottom() {
  const container = $("messagesContainer");
  if (container) {
    container.scrollTop = container.scrollHeight;
  }
}

/**
 * Appends a user or assistant message row into the chat window.
 */
function appendMessageRow(role, contentHtml = "") {
  const hero = $("welcomeHero");
  if (hero) hero.remove();

  const container = $("messagesContainer");
  const row = document.createElement("div");
  row.className = `msg-row ${role}`;

  const avatar = document.createElement("div");
  avatar.className = `avatar ${role}`;
  avatar.innerHTML = role === "user" 
    ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`
    : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>`;

  const bubble = document.createElement("div");
  bubble.className = "msg-bubble";
  bubble.innerHTML = contentHtml;

  if (role === "user") {
    const wrapper = document.createElement("div");
    wrapper.className = "user-content-wrapper";
    wrapper.appendChild(bubble);
    row.append(avatar, wrapper);
  } else {
    row.append(avatar, bubble);
  }

  container.appendChild(row);
  scrollToBottom();
  return bubble;
}

// Track last failed prompt for one-click resend
let lastFailedPrompt = null;
let lastFailedUserRow = null;
let lastFailedAssistantRow = null;

function setFailedPrompt(promptText, userRow, assistantRow) {
  lastFailedPrompt = promptText;
  lastFailedUserRow = userRow;
  lastFailedAssistantRow = assistantRow;

  const resendBtn = $("resendBtn");
  if (resendBtn) {
    resendBtn.style.display = "inline-flex";
  }
}

function clearFailedPrompt() {
  lastFailedPrompt = null;
  lastFailedUserRow = null;
  lastFailedAssistantRow = null;

  const resendBtn = $("resendBtn");
  if (resendBtn) {
    resendBtn.style.display = "none";
  }
}

function resendPrompt(promptText, userRow = null, assistantRow = null) {
  if (activeAbortController) return;

  const targetUserRow = userRow || lastFailedUserRow;
  const targetAssistantRow = assistantRow || lastFailedAssistantRow;

  if (targetUserRow && targetUserRow.parentNode) {
    targetUserRow.remove();
  }
  if (targetAssistantRow && targetAssistantRow.parentNode) {
    targetAssistantRow.remove();
  }

  clearFailedPrompt();
  sendQuestion(promptText);
}

/**
 * Sends a question to the backend and processes the incoming token stream.
 */
async function sendQuestion(customMessage = null) {
  if (activeAbortController) return;

  const message = customMessage || $("question").value.trim();
  if (!message) return;

  clearFailedPrompt();

  const userBubble = appendMessageRow("user", escapeHtml(message));
  const userRow = userBubble.closest(".msg-row");

  if (!customMessage) {
    $("question").value = "";
    $("question").style.height = "auto";
  }

  const sendBtn = $("sendBtn");
  const stopBtn = $("stopBtn");
  sendBtn.style.display = "none";
  stopBtn.style.display = "inline-flex";

  $("chatStatusText").textContent = "Thinking…";

  // Display initial 3-dot thinking loader
  const assistantBubble = appendMessageRow("assistant", `
    <div class="ai-loader">
      <div class="ai-dots">
        <span></span><span></span><span></span>
      </div>
      <span class="ai-loader-status">Thinking…</span>
    </div>
  `);
  const assistantRow = assistantBubble.closest(".msg-row");

  activeAbortController = new AbortController();
  let accumulated = "";
  let sources = [];
  let retrievedChunks = 0;

  try {
    const response = await authFetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        message,
        workspace: getWorkspace(),
        conversation_id: getConversationId(),
        stream: true
      }),
      signal: activeAbortController.signal
    });

    if (!response.ok) {
      const errText = await response.text();
      let detail = errText;
      try { detail = JSON.parse(errText).detail || errText; } catch(e){}
      throw new Error("HTTP " + response.status + ": " + detail);
    }

    // Read the Server-Sent Events stream chunk by chunk
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";

      for (const part of parts) {
        if (!part.trim()) continue;
        const lines = part.split("\n");
        let event = "message";
        let data = "";

        lines.forEach(l => {
          if (l.startsWith("event: ")) event = l.slice(7).trim();
          else if (l.startsWith("data: ")) data = l.slice(6);
        });

        if (event === "meta") {
          try {
            const meta = JSON.parse(data);
            sources = meta.sources || [];
            retrievedChunks = meta.retrieved_chunks || 0;
            const statusEl = assistantBubble.querySelector(".ai-loader-status");
            if (retrievedChunks > 0) {
              $("chatStatusText").textContent = `Referencing ${retrievedChunks} ${retrievedChunks === 1 ? 'passage' : 'passages'}…`;
              if (statusEl) statusEl.textContent = `Referencing documents (${retrievedChunks})…`;
            } else {
              $("chatStatusText").textContent = "Thinking…";
              if (statusEl) statusEl.textContent = "Thinking…";
            }
          } catch(e){}
        } else if (event === "delta") {
          try {
            const chunk = JSON.parse(data);
            if (chunk.token) {
              accumulated += chunk.token;
              assistantBubble.innerHTML = renderMarkdownWithCursor(accumulated);
              scrollToBottom();
            }
          } catch(e){}
        } else if (event === "done") {
          try {
            const donePayload = JSON.parse(data);
            if (donePayload.answer) accumulated = donePayload.answer;
          } catch(e){}
        } else if (event === "error") {
          try { throw new Error(JSON.parse(data).detail || "Model stream error"); }
          catch(e) { throw new Error(data); }
        }
      }
    }

    // Finalize assistant response (remove cursor, format code, attach source citations)
    assistantBubble.innerHTML = renderMarkdown(accumulated || "(No response generated)");
    postProcessAssistantBubble(assistantBubble);

    if (sources.length) {
      const sourcesDiv = document.createElement("div");
      sourcesDiv.className = "sources-row";
      sourcesDiv.innerHTML = `<span>Sources:</span> ` + sources.map(s => `
        <span class="source-tag">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6"/></svg>
          ${escapeHtml(s)}
        </span>
      `).join("");
      assistantBubble.appendChild(sourcesDiv);
    }

    // Add Copy Response Button
    const actionsDiv = document.createElement("div");
    actionsDiv.className = "bubble-actions";
    const copyBtn = document.createElement("button");
    copyBtn.className = "btn-copy-bubble";
    copyBtn.textContent = "Copy response";
    copyBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(accumulated).then(() => {
        copyBtn.textContent = "Copied!";
        setTimeout(() => { copyBtn.textContent = "Copy response"; }, 2000);
      });
    });
    actionsDiv.appendChild(copyBtn);
    assistantBubble.appendChild(actionsDiv);

    if (sources.length > 0) {
      $("chatStatusText").textContent = `Cited ${sources.length} ${sources.length === 1 ? 'source' : 'sources'} (${retrievedChunks} passages).`;
    } else {
      $("chatStatusText").textContent = "Ready";
    }
    loadHealth().catch(() => {});

  } catch (err) {
    if (err.name === "AbortError") {
      assistantBubble.innerHTML = renderMarkdown(accumulated || "Generation stopped.");
      postProcessAssistantBubble(assistantBubble);
      $("chatStatusText").textContent = "Generation cancelled by user.";
    } else {
      $("chatStatusText").textContent = "Request failed.";

      // 1. Mark user message row as failed and attach Resend button near user prompt
      if (userRow) {
        userRow.classList.add("has-error");
        const userWrapper = userBubble.parentElement;
        if (userWrapper) {
          let userActions = userWrapper.querySelector(".user-prompt-actions");
          if (!userActions) {
            userActions = document.createElement("div");
            userActions.className = "user-prompt-actions";
            userWrapper.appendChild(userActions);
          }
          userActions.innerHTML = `
            <span class="prompt-failed-badge">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
              Failed
            </span>
            <button type="button" class="btn-resend-prompt" title="Resend this prompt">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 4v6h6M23 20v-6h-6"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"/></svg>
              <span>Resend</span>
            </button>
            <button type="button" class="btn-edit-prompt" title="Edit prompt in input box">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
              <span>Edit</span>
            </button>
          `;

          const promptResendBtn = userActions.querySelector(".btn-resend-prompt");
          if (promptResendBtn) {
            promptResendBtn.addEventListener("click", () => {
              resendPrompt(message, userRow, assistantRow);
            });
          }

          const promptEditBtn = userActions.querySelector(".btn-edit-prompt");
          if (promptEditBtn) {
            promptEditBtn.addEventListener("click", () => {
              const input = $("question");
              input.value = message;
              input.focus();
              input.style.height = "auto";
              input.style.height = Math.min(input.scrollHeight, 180) + "px";
              if (userRow && userRow.parentNode) userRow.remove();
              if (assistantRow && assistantRow.parentNode) assistantRow.remove();
              clearFailedPrompt();
            });
          }
        }
      }

      // 2. Format assistant bubble as error card with Resend button
      assistantBubble.className = "msg-bubble error-bubble";
      assistantBubble.innerHTML = `
        <div class="assistant-error-card">
          <div class="error-msg-header">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2">
              <circle cx="12" cy="12" r="10"/>
              <line x1="12" y1="8" x2="12" y2="12"/>
              <line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
            <span class="error-title">Error: ${escapeHtml(err.message)}</span>
          </div>
          <div class="error-msg-actions">
            <button type="button" class="btn-resend-prompt error-retry-btn" title="Resend prompt">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M1 4v6h6M23 20v-6h-6"/>
                <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"/>
              </svg>
              <span>Resend Prompt</span>
            </button>
          </div>
        </div>
      `;

      const errorRetryBtn = assistantBubble.querySelector(".error-retry-btn");
      if (errorRetryBtn) {
        errorRetryBtn.addEventListener("click", () => {
          resendPrompt(message, userRow, assistantRow);
        });
      }

      // 3. Register failed prompt for bottom action bar
      setFailedPrompt(message, userRow, assistantRow);
    }
  } finally {
    activeAbortController = null;
    sendBtn.style.display = "inline-flex";
    stopBtn.style.display = "none";
    scrollToBottom();
    // Refresh conversation threads in sidebar to reflect new message and title
    loadConversations();
  }
}

// ==============================================================================
// 7. SQLite Persistent Chat History Loading
// ==============================================================================

async function loadChatHistory(explicitConvId = null) {
  if (!currentUser) {
    // When not logged in, chat history is locked; show welcome hero
    const container = $("messagesContainer");
    if (container && !container.querySelector(".msg-row")) {
      renderWelcomeHero(container);
    }
    return;
  }

  try {
    const workspace = encodeURIComponent(getWorkspace());
    const convId = encodeURIComponent(explicitConvId || getConversationId());
    const response = await authFetch(`/api/chat/history?workspace=${workspace}&conversation_id=${convId}`);
    if (response.status === 401) {
      return;
    }
    const data = await response.json();
    const messages = data.messages || [];

    const container = $("messagesContainer");
    container.replaceChildren();
    clearFailedPrompt();

    if (messages.length > 0) {
      for (const msg of messages) {
        const row = document.createElement("div");
        row.className = `msg-row ${msg.role}`;

        const avatar = document.createElement("div");
        avatar.className = `avatar ${msg.role}`;
        avatar.innerHTML = msg.role === "user" 
          ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`
          : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>`;

        const bubble = document.createElement("div");
        bubble.className = "msg-bubble";

        if (msg.role === "assistant") {
          bubble.innerHTML = renderMarkdown(msg.content);
          postProcessAssistantBubble(bubble);

          if (msg.sources && msg.sources.length) {
            const sourcesDiv = document.createElement("div");
            sourcesDiv.className = "sources-row";
            sourcesDiv.innerHTML = `<span>Sources:</span> ` + msg.sources.map(s => `
              <span class="source-tag">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6"/></svg>
                ${escapeHtml(s)}
              </span>
            `).join("");
            bubble.appendChild(sourcesDiv);
          }
          row.append(avatar, bubble);
        } else {
          bubble.textContent = msg.content;
          const wrapper = document.createElement("div");
          wrapper.className = "user-content-wrapper";
          wrapper.appendChild(bubble);
          row.append(avatar, wrapper);
        }

        container.appendChild(row);
      }
      scrollToBottom();
    } else {
      // Empty conversation - display initial welcome hero
      renderWelcomeHero(container);
    }
  } catch (err) {
    console.warn("Could not load history:", err);
  }
}

function renderWelcomeHero(container) {
  if (!container) return;
  container.innerHTML = `
    <div id="welcomeHero" class="welcome-hero">
      <div class="welcome-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
        </svg>
      </div>
      <h2>How can I help you today?</h2>
      <p>I can analyze, summarize, and cross-reference documents stored in this workspace with accurate source citations.</p>

      <div class="starter-chips">
        <button class="chip" data-prompt="Summarize the key objectives and conclusions from the uploaded documents.">
          📑 Summarize key objectives
        </button>
        <button class="chip" data-prompt="Extract all dates, milestones, and project deadlines.">
          🗓️ Find deadlines & milestones
        </button>
        <button class="chip" data-prompt="Who are the key people mentioned and what are their designated roles?">
          👤 Identify team & roles
        </button>
        <button class="chip" data-prompt="List all financial numbers, budgets, and cost metrics mentioned.">
          💰 Extract budget & financials
        </button>
      </div>
    </div>
  `;
}

// ==============================================================================
// 8. Event Listeners & Startup
// ==============================================================================

// Textarea auto-resize and Enter key submission
const questionInput = $("question");
questionInput.addEventListener("input", function() {
  this.style.height = "auto";
  this.style.height = Math.min(this.scrollHeight, 180) + "px";
});

questionInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendQuestion();
  }
});

// Button event handlers
$("sendBtn").addEventListener("click", () => sendQuestion());
if ($("resendBtn")) {
  $("resendBtn").addEventListener("click", () => {
    if (lastFailedPrompt) {
      resendPrompt(lastFailedPrompt, lastFailedUserRow, lastFailedAssistantRow);
    }
  });
}
$("refreshBtn").addEventListener("click", async () => {
  await loadWorkspaces(getWorkspace());
  await Promise.all([
    loadFiles(),
    loadHealth(),
    loadConversations(),
  ]);
});

// New Conversation buttons
if ($("sidebarNewChatBtn")) {
  $("sidebarNewChatBtn").addEventListener("click", () => startNewConversation());
}
if ($("chatNewChatBtn")) {
  $("chatNewChatBtn").addEventListener("click", () => startNewConversation());
}

$("stopBtn").addEventListener("click", () => {
  if (activeAbortController) {
    activeAbortController.abort();
  }
});

// Workspace dropdown selection
if ($("workspaceSelect")) {
  $("workspaceSelect").addEventListener("change", (e) => {
    const newWs = e.target.value;
    setWorkspace(newWs);
    sessionStorage.removeItem("coworkerConversationId:" + newWs);
    startNewConversation();
    loadFiles();
    loadHealth();
    loadConversations();
    loadChatHistory();
    $("chatStatusText").textContent = `Switched to workspace '${newWs}'.`;
  });
}

// Create Workspace Modal Handling
const newWsDialog = $("newWorkspaceDialog");
const newWsForm = $("newWorkspaceForm");
const newWsInput = $("newWorkspaceInput");

if ($("newWorkspaceBtn")) {
  $("newWorkspaceBtn").addEventListener("click", () => {
    if (newWsDialog) {
      newWsInput.value = "";
      newWsDialog.showModal();
      setTimeout(() => newWsInput.focus(), 60);
    }
  });
}

if ($("closeWsDialogBtn")) {
  $("closeWsDialogBtn").addEventListener("click", () => newWsDialog?.close());
}

if ($("cancelWsDialogBtn")) {
  $("cancelWsDialogBtn").addEventListener("click", () => newWsDialog?.close());
}

if (newWsForm) {
  newWsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const rawName = newWsInput.value.trim();
    if (!rawName) return;

    const submitBtn = $("submitWsDialogBtn");
    try {
      if (submitBtn) submitBtn.disabled = true;

      const res = await fetch("/api/workspaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: rawName }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to create workspace");

      newWsDialog.close();
      const newWs = data.workspace;
      setWorkspace(newWs);
      await loadWorkspaces(newWs);
      startNewConversation();
      await Promise.all([
        loadFiles(),
        loadConversations(),
      ]);
      $("chatStatusText").textContent = `Workspace '${newWs}' created.`;
    } catch (err) {
      alert("Error creating workspace: " + err.message);
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });
}

// Help & Beginner's Guide PDF Modal Popup
const helpDialog = $("helpDialog");
const pdfIframe = $("pdfIframe");
const pdfLoader = $("pdfLoader");

function openHelpModal() {
  if (!helpDialog) return;

  // Show loader, hide iframe initially
  if (pdfLoader) {
    pdfLoader.style.display = "flex";
    pdfLoader.style.opacity = "1";
  }
  if (pdfIframe) {
    pdfIframe.style.display = "none";

    const targetSrc = "/docs/beginners_guide.pdf";
    if (pdfIframe.getAttribute("src") !== targetSrc) {
      pdfIframe.src = targetSrc;
    }

    const onIframeLoaded = () => {
      if (pdfLoader) {
        pdfLoader.style.opacity = "0";
        setTimeout(() => {
          if (pdfLoader) pdfLoader.style.display = "none";
        }, 200);
      }
      pdfIframe.style.display = "block";
    };

    pdfIframe.onload = onIframeLoaded;
    // Safety fallback: Reveal viewer after 1.2s even if onload is suppressed by browser PDF plugin
    setTimeout(onIframeLoaded, 1200);
  }

  helpDialog.showModal();
}

if ($("helpBtn")) {
  $("helpBtn").addEventListener("click", openHelpModal);
}

if ($("closeHelpDialogBtn")) {
  $("closeHelpDialogBtn").addEventListener("click", () => helpDialog?.close());
}

if (helpDialog) {
  // Close when clicking directly on dialog backdrop
  helpDialog.addEventListener("click", (e) => {
    if (e.target === helpDialog) {
      helpDialog.close();
    }
  });
}

// Delete Workspace
if ($("deleteWorkspaceBtn")) {
  $("deleteWorkspaceBtn").addEventListener("click", async () => {
    const activeWs = getWorkspace();
    if (activeWs === "default") {
      alert("The 'default' workspace cannot be deleted.");
      return;
    }

    const confirmed = confirm(
      `Are you sure you want to permanently delete workspace '${activeWs}'?\n\n` +
      `This will permanently remove:\n` +
      `• All uploaded documents in '${activeWs}'\n` +
      `• All vector embeddings in ChromaDB\n` +
      `• All chat conversations and messages in SQLite\n\n` +
      `This action cannot be undone.`
    );
    if (!confirmed) return;

    try {
      $("deleteWorkspaceBtn").disabled = true;
      const res = await fetch("/api/workspaces", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace: activeWs }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to delete workspace");

      setWorkspace("default");
      await loadWorkspaces("default");
      startNewConversation();
      await Promise.all([
        loadFiles(),
        loadConversations(),
        loadChatHistory(),
      ]);
      loadHealth().catch(() => {});
      $("chatStatusText").textContent = `Workspace '${activeWs}' deleted.`;
    } catch (err) {
      alert("Error deleting workspace: " + err.message);
    } finally {
      $("deleteWorkspaceBtn").disabled = false;
    }
  });
}

// Clear conversation memory
$("clearChatBtn").addEventListener("click", async () => {
  const convId = getConversationId();
  if (!confirm("Clear memory for this current conversation?")) return;

  try {
    await authFetch("/api/chat/reset", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ workspace: getWorkspace(), conversation_id: convId })
    });
  } catch(e){}

  startNewConversation();
  $("chatStatusText").textContent = "Conversation memory cleared.";
});

// Sidebar Tab Switching (Chats vs Documents)
function switchSidebarTab(tabName) {
  const isChats = tabName === "chats";
  if ($("tabChatsBtn")) $("tabChatsBtn").classList.toggle("active", isChats);
  if ($("tabFilesBtn")) $("tabFilesBtn").classList.toggle("active", !isChats);
  if ($("tabContentChats")) $("tabContentChats").classList.toggle("active", isChats);
  if ($("tabContentFiles")) $("tabContentFiles").classList.toggle("active", !isChats);
  if (isChats) {
    if (!currentUser) {
      showChatLockedState();
    } else {
      showChatUnlockedState();
    }
  }
  localStorage.setItem("coworker_sidebar_tab", tabName);
}

if ($("tabChatsBtn")) {
  $("tabChatsBtn").addEventListener("click", () => switchSidebarTab("chats"));
}
if ($("tabFilesBtn")) {
  $("tabFilesBtn").addEventListener("click", () => switchSidebarTab("files"));
}

// Search Filter on Conversation Titles
if ($("chatSearchInput")) {
  $("chatSearchInput").addEventListener("input", applyChatFilter);
}

// Workspace Card Collapse / Expand Toggle
const workspaceCard = $("workspaceCard");
const workspaceToggleBtn = $("toggleWorkspaceBtn");
const workspaceCardHeader = $("workspaceCardHeader");

function toggleWorkspaceCard(forceCollapsed) {
  if (!workspaceCard) return;
  const isCurrentlyCollapsed = workspaceCard.classList.contains("collapsed");
  const willBeCollapsed = (typeof forceCollapsed === "boolean")
    ? forceCollapsed
    : !isCurrentlyCollapsed;

  workspaceCard.classList.toggle("collapsed", willBeCollapsed);

  if (workspaceToggleBtn) {
    workspaceToggleBtn.setAttribute("aria-expanded", (!willBeCollapsed).toString());
    workspaceToggleBtn.title = willBeCollapsed ? "Expand workspace" : "Collapse workspace";
    workspaceToggleBtn.setAttribute("aria-label", willBeCollapsed ? "Expand workspace" : "Collapse workspace");
  }
  if (workspaceCardHeader) {
    workspaceCardHeader.setAttribute("aria-expanded", (!willBeCollapsed).toString());
  }
}

if (workspaceToggleBtn) {
  workspaceToggleBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleWorkspaceCard();
  });
}

if (workspaceCardHeader) {
  workspaceCardHeader.addEventListener("click", (e) => {
    if (e.target.closest(".workspace-header-actions")) {
      return;
    }
    toggleWorkspaceCard();
  });

  workspaceCardHeader.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && !e.target.closest(".workspace-header-actions")) {
      e.preventDefault();
      toggleWorkspaceCard();
    }
  });
}

// Sidebar Collapse / Expand Toggle
function updateSidebarUI(isCollapsed) {
  const mainEl = $("appMain");
  const sidebarEl = $("appSidebar");
  const collapseBtn = $("sidebarCollapseBtn");
  const expandBtn = $("sidebarExpandBtn");
  const backdrop = $("sidebarBackdrop");

  if (mainEl) {
    mainEl.classList.toggle("sidebar-collapsed", isCollapsed);
  }
  if (sidebarEl) {
    sidebarEl.classList.toggle("collapsed", isCollapsed);
    sidebarEl.setAttribute("aria-hidden", isCollapsed ? "true" : "false");
    if (isCollapsed) {
      sidebarEl.classList.remove("mobile-open");
      if (backdrop) backdrop.classList.remove("active");
    }
  }

  if (collapseBtn) {
    collapseBtn.setAttribute("aria-expanded", isCollapsed ? "false" : "true");
  }
  if (expandBtn) {
    expandBtn.setAttribute("aria-expanded", isCollapsed ? "false" : "true");
  }
}

function toggleSidebar(forceState) {
  const mainEl = $("appMain");
  const sidebarEl = $("appSidebar");
  if (!mainEl && !sidebarEl) return;

  const isMobile = window.innerWidth <= 860;
  if (isMobile) {
    const backdrop = $("sidebarBackdrop");
    const isOpen = sidebarEl ? sidebarEl.classList.contains("mobile-open") : false;
    const shouldOpen = typeof forceState === "boolean" ? !forceState : !isOpen;
    if (sidebarEl) sidebarEl.classList.toggle("mobile-open", shouldOpen);
    if (backdrop) backdrop.classList.toggle("active", shouldOpen);
    return;
  }

  const currentlyCollapsed = mainEl ? mainEl.classList.contains("sidebar-collapsed") : false;
  const nextCollapsed = typeof forceState === "boolean" ? forceState : !currentlyCollapsed;
  updateSidebarUI(nextCollapsed);
  localStorage.setItem("coworker_sidebar_collapsed", nextCollapsed ? "1" : "0");
}

if ($("sidebarCollapseBtn")) {
  $("sidebarCollapseBtn").addEventListener("click", () => {
    if (window.innerWidth <= 860) {
      $("appSidebar")?.classList.remove("mobile-open");
      $("sidebarBackdrop")?.classList.remove("active");
    } else {
      toggleSidebar(true);
    }
  });
}

if ($("sidebarExpandBtn")) {
  $("sidebarExpandBtn").addEventListener("click", () => toggleSidebar(false));
}

if ($("sidebarBackdrop")) {
  $("sidebarBackdrop").addEventListener("click", () => {
    $("appSidebar")?.classList.remove("mobile-open");
    $("sidebarBackdrop")?.classList.remove("active");
  });
}

// Keyboard Shortcut: Ctrl+B or Cmd+B to toggle sidebar, Esc to close mobile drawer
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") {
    e.preventDefault();
    toggleSidebar();
  } else if (e.key === "Escape") {
    const sidebarEl = $("appSidebar");
    const backdrop = $("sidebarBackdrop");
    if (sidebarEl && sidebarEl.classList.contains("mobile-open")) {
      sidebarEl.classList.remove("mobile-open");
      if (backdrop) backdrop.classList.remove("active");
    }
  }
});

// Clean up mobile drawer when resizing back to desktop
window.addEventListener("resize", () => {
  if (window.innerWidth > 860) {
    const sidebarEl = $("appSidebar");
    const backdrop = $("sidebarBackdrop");
    if (sidebarEl) sidebarEl.classList.remove("mobile-open");
    if (backdrop) backdrop.classList.remove("active");
  }
});

// Restore saved sidebar tab & collapsed preference
const savedTab = localStorage.getItem("coworker_sidebar_tab") || "chats";
switchSidebarTab(savedTab);

// Default to collapsed unless explicitly expanded by user ("0")
const savedCollapsed = localStorage.getItem("coworker_sidebar_collapsed");
const shouldBeCollapsed = savedCollapsed !== "0";
if (window.innerWidth > 860) {
  updateSidebarUI(shouldBeCollapsed);
} else {
  updateSidebarUI(false);
}

// ==============================================================================
// 9. Auth Dialog & Session Interaction Handlers
// ==============================================================================

const authDialog = $("authDialog");
const authForm = $("authForm");
const authUsernameInput = $("authUsernameInput");
const authPasswordInput = $("authPasswordInput");
const authErrorMessage = $("authErrorMessage");
const authSubmitBtn = $("submitAuthDialogBtn");
const authSubmitText = $("authSubmitText");
const authModeLoginBtn = $("authModeLoginBtn");
const authModeRegisterBtn = $("authModeRegisterBtn");

function setAuthMode(mode) {
  authMode = mode;
  if (authErrorMessage) {
    authErrorMessage.style.display = "none";
    authErrorMessage.textContent = "";
  }
  if (mode === "register") {
    if (authModeLoginBtn) authModeLoginBtn.classList.remove("active");
    if (authModeRegisterBtn) authModeRegisterBtn.classList.add("active");
    if ($("authModalTitle")) $("authModalTitle").textContent = "Create Account";
    if ($("authModalDesc")) $("authModalDesc").textContent = "Create a local account to save, search, and secure your conversation history.";
    if (authSubmitText) authSubmitText.textContent = "Create Account";
    if ($("authUsernameHint")) $("authUsernameHint").style.display = "block";
    if ($("authPasswordHint")) $("authPasswordHint").style.display = "block";
  } else {
    if (authModeLoginBtn) authModeLoginBtn.classList.add("active");
    if (authModeRegisterBtn) authModeRegisterBtn.classList.remove("active");
    if ($("authModalTitle")) $("authModalTitle").textContent = "Sign In";
    if ($("authModalDesc")) $("authModalDesc").textContent = "Sign in to access, search, and manage your private chat history threads.";
    if (authSubmitText) authSubmitText.textContent = "Sign In";
    if ($("authUsernameHint")) $("authUsernameHint").style.display = "none";
    if ($("authPasswordHint")) $("authPasswordHint").style.display = "none";
  }
}

function openAuthModal(defaultMode = "login") {
  if (!authDialog) return;
  setAuthMode(defaultMode);
  if (authUsernameInput) authUsernameInput.value = "";
  if (authPasswordInput) authPasswordInput.value = "";
  if (authErrorMessage) {
    authErrorMessage.style.display = "none";
    authErrorMessage.textContent = "";
  }
  authDialog.showModal();
  setTimeout(() => {
    if (authUsernameInput) authUsernameInput.focus();
  }, 100);
}

function closeAuthModal() {
  if (authDialog) authDialog.close();
}

if ($("headerLoginBtn")) {
  $("headerLoginBtn").addEventListener("click", () => openAuthModal("login"));
}

if ($("chatsUnlockBtn")) {
  $("chatsUnlockBtn").addEventListener("click", () => openAuthModal("login"));
}

if ($("closeAuthDialogBtn")) {
  $("closeAuthDialogBtn").addEventListener("click", closeAuthModal);
}

if ($("cancelAuthDialogBtn")) {
  $("cancelAuthDialogBtn").addEventListener("click", closeAuthModal);
}

if (authDialog) {
  authDialog.addEventListener("click", (e) => {
    if (e.target === authDialog) closeAuthModal();
  });
}

if (authModeLoginBtn) {
  authModeLoginBtn.addEventListener("click", () => setAuthMode("login"));
}

if (authModeRegisterBtn) {
  authModeRegisterBtn.addEventListener("click", () => setAuthMode("register"));
}

if (authForm) {
  authForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = (authUsernameInput?.value || "").trim();
    const password = authPasswordInput?.value || "";

    if (!username || !password) return;

    if (authErrorMessage) {
      authErrorMessage.style.display = "none";
      authErrorMessage.textContent = "";
    }
    if (authSubmitBtn) authSubmitBtn.disabled = true;

    try {
      const endpoint = authMode === "register" ? "/api/auth/register" : "/api/auth/login";
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Authentication failed");
      }

      setAuthToken(data.token);
      currentUser = { id: data.user_id, username: data.username };
      updateAuthUI();
      closeAuthModal();

      $("chatStatusText").textContent = `Signed in as ${data.username}.`;
      await Promise.all([
        loadConversations(),
        loadChatHistory(),
      ]);
    } catch (err) {
      if (authErrorMessage) {
        authErrorMessage.textContent = err.message;
        authErrorMessage.style.display = "block";
      }
    } finally {
      if (authSubmitBtn) authSubmitBtn.disabled = false;
    }
  });
}

async function handleLogout() {
  try {
    await authFetch("/api/auth/logout", { method: "POST" });
  } catch (err) {
    console.warn("Logout error:", err);
  }
  setAuthToken(null);
  currentUser = null;
  updateAuthUI();
  startNewConversation();
  $("chatStatusText").textContent = "Signed out. Chat history locked.";
}

if ($("headerLogoutBtn")) {
  $("headerLogoutBtn").addEventListener("click", handleLogout);
}

// Delegate click on prompt starter chips
document.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip && chip.dataset.prompt) {
    sendQuestion(chip.dataset.prompt);
  }
});

// Initial page startup
(async function init() {
  await checkAuthStatus();
  await loadWorkspaces();
  await Promise.all([
    loadFiles(),
    loadHealth(),
    currentUser ? loadConversations() : Promise.resolve(),
    currentUser ? loadChatHistory() : Promise.resolve(),
  ]);
})();
