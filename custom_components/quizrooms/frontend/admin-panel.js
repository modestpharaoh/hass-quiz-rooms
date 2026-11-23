/* Home Assistant Sidebar Admin Panel Web Component (<quizrooms-admin-panel>)
   Authenticated via hass.fetchWithAuth and hass.callService.
   Includes Live Room Monitor, TV Remote Control, Pack Importer (.json/.zip), and Light/Dark/Auto theme pill. */

const THEME_KEY = "quizrooms_theme";

class QuizRoomsAdminPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._state = null;
    this._pollTimer = null;
    this._themeMode = this._loadThemeMode();
    this._uploadStatus = null;
    this._pendingFile = null;
    this._overwrite = false;
    this._showDocs = false;
    this._stageDraft = null;
    this._stageDirty = false;
    this._stageRendered = false;
    this._lastStageSig = null;
    this._lastPacksSig = null;
    this._lightFilter = "";
    this._mql = window.matchMedia("(prefers-color-scheme: dark)");
    this._onMediaChange = () => this._applyTheme();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._renderShell();
      this._applyTheme();
      this._fetchState();
      this._startPolling();
    }
  }

  _startPolling() {
    if (!this._pollTimer && this._hass) {
      this._pollTimer = setInterval(() => this._fetchState(), 4000);
    }
  }

  connectedCallback() {
    if (this._mql.addEventListener) this._mql.addEventListener("change", this._onMediaChange);
    this._startPolling();
  }

  disconnectedCallback() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
    if (this._bannerTimer) {
      clearTimeout(this._bannerTimer);
      this._bannerTimer = null;
    }
    if (this._mql.removeEventListener) this._mql.removeEventListener("change", this._onMediaChange);
  }

  _loadThemeMode() {
    try {
      const saved = localStorage.getItem(THEME_KEY);
      return ["auto", "light", "dark"].includes(saved) ? saved : "auto";
    } catch (_) {
      return "auto";
    }
  }

  _setThemeMode(mode) {
    this._themeMode = mode;
    try { localStorage.setItem(THEME_KEY, mode); } catch (_) {}
    this._applyTheme();
  }

  _applyTheme() {
    const host = this.shadowRoot && this.shadowRoot.getElementById("panel-root");
    if (!host) return;
    let resolved = this._themeMode;
    if (resolved === "auto") {
      const haDark = this._hass && this._hass.themes && this._hass.themes.darkMode;
      resolved = typeof haDark === "boolean" ? (haDark ? "dark" : "light") : (this._mql.matches ? "dark" : "light");
    }
    host.dataset.theme = resolved;
    this.shadowRoot.querySelectorAll(".theme-pill button").forEach((btn) => {
      btn.setAttribute("aria-pressed", String(btn.dataset.mode === this._themeMode));
    });
  }

  async _fetchState(opts = {}) {
    if (!this._hass) return;
    try {
      const resp = await this._hass.fetchWithAuth("/quizrooms/api/admin/state");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      this._state = await resp.json();
      if (this._state && this._state.stage_effects && this._state.stage_effects.config) {
        if (!this._stageDirty || !this._stageDraft) {
          this._stageDraft = JSON.parse(JSON.stringify(this._state.stage_effects.config));
        }
      }
      this._renderContent(opts);
    } catch (err) {
      console.error("QuizRooms Admin fetch failed", err);
    }
  }

  _isInteractingInside(container) {
    if (!container || !this.shadowRoot) return false;
    const active = this.shadowRoot.activeElement;
    if (!active || !container.contains(active)) return false;
    const tag = (active.tagName || "").toUpperCase();
    return tag === "SELECT" || tag === "INPUT" || tag === "TEXTAREA";
  }

  _markStageDirty() {
    this._stageDirty = true;
    const badge = this.shadowRoot && this.shadowRoot.getElementById("stage-sync-badge");
    if (badge) {
      badge.textContent = "Unsaved Changes";
      badge.className = "badge badge-warn";
    }
  }

  async _saveStageConfig() {
    if (!this._stageDraft) return;
    try {
      const resp = await this._hass.fetchWithAuth("/quizrooms/api/admin/stage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "save", config: this._stageDraft }),
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        this._setBanner(body.message || "Failed to save stage effects settings", true);
      } else {
        this._stageDirty = false;
        if (body.config) this._stageDraft = JSON.parse(JSON.stringify(body.config));
        this._setBanner(body.message || "Smart-Home Stage Effects settings saved.", false);
      }
      await this._fetchState({ forceStage: true });
    } catch (err) {
      this._setBanner("Failed to save stage settings: " + err.message, true);
    }
  }

  async _testStageEffect(effect, btnEl = null) {
    if (!effect) return;
    const origLabel = btnEl ? btnEl.textContent : "";
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.textContent = "Running…";
    }
    try {
      const payload = { action: "test", effect };
      if (this._stageDraft) payload.config = this._stageDraft;
      const resp = await this._hass.fetchWithAuth("/quizrooms/api/admin/stage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        this._setBanner(body.message || `Stage test '${effect}' failed`, true);
      } else {
        this._stageDirty = false;
        if (body.config) this._stageDraft = JSON.parse(JSON.stringify(body.config));
        const badge = this.shadowRoot && this.shadowRoot.getElementById("stage-sync-badge");
        if (badge) {
          badge.textContent = "Synced";
          badge.className = "badge badge-ok";
        }
        this._setBanner(body.message || `Triggered stage effect: ${effect}`, false);
      }
    } catch (err) {
      this._setBanner("Stage test failed: " + err.message, true);
    } finally {
      if (btnEl) {
        btnEl.disabled = false;
        btnEl.textContent = origLabel;
      }
    }
  }

  async _roomAction(code, action) {
    try {
      const resp = await this._hass.fetchWithAuth(`/quizrooms/api/admin/rooms/${encodeURIComponent(code)}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        this._setBanner(body.message || `Action '${action}' failed`, true);
      } else {
        this._setBanner(`Room ${code}: ${action} applied.`, false);
      }
      await this._fetchState();
    } catch (err) {
      this._setBanner(String(err), true);
    }
  }

  async _setTVMode(mode, roomCode = null, theme = null) {
    try {
      const payload = { mode };
      if (roomCode) payload.room_code = roomCode;
      if (theme) payload.theme = theme;
      const resp = await this._hass.fetchWithAuth("/quizrooms/api/admin/tv", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        this._setBanner(body.message || "Failed to update TV mode", true);
      } else {
        this._setBanner(`TV mode set to ${mode}${roomCode ? ` (${roomCode})` : ""}.`, false);
      }
      await this._fetchState();
    } catch (err) {
      this._setBanner(String(err), true);
    }
  }

  async _openOnTV(entityId, btnEl = null) {
    if (!entityId) return;
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.textContent = "Opening…";
    }
    try {
      const resp = await this._hass.fetchWithAuth("/quizrooms/api/admin/tv/open", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entity_id: entityId }),
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        this._setBanner(body.message || `Failed to open TV dashboard on ${entityId}`, true);
      } else {
        this._setBanner(body.message || `Opened TV dashboard on ${entityId}.`, false);
      }
      await this._fetchState();
    } catch (err) {
      this._setBanner("Failed to open on TV: " + err.message, true);
      if (btnEl) {
        btnEl.disabled = false;
        btnEl.textContent = "Open Dashboard";
      }
    }
  }

  async _setMaxRooms(val) {
    try {
      await this._hass.callService("number", "set_value", {
        entity_id: "number.quizrooms_max_rooms",
        value: Number(val),
      });
      this._setBanner(`Max rooms updated to ${val}.`, false);
      await this._fetchState();
    } catch (err) {
      this._setBanner("Failed to update max rooms: " + err.message, true);
    }
  }

  async _reloadPacks() {
    try {
      await this._hass.callService("quizrooms", "reload_packs", {});
      this._setBanner("Trivia packs reloaded from disk.", false);
      await this._fetchState();
    } catch (err) {
      this._setBanner("Reload failed: " + err.message, true);
    }
  }

  async _deletePack(packId) {
    if (!confirm(`Delete custom pack "${packId}"? Active games using it will keep their snapshot.`)) return;
    try {
      const resp = await this._hass.fetchWithAuth(`/quizrooms/api/admin/packs/${encodeURIComponent(packId)}`, {
        method: "DELETE",
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        this._setBanner(body.message || "Delete failed", true);
      } else {
        this._setBanner(`Deleted pack "${packId}".`, false);
      }
      await this._fetchState();
    } catch (err) {
      this._setBanner(String(err), true);
    }
  }

  async _downloadTemplate() {
    try {
      const resp = await this._hass.fetchWithAuth("/quizrooms/api/admin/packs/template");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "quizrooms-sample-pack.json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      this._setBanner("Template download failed: " + err.message, true);
    }
  }

  async _uploadPack(file, overwrite = false) {
    if (!file) return;
    this._pendingFile = file;
    this._uploadStatus = { ok: null, message: `Uploading ${file.name}…`, details: [] };
    this._renderUploadStatus();

    try {
      const fd = new FormData();
      fd.append("overwrite", overwrite ? "true" : "false");
      fd.append("file", file, file.name);
      const resp = await this._hass.fetchWithAuth(
        `/quizrooms/api/admin/packs/import?overwrite=${overwrite ? "true" : "false"}`,
        { method: "POST", body: fd }
      );
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        this._uploadStatus = {
          ok: false,
          code: body.error,
          message: body.message || "Import validation failed.",
          details: body.details || [],
        };
      } else {
        this._pendingFile = null;
        this._uploadStatus = {
          ok: true,
          message: `Imported "${body.pack.title}" (${body.pack.question_count} questions) and registered live!`,
          details: [],
        };
        await this._fetchState();
      }
    } catch (err) {
      this._uploadStatus = { ok: false, message: String(err), details: [] };
    }
    this._renderUploadStatus();
  }

  _setBanner(text, isError) {
    const el = this.shadowRoot && this.shadowRoot.getElementById("admin-banner");
    if (!el) return;
    el.textContent = text;
    el.className = "banner " + (isError ? "error" : "success");
    el.style.display = "block";
    clearTimeout(this._bannerTimer);
    this._bannerTimer = setTimeout(() => { el.style.display = "none"; }, 4500);
  }

  _esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; min-height: 100vh; font-family: system-ui, -apple-system, sans-serif; }
        #panel-root {
          --bg: #f7f5f0; --card: #ffffff; --card2: #f0ece3; --text: #141926; --muted: #556076;
          --border: rgba(20,25,38,0.12); --accent: #4338ca; --accent-ink: #ffffff;
          --ok: #059669; --warn: #d97706; --err: #e11d48;
          min-height: 100vh; padding: 22px; background: var(--bg); color: var(--text); box-sizing: border-box;
        }
        #panel-root[data-theme="dark"] {
          --bg: #121214; --card: #1c1c1f; --card2: #26262a; --text: #f4f4f5; --muted: #a1a1aa;
          --border: rgba(255,255,255,0.12); --accent: #2dd4bf; --accent-ink: #0f1212;
          --ok: #34d399; --warn: #fbbf24; --err: #fb7185;
        }
        .wrap { max-width: 1120px; margin-inline: auto; }
        .header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }
        .title-row { display: flex; align-items: center; gap: 12px; }
        .logo { width: 44px; height: 44px; display: block; flex: none; filter: drop-shadow(0 4px 10px rgba(0,0,0,0.28)); }
        h1 { margin: 0; font-size: 1.45rem; }
        h2 { margin: 0 0 12px; font-size: 1.1rem; }
        .sub { margin: 0; font-size: 0.85rem; color: var(--muted); }
        .theme-pill { display: inline-flex; padding: 3px; border-radius: 999px; background: var(--card2); border: 1px solid var(--border); gap: 2px; }
        .theme-pill button { border: none; background: transparent; color: var(--muted); font-weight: 700; font-size: 0.78rem; padding: 6px 12px; border-radius: 999px; cursor: pointer; }
        .theme-pill button[aria-pressed="true"] { background: var(--accent); color: var(--accent-ink); }
        .grid-kpi { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-bottom: 18px; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 18px; margin-bottom: 16px; }
        .kpi-val { font-size: 1.8rem; font-weight: 800; color: var(--accent); }
        .kpi-lbl { font-size: 0.82rem; color: var(--muted); font-weight: 600; }
        .row { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
        .btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 14px; border-radius: 10px; border: 1px solid var(--border); background: var(--card2); color: var(--text); font-weight: 700; font-size: 0.85rem; cursor: pointer; text-decoration: none; }
        .btn-primary { background: var(--accent); color: var(--accent-ink); border-color: transparent; }
        .btn-danger { color: var(--err); border-color: var(--err); background: transparent; }
        .btn:hover { filter: brightness(1.06); }
        .list { display: grid; gap: 10px; }
        .item { padding: 12px 14px; border-radius: 12px; background: var(--card2); border: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
        .badge { display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 0.75rem; font-weight: 700; background: var(--card); border: 1px solid var(--border); color: var(--muted); }
        .badge-ok { color: var(--ok); border-color: var(--ok); }
        .badge-warn { color: var(--warn); border-color: var(--warn); }
        .dropzone { border: 2px dashed var(--border); border-radius: 14px; padding: 26px 16px; text-align: center; cursor: pointer; background: var(--card2); transition: border-color 0.15s; }
        .dropzone.drag { border-color: var(--accent); }
        .banner { display: none; padding: 12px 16px; border-radius: 12px; margin-bottom: 14px; font-weight: 700; font-size: 0.9rem; }
        .banner.success { background: rgba(5,150,105,0.14); color: var(--ok); border: 1px solid var(--ok); }
        .banner.error { background: rgba(225,29,72,0.14); color: var(--err); border: 1px solid var(--err); }
        .input { padding: 8px 10px; border-radius: 10px; border: 1px solid var(--border); background: var(--card2); color: var(--text); font-weight: 700; }
        pre { background: var(--card2); padding: 12px; border-radius: 10px; overflow-x: auto; font-size: 0.8rem; border: 1px solid var(--border); }
        .upload-box { margin-top: 12px; padding: 12px; border-radius: 12px; border: 1px solid var(--border); background: var(--card2); }
        .upload-box.err { border-color: var(--err); color: var(--err); }
        .upload-box.ok { border-color: var(--ok); color: var(--ok); }
        .player-pills { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
        .player-pill { display: inline-flex; align-items: center; gap: 6px; padding: 3px 9px 3px 4px; border-radius: 999px; background: var(--card); border: 1px solid var(--border); font-size: 0.78rem; font-weight: 700; }
        .player-pill img { width: 22px; height: 22px; border-radius: 50%; object-fit: cover; display: block; }
        .pack-thumb { width: 40px; height: 40px; border-radius: 10px; object-fit: cover; flex: none; border: 1px solid var(--border); background: var(--card); display: block; }
        .stage-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 14px; margin-top: 12px; }
        .stage-box { padding: 14px; border-radius: 12px; background: var(--card2); border: 1px solid var(--border); display: flex; flex-direction: column; gap: 10px; }
        .stage-lock { padding: 14px; border-radius: 12px; border: 1px solid var(--warn); background: rgba(217,119,6,0.12); color: var(--text); font-size: 0.88rem; line-height: 1.5; }
        .light-filter-row { display: flex; gap: 6px; align-items: center; }
        .light-filter-input { flex: 1; padding: 7px 10px; border-radius: 10px; border: 1px solid var(--border); background: var(--card); color: var(--text); font-size: 0.82rem; font-weight: 600; }
        .chip-list { display: flex; flex-wrap: wrap; gap: 6px; max-height: 210px; overflow-y: auto; padding: 4px 2px; overscroll-behavior: contain; }
        .light-chip { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border-radius: 999px; border: 1px solid var(--border); background: var(--card); font-size: 0.78rem; font-weight: 700; cursor: pointer; user-select: none; transition: border-color 0.12s, background 0.12s; }
        .light-chip.selected { border-color: var(--accent); background: rgba(45,212,191,0.16); box-shadow: inset 0 0 0 1px rgba(45,212,191,0.22); }
        .light-chip .pin-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); display: inline-block; flex: none; }
        .field-lbl { display: flex; flex-direction: column; gap: 4px; font-size: 0.8rem; font-weight: 700; color: var(--muted); }
        .check-lbl { display: inline-flex; align-items: center; gap: 8px; font-size: 0.84rem; font-weight: 700; cursor: pointer; }
        .vol-card { padding: 10px 12px; border-radius: 10px; background: var(--card); border: 1px solid var(--border); display: flex; flex-direction: column; gap: 9px; }
        .vol-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
        .vol-val-pill { font-family: monospace; font-size: 0.82rem; font-weight: 800; padding: 2px 9px; border-radius: 999px; background: var(--card2); border: 1px solid var(--border); color: var(--muted); }
        .vol-val-pill.active { color: var(--accent); border-color: var(--accent); background: rgba(45,212,191,0.12); }
        .vol-slider-wrap { display: flex; align-items: center; gap: 10px; }
        .vol-slider { -webkit-appearance: none; appearance: none; flex: 1; height: 7px; border-radius: 999px; background: var(--border); outline: none; cursor: pointer; }
        .vol-slider::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; width: 18px; height: 18px; border-radius: 50%; background: var(--accent); border: 2px solid var(--card); box-shadow: 0 2px 6px rgba(0,0,0,0.3); cursor: pointer; }
        .vol-slider::-moz-range-thumb { width: 18px; height: 18px; border-radius: 50%; background: var(--accent); border: 2px solid var(--card); box-shadow: 0 2px 6px rgba(0,0,0,0.3); cursor: pointer; }
        .vol-presets { display: flex; gap: 5px; flex-wrap: wrap; }
        .vol-preset-btn { padding: 3px 9px; border-radius: 999px; border: 1px solid var(--border); background: var(--card2); color: var(--text); font-size: 0.72rem; font-weight: 700; cursor: pointer; }
        .vol-preset-btn.active { background: var(--accent); color: var(--accent-ink); border-color: transparent; }
      </style>
      <div id="panel-root" data-theme="light">
        <div class="wrap">
          <div class="header">
            <div class="title-row">
              <svg class="logo" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="44" height="44" role="img" aria-label="QuizRooms Logo">
                <defs>
                  <linearGradient id="qr-admin-bg" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#242428"/><stop offset="100%" stop-color="#141417"/></linearGradient>
                  <linearGradient id="qr-admin-ring" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#2DD4BF"/><stop offset="55%" stop-color="#34D399"/><stop offset="100%" stop-color="#FBBF24"/></linearGradient>
                  <linearGradient id="qr-admin-spark" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#FDE68A"/><stop offset="100%" stop-color="#F59E0B"/></linearGradient>
                </defs>
                <rect x="6" y="6" width="116" height="116" rx="28" fill="url(#qr-admin-bg)" stroke="url(#qr-admin-ring)" stroke-width="5"/>
                <circle cx="60" cy="60" r="29" fill="none" stroke="url(#qr-admin-ring)" stroke-width="11"/>
                <path d="M74 74 L95 95" stroke="url(#qr-admin-spark)" stroke-width="11.5" stroke-linecap="round"/>
                <path d="M52 52 C52 46 56 43 61 43 C66 43 69 46 69 50 C69 54 65 56 62 58 C60.5 59 60 60.5 60 63" fill="none" stroke="#F4F4F5" stroke-width="6.5" stroke-linecap="round"/>
                <circle cx="60" cy="72.5" r="4" fill="#FBBF24"/>
                <path d="M97 22 L99.5 29.5 L107 32 L99.5 34.5 L97 42 L94.5 34.5 L87 32 L94.5 29.5 Z" fill="url(#qr-admin-spark)"/>
              </svg>
              <div>
                <h1>QuizRooms Admin</h1>
                <p class="sub">Local-First Multi-Room Trivia Control Center</p>
              </div>
            </div>
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
              <a class="btn" href="/quizrooms/play" target="_blank" rel="noopener">Open Player Lobby ↗</a>
              <a class="btn" href="/quizrooms/tv" target="_blank" rel="noopener">Open TV Dashboard ↗</a>
              <div class="theme-pill" role="group" aria-label="Theme">
                <button type="button" data-mode="auto">Auto</button>
                <button type="button" data-mode="light">Light</button>
                <button type="button" data-mode="dark">Dark</button>
              </div>
            </div>
          </div>
          <div id="admin-banner" class="banner"></div>
          <div id="admin-live-mount"><div class="card">Loading QuizRooms state…</div></div>
          <div id="admin-stage-mount"></div>
          <div id="admin-packs-mount"></div>
        </div>
      </div>
    `;

    this.shadowRoot.querySelectorAll(".theme-pill button").forEach((btn) => {
      btn.addEventListener("click", () => this._setThemeMode(btn.dataset.mode));
    });
  }

  _renderContent(opts = {}) {
    if (!this._state) return;
    this._renderLiveSection(Boolean(opts.forceLive));
    this._renderStageSection(Boolean(opts.forceStage));
    this._renderPacksSection(Boolean(opts.forcePacks));
  }

  _renderLiveSection(force = false) {
    const mount = this.shadowRoot && this.shadowRoot.getElementById("admin-live-mount");
    const st = this._state;
    if (!mount || !st) return;
    if (!force && this._isInteractingInside(mount)) return;

    const roomsHtml = st.rooms.length
      ? `<div class="list">${st.rooms
          .map(
            (r) => `
          <div class="item">
            <div style="display:flex;align-items:flex-start;gap:12px;">
              ${r.cover_url ? `<img src="${this._esc(r.cover_url)}" alt="" class="pack-thumb" />` : ""}
              <div>
                <strong style="font-family:monospace;font-size:1rem;color:var(--accent);">${this._esc(r.code)}</strong>
                <strong style="margin-inline:8px;" dir="auto">${this._esc(r.name)}</strong>
                <span class="badge">${this._esc(r.visibility)}</span>
                <span class="badge ${r.status === "lobby" ? "badge-ok" : "badge-warn"}">
                  ${this._esc(r.status)}${r.paused ? " (PAUSED)" : ""} · R${r.current_round}/${r.total_rounds}
                </span>
                <span class="badge">${r.connected_count}/${r.player_count} online</span>
                ${r.unique_answers_only ? `<span class="badge badge-warn">No Repeats</span>` : ""}
                ${
                  r.players && r.players.length
                    ? `<div class="player-pills">${r.players
                        .map(
                          (p) => `<span class="player-pill" style="${p.connected ? "" : "opacity:0.5;"}">
                            ${p.avatar_url ? `<img src="${this._esc(p.avatar_url)}" alt="" />` : ""}
                            <span dir="auto">${this._esc(p.nickname)}${p.is_host ? " ★" : ""}</span>
                            <span style="color:var(--accent);font-family:monospace;">${p.score}</span>
                          </span>`
                        )
                        .join("")}</div>`
                    : ""
                }
              </div>
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
              ${r.status === "lobby" ? `<button class="btn" data-act="start" data-code="${this._esc(r.code)}">Start</button>` : ""}
              ${r.status === "in_progress" ? `<button class="btn" data-act="${r.paused ? "resume" : "pause"}" data-code="${this._esc(r.code)}">${r.paused ? "Resume" : "Pause"}</button>` : ""}
              ${r.status === "in_progress" ? `<button class="btn" data-act="next" data-code="${this._esc(r.code)}">Advance</button>` : ""}
              <button class="btn" data-tv-follow="${this._esc(r.code)}">Follow on TV</button>
              <button class="btn btn-danger" data-act="close" data-code="${this._esc(r.code)}">Force Close</button>
            </div>
          </div>`
          )
          .join("")}</div>`
      : `<p class="sub">No active game rooms right now.</p>`;

    const tvTargets = Array.isArray(st.tv_targets) ? st.tv_targets : [];
    const tvDashboardUrl = `${st.lan_base_url || window.location.origin}/quizrooms/tv`;
    const tvTargetsHtml = tvTargets.length
      ? `<div class="list" style="margin-top:10px;">${tvTargets
          .map(
            (t) => `
          <div class="item">
            <div>
              <strong>${this._esc(t.name)}</strong>
              <span class="badge" style="margin-inline-start:6px;">${this._esc(t.platform_label)}</span>
              <span class="badge ${t.is_on ? "badge-ok" : ""}">${this._esc(t.state)}</span>
              <div class="sub" style="margin-top:3px;font-family:monospace;font-size:0.76rem;">
                ${this._esc(t.entity_id)} · ${this._esc(t.launch_method)}
              </div>
            </div>
            <button class="btn btn-primary" data-open-tv="${this._esc(t.entity_id)}">Open Dashboard</button>
          </div>`
          )
          .join("")}</div>`
      : `<p class="sub" style="margin-top:8px;">No connected supported Smart TV media players detected right now. Supported Home Assistant integrations: <code>webostv</code> (LG webOS), <code>androidtv</code> / <code>androidtv_remote</code> (Android TV / Google TV with browser), and <code>samsungtv</code> / <code>samsungtv_smart</code> (Samsung Smart TV).</p>`;

    const roomOptions = st.rooms
      .map((r) => `<option value="${this._esc(r.code)}" ${st.tv.room_code === r.code ? "selected" : ""}>${this._esc(r.code)} — ${this._esc(r.name)}</option>`)
      .join("");

    mount.innerHTML = `
      <div class="grid-kpi">
        <div class="card">
          <div class="kpi-val">${st.active_rooms} / ${st.max_rooms}</div>
          <div class="kpi-lbl">Active Rooms / Max Limit</div>
        </div>
        <div class="card">
          <div class="kpi-val">${st.active_players}</div>
          <div class="kpi-lbl">Connected Players</div>
        </div>
        <div class="card">
          <div class="kpi-val">${st.packs.length}</div>
          <div class="kpi-lbl">Loaded Trivia Packs</div>
        </div>
        <div class="card">
          <div class="kpi-val" style="font-size:1.2rem;">${st.allow_external_access ? "External Allowed" : "LAN Only"}</div>
          <div class="kpi-lbl">Network Guard Policy</div>
        </div>
      </div>

      <section class="card">
        <div class="row" style="margin-bottom:12px;">
          <h2 style="margin:0;">Active Rooms</h2>
          <div style="display:flex;align-items:center;gap:8px;">
            <label for="max-rooms-input" class="sub">Max Rooms (1–50):</label>
            <input id="max-rooms-input" type="number" min="1" max="50" value="${st.max_rooms}" class="input" style="width:74px;" />
            <button id="save-max-rooms-btn" class="btn btn-primary">Apply</button>
          </div>
        </div>
        ${roomsHtml}
      </section>

      <section class="card">
        <h2>TV Dashboard Remote Control</h2>
        <div class="row">
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
            <button id="tv-mode-overview" class="btn ${st.tv.mode === "overview" ? "btn-primary" : ""}">Overview Mode</button>
            <select id="tv-room-select" class="input">
              <option value="">Select room to follow…</option>
              ${roomOptions}
            </select>
            <button id="tv-mode-follow" class="btn ${st.tv.mode === "follow" ? "btn-primary" : ""}">Follow Selected</button>
          </div>
          <div style="display:flex;gap:6px;align-items:center;">
            <span class="sub">Push TV Theme:</span>
            <button class="btn" data-tv-theme="auto">Auto</button>
            <button class="btn" data-tv-theme="light">Light</button>
            <button class="btn" data-tv-theme="dark">Dark</button>
          </div>
        </div>

        <div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--border);">
          <div class="row">
            <div>
              <strong style="font-size:0.96rem;">Open TV Dashboard on Connected Smart TVs (${tvTargets.length})</strong>
              <div class="sub">Launches <code>${this._esc(tvDashboardUrl)}</code> directly on supported LG webOS, Android TV / Google TV, and Samsung Smart TVs.</div>
            </div>
            <button id="refresh-tvs-btn" class="btn">Refresh TVs</button>
          </div>
          ${tvTargetsHtml}
        </div>
      </section>
    `;

    mount.querySelectorAll("[data-act]").forEach((btn) => {
      btn.addEventListener("click", () => this._roomAction(btn.dataset.code, btn.dataset.act));
    });
    mount.querySelectorAll("[data-tv-follow]").forEach((btn) => {
      btn.addEventListener("click", () => this._setTVMode("follow", btn.dataset.tvFollow));
    });
    mount.querySelector("#save-max-rooms-btn").addEventListener("click", () => {
      const val = mount.querySelector("#max-rooms-input").value;
      this._setMaxRooms(val);
    });
    mount.querySelector("#tv-mode-overview").addEventListener("click", () => this._setTVMode("overview"));
    mount.querySelector("#tv-mode-follow").addEventListener("click", () => {
      const code = mount.querySelector("#tv-room-select").value;
      if (!code) return this._setBanner("Select an active room first.", true);
      this._setTVMode("follow", code);
    });
    mount.querySelectorAll("[data-tv-theme]").forEach((btn) => {
      btn.addEventListener("click", () => this._setTVMode(st.tv.mode, st.tv.room_code, btn.dataset.tvTheme));
    });
    mount.querySelectorAll("[data-open-tv]").forEach((btn) => {
      btn.addEventListener("click", () => this._openOnTV(btn.dataset.openTv, btn));
    });
    const refreshTvsBtn = mount.querySelector("#refresh-tvs-btn");
    if (refreshTvsBtn) refreshTvsBtn.addEventListener("click", () => this._fetchState({ forceLive: true }));
  }

  _getStageData() {
    const st = this._state || {};
    return st.stage_effects || {
      available: !st.allow_external_access,
      config: {
        enabled: false,
        only_tv_followed_room: true,
        light_entities: [],
        light_preset: "studio_showdown",
        reveal_green_rule: "any_correct",
        restore_lights_on_end: true,
        speaker_entity: "",
        tts_entity: "",
        speaker_preset: "winner_and_finale",
        speaker_language: "auto",
        speaker_volume_enabled: false,
        speaker_volume: 0.6,
      },
      lights: [],
      speakers: [],
      tts_engines: [],
    };
  }

  _renderStageLightsList() {
    const container = this.shadowRoot && this.shadowRoot.getElementById("stage-lights-list");
    const countEl = this.shadowRoot && this.shadowRoot.getElementById("stage-lights-count");
    if (!container) return;

    const stage = this._getStageData();
    const draft = this._stageDraft || stage.config;
    const selectedLights = new Set(Array.isArray(draft.light_entities) ? draft.light_entities : []);
    if (countEl) countEl.textContent = String(selectedLights.size);

    const prevScrollTop = container.scrollTop;
    const filterTerm = (this._lightFilter || "").trim().toLowerCase();

    const allLights = Array.isArray(stage.lights) ? stage.lights : [];
    // Sort selected lights to the top, then preserve RGB -> Dimmer -> alphabetical order
    const ordered = allLights
      .map((item, idx) => ({ item, idx, selected: selectedLights.has(item.entity_id) }))
      .filter(({ item }) => {
        if (!filterTerm) return true;
        return (
          String(item.name || "").toLowerCase().includes(filterTerm) ||
          String(item.entity_id || "").toLowerCase().includes(filterTerm)
        );
      })
      .sort((a, b) => {
        if (a.selected !== b.selected) return a.selected ? -1 : 1;
        return a.idx - b.idx;
      });

    if (!allLights.length) {
      container.innerHTML = `<p class="sub">No connected <code>light.*</code> entities detected in Home Assistant.</p>`;
      return;
    }
    if (!ordered.length) {
      container.innerHTML = `<p class="sub">No lights match "${this._esc(this._lightFilter)}".</p>`;
      return;
    }

    container.innerHTML = ordered
      .map(({ item: l, selected }) => {
        const cap = l.supports_color ? "RGB" : (l.supports_brightness ? "Dimmer" : "Switch");
        return `<label class="light-chip ${selected ? "selected" : ""}" title="${this._esc(l.entity_id)}">
          <input type="checkbox" data-stage-light="${this._esc(l.entity_id)}" ${selected ? "checked" : ""} />
          ${selected ? `<span class="pin-dot" aria-hidden="true"></span>` : ""}
          <span>${this._esc(l.name)}</span>
          <span class="badge" style="padding:1px 6px;font-size:0.68rem;">${cap}</span>
        </label>`;
      })
      .join("");

    container.scrollTop = prevScrollTop;

    container.querySelectorAll("[data-stage-light]").forEach((cb) => {
      cb.addEventListener("change", () => {
        const eid = cb.dataset.stageLight;
        const nextSet = new Set(Array.isArray(draft.light_entities) ? draft.light_entities : []);
        if (cb.checked) nextSet.add(eid);
        else nextSet.delete(eid);
        draft.light_entities = Array.from(nextSet);
        this._stageDraft = draft;
        this._markStageDirty();
        this._renderStageLightsList();
      });
    });
  }

  _updateVolumeUI(volPct, enabled) {
    const panel = this.shadowRoot && this.shadowRoot.getElementById("stage-vol-panel");
    const pill = this.shadowRoot && this.shadowRoot.getElementById("stage-vol-pill");
    const slider = this.shadowRoot && this.shadowRoot.getElementById("stage-vol-slider");
    if (panel) panel.style.display = enabled ? "flex" : "none";
    if (pill) {
      pill.textContent = enabled ? `${volPct}%` : "Speaker Default";
      pill.className = "vol-val-pill" + (enabled ? " active" : "");
    }
    if (slider) {
      slider.value = String(volPct);
      slider.style.background = `linear-gradient(to right, var(--accent) 0%, var(--accent) ${volPct}%, var(--border) ${volPct}%, var(--border) 100%)`;
    }
    if (this.shadowRoot) {
      this.shadowRoot.querySelectorAll("[data-vol-preset]").forEach((btn) => {
        btn.classList.toggle("active", enabled && Number(btn.dataset.volPreset) === volPct);
      });
    }
  }

  _renderStageSection(force = false) {
    const mount = this.shadowRoot && this.shadowRoot.getElementById("admin-stage-mount");
    if (!mount || !this._state) return;

    const stage = this._getStageData();
    const sig = JSON.stringify({
      available: Boolean(stage.available),
      lights: (stage.lights || []).map((l) => l.entity_id),
      speakers: (stage.speakers || []).map((s) => s.entity_id),
      tts: (stage.tts_engines || []).map((t) => t.entity_id),
    });

    // Never wipe the Stage Effects form during 4-second background polls unless availability
    // or device catalog changed while the user is NOT actively editing/focused inside it.
    if (!force && this._stageRendered) {
      const availChanged = this._lastStageAvailable !== Boolean(stage.available);
      if (!availChanged && (sig === this._lastStageSig || this._stageDirty || this._isInteractingInside(mount))) {
        return;
      }
    }
    this._lastStageSig = sig;
    this._lastStageAvailable = Boolean(stage.available);
    this._stageRendered = true;

    const draft = this._stageDraft || JSON.parse(JSON.stringify(stage.config));
    this._stageDraft = draft;
    if (typeof draft.speaker_language !== "string" || !draft.speaker_language) draft.speaker_language = "auto";
    if (typeof draft.speaker_volume_enabled !== "boolean") draft.speaker_volume_enabled = false;
    const volPct = Math.round(Math.max(5, Math.min(100, Number(draft.speaker_volume || 0.6) * 100)));

    const speakerOptions = (stage.speakers || [])
      .map((s) => `<option value="${this._esc(s.entity_id)}" ${draft.speaker_entity === s.entity_id ? "selected" : ""}>${this._esc(s.name)} (${this._esc(s.entity_id)})</option>`)
      .join("");
    const ttsOptions = (stage.tts_engines || [])
      .map((t) => `<option value="${this._esc(t.entity_id)}" ${draft.tts_entity === t.entity_id ? "selected" : ""}>${this._esc(t.name)} (${this._esc(t.entity_id)})</option>`)
      .join("");

    const stageCardHtml = !stage.available
      ? `<div class="stage-lock">
          <strong>Disabled while External Access is enabled.</strong><br/>
          For home security, native Home Assistant events (<code>quizrooms_phase_changed</code>, <code>quizrooms_finale</code>) and TV-synced light/speaker Stage Effects are automatically discarded whenever <strong>Allow External Access</strong> is turned on. Disable External Access in <strong>Settings → Devices &amp; Services → QuizRooms → Configure</strong> to use LAN-only Stage Effects.
        </div>`
      : `
        <div class="row" style="margin-bottom:8px;">
          <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;">
            <label class="check-lbl">
              <input id="stage-enabled-check" type="checkbox" ${draft.enabled ? "checked" : ""} />
              Enable Built-In Stage Effects
            </label>
            <label class="check-lbl">
              <input id="stage-tv-only-check" type="checkbox" ${draft.only_tv_followed_room ? "checked" : ""} />
              Only trigger for the game currently followed on TV
            </label>
          </div>
          <div style="display:flex;gap:8px;align-items:center;">
            <span id="stage-sync-badge" class="badge ${this._stageDirty ? "badge-warn" : "badge-ok"}">${this._stageDirty ? "Unsaved Changes" : "Synced"}</span>
            <button id="stage-refresh-btn" class="btn" style="padding:5px 10px;font-size:0.78rem;">Refresh Devices</button>
          </div>
        </div>

        <div class="stage-grid">
          <div class="stage-box">
            <div class="row">
              <strong>Stage Lights (<span id="stage-lights-count">${(draft.light_entities || []).length}</span> selected)</strong>
              <span class="sub" style="font-size:0.74rem;">Selected pinned to top</span>
            </div>
            <div class="light-filter-row">
              <input id="stage-light-filter" type="search" class="light-filter-input" placeholder="Filter lights by name or entity…" value="${this._esc(this._lightFilter)}" />
            </div>
            <div id="stage-lights-list" class="chip-list"></div>
            <label class="field-lbl">
              <span>Light Choreography Preset</span>
              <select id="stage-light-preset" class="input">
                <option value="studio_showdown" ${draft.light_preset === "studio_showdown" ? "selected" : ""}>Studio Showdown (Teal · Amber 5s · Green/Red · Gold)</option>
                <option value="subtle_cue" ${draft.light_preset === "subtle_cue" ? "selected" : ""}>Subtle Cue (Soft Ambient Glows, No Flashing)</option>
                <option value="dramatic_pulse" ${draft.light_preset === "dramatic_pulse" ? "selected" : ""}>Dramatic Pulse (High-Energy Party Strobes)</option>
                <option value="off" ${draft.light_preset === "off" ? "selected" : ""}>Off (Lights Disabled)</option>
              </select>
            </label>
            <label class="field-lbl">
              <span>Flash Green on Reveal When</span>
              <select id="stage-reveal-rule" class="input">
                <option value="any_correct" ${draft.reveal_green_rule === "any_correct" ? "selected" : ""}>At Least One Player Correct</option>
                <option value="majority_correct" ${draft.reveal_green_rule === "majority_correct" ? "selected" : ""}>Majority of Players Correct</option>
                <option value="all_correct" ${draft.reveal_green_rule === "all_correct" ? "selected" : ""}>All Players Correct</option>
              </select>
            </label>
            <label class="check-lbl">
              <input id="stage-restore-check" type="checkbox" ${draft.restore_lights_on_end ? "checked" : ""} />
              Restore pre-game light state after Finale or Room Close
            </label>
          </div>

          <div class="stage-box">
            <strong>Local Speaker Announcer (Multilingual)</strong>
            <label class="field-lbl">
              <span>Speaker Media Player</span>
              <select id="stage-speaker-select" class="input">
                <option value="">None (Speaker Disabled)</option>
                ${speakerOptions}
              </select>
            </label>
            <label class="field-lbl">
              <span>TTS Engine Entity</span>
              <select id="stage-tts-select" class="input">
                <option value="">Auto-Detect Available HA TTS</option>
                ${ttsOptions}
              </select>
            </label>
            <label class="field-lbl">
              <span>Announcer Preset</span>
              <select id="stage-speaker-preset" class="input">
                <option value="winner_and_finale" ${draft.speaker_preset === "winner_and_finale" ? "selected" : ""}>Round Winner &amp; Finale Champion</option>
                <option value="finale_only" ${draft.speaker_preset === "finale_only" ? "selected" : ""}>Finale Champion Only</option>
                <option value="full_show_host" ${draft.speaker_preset === "full_show_host" ? "selected" : ""}>Full Game Show Host (Rounds, 5s, Winners &amp; Finale)</option>
                <option value="off" ${draft.speaker_preset === "off" ? "selected" : ""}>Off (Announcements Disabled)</option>
              </select>
            </label>
            <label class="field-lbl">
              <span>Announcement Language</span>
              <select id="stage-speaker-language" class="input">
                <option value="auto" ${draft.speaker_language === "auto" ? "selected" : ""}>Auto (Match Pack Language)</option>
                <option value="en" ${draft.speaker_language === "en" ? "selected" : ""}>Force English</option>
                <option value="ar" ${draft.speaker_language === "ar" ? "selected" : ""}>Force Arabic (العربية)</option>
                <option value="de" ${draft.speaker_language === "de" ? "selected" : ""}>Force German (Deutsch)</option>
                <option value="fr" ${draft.speaker_language === "fr" ? "selected" : ""}>Force French (Français)</option>
                <option value="es" ${draft.speaker_language === "es" ? "selected" : ""}>Force Spanish (Español)</option>
                <option value="pt" ${draft.speaker_language === "pt" ? "selected" : ""}>Force Portuguese (Português)</option>
              </select>
            </label>

            <div class="vol-card">
              <div class="vol-head">
                <label class="check-lbl" style="font-size:0.82rem;">
                  <input id="stage-vol-enabled" type="checkbox" ${draft.speaker_volume_enabled ? "checked" : ""} />
                  Predefined Announcement Volume
                </label>
                <span id="stage-vol-pill" class="vol-val-pill ${draft.speaker_volume_enabled ? "active" : ""}">
                  ${draft.speaker_volume_enabled ? `${volPct}%` : "Speaker Default"}
                </span>
              </div>
              <div id="stage-vol-panel" style="display:${draft.speaker_volume_enabled ? "flex" : "none"};flex-direction:column;gap:8px;">
                <div class="vol-slider-wrap">
                  <span class="sub" style="font-size:0.74rem;">5%</span>
                  <input id="stage-vol-slider" type="range" min="5" max="100" step="5" value="${volPct}" class="vol-slider" />
                  <span class="sub" style="font-size:0.74rem;">100%</span>
                </div>
                <div class="vol-presets">
                  <button type="button" class="vol-preset-btn ${volPct === 30 ? "active" : ""}" data-vol-preset="30">30% Quiet</button>
                  <button type="button" class="vol-preset-btn ${volPct === 55 ? "active" : ""}" data-vol-preset="55">55% Normal</button>
                  <button type="button" class="vol-preset-btn ${volPct === 75 ? "active" : ""}" data-vol-preset="75">75% Loud</button>
                  <button type="button" class="vol-preset-btn ${volPct === 90 ? "active" : ""}" data-vol-preset="90">90% Party</button>
                </div>
              </div>
              <p class="sub" style="font-size:0.74rem;">
                When enabled, sets speaker volume before each announcement. When disabled, uses whatever volume is currently set on the speaker.
              </p>
            </div>
          </div>
        </div>

        <div class="row" style="margin-top:14px;">
          <button id="stage-save-btn" class="btn btn-primary">Save Stage Settings</button>
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            <button class="btn" data-stage-test="amber_countdown">Test Amber 5s</button>
            <button class="btn" data-stage-test="reveal_green">Test Green</button>
            <button class="btn" data-stage-test="reveal_red">Test Red</button>
            <button class="btn" data-stage-test="test_speaker">Test Speaker</button>
            <button class="btn" data-stage-test="restore_lights">Restore Lights</button>
          </div>
        </div>
      `;

    mount.innerHTML = `
      <section class="card">
        <div class="row" style="margin-bottom:8px;">
          <div>
            <h2 style="margin:0;">Smart-Home Stage Effects &amp; HA Event Bus</h2>
            <p class="sub">Sync living-room RGB lights and local TTS speakers with the TV-followed game, or automate on <code>quizrooms_phase_changed</code> &amp; <code>quizrooms_finale</code>.</p>
          </div>
          <span class="badge ${stage.available ? "badge-ok" : "badge-warn"}">${stage.available ? "LAN-Only Active" : "Locked (External Mode)"}</span>
        </div>
        ${stageCardHtml}
      </section>
    `;

    if (!stage.available) return;

    this._renderStageLightsList();
    this._updateVolumeUI(volPct, Boolean(draft.speaker_volume_enabled));

    const bind = (id, ev, fn) => {
      const el = mount.querySelector(`#${id}`);
      if (el) el.addEventListener(ev, () => fn(el));
    };

    bind("stage-enabled-check", "change", (el) => { draft.enabled = el.checked; this._markStageDirty(); });
    bind("stage-tv-only-check", "change", (el) => { draft.only_tv_followed_room = el.checked; this._markStageDirty(); });
    bind("stage-restore-check", "change", (el) => { draft.restore_lights_on_end = el.checked; this._markStageDirty(); });
    bind("stage-light-preset", "change", (el) => { draft.light_preset = el.value; this._markStageDirty(); });
    bind("stage-reveal-rule", "change", (el) => { draft.reveal_green_rule = el.value; this._markStageDirty(); });
    bind("stage-speaker-select", "change", (el) => { draft.speaker_entity = el.value; this._markStageDirty(); });
    bind("stage-tts-select", "change", (el) => { draft.tts_entity = el.value; this._markStageDirty(); });
    bind("stage-speaker-preset", "change", (el) => { draft.speaker_preset = el.value; this._markStageDirty(); });
    bind("stage-speaker-language", "change", (el) => { draft.speaker_language = el.value; this._markStageDirty(); });

    bind("stage-light-filter", "input", (el) => {
      this._lightFilter = el.value;
      this._renderStageLightsList();
    });

    bind("stage-vol-enabled", "change", (el) => {
      draft.speaker_volume_enabled = el.checked;
      const curPct = Math.round(Math.max(5, Math.min(100, Number(draft.speaker_volume || 0.6) * 100)));
      this._updateVolumeUI(curPct, el.checked);
      this._markStageDirty();
    });

    bind("stage-vol-slider", "input", (el) => {
      const pct = Math.max(5, Math.min(100, Number(el.value) || 60));
      draft.speaker_volume = Math.round(pct) / 100;
      this._updateVolumeUI(pct, Boolean(draft.speaker_volume_enabled));
      this._markStageDirty();
    });

    mount.querySelectorAll("[data-vol-preset]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const pct = Number(btn.dataset.volPreset) || 60;
        draft.speaker_volume_enabled = true;
        draft.speaker_volume = pct / 100;
        const chk = mount.querySelector("#stage-vol-enabled");
        if (chk) chk.checked = true;
        this._updateVolumeUI(pct, true);
        this._markStageDirty();
      });
    });

    const refreshStageBtn = mount.querySelector("#stage-refresh-btn");
    if (refreshStageBtn) {
      refreshStageBtn.addEventListener("click", () => this._fetchState({ forceStage: true }));
    }

    const saveStageBtn = mount.querySelector("#stage-save-btn");
    if (saveStageBtn) {
      saveStageBtn.addEventListener("click", () => {
        this._stageDraft = draft;
        this._saveStageConfig();
      });
    }

    mount.querySelectorAll("[data-stage-test]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._stageDraft = draft;
        this._testStageEffect(btn.dataset.stageTest, btn);
      });
    });
  }

  _renderPacksSection(force = false) {
    const mount = this.shadowRoot && this.shadowRoot.getElementById("admin-packs-mount");
    const st = this._state;
    if (!mount || !st) return;

    const packsSig = JSON.stringify((st.packs || []).map((p) => [p.pack_id, p.question_count, p.title])) + `|docs:${this._showDocs}`;
    if (!force && packsSig === this._lastPacksSig) return;
    this._lastPacksSig = packsSig;

    const packsHtml = `<div class="list">${st.packs
      .map(
        (p) => `
      <div class="item">
        <div style="display:flex;align-items:center;gap:12px;">
          ${p.cover_url ? `<img src="${this._esc(p.cover_url)}" alt="" class="pack-thumb" />` : ""}
          <div>
            <strong dir="auto">${this._esc(p.title)}</strong>
            <span class="badge" style="margin-inline-start:6px;">${this._esc(p.pack_id)}</span>
            <span class="badge">${p.question_count} Qs · ${this._esc(p.language.toUpperCase())}${p.rtl ? " · RTL" : ""}${p.has_media ? " · Media" : ""}</span>
            ${p.open_ended ? `<span class="badge badge-warn">Open-Ended</span>` : ""}
            <span class="badge ${p.is_builtin ? "badge-ok" : "badge-warn"}">${p.is_builtin ? "Built-in" : "Custom"}</span>
          </div>
        </div>
        <div>
          ${p.is_builtin ? `<span class="sub">Protected</span>` : `<button class="btn btn-danger" data-del-pack="${this._esc(p.pack_id)}">Delete</button>`}
        </div>
      </div>`
      )
      .join("")}</div>`;

    mount.innerHTML = `
      <section class="card">
        <div class="row" style="margin-bottom:12px;">
          <h2 style="margin:0;">Import Trivia Pack (.json or .zip)</h2>
          <div style="display:flex;gap:8px;flex-wrap:wrap;">
            <button id="download-template-btn" class="btn">Download Sample Pack Template (.json)</button>
            <button id="toggle-docs-btn" class="btn">${this._showDocs ? "Hide Format Docs" : "Format & ZIP Docs"}</button>
            <button id="reload-packs-btn" class="btn">Reload Packs</button>
          </div>
        </div>

        <div id="pack-dropzone" class="dropzone" tabindex="0">
          <strong>Drag &amp; drop a <code>.json</code> or <code>.zip</code> pack here, or click to browse</strong>
          <p class="sub" style="margin-top:6px;">
            Text-only packs: single <code>.json</code> · Photo/flag packs: <code>.zip</code> containing <code>pack.json</code> + <code>media/</code> (PNG, JPG, WebP, GIF, SVG · max 25 MB).
          </p>
          <input id="pack-file-input" type="file" accept=".json,.zip,application/json,application/zip" style="display:none;" />
        </div>

        <label style="display:inline-flex;align-items:center;gap:8px;margin-top:10px;font-size:0.86rem;font-weight:600;cursor:pointer;">
          <input id="overwrite-check" type="checkbox" ${this._overwrite ? "checked" : ""} />
          Overwrite existing custom pack if <code>pack_id</code> already exists
        </label>

        <div id="upload-status-mount"></div>

        ${
          this._showDocs
            ? `<div style="margin-top:14px;">
                <p class="sub"><strong>ZIP Layout:</strong> Put <code>pack.json</code> at the archive root (or inside one top-level folder) with an optional pack cover (<code>"cover": "media/cover.svg"</code>) and question images under <code>media/</code>. Set <code>"open_ended": true</code> for free-answer packs (like أتوبيس كومبليت) where <code>choices</code> are omitted and optional <code>"starts_with"</code> checks the starting letter.</p>
                <pre>{
  "pack_id": "custom_trivia",
  "title": "Family Movie Night",
  "cover": "media/cover.png",
  "open_ended": false,
  "language": "en",
  "rtl": false,
  "questions": [
    {
      "id": "q1",
      "prompt": "Who directed Inception?",
      "image": "media/scene.jpg",
      "choices": ["Christopher Nolan", "Denis Villeneuve", "Ridley Scott", "James Cameron"],
      "answer": "Christopher Nolan",
      "aliases": ["Nolan"],
      "difficulty": "easy",
      "explanation": "Released in 2010."
    }
  ]
}</pre>
              </div>`
            : ""
        }
      </section>

      <section class="card">
        <h2>Installed Trivia Packs (${st.packs.length})</h2>
        ${packsHtml}
      </section>
    `;

    this._renderUploadStatus();

    mount.querySelectorAll("[data-del-pack]").forEach((btn) => {
      btn.addEventListener("click", () => this._deletePack(btn.dataset.delPack));
    });
    mount.querySelector("#download-template-btn").addEventListener("click", () => this._downloadTemplate());
    mount.querySelector("#reload-packs-btn").addEventListener("click", () => this._reloadPacks());
    mount.querySelector("#toggle-docs-btn").addEventListener("click", () => {
      this._showDocs = !this._showDocs;
      this._renderPacksSection(true);
    });

    const overwriteEl = mount.querySelector("#overwrite-check");
    overwriteEl.addEventListener("change", () => { this._overwrite = overwriteEl.checked; });

    const dropzone = mount.querySelector("#pack-dropzone");
    const fileInput = mount.querySelector("#pack-file-input");
    dropzone.addEventListener("click", () => fileInput.click());
    dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("drag"); });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropzone.classList.remove("drag");
      const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (file) this._uploadPack(file, this._overwrite);
    });
    fileInput.addEventListener("change", () => {
      const file = fileInput.files && fileInput.files[0];
      if (file) this._uploadPack(file, this._overwrite);
      fileInput.value = "";
    });
  }

  _renderUploadStatus() {
    const mount = this.shadowRoot && this.shadowRoot.getElementById("upload-status-mount");
    if (!mount) return;
    if (!this._uploadStatus) {
      mount.innerHTML = "";
      return;
    }
    const u = this._uploadStatus;
    const cls = u.ok === true ? "ok" : u.ok === false ? "err" : "";
    const list =
      u.details && u.details.length > 1
        ? `<ul style="margin:8px 0 0;padding-inline-start:18px;">${u.details.map((d) => `<li>${this._esc(d)}</li>`).join("")}</ul>`
        : "";
    const dupBtn =
      u.code === "duplicate_pack_id" && this._pendingFile
        ? `<div style="margin-top:10px;"><button id="force-overwrite-btn" class="btn btn-primary">Overwrite Existing Custom Pack</button></div>`
        : "";
    mount.innerHTML = `<div class="upload-box ${cls}"><strong>${this._esc(u.message)}</strong>${list}${dupBtn}</div>`;
    const forceBtn = this.shadowRoot.getElementById("force-overwrite-btn");
    if (forceBtn && this._pendingFile) {
      forceBtn.addEventListener("click", () => this._uploadPack(this._pendingFile, true));
    }
  }
}

customElements.define("quizrooms-admin-panel", QuizRoomsAdminPanel);
