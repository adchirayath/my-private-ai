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

// Active streaming generation controllers isolated per conversation ID (convId -> AbortController)
const activeControllers = new Map();

/**
 * Returns true if the specified conversation (or currently active conversation) is actively generating.
 */
function isGenerating(convId = null) {
  const targetId = convId || getConversationId();
  return activeControllers.has(targetId);
}

/**
 * Synchronizes the Send vs Stop action button state for the active conversation.
 * Ensures the action button resets to Send if the current conversation is not actively generating.
 */
function updateChatActionButtons(convId = null) {
  const targetId = convId || getConversationId();
  const generating = activeControllers.has(targetId);
  const sendBtn = $("sendBtn");
  const stopBtn = $("stopBtn");
  if (sendBtn && stopBtn) {
    if (generating) {
      sendBtn.style.display = "none";
      stopBtn.style.display = "inline-flex";
    } else {
      sendBtn.style.display = "inline-flex";
      stopBtn.style.display = "none";
    }
  }
}

// ==============================================================================
// 0. Warning & Confirmation Modal Popup Management
// ==============================================================================

let activeConfirmResolve = null;
let activeAlertResolve = null;
let popupDialogsInitialized = false;

function ensurePopupDialogsExist() {
  if (!$("appAlertDialog")) {
    const d = document.createElement("dialog");
    d.id = "appAlertDialog";
    d.className = "modal-dialog modal-prompt-dialog";
    d.innerHTML = `
      <div class="modal-card modal-prompt-card">
        <div class="modal-header">
          <div class="modal-title-wrap">
            <div id="alertPopupIcon" class="modal-icon modal-icon-warning">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/>
                <line x1="12" y1="9" x2="12" y2="13"/>
                <line x1="12" y1="17" x2="12.01" y2="17"/>
              </svg>
            </div>
            <h3 id="alertPopupTitle">Warning</h3>
          </div>
          <button id="closeAlertPopupBtn" class="btn-icon" type="button" title="Close (Esc)">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"/>
              <line x1="6" y1="18" x2="18" y2="6"/>
            </svg>
          </button>
        </div>
        <div class="modal-prompt-body">
          <p id="alertPopupMessage" class="modal-desc modal-prompt-message"></p>
        </div>
        <div class="modal-actions">
          <button type="button" id="confirmAlertPopupBtn" class="btn-primary">OK</button>
        </div>
      </div>
    `;
    document.body.appendChild(d);
  }

  if (!$("appConfirmDialog")) {
    const d = document.createElement("dialog");
    d.id = "appConfirmDialog";
    d.className = "modal-dialog modal-prompt-dialog";
    d.innerHTML = `
      <div class="modal-card modal-prompt-card">
        <div class="modal-header">
          <div class="modal-title-wrap">
            <div id="confirmPopupIcon" class="modal-icon modal-icon-danger">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"/>
              </svg>
            </div>
            <h3 id="confirmPopupTitle">Confirm Action</h3>
          </div>
          <button id="closeConfirmPopupBtn" class="btn-icon" type="button" title="Close (Esc)">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"/>
              <line x1="6" y1="18" x2="18" y2="6"/>
            </svg>
          </button>
        </div>
        <div class="modal-prompt-body">
          <p id="confirmPopupMessage" class="modal-desc modal-prompt-message"></p>
        </div>
        <div class="modal-actions">
          <button type="button" id="cancelConfirmPopupBtn" class="btn-secondary">Cancel</button>
          <button type="button" id="acceptConfirmPopupBtn" class="btn-danger">Confirm</button>
        </div>
      </div>
    `;
    document.body.appendChild(d);
  }
}

function initPopupDialogs() {
  if (popupDialogsInitialized) return;
  if (!document.body) return;
  ensurePopupDialogsExist();

  const alertDialog = $("appAlertDialog");
  const confirmDialog = $("appConfirmDialog");

  if (alertDialog) {
    const handleAlertClose = () => {
      if (activeAlertResolve) {
        const resolve = activeAlertResolve;
        activeAlertResolve = null;
        resolve();
      }
      if (alertDialog.open) alertDialog.close();
    };

    $("closeAlertPopupBtn")?.addEventListener("click", handleAlertClose);
    $("confirmAlertPopupBtn")?.addEventListener("click", handleAlertClose);
    alertDialog.addEventListener("cancel", (e) => {
      e.preventDefault();
      handleAlertClose();
    });
    alertDialog.addEventListener("click", (e) => {
      if (e.target === alertDialog) handleAlertClose();
    });
  }

  if (confirmDialog) {
    const handleConfirmClose = (result) => {
      if (activeConfirmResolve) {
        const resolve = activeConfirmResolve;
        activeConfirmResolve = null;
        resolve(result);
      }
      if (confirmDialog.open) confirmDialog.close();
    };

    $("closeConfirmPopupBtn")?.addEventListener("click", () => handleConfirmClose(false));
    $("cancelConfirmPopupBtn")?.addEventListener("click", () => handleConfirmClose(false));
    $("acceptConfirmPopupBtn")?.addEventListener("click", () => handleConfirmClose(true));
    confirmDialog.addEventListener("cancel", (e) => {
      e.preventDefault();
      handleConfirmClose(false);
    });
    confirmDialog.addEventListener("click", (e) => {
      if (e.target === confirmDialog) handleConfirmClose(false);
    });
  }

  popupDialogsInitialized = true;
}

/**
 * Modern non-blocking warning / alert modal popup.
 *
 * @param {string|object} options - Message string or options { title, message, type, buttonText }
 * @param {string} [defaultTitle] - Title if options is string
 * @param {string} [defaultType] - "warning" | "error" | "info"
 * @returns {Promise<void>} Resolves when dismissed
 */
function showWarningPopup(options, defaultTitle = "Warning", defaultType = "warning") {
  return new Promise((resolve) => {
    initPopupDialogs();
    const dialog = $("appAlertDialog");
    if (!dialog) {
      resolve();
      return;
    }

    if (activeAlertResolve) {
      activeAlertResolve();
    }
    activeAlertResolve = resolve;

    let opts = {};
    if (typeof options === "string") {
      opts = {
        message: options,
        title: defaultTitle,
        type: defaultType,
      };
    } else {
      opts = options || {};
    }

    const title = opts.title || defaultTitle || "Warning";
    const message = opts.message || "";
    const type = opts.type || defaultType || "warning";
    const buttonText = opts.buttonText || opts.okText || "Understood";

    if ($("alertPopupTitle")) $("alertPopupTitle").textContent = title;
    if ($("alertPopupMessage")) $("alertPopupMessage").textContent = message;

    const btn = $("confirmAlertPopupBtn");
    if (btn) {
      btn.textContent = buttonText;
      btn.className = type === "error" ? "btn-danger" : (type === "warning" ? "btn-warning" : "btn-primary");
    }

    const iconEl = $("alertPopupIcon");
    if (iconEl) {
      iconEl.className = `modal-icon ${type === "error" ? "modal-icon-danger" : (type === "warning" ? "modal-icon-warning" : "modal-icon-info")}`;
      if (type === "error") {
        iconEl.innerHTML = `
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/>
            <line x1="15" y1="9" x2="9" y2="15"/>
            <line x1="9" y1="9" x2="15" y2="15"/>
          </svg>
        `;
      } else if (type === "info") {
        iconEl.innerHTML = `
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/>
            <line x1="12" y1="16" x2="12" y2="12"/>
            <line x1="12" y1="8" x2="12.01" y2="8"/>
          </svg>
        `;
      } else {
        iconEl.innerHTML = `
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/>
            <line x1="12" y1="9" x2="12" y2="13"/>
            <line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
        `;
      }
    }

    if (!dialog.open) {
      dialog.showModal();
    }
    btn?.focus();
  });
}

/**
 * Modern non-blocking confirmation modal popup.
 *
 * @param {string|object} options - Message string or options { title, message, confirmText, cancelText, confirmVariant }
 * @param {string} [defaultTitle] - Title if options is string
 * @param {string} [defaultVariant] - "danger" | "warning" | "primary"
 * @returns {Promise<boolean>} Resolves to true if accepted, false if canceled
 */
function showConfirmPopup(options, defaultTitle = "Confirm Action", defaultVariant = "danger") {
  return new Promise((resolve) => {
    initPopupDialogs();
    const dialog = $("appConfirmDialog");
    if (!dialog) {
      resolve(false);
      return;
    }

    if (activeConfirmResolve) {
      activeConfirmResolve(false);
    }
    activeConfirmResolve = resolve;

    let opts = {};
    if (typeof options === "string") {
      opts = {
        message: options,
        title: defaultTitle,
        confirmVariant: defaultVariant,
      };
    } else {
      opts = options || {};
    }

    const title = opts.title || defaultTitle || "Confirm Action";
    const message = opts.message || "";
    const confirmText = opts.confirmText || "Confirm";
    const cancelText = opts.cancelText || "Cancel";
    const variant = opts.confirmVariant || opts.variant || defaultVariant || "danger";

    if ($("confirmPopupTitle")) $("confirmPopupTitle").textContent = title;
    if ($("confirmPopupMessage")) $("confirmPopupMessage").textContent = message;

    const acceptBtn = $("acceptConfirmPopupBtn");
    if (acceptBtn) {
      acceptBtn.textContent = confirmText;
      acceptBtn.className = variant === "danger" ? "btn-danger" : (variant === "warning" ? "btn-warning" : "btn-primary");
    }

    const cancelBtn = $("cancelConfirmPopupBtn");
    if (cancelBtn) {
      cancelBtn.textContent = cancelText;
    }

    const iconEl = $("confirmPopupIcon");
    if (iconEl) {
      iconEl.className = `modal-icon ${variant === "danger" ? "modal-icon-danger" : (variant === "warning" ? "modal-icon-warning" : "modal-icon-info")}`;
      if (variant === "danger") {
        iconEl.innerHTML = `
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"/>
          </svg>
        `;
      } else if (variant === "warning") {
        iconEl.innerHTML = `
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/>
            <line x1="12" y1="9" x2="12" y2="13"/>
            <line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
        `;
      } else {
        iconEl.innerHTML = `
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/>
            <line x1="12" y1="16" x2="12" y2="12"/>
            <line x1="12" y1="8" x2="12.01" y2="8"/>
          </svg>
        `;
      }
    }

    if (!dialog.open) {
      dialog.showModal();
    }
    acceptBtn?.focus();
  });
}

// Redirect native alert() to modern warning modal popup
window.alert = function (msg) {
  showWarningPopup(msg, "Warning", "warning");
};

window.showWarningPopup = showWarningPopup;
window.showConfirmPopup = showConfirmPopup;

// ==============================================================================
// 0. Dark / Light Theme Mode Management
// ==============================================================================

const THEME_STORAGE_KEY = "coworker_app_theme";

/**
 * Check system preference (prefers-color-scheme)
 */
function getSystemPreferredTheme() {
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/**
 * Get currently applied or stored theme
 */
function getCurrentTheme() {
  const domTheme = document.documentElement.getAttribute("data-theme");
  if (domTheme) return domTheme;
  const storedTheme = localStorage.getItem(THEME_STORAGE_KEY);
  if (storedTheme) return storedTheme;
  return getSystemPreferredTheme();
}

/**
 * Apply theme to document and update toggle UI controls
 */
function applyTheme(theme, save = true) {
  const normalized = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", normalized);
  if (normalized === "dark") {
    document.documentElement.classList.add("dark");
  } else {
    document.documentElement.classList.remove("dark");
  }

  const toggleBtn = $("themeToggleBtn");
  const toggleText = $("themeToggleText");
  const nextModeText = normalized === "dark" ? "Light" : "Dark";
  const currentModeText = normalized === "dark" ? "Dark" : "Light";

  if (toggleText) {
    toggleText.textContent = nextModeText;
  }
  if (toggleBtn) {
    toggleBtn.setAttribute("title", `Switch to ${nextModeText} mode (Ctrl+Shift+D)`);
    toggleBtn.setAttribute("aria-label", `Current: ${currentModeText} mode. Switch to ${nextModeText} mode.`);
  }

  if (save) {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, normalized);
    } catch (e) {
      console.warn("Could not save theme preference to localStorage:", e);
    }
  }
}

/**
 * Toggle between dark and light themes
 */
function toggleTheme() {
  const current = getCurrentTheme();
  const nextTheme = current === "dark" ? "light" : "dark";
  applyTheme(nextTheme, true);
}

/**
 * Initialize theme listeners, system preference observers, and shortcut keys
 */
function initTheme() {
  const saved = localStorage.getItem(THEME_STORAGE_KEY);
  const initial = saved || getSystemPreferredTheme();
  applyTheme(initial, false);

  const toggleBtn = $("themeToggleBtn");
  if (toggleBtn) {
    toggleBtn.addEventListener("click", toggleTheme);
  }

  // React to OS-level dark/light mode changes when user hasn't explicitly set a preference
  if (window.matchMedia) {
    try {
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
        if (!localStorage.getItem(THEME_STORAGE_KEY)) {
          applyTheme(e.matches ? "dark" : "light", false);
        }
      });
    } catch (err) {
      // Fallback for older browsers
    }
  }

  // Keyboard shortcut Ctrl+Shift+D or Cmd+Shift+D for quick switching
  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === "D" || e.key === "d")) {
      e.preventDefault();
      toggleTheme();
    }
  });
}

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
  const workspaceCard = $("workspaceCard");

  if (currentUser) {
    if (loginBtn) loginBtn.style.display = "none";
    if (profileBadge) profileBadge.style.display = "inline-flex";
    if (usernameSpan) usernameSpan.textContent = currentUser.username;
    if (workspaceCard) workspaceCard.style.display = "block";
    showChatUnlockedState();
    showFilesUnlockedState();
    const userDocsTitle = $("userDocsSectionTitle");
    if (userDocsTitle) userDocsTitle.textContent = `${currentUser.username}'s Documents`;
  } else {
    if (loginBtn) loginBtn.style.display = "inline-flex";
    if (profileBadge) profileBadge.style.display = "none";
    if (workspaceCard) workspaceCard.style.display = "none";
    showChatLockedState();
    showFilesLockedState();
    const userDocsTitle = $("userDocsSectionTitle");
    if (userDocsTitle) userDocsTitle.textContent = `Your Documents`;
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

function showFilesLockedState() {
  const lockedState = $("filesAuthLockedState");
  const unlockedView = $("filesUnlockedView");
  const tabBadge = $("tabFileCount");

  if (lockedState) lockedState.style.display = "flex";
  if (unlockedView) unlockedView.style.display = "none";
  if (tabBadge) tabBadge.textContent = "🔒";
  const fileBadgeCount = $("fileBadgeCount");
  if (fileBadgeCount) fileBadgeCount.textContent = "0";
  const fileList = $("fileList");
  if (fileList) fileList.innerHTML = `<p class="file-size" style="padding:10px 0;">Sign in to view your documents.</p>`;
  const healthChunks = $("healthChunks");
  if (healthChunks) {
    healthChunks.textContent = "";
    healthChunks.style.display = "none";
  }
}

function showFilesUnlockedState() {
  const lockedState = $("filesAuthLockedState");
  const unlockedView = $("filesUnlockedView");

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
 * Returns the storage key for saving the active workspace, isolated per user.
 */
function getWorkspaceStorageKey() {
  return currentUser ? `coworker_active_workspace_${currentUser.id}` : "coworker_active_workspace_anon";
}

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
  localStorage.setItem(getWorkspaceStorageKey(), ws);
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
  const workspaceCard = $("workspaceCard");
  if (!currentUser) {
    if (workspaceCard) workspaceCard.style.display = "none";
    if ($("workspace")) $("workspace").value = "default";
    return;
  }
  if (workspaceCard) workspaceCard.style.display = "block";
  try {
    const res = await authFetch("/api/workspaces");
    if (!res.ok) throw new Error("Failed to load workspaces");
    const data = await res.json();
    cachedWorkspaces = data.workspaces || [];

    const select = $("workspaceSelect");
    if (!select) return;

    const storageKey = getWorkspaceStorageKey();
    const savedWs = preferredWorkspace 
      || localStorage.getItem(storageKey) 
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

    if (!matched) {
      if (cachedWorkspaces.length > 0) {
        select.value = cachedWorkspaces[0].name;
      } else {
        const option = document.createElement("option");
        option.value = "default";
        option.textContent = "default (0 files · 0 chats)";
        option.selected = true;
        select.appendChild(option);
      }
    }

    const finalActive = select.value || "default";
    if ($("workspace")) $("workspace").value = finalActive;
    localStorage.setItem(storageKey, finalActive);
    updateWorkspaceUI();
  } catch (err) {
    console.warn("Could not load workspaces:", err);
  }
}

/**
 * Generates or retrieves the active conversation ID for the current workspace and user.
 * Stored in sessionStorage so switching tabs retains the current conversation
 * within the same browser session.
 */
function getConversationId() {
  const userPrefix = currentUser ? `${currentUser.id}:` : "";
  const key = `coworkerConversationId:${userPrefix}${getWorkspace()}`;
  let id = sessionStorage.getItem(key);
  if (!id) {
    id = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : "conv-" + Date.now();
    sessionStorage.setItem(key, id);
  }
  return id;
}

/**
 * Ensures every initial page load or browser refresh starts with a clean New Chat session.
 * Clears any active conversation IDs stored in sessionStorage so that
 * no previous conversation is automatically selected or highlighted,
 * and getConversationId() generates a fresh unique conversation ID.
 */
function ensureFreshSessionOnLoad() {
  clearFailedPrompt();
  for (let i = sessionStorage.length - 1; i >= 0; i--) {
    const key = sessionStorage.key(i);
    if (key && (key.startsWith("coworkerConversationId:") || key === "coworkerSessionInitialized")) {
      sessionStorage.removeItem(key);
    }
  }
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
  setConversationStatus(newId, "incomplete");
  markConversationSeen(newId);

  // 1. Immediately reset action buttons to Send (never stay as Stop)
  updateChatActionButtons(newId);

  // 2. Fresh session UI: Welcome hero, clear input, reset status
  const container = $("messagesContainer");
  if (container) {
    renderWelcomeHero(container, true);
  }
  if ($("chatStatusText")) {
    $("chatStatusText").textContent = "New conversation ready.";
  }
  const questionInput = $("question");
  if (questionInput) {
    questionInput.value = "";
    questionInput.style.height = "auto";
    questionInput.focus();
  }

  // 3. Immediately update and display chat history
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
    const ws = typeof getWorkspace === "function" ? getWorkspace() : "default";
    const response = await authFetch(`/api/health?workspace=${encodeURIComponent(ws)}`);
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

    const activeChatModel = modelsState.selectedModel || ($("chatModelSelect") ? $("chatModelSelect").value : "") || localStorage.getItem("coworker_selected_model") || data.chat_model || "llama3.2";
    if ($("healthChatModel")) $("healthChatModel").textContent = activeChatModel;
    if ($("healthEmbedModel")) $("healthEmbedModel").textContent = data.embedding_model || "nomic-embed-text";
    const chunks = currentUser ? (data.chroma_document_chunks || 0) : 0;
    const healthChunks = $("healthChunks");
    if (healthChunks) {
      if (chunks > 0) {
        healthChunks.textContent = `${chunks} chunk${chunks === 1 ? "" : "s"}`;
        healthChunks.style.display = "";
      } else {
        healthChunks.textContent = "";
        healthChunks.style.display = "none";
      }
    }

    // Refresh models dropdown with installed & downloadable models
    loadModels().catch(() => {});
  } catch (err) {
    dots.forEach((dot) => {
      dot.className = "status-dot offline";
    });
    if (statusText) statusText.textContent = "Service Unreachable";
    if (healthLabel) healthLabel.textContent = "Service Offline";
    const healthChunks = $("healthChunks");
    if (healthChunks) {
      healthChunks.textContent = "";
      healthChunks.style.display = "none";
    }
  }
}

// ==============================================================================
// 4. File Management & Upload Handlers
// ==============================================================================

async function loadFiles() {
  if (!currentUser) {
    showFilesLockedState();
    return;
  }
  const list = $("fileList");
  list.innerHTML = `<p class="file-size">Refreshing files…</p>`;

  try {
    const response = await authFetch("/api/files?workspace=" + encodeURIComponent(getWorkspace()));
    if (response.status === 401) {
      showFilesLockedState();
      return;
    }
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
        <div style="display: flex; gap: 4px; align-items: center;">
          <button class="btn-file-preview" title="Preview extracted text and indexed chunks" type="button">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>
              <circle cx="12" cy="12" r="3"/>
            </svg>
          </button>
          <button class="btn-delete" title="Delete file and vectors" type="button">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"/>
            </svg>
          </button>
        </div>
      `;

      item.querySelector(".btn-file-preview").addEventListener("click", () => {
        openDocumentPreview(file.file_id, file.filename);
      });

      item.querySelector(".btn-delete").addEventListener("click", async () => {
        const confirmed = await showConfirmPopup({
          title: "Delete Document",
          message: `Delete ${file.filename} and remove from vector index?`,
          confirmText: "Delete",
          confirmVariant: "danger"
        });
        if (!confirmed) return;
        try {
          const res = await authFetch("/api/files", {
            method: "DELETE",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ file_id: file.file_id, workspace: getWorkspace() })
          });
          if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || "Delete failed");
          }
          await loadFiles();
          await loadHealth();
          refreshStarterChips();
          loadWorkspaces(getWorkspace()).catch(() => {});
        } catch (e) {
          showWarningPopup({
            title: "Delete Failed",
            message: e.message,
            type: "error"
          });
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
  if (!currentUser) {
    $("uploadStatus").textContent = "Please sign in to upload documents.";
    openAuthModal("login");
    return;
  }

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
    const response = await authFetch("/api/upload", { method: "POST", body: form });
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
    refreshStarterChips();
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
 * Tracks runtime status of conversations (completed, ongoing, interrupted, incomplete)
 * to accurately display the subtle green dot indicator next to the chat title.
 */
const CONV_STATUS_STORAGE_KEY = "my_private_ai_conv_status";
const conversationStates = new Map();

try {
  const rawStatus = sessionStorage.getItem(CONV_STATUS_STORAGE_KEY);
  if (rawStatus) {
    const parsed = JSON.parse(rawStatus);
    for (const [k, v] of Object.entries(parsed)) {
      conversationStates.set(k, v);
    }
  }
} catch (e) {}

function setConversationStatus(convId, status) {
  if (!convId) return;
  conversationStates.set(convId, status);
  try {
    const raw = sessionStorage.getItem(CONV_STATUS_STORAGE_KEY);
    const map = raw ? JSON.parse(raw) : {};
    map[convId] = status;
    sessionStorage.setItem(CONV_STATUS_STORAGE_KEY, JSON.stringify(map));
  } catch (e) {}
}

function getConversationStatus(convId, conv = null) {
  // 1. If actively generating right now for this conversation:
  if (activeControllers.has(convId)) {
    return "ongoing";
  }
  // 2. Check session status:
  if (conversationStates.has(convId)) {
    return conversationStates.get(convId);
  }
  // 3. Fall back to conversation backend data:
  if (!conv || !conv.message_count || conv.message_count <= 0) {
    return "incomplete";
  }
  if (conv.is_completed !== undefined) {
    return conv.is_completed ? "completed" : "incomplete";
  }
  if (conv.last_role !== undefined) {
    return conv.last_role === "assistant" ? "completed" : "incomplete";
  }
  return "completed";
}

/**
 * Tracks chats that have an unseen / unread completed response because
 * the chat was left before the response was shown.
 */
const UNSEEN_CONV_STORAGE_KEY = "my_private_ai_unseen_convs";

function getUnseenConversations() {
  try {
    const raw = sessionStorage.getItem(UNSEEN_CONV_STORAGE_KEY);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch (e) {
    return new Set();
  }
}

function markConversationUnseen(convId) {
  if (!convId) return;
  const unseen = getUnseenConversations();
  unseen.add(convId);
  try {
    sessionStorage.setItem(UNSEEN_CONV_STORAGE_KEY, JSON.stringify([...unseen]));
  } catch (e) {}
}

function markConversationSeen(convId) {
  if (!convId) return;
  const unseen = getUnseenConversations();
  if (unseen.has(convId)) {
    unseen.delete(convId);
    try {
      sessionStorage.setItem(UNSEEN_CONV_STORAGE_KEY, JSON.stringify([...unseen]));
    } catch (e) {}
  }
}

function isConversationUnseen(convId) {
  if (!convId) return false;
  const unseen = getUnseenConversations();
  return unseen.has(convId);
}

// Automatically clear unseen indicator when returning to a tab viewing the active conversation
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    const activeId = getConversationId();
    if (activeId && isConversationUnseen(activeId)) {
      markConversationSeen(activeId);
      const activeItem = document.querySelector(`#conversationList .conversation-item[data-conv-id="${activeId}"]`);
      if (activeItem) {
        const dot = activeItem.querySelector(".conv-status-dot");
        if (dot) dot.remove();
      }
    }
  }
});

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

      // If active conversation is currently being viewed, mark it seen
      if (isActive) {
        markConversationSeen(conv.conversation_id);
      }

      // Show the green dot only for a chat which was left before showing the response (unseen completed response)
      const status = getConversationStatus(conv.conversation_id, conv);
      const isCompleted = status === "completed";
      const hasUnseenResponse = isCompleted && isConversationUnseen(conv.conversation_id) && !isActive;

      const statusDotHtml = hasUnseenResponse
        ? `<span class="conv-status-dot" title="Unseen response ready" aria-label="Unseen response"></span>`
        : "";

      item.innerHTML = `
        <div class="conversation-info">
          <div class="conversation-icon">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
          </div>
          <div class="conversation-meta">
            <div class="conversation-title-row">
              <span class="conversation-title" title="${escapeHtml(conv.title)}">${escapeHtml(conv.title)}</span>
              ${statusDotHtml}
            </div>
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

      // Click to switch active conversation and load its full history
      item.addEventListener("click", (e) => {
        if (e.target.closest(".btn-delete")) return;
        if (conv.conversation_id === getConversationId()) return;
        clearFailedPrompt();
        markConversationSeen(conv.conversation_id);
        setConversationId(conv.conversation_id);
        updateChatActionButtons(conv.conversation_id);
        loadChatHistory(conv.conversation_id);
        loadConversations();
      });

      // Click to delete conversation
      item.querySelector(".btn-delete").addEventListener("click", async (e) => {
        e.stopPropagation();
        const confirmed = await showConfirmPopup({
          title: "Delete Conversation",
          message: `Delete conversation "${conv.title}"?`,
          confirmText: "Delete",
          confirmVariant: "danger"
        });
        if (!confirmed) return;
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

          if (activeControllers.has(conv.conversation_id)) {
            activeControllers.get(conv.conversation_id).abort();
            activeControllers.delete(conv.conversation_id);
          }
          markConversationSeen(conv.conversation_id);
          conversationStates.delete(conv.conversation_id);
          try {
            const raw = sessionStorage.getItem(CONV_STATUS_STORAGE_KEY);
            if (raw) {
              const map = JSON.parse(raw);
              delete map[conv.conversation_id];
              sessionStorage.setItem(CONV_STATUS_STORAGE_KEY, JSON.stringify(map));
            }
          } catch (e) {}

          if (conv.conversation_id === getConversationId()) {
            startNewConversation();
          }
          await loadConversations();
          loadWorkspaces(getWorkspace()).catch(() => {});
        } catch (err) {
          showWarningPopup({
            title: "Delete Failed",
            message: "Could not delete conversation: " + err.message,
            type: "error"
          });
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
  const currentConvId = getConversationId();
  if (activeControllers.has(currentConvId)) return;

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
  const currentConvId = getConversationId();
  if (activeControllers.has(currentConvId)) return;

  // Cleanly close voice dictation listen area if currently active
  stopVoiceDictation(true);

  const message = customMessage || $("question").value.trim();
  if (!message) return;

  clearFailedPrompt();

  setConversationStatus(currentConvId, "ongoing");
  markConversationSeen(currentConvId);
  const currentItem = document.querySelector(`#conversationList .conversation-item[data-conv-id="${currentConvId}"]`);
  if (currentItem) {
    const existingDot = currentItem.querySelector(".conv-status-dot");
    if (existingDot) existingDot.remove();
  }

  const userBubble = appendMessageRow("user", escapeHtml(message));
  const userRow = userBubble.closest(".msg-row");

  if (!customMessage) {
    $("question").value = "";
    $("question").style.height = "auto";
  }

  const controller = new AbortController();
  activeControllers.set(currentConvId, controller);
  updateChatActionButtons(currentConvId);

  if (getConversationId() === currentConvId) {
    $("chatStatusText").textContent = "Thinking…";
  }

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

  let accumulated = "";
  let sources = [];
  let citations = [];
  let generationStats = null;
  let retrievedChunks = 0;

  try {
    const chosenModel = $("chatModelSelect") ? $("chatModelSelect").value : "";
    const response = await authFetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        message,
        workspace: getWorkspace(),
        conversation_id: currentConvId,
        stream: true,
        model: chosenModel || undefined,
      }),
      signal: controller.signal
    });

    // Update and display chat history immediately without waiting for AI response
    loadConversations();

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
            citations = meta.citations || [];
            retrievedChunks = meta.retrieved_chunks || 0;
            if (getConversationId() === currentConvId) {
              const statusEl = assistantBubble.querySelector(".ai-loader-status");
              if (retrievedChunks > 0) {
                $("chatStatusText").textContent = `Referencing ${retrievedChunks} ${retrievedChunks === 1 ? 'passage' : 'passages'}…`;
                if (statusEl) statusEl.textContent = `Referencing documents (${retrievedChunks})…`;
              } else {
                $("chatStatusText").textContent = "Thinking…";
                if (statusEl) statusEl.textContent = "Thinking…";
              }
            }
          } catch(e){}
        } else if (event === "delta") {
          try {
            const chunk = JSON.parse(data);
            if (chunk.token) {
              accumulated += chunk.token;
              if (getConversationId() === currentConvId && assistantBubble.isConnected) {
                assistantBubble.innerHTML = renderMarkdownWithCursor(accumulated);
                scrollToBottom();
              }
            }
          } catch(e){}
        } else if (event === "done") {
          try {
            const donePayload = JSON.parse(data);
            if (donePayload.answer) accumulated = donePayload.answer;
            if (donePayload.stats) generationStats = donePayload.stats;
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

    setConversationStatus(currentConvId, "completed");

    // Show the green dot only for a chat which was left before showing the response (unseen response)
    const wasLeftBeforeShowing = (getConversationId() !== currentConvId) || document.hidden;
    if (wasLeftBeforeShowing) {
      markConversationUnseen(currentConvId);
    } else {
      markConversationSeen(currentConvId);
    }

    if (citations && citations.length > 0) {
      const sourcesDiv = document.createElement("div");
      sourcesDiv.className = "sources-row";
      sourcesDiv.innerHTML = `<span>Sources:</span> `;
      citations.forEach((cit, idx) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "source-tag source-pill-interactive";
        btn.title = "Click to inspect exact retrieved passage";
        const pageLabel = cit.page_number ? ` • ${escapeHtml(cit.page_number)}` : "";
        btn.innerHTML = `
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6"/></svg>
          <span>${escapeHtml(cit.filename)}${pageLabel}</span>
        `;
        btn.addEventListener("click", () => openCitationModal(cit, idx + 1));
        sourcesDiv.appendChild(btn);
      });
      assistantBubble.appendChild(sourcesDiv);
    } else if (sources.length) {
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

    // Add Copy Response Button, Read Aloud Button, and Speed Badge
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

    const speakBtn = document.createElement("button");
    speakBtn.type = "button";
    speakBtn.className = "btn-speak";
    speakBtn.innerHTML = `
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
        <path d="M15.54 8.46a5 5 0 0 1 0 7.07M19.07 4.93a10 10 0 0 1 0 14.14"/>
      </svg>
      <span>Read aloud</span>
    `;
    speakBtn.addEventListener("click", () => toggleSpeechReadout(accumulated, speakBtn));
    actionsDiv.appendChild(speakBtn);

    if (generationStats && generationStats.tokens_per_second) {
      const statBadge = document.createElement("div");
      statBadge.className = "speed-stat-badge";
      statBadge.innerHTML = `⚡ ${generationStats.tokens_per_second} tok/s · ${generationStats.tokens} tokens`;
      actionsDiv.appendChild(statBadge);
    }

    assistantBubble.appendChild(actionsDiv);

    if (getConversationId() === currentConvId) {
      if (sources.length > 0) {
        $("chatStatusText").textContent = `Cited ${sources.length} ${sources.length === 1 ? 'source' : 'sources'} (${retrievedChunks} passages).`;
      } else {
        $("chatStatusText").textContent = "Ready";
      }
    }
    loadHealth().catch(() => {});

  } catch (err) {
    markConversationSeen(currentConvId);
    if (err.name === "AbortError") {
      setConversationStatus(currentConvId, "interrupted");
      if (getConversationId() === currentConvId && assistantBubble.isConnected) {
        assistantBubble.innerHTML = renderMarkdown(accumulated || "Generation stopped.");
        postProcessAssistantBubble(assistantBubble);
        $("chatStatusText").textContent = "Generation cancelled by user.";
      }
    } else {
      setConversationStatus(currentConvId, "incomplete");
      if (getConversationId() === currentConvId) {
        $("chatStatusText").textContent = "Request failed.";
      }

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
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
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
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
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
    activeControllers.delete(currentConvId);
    if (getConversationId() === currentConvId) {
      updateChatActionButtons(currentConvId);
      scrollToBottom();
    }
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
    const targetConvId = explicitConvId || getConversationId();
    markConversationSeen(targetConvId);
    const convId = encodeURIComponent(targetConvId);
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

          // Add copy and read aloud buttons for restored turns
          const actionsDiv = document.createElement("div");
          actionsDiv.className = "bubble-actions";
          const copyBtn = document.createElement("button");
          copyBtn.className = "btn-copy-bubble";
          copyBtn.textContent = "Copy response";
          copyBtn.addEventListener("click", () => {
            navigator.clipboard.writeText(msg.content).then(() => {
              copyBtn.textContent = "Copied!";
              setTimeout(() => { copyBtn.textContent = "Copy response"; }, 2000);
            });
          });
          actionsDiv.appendChild(copyBtn);

          const speakBtn = document.createElement("button");
          speakBtn.type = "button";
          speakBtn.className = "btn-speak";
          speakBtn.innerHTML = `
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
              <path d="M15.54 8.46a5 5 0 0 1 0 7.07M19.07 4.93a10 10 0 0 1 0 14.14"/>
            </svg>
            <span>Read aloud</span>
          `;
          speakBtn.addEventListener("click", () => toggleSpeechReadout(msg.content, speakBtn));
          actionsDiv.appendChild(speakBtn);
          bubble.appendChild(actionsDiv);

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

function renderWelcomeHero(container, isNew = false) {
  if (!container) return;
  const ws = getWorkspace();
  const title = isNew ? "New Conversation" : "How can I help you today?";
  const subtext = isNew 
    ? `Started a fresh chat in workspace '<strong>${escapeHtml(ws)}</strong>'. Ask a question or click a prompt below.`
    : `Ask a question, write code, brainstorm ideas, or attach documents to analyze them with citations.`;

  container.innerHTML = `
    <div id="welcomeHero" class="welcome-hero">
      <div class="welcome-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
        </svg>
      </div>
      <h2>${title}</h2>
      <p id="welcomeDescription">${subtext}</p>

      <div id="starterChipsContainer" class="starter-chips">
        <!-- Generic starter chips as immediate baseline -->
        <button class="chip" data-prompt="Brainstorm 5 innovative, practical ideas for a new software project.">
          💡 Brainstorm ideas
        </button>
        <button class="chip" data-prompt="Write a clean Python script to automate everyday file processing and data cleanup.">
          💻 Python automation
        </button>
        <button class="chip" data-prompt="Draft a concise, professional follow-up email after a project kickoff meeting.">
          ✍️ Draft email
        </button>
        <button class="chip" data-prompt="Explain how large language models and vector embeddings work in simple terms.">
          🧠 Explain a concept
        </button>
      </div>
    </div>
  `;
  refreshStarterChips();
}

/**
 * Renders generic baseline starter prompt chips.
 */
function renderGenericChips(container) {
  if (!container) return;
  const descEl = $("welcomeDescription");
  if (descEl && (descEl.textContent.includes("attached document") || descEl.textContent.includes("click a suggestion"))) {
    descEl.textContent = "Ask a question, write code, brainstorm ideas, or attach documents to analyze them with citations.";
  }
  container.innerHTML = `
    <button class="chip" data-prompt="Brainstorm 5 innovative, practical ideas for a new software project.">
      💡 Brainstorm ideas
    </button>
    <button class="chip" data-prompt="Write a clean Python script to automate everyday file processing and data cleanup.">
      💻 Python automation
    </button>
    <button class="chip" data-prompt="Draft a concise, professional follow-up email after a project kickoff meeting.">
      ✍️ Draft email
    </button>
    <button class="chip" data-prompt="Explain how large language models and vector embeddings work in simple terms.">
      🧠 Explain a concept
    </button>
  `;
}

/**
 * Dynamically fetches and updates starter prompts:
 * - If user is not authenticated: strictly renders generic assistant prompt chips.
 * - If NO documents are attached: ensures generic assistant prompt chips.
 * - If documents ARE attached: renders dynamic document-specific questions.
 */
async function refreshStarterChips() {
  const chipsContainer = $("starterChipsContainer") || document.querySelector(".starter-chips");
  if (!chipsContainer) return;

  // Anonymous / unauthenticated sessions must never display document-specific suggestions
  if (!currentUser) {
    renderGenericChips(chipsContainer);
    return;
  }

  const ws = getWorkspace();
  try {
    const res = await authFetch(`/api/chat/starter-prompts?workspace=${encodeURIComponent(ws)}`);
    if (!res.ok) return;
    const data = await res.json();

    const container = $("starterChipsContainer") || document.querySelector(".starter-chips");
    if (!container) return;

    const chips = data.chips || [];
    if (chips.length > 0) {
      container.replaceChildren();
      for (const chip of chips) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "chip";
        btn.dataset.prompt = chip.prompt;
        btn.textContent = chip.label;
        btn.title = chip.prompt;
        container.appendChild(btn);
      }
    }

    const descEl = $("welcomeDescription");
    if (descEl && data.description) {
      descEl.textContent = data.description;
    }
  } catch (err) {
    console.debug("Could not refresh starter chips:", err);
  }
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
  const currentConvId = getConversationId();
  const controller = activeControllers.get(currentConvId);
  if (controller) {
    controller.abort();
    activeControllers.delete(currentConvId);
  }
  updateChatActionButtons(currentConvId);
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
    if (!currentUser) {
      openAuthModal("login");
      return;
    }
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

      const res = await authFetch("/api/workspaces", {
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
      showWarningPopup({
        title: "Workspace Error",
        message: "Error creating workspace: " + err.message,
        type: "error"
      });
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
      showWarningPopup({
        title: "Action Not Allowed",
        message: "The 'default' workspace cannot be deleted.",
        type: "warning"
      });
      return;
    }

    const confirmed = await showConfirmPopup({
      title: "Delete Workspace",
      message:
        `Are you sure you want to permanently delete workspace '${activeWs}'?\n\n` +
        `This will permanently remove:\n` +
        `• All uploaded documents in '${activeWs}'\n` +
        `• All vector embeddings in ChromaDB\n` +
        `• All chat conversations and messages in SQLite\n\n` +
        `This action cannot be undone.`,
      confirmText: "Delete Workspace",
      confirmVariant: "danger"
    });
    if (!confirmed) return;

    try {
      $("deleteWorkspaceBtn").disabled = true;
      const res = await authFetch("/api/workspaces", {
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
      showWarningPopup({
        title: "Workspace Error",
        message: "Error deleting workspace: " + err.message,
        type: "error"
      });
    } finally {
      $("deleteWorkspaceBtn").disabled = false;
    }
  });
}

// Clear conversation memory
$("clearChatBtn").addEventListener("click", async () => {
  const convId = getConversationId();
  const confirmed = await showConfirmPopup({
    title: "Clear Memory",
    message: "Clear memory for this current conversation? The LLM context will be refreshed for new questions.",
    confirmText: "Clear Memory",
    confirmVariant: "warning"
  });
  if (!confirmed) return;

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

if ($("filesUnlockBtn")) {
  $("filesUnlockBtn").addEventListener("click", () => openAuthModal("login"));
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
        loadWorkspaces(),
        loadFiles(),
        loadConversations(),
        loadChatHistory(),
        refreshStarterChips(),
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
  setWorkspace("default");
  await loadWorkspaces("default");
  await loadFiles();
  startNewConversation();
  $("chatStatusText").textContent = "Signed out. Chat and documents locked.";
}

if ($("headerLogoutBtn")) {
  $("headerLogoutBtn").addEventListener("click", handleLogout);
}

// ==============================================================================
// 12. Document Content Preview, Citations, Export, and Voice Workstation Helpers
// ==============================================================================

async function openDocumentPreview(fileId, filename) {
  const dialog = $("previewDialog");
  if (!dialog) return;

  $("previewDocTitle").textContent = filename;
  $("previewFileSize").textContent = "Loading…";
  $("previewChunkCount").textContent = "…";
  $("previewCharCount").textContent = "…";
  $("previewRawText").textContent = "Fetching extracted text and indexed chunks…";
  $("previewChunksList").innerHTML = "";

  dialog.showModal();

  try {
    const res = await authFetch(`/api/files/preview?file_id=${encodeURIComponent(fileId)}&workspace=${encodeURIComponent(getWorkspace())}`);
    if (!res.ok) throw new Error("Could not load preview");
    const data = await res.json();

    $("previewFileSize").textContent = formatBytes(data.size_bytes);
    $("previewChunkCount").textContent = `${data.chunk_count} chunks indexed`;
    $("previewCharCount").textContent = `${data.characters_extracted.toLocaleString()} characters`;
    $("previewRawText").textContent = data.text_preview;

    const chunksList = $("previewChunksList");
    chunksList.innerHTML = "";
    if (data.sample_chunks && data.sample_chunks.length > 0) {
      data.sample_chunks.forEach((chunk) => {
        const item = document.createElement("div");
        item.className = "preview-chunk-item";
        const pageLabel = chunk.page_number ? ` • ${escapeHtml(chunk.page_number)}` : "";
        item.innerHTML = `
          <div class="preview-chunk-header">
            <span>Chunk #${chunk.chunk_index + 1}${pageLabel}</span>
          </div>
          <div class="preview-chunk-snippet">${escapeHtml(chunk.snippet)}</div>
        `;
        chunksList.appendChild(item);
      });
    } else {
      chunksList.innerHTML = `<p class="file-size" style="padding: 10px;">No sample chunks available.</p>`;
    }
  } catch (err) {
    $("previewRawText").textContent = `Error loading preview: ${err.message}`;
  }
}

function openCitationModal(citation, rank = 1) {
  const dialog = $("citationDialog");
  if (!dialog) return;

  $("citationDocName").textContent = citation.filename;
  const pagePill = $("citationPagePill");
  if (citation.page_number) {
    pagePill.textContent = citation.page_number;
    pagePill.style.display = "inline-flex";
  } else {
    pagePill.style.display = "none";
  }

  $("citationScorePill").textContent = `Relevance Rank #${rank}`;
  $("citationSnippetBox").textContent = citation.snippet || "(No passage snippet available)";
  dialog.showModal();
}

// Web Speech API Voice Dictation
let speechRecognitionInstance = null;
let isDictating = false;
let stopVoiceDictationFn = null;

/**
 * Public helper to stop voice dictation from other interactions (e.g. sending message)
 */
function stopVoiceDictation(commit = true) {
  if (isDictating && stopVoiceDictationFn) {
    stopVoiceDictationFn(commit);
  }
}

/**
 * Play a subtle Web Audio API chime on start / stop (zero audio files needed)
 */
function playVoiceAudioCue(type) {
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    if (type === "start") {
      osc.frequency.setValueAtTime(440, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.1);
      gain.gain.setValueAtTime(0.06, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.14);
      osc.start();
      osc.stop(ctx.currentTime + 0.14);
    } else {
      osc.frequency.setValueAtTime(660, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(330, ctx.currentTime + 0.1);
      gain.gain.setValueAtTime(0.05, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.14);
      osc.start();
      osc.stop(ctx.currentTime + 0.14);
    }
  } catch (_) {
    // Audio context may be restricted before user gesture
  }
}

function initVoiceDictation() {
  const dictateBtn = $("voiceDictateBtn");
  const textarea = $("question");
  const hud = $("voiceSpeechHud");
  const hudTranscript = $("voiceHudTranscript");
  const hudDoneBtn = $("voiceHudDoneBtn");
  const hudCancelBtn = $("voiceHudCancelBtn");
  const chatStatusText = $("chatStatusText");
  if (!dictateBtn || !textarea) return;

  let baseText = "";
  let finalTranscript = "";

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!SpeechRecognition) {
    dictateBtn.title = "Voice dictation is not supported in this browser.";
    dictateBtn.style.opacity = "0.5";
    dictateBtn.addEventListener("click", () => {
      showWarningPopup({
        title: "Voice Dictation Unsupported",
        message: "Voice dictation (SpeechRecognition) is not supported in this browser.\n\nPlease use Google Chrome, Microsoft Edge, or a browser with Web Speech API support.",
        type: "warning"
      });
    });
    return;
  }

  const setListeningUI = (listening) => {
    isDictating = listening;
    if (listening) {
      dictateBtn.classList.add("listening");
      dictateBtn.setAttribute("title", "Listening… Click to finish dictation");
      if (hud) hud.style.display = "flex";
      if (hudTranscript) {
        hudTranscript.textContent = "Speak now into your microphone…";
        hudTranscript.classList.add("listening-placeholder");
      }
      if (chatStatusText) chatStatusText.textContent = "Listening to voice input…";
      playVoiceAudioCue("start");
    } else {
      dictateBtn.classList.remove("listening");
      dictateBtn.setAttribute("title", "Voice dictation (Speech-to-Text)");
      if (hud) hud.style.display = "none";
      if (chatStatusText && chatStatusText.textContent === "Listening to voice input…") {
        chatStatusText.textContent = "Ready";
      }
      playVoiceAudioCue("stop");
    }
  };

  const commitDictation = () => {
    if (speechRecognitionInstance) {
      try { speechRecognitionInstance.stop(); } catch (e) {
        try { speechRecognitionInstance.abort(); } catch (_) {}
      }
    }
    setListeningUI(false);
    textarea.focus();
  };

  const cancelDictation = () => {
    if (speechRecognitionInstance) {
      try { speechRecognitionInstance.abort(); } catch (_) {}
    }
    textarea.value = baseText;
    textarea.dispatchEvent(new Event("input"));
    setListeningUI(false);
    if (chatStatusText) {
      chatStatusText.textContent = "Dictation cancelled";
      setTimeout(() => {
        if (chatStatusText.textContent === "Dictation cancelled") {
          chatStatusText.textContent = "Ready";
        }
      }, 2500);
    }
    textarea.focus();
  };

  stopVoiceDictationFn = (commit = true) => {
    if (commit) {
      commitDictation();
    } else {
      cancelDictation();
    }
  };

  if (hudDoneBtn) {
    hudDoneBtn.addEventListener("click", (e) => {
      e.preventDefault();
      commitDictation();
    });
  }

  if (hudCancelBtn) {
    hudCancelBtn.addEventListener("click", (e) => {
      e.preventDefault();
      cancelDictation();
    });
  }

  // Allow Escape key to cancel dictation
  window.addEventListener("keydown", (e) => {
    if (isDictating && e.key === "Escape") {
      cancelDictation();
    }
  });

  dictateBtn.addEventListener("click", () => {
    if (isDictating) {
      commitDictation();
      return;
    }

    // Require secure context or localhost for mic permissions
    if (window.isSecureContext === false && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
      showWarningPopup({
        title: "Microphone Access Restricted",
        message: "Microphone access requires a secure context (HTTPS or localhost). Voice dictation is disabled because this page is loaded over insecure HTTP from a network IP.",
        type: "warning"
      });
      return;
    }

    try {
      if (speechRecognitionInstance) {
        try { speechRecognitionInstance.abort(); } catch (_) {}
        speechRecognitionInstance = null;
      }

      speechRecognitionInstance = new SpeechRecognition();
      speechRecognitionInstance.continuous = true;
      speechRecognitionInstance.interimResults = true;
      speechRecognitionInstance.lang = navigator.language || "en-US";

      baseText = textarea.value.trim();
      finalTranscript = "";

      speechRecognitionInstance.onstart = () => {
        setListeningUI(true);
      };

      speechRecognitionInstance.onresult = (event) => {
        let interimTranscript = "";
        for (let i = event.resultIndex; i < event.results.length; ++i) {
          const part = event.results[i][0].transcript;
          if (event.results[i].isFinal) {
            finalTranscript += (finalTranscript ? " " : "") + part.trim();
          } else {
            interimTranscript += part;
          }
        }

        const newlySpoken = [finalTranscript, interimTranscript.trim()].filter(Boolean).join(" ");
        if (hudTranscript) {
          if (newlySpoken) {
            hudTranscript.textContent = `“${newlySpoken}”`;
            hudTranscript.classList.remove("listening-placeholder");
          } else {
            hudTranscript.textContent = "Speak now into your microphone…";
            hudTranscript.classList.add("listening-placeholder");
          }
        }

        const totalText = [baseText, newlySpoken].filter(Boolean).join(" ");
        textarea.value = totalText;
        textarea.dispatchEvent(new Event("input"));
      };

      speechRecognitionInstance.onerror = (event) => {
        console.warn("Speech recognition error:", event.error, event);
        setListeningUI(false);

        if (event.error === "not-allowed" || event.error === "service-not-allowed") {
          showWarningPopup({
            title: "Microphone Permission Denied",
            message: "Microphone permission was denied. Please allow microphone access in your browser site permissions to use voice dictation.",
            type: "warning"
          });
        } else if (event.error === "network") {
          showWarningPopup({
            title: "Speech Recognition Network Error",
            message: "The browser's speech recognition service failed to connect (common in Brave shields or offline environments).",
            type: "warning"
          });
        } else if (event.error === "audio-capture") {
          showWarningPopup({
            title: "No Microphone Detected",
            message: "No microphone detected. Please connect or enable a microphone in your system settings.",
            type: "warning"
          });
        } else if (event.error === "no-speech") {
          if (chatStatusText) {
            chatStatusText.textContent = "No speech detected.";
            setTimeout(() => {
              if (chatStatusText && chatStatusText.textContent === "No speech detected.") {
                chatStatusText.textContent = "Ready";
              }
            }, 3000);
          }
        }
      };

      speechRecognitionInstance.onend = () => {
        setListeningUI(false);
      };

      speechRecognitionInstance.start();
    } catch (err) {
      console.error("Error starting speech recognition:", err);
      setListeningUI(false);
      showWarningPopup({
        title: "Voice Dictation Error",
        message: "Could not start voice dictation: " + (err.message || err),
        type: "error"
      });
    }
  });
}

// Text-to-Speech (TTS) Read Aloud
let currentSpeakingBtn = null;

function toggleSpeechReadout(textToSpeak, buttonEl) {
  if (!window.speechSynthesis) {
    showWarningPopup({
      title: "Speech Synthesis Unsupported",
      message: "Speech synthesis is not supported in this browser.",
      type: "warning"
    });
    return;
  }

  if (window.speechSynthesis.speaking) {
    window.speechSynthesis.cancel();
    if (currentSpeakingBtn) {
      currentSpeakingBtn.classList.remove("speaking");
      const span = currentSpeakingBtn.querySelector("span");
      if (span) span.textContent = "Read aloud";
    }
    if (currentSpeakingBtn === buttonEl) {
      currentSpeakingBtn = null;
      return;
    }
  }

  const cleanText = textToSpeak.replace(/```[\s\S]*?```/g, "Code block omitted.").replace(/[#*`_]/g, "");
  const utterance = new SpeechSynthesisUtterance(cleanText);
  utterance.rate = 1.0;
  utterance.pitch = 1.0;

  utterance.onstart = () => {
    buttonEl.classList.add("speaking");
    const span = buttonEl.querySelector("span");
    if (span) span.textContent = "Stop reading";
    currentSpeakingBtn = buttonEl;
  };

  utterance.onend = utterance.onerror = () => {
    buttonEl.classList.remove("speaking");
    const span = buttonEl.querySelector("span");
    if (span) span.textContent = "Read aloud";
    currentSpeakingBtn = null;
  };

  window.speechSynthesis.speak(utterance);
}

function initCitationDialog() {
  const dialog = $("citationDialog");
  const closeBtn = $("closeCitationDialogBtn");
  const bottomCloseBtn = $("closeCitationBtn");
  const copyBtn = $("copyCitationBtn");

  if (!dialog) return;
  if (closeBtn) closeBtn.addEventListener("click", () => dialog.close());
  if (bottomCloseBtn) bottomCloseBtn.addEventListener("click", () => dialog.close());

  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      const snippet = $("citationSnippetBox").textContent;
      navigator.clipboard.writeText(snippet).then(() => {
        const span = copyBtn.querySelector("span");
        if (span) span.textContent = "Copied!";
        setTimeout(() => {
          if (span) span.textContent = "Copy Excerpt";
        }, 2000);
      });
    });
  }
}

function initPreviewDialog() {
  const dialog = $("previewDialog");
  const closeBtn = $("closePreviewDialogBtn");
  const rawTabBtn = $("previewTabRawBtn");
  const chunksTabBtn = $("previewTabChunksBtn");
  const rawPane = $("previewRawPane");
  const chunksPane = $("previewChunksPane");

  if (!dialog) return;
  if (closeBtn) closeBtn.addEventListener("click", () => dialog.close());

  if (rawTabBtn && chunksTabBtn) {
    rawTabBtn.addEventListener("click", () => {
      rawTabBtn.classList.add("active");
      chunksTabBtn.classList.remove("active");
      if (rawPane) rawPane.classList.add("active");
      if (chunksPane) chunksPane.classList.remove("active");
    });

    chunksTabBtn.addEventListener("click", () => {
      chunksTabBtn.classList.add("active");
      rawTabBtn.classList.remove("active");
      if (chunksPane) chunksPane.classList.add("active");
      if (rawPane) rawPane.classList.remove("active");
    });
  }
}

function initExportDialog() {
  const exportBtn = $("exportChatBtn");
  const dialog = $("exportDialog");
  const cancelBtn = $("cancelExportDialogBtn");
  const closeBtn = $("closeExportDialogBtn");
  const confirmBtn = $("confirmDownloadExportBtn");

  if (!exportBtn || !dialog) return;
  exportBtn.addEventListener("click", () => dialog.showModal());
  if (cancelBtn) cancelBtn.addEventListener("click", () => dialog.close());
  if (closeBtn) closeBtn.addEventListener("click", () => dialog.close());

  if (confirmBtn) {
    confirmBtn.addEventListener("click", async () => {
      const selectedRadio = document.querySelector('input[name="exportFormat"]:checked');
      const format = selectedRadio ? selectedRadio.value : "markdown";
      const ws = getWorkspace();
      const convId = getConversationId();

      try {
        const res = await authFetch(`/api/chat/export?workspace=${encodeURIComponent(ws)}&conversation_id=${encodeURIComponent(convId)}&format=${format}`);
        if (!res.ok) throw new Error("Export request failed");
        const data = await res.json();

        let blob, extension;
        if (format === "json") {
          blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
          extension = "json";
        } else {
          blob = new Blob([data.content || ""], { type: "text/markdown;charset=utf-8" });
          extension = "md";
        }

        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `chat_${ws}_${convId}.${extension}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        dialog.close();
      } catch (err) {
        showWarningPopup({
          title: "Export Failed",
          message: "Failed to export chat: " + err.message,
          type: "error"
        });
      }
    });
  }
}

// ==============================================================================
// AI Model Management & Dropdown Controls
// ==============================================================================

let modelsState = {
  installed: [],
  recommended: [],
  defaultModel: "llama3.2",
  selectedModel: localStorage.getItem("coworker_selected_model") || "",
  isPulling: false,
};

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

async function loadModels() {
  const select = $("chatModelSelect");
  if (!select) return;

  try {
    const res = await fetch("/api/models");
    if (!res.ok) throw new Error("Failed to load models");
    const data = await res.json();

    modelsState.installed = data.installed || [];
    modelsState.recommended = data.recommended || [];
    modelsState.defaultModel = data.default_model || "llama3.2";

    const saved = localStorage.getItem("coworker_selected_model") || "";
    const hasInstalled = modelsState.installed.some((m) => m.name === (select.value || saved));
    let chosen = "";
    if (hasInstalled) {
      chosen = select.value || saved;
    } else if (modelsState.installed.length > 0) {
      const def = modelsState.installed.find((m) => m.is_default);
      chosen = def ? def.name : modelsState.installed[0].name;
    }
    modelsState.selectedModel = chosen;
    if (chosen) {
      localStorage.setItem("coworker_selected_model", chosen);
      if ($("healthChatModel")) {
        $("healthChatModel").textContent = chosen;
      }
    }

    // Build option groups
    select.replaceChildren();

    // Group 1: Installed Models
    const groupInstalled = document.createElement("optgroup");
    groupInstalled.label = `Installed Local Models (${modelsState.installed.length})`;

    modelsState.installed.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.name;
      const tagText = m.is_default ? "Default" : (m.tag || "Local");
      const nameText = m.displayName || m.name;
      opt.textContent = `${nameText} (${m.size_str || 'Local'}) • ${tagText}`;
      if (m.name === chosen) opt.selected = true;
      groupInstalled.appendChild(opt);
    });
    select.appendChild(groupInstalled);

    // Group 2: Available to Download (Recommended models not yet installed)
    const installedNames = new Set(modelsState.installed.map((m) => m.name));
    const availableToPull = modelsState.recommended.filter((r) => !installedNames.has(r.name));

    const groupPull = document.createElement("optgroup");
    groupPull.label = "⚡ Available to Download (1-Click Pull)";

    availableToPull.forEach((r) => {
      const opt = document.createElement("option");
      opt.value = `__pull:${r.name}`;
      opt.textContent = `⬇️ ${r.displayName} (${r.size_str}) — ${r.tag}`;
      groupPull.appendChild(opt);
    });

    const optManage = document.createElement("option");
    optManage.value = "__manage_models__";
    optManage.textContent = "⚙️ Manage Models & Pull Custom…";
    groupPull.appendChild(optManage);

    select.appendChild(groupPull);

    renderModelsDialog();
  } catch (err) {
    console.warn("Could not load models list:", err);
  }
}

function renderModelsDialog() {
  const badge = $("installedModelsBadge");
  if (badge) {
    badge.textContent = `${modelsState.installed.length} installed`;
  }

  const installedGrid = $("installedModelsGrid");
  if (installedGrid) {
    installedGrid.replaceChildren();

    if (modelsState.installed.length === 0) {
      const p = document.createElement("p");
      p.className = "file-size";
      p.textContent = "No models currently installed in Ollama.";
      installedGrid.appendChild(p);
    } else {
      modelsState.installed.forEach((m) => {
        const isCurrent = m.name === modelsState.selectedModel;
        const card = document.createElement("div");
        card.className = `model-card ${isCurrent ? 'active-model-card' : ''}`;

        const isDefault = m.is_default || m.name === modelsState.defaultModel;

        card.innerHTML = `
          <div class="model-card-top">
            <div class="model-card-name-wrap">
              <span class="model-card-title">${escapeHtml(m.displayName || m.name)}</span>
              <span class="model-card-subname">${escapeHtml(m.name)}</span>
            </div>
            <div class="model-tags-wrap">
              ${isDefault ? '<span class="model-tag-pill">Default</span>' : ''}
              ${m.tag && !isDefault ? `<span class="model-tag-pill">${escapeHtml(m.tag)}</span>` : ''}
              <span class="model-size-pill">${escapeHtml(m.size_str || 'Installed')}</span>
            </div>
          </div>
          <div class="model-card-desc">${escapeHtml(m.description || 'Local conversational model.')}</div>
          <div class="model-card-actions">
            ${
              isCurrent
                ? '<button class="btn-model-action btn-model-active" type="button" disabled>✓ Active</button>'
                : `<button class="btn-model-action" type="button" data-action="select" data-model="${escapeHtml(m.name)}">Select as Active</button>`
            }
            ${
              !isDefault
                ? `<button class="btn-model-delete" type="button" title="Delete model to free space" data-action="delete" data-model="${escapeHtml(m.name)}">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      <polyline points="3 6 5 6 21 6"/>
                      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                    </svg>
                   </button>`
                : ''
            }
          </div>
        `;
        installedGrid.appendChild(card);
      });
    }
  }

  const recGrid = $("recommendedModelsGrid");
  if (recGrid) {
    recGrid.replaceChildren();

    const installedNames = new Set(modelsState.installed.map((m) => m.name));
    const available = modelsState.recommended.filter((r) => !installedNames.has(r.name));

    if (available.length === 0) {
      const p = document.createElement("p");
      p.className = "file-size";
      p.textContent = "All recommended models are installed and ready to use!";
      recGrid.appendChild(p);
    } else {
      available.forEach((r) => {
        const card = document.createElement("div");
        card.className = "model-card";
        card.innerHTML = `
          <div class="model-card-top">
            <div class="model-card-name-wrap">
              <span class="model-card-title">${escapeHtml(r.displayName)}</span>
              <span class="model-card-subname">${escapeHtml(r.name)}</span>
            </div>
            <div class="model-tags-wrap">
              <span class="model-tag-pill">${escapeHtml(r.tag)}</span>
              <span class="model-size-pill">${escapeHtml(r.size_str)}</span>
            </div>
          </div>
          <div class="model-card-desc">${escapeHtml(r.description)}</div>
          <div class="model-card-actions">
            <button class="btn-model-action" type="button" data-action="pull" data-model="${escapeHtml(r.name)}">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>
              </svg>
              <span>Pull & Use</span>
            </button>
          </div>
        `;
        recGrid.appendChild(card);
      });
    }
  }
}

function openModelsModal() {
  const dialog = $("modelsDialog");
  if (dialog && !dialog.open) {
    dialog.showModal();
    renderModelsDialog();
  }
}

function selectActiveModel(modelName) {
  modelsState.selectedModel = modelName;
  localStorage.setItem("coworker_selected_model", modelName);
  const select = $("chatModelSelect");
  if (select) {
    select.value = modelName;
  }
  if ($("chatStatusText")) {
    $("chatStatusText").textContent = `Active Model: ${modelName}`;
  }
  if ($("healthChatModel")) {
    $("healthChatModel").textContent = modelName;
  }
  renderModelsDialog();
}

async function confirmDeleteModel(modelName) {
  const confirmed = await showConfirmPopup({
    title: "Delete Local Model",
    message: `Are you sure you want to delete model '${modelName}' from Ollama local storage?`,
    confirmText: "Delete Model",
    confirmVariant: "danger"
  });
  if (!confirmed) {
    return;
  }

  try {
    const res = await authFetch(`/api/models/${encodeURIComponent(modelName)}`, {
      method: "DELETE",
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Failed to delete model");
    }

    if (modelsState.selectedModel === modelName) {
      selectActiveModel(modelsState.defaultModel);
    }
    await loadModels();
    if ($("chatStatusText")) {
      $("chatStatusText").textContent = `Deleted model ${modelName}`;
    }
  } catch (err) {
    showWarningPopup({
      title: "Model Deletion Failed",
      message: "Could not delete model: " + err.message,
      type: "error"
    });
  }
}

async function triggerModelPull(modelName) {
  if (modelsState.isPulling) {
    showWarningPopup({
      title: "Download in Progress",
      message: "Another model is currently downloading. Please wait.",
      type: "warning"
    });
    return;
  }
  openModelsModal();
  await pullModelStream(modelName);
}

async function pullModelStream(modelName) {
  const progressWrap = $("modelPullProgressWrap");
  const modelNameEl = $("pullModelName");
  const percentEl = $("pullModelPercent");
  const barEl = $("pullModelProgressBar");
  const statusEl = $("pullModelStatusText");
  const bytesEl = $("pullModelBytesText");

  modelsState.isPulling = true;
  if (progressWrap) progressWrap.style.display = "flex";
  if (modelNameEl) modelNameEl.textContent = `Downloading ${modelName}…`;
  if (percentEl) percentEl.textContent = "0%";
  if (barEl) {
    barEl.style.width = "0%";
    barEl.style.backgroundColor = "";
  }
  if (statusEl) statusEl.textContent = "Starting download from Ollama…";
  if (bytesEl) bytesEl.textContent = "";

  try {
    const response = await fetch("/api/models/pull", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: modelName }),
    });

    if (!response.ok) {
      throw new Error(`Server returned HTTP ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop() || "";

      for (const block of lines) {
        const line = block.trim();
        if (!line.startsWith("data: ")) continue;
        const jsonStr = line.slice(6);
        try {
          const ev = JSON.parse(jsonStr);

          if (ev.status === "error") {
            throw new Error(ev.error || "Ollama download error");
          }

          if (ev.percent !== undefined) {
            if (percentEl) percentEl.textContent = `${ev.percent}%`;
            if (barEl) barEl.style.width = `${ev.percent}%`;
          }

          if (ev.status) {
            if (statusEl) statusEl.textContent = ev.status;
          }

          if (ev.completed_str && ev.total_str) {
            if (bytesEl) bytesEl.textContent = `${ev.completed_str} / ${ev.total_str}`;
          }

          if (ev.status === "done" || ev.status === "success") {
            if (percentEl) percentEl.textContent = "100%";
            if (barEl) barEl.style.width = "100%";
            if (statusEl) statusEl.textContent = "Model installed successfully!";
          }
        } catch (jsonErr) {
          if (jsonErr.message && !jsonErr.message.includes("JSON")) {
            throw jsonErr;
          }
        }
      }
    }

    // Refresh model catalog and select the newly pulled model
    await loadModels();
    selectActiveModel(modelName);

    setTimeout(() => {
      if (progressWrap) progressWrap.style.display = "none";
    }, 2500);

    if ($("chatStatusText")) {
      $("chatStatusText").textContent = `Model ${modelName} installed & active!`;
    }
  } catch (err) {
    if (statusEl) statusEl.textContent = `Error: ${err.message}`;
    if (barEl) barEl.style.backgroundColor = "#ef4444";
    showWarningPopup({
      title: "Model Download Failed",
      message: `Failed to pull model '${modelName}': ${err.message}`,
      type: "error"
    });
  } finally {
    modelsState.isPulling = false;
  }
}

function initModelsDialog() {
  const openBtn = $("openModelManagerBtn");
  if (openBtn) {
    openBtn.addEventListener("click", () => openModelsModal());
  }

  const closeBtn = $("closeModelsDialogBtn");
  if (closeBtn) {
    closeBtn.addEventListener("click", () => $("modelsDialog").close());
  }

  const doneBtn = $("doneModelsDialogBtn");
  if (doneBtn) {
    doneBtn.addEventListener("click", () => $("modelsDialog").close());
  }

  const customPullBtn = $("customModelPullBtn");
  const customInput = $("customModelNameInput");
  if (customPullBtn && customInput) {
    const handleCustomPull = () => {
      const name = customInput.value.trim();
      if (!name) {
        showWarningPopup({
          title: "Model Name Required",
          message: "Please enter a model name (e.g. 'mistral:7b', 'phi4', 'tinyllama').",
          type: "warning"
        });
        return;
      }
      customInput.value = "";
      triggerModelPull(name);
    };

    customPullBtn.addEventListener("click", handleCustomPull);
    customInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleCustomPull();
      }
    });
  }

  // Delegate actions on model cards
  const modelsDialog = $("modelsDialog");
  if (modelsDialog) {
    modelsDialog.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-action]");
      if (!btn) return;
      const action = btn.dataset.action;
      const model = btn.dataset.model;
      if (action === "select") {
        selectActiveModel(model);
      } else if (action === "pull") {
        triggerModelPull(model);
      } else if (action === "delete") {
        confirmDeleteModel(model);
      }
    });
  }

  // Handle dropdown selection changes
  const modelSelect = $("chatModelSelect");
  if (modelSelect) {
    modelSelect.addEventListener("change", (e) => {
      const val = e.target.value;
      if (val.startsWith("__pull:")) {
        const pullModelName = val.slice(7);
        // Reset dropdown to currently selected model
        modelSelect.value = modelsState.selectedModel;
        triggerModelPull(pullModelName);
      } else if (val === "__manage_models__") {
        modelSelect.value = modelsState.selectedModel;
        openModelsModal();
      } else if (val) {
        selectActiveModel(val);
      }
    });
  }
}

/**
 * Quick file attachment from the chat toolbar
 */
function initChatAttach() {
  const chatAttachBtn = $("chatAttachBtn");
  const chatFileInput = $("chatFileInput");
  if (!chatAttachBtn || !chatFileInput) return;

  chatAttachBtn.addEventListener("click", () => {
    if (!currentUser) {
      $("chatStatusText").textContent = "Please sign in to attach documents.";
      openAuthModal("login");
      return;
    }
    chatFileInput.click();
  });

  chatFileInput.addEventListener("change", async () => {
    const files = chatFileInput.files;
    if (!files || !files.length) return;

    if (!currentUser) {
      $("chatStatusText").textContent = "Please sign in to attach documents.";
      openAuthModal("login");
      return;
    }

    const form = new FormData();
    form.append("workspace", getWorkspace());
    for (const file of files) {
      form.append("files", file);
    }

    $("chatStatusText").textContent = "Indexing attached document(s)…";
    chatAttachBtn.disabled = true;

    try {
      const response = await authFetch("/api/upload", { method: "POST", body: form });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Upload failed");

      const count = (data.uploaded || []).length;
      $("chatStatusText").textContent = `Attached & indexed ${count} document(s).`;
      chatFileInput.value = "";

      await Promise.all([
        loadFiles(),
        loadHealth(),
        refreshStarterChips(),
      ]);
      loadWorkspaces(getWorkspace()).catch(() => {});
    } catch (err) {
      $("chatStatusText").textContent = "Attach error: " + err.message;
    } finally {
      chatAttachBtn.disabled = false;
    }
  });
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
  // Ensure every page refresh starts a new, empty conversation
  ensureFreshSessionOnLoad();

  initTheme();
  initPopupDialogs();
  initVoiceDictation();
  initCitationDialog();
  initPreviewDialog();
  initExportDialog();
  initModelsDialog();
  initChatAttach();
  await checkAuthStatus();
  await loadWorkspaces();
  updateChatActionButtons();
  await Promise.all([
    loadFiles(),
    loadHealth(),
    loadModels(),
    refreshStarterChips(),
    currentUser ? loadConversations() : Promise.resolve(),
    // Don't auto-load last conversation history on refresh —
    // always start with a fresh welcome screen
  ]);
})();
