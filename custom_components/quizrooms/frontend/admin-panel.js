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
      this._pollTimer = setInterval(() => this._fetchState(), 4000);
    }
  }

  connectedCallback() {
    if (this._mql.addEventListener) this._mql.addEventListener("change", this._onMediaChange);
  }

  disconnectedCallback() {
    if (this._pollTimer) clearInterval(this._pollTimer);
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

  async _fetchState() {
    if (!this._hass) return;
    try {
      const resp = await this._hass.fetchWithAuth("/quizrooms/api/admin/state");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      this._state = await resp.json();
      this._renderContent();
    } catch (err) {
      console.error("QuizRooms Admin fetch failed", err);
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
          --bg: #0b1020; --card: #131b30; --card2: #1b2642; --text: #f3f6ff; --muted: #9aa7c7;
          --border: rgba(148,163,184,0.18); --accent: #22d3ee; --accent-ink: #07131e;
          --ok: #34d399; --warn: #fbbf24; --err: #fb7185;
        }
        .wrap { max-width: 1120px; margin-inline: auto; }
        .header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }
        .title-row { display: flex; align-items: center; gap: 12px; }
        .logo { width: 42px; height: 42px; border-radius: 12px; display: grid; place-items: center; font-weight: 800; font-size: 1.3rem; background: linear-gradient(135deg, var(--accent), var(--warn)); color: #fff; }
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
      </style>
      <div id="panel-root" data-theme="light">
        <div class="wrap">
          <div class="header">
            <div class="title-row">
              <div class="logo">Q</div>
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
          <div id="admin-body"><div class="card">Loading QuizRooms state…</div></div>
        </div>
      </div>
    `;

    this.shadowRoot.querySelectorAll(".theme-pill button").forEach((btn) => {
      btn.addEventListener("click", () => this._setThemeMode(btn.dataset.mode));
    });
  }

  _renderContent() {
    const body = this.shadowRoot.getElementById("admin-body");
    const st = this._state;
    if (!body || !st) return;

    const roomsHtml = st.rooms.length
      ? `<div class="list">${st.rooms
          .map(
            (r) => `
          <div class="item">
            <div>
              <strong style="font-family:monospace;font-size:1rem;color:var(--accent);">${this._esc(r.code)}</strong>
              <strong style="margin-inline:8px;" dir="auto">${this._esc(r.name)}</strong>
              <span class="badge">${this._esc(r.visibility)}</span>
              <span class="badge ${r.status === "lobby" ? "badge-ok" : "badge-warn"}">
                ${this._esc(r.status)}${r.paused ? " (PAUSED)" : ""} · R${r.current_round}/${r.total_rounds}
              </span>
              <span class="badge">${r.connected_count}/${r.player_count} online</span>
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

    const packsHtml = `<div class="list">${st.packs
      .map(
        (p) => `
      <div class="item">
        <div>
          <strong dir="auto">${this._esc(p.title)}</strong>
          <span class="badge" style="margin-inline-start:6px;">${this._esc(p.pack_id)}</span>
          <span class="badge">${p.question_count} Qs · ${this._esc(p.language.toUpperCase())}${p.rtl ? " · RTL" : ""}${p.has_media ? " · Media" : ""}</span>
          <span class="badge ${p.is_builtin ? "badge-ok" : "badge-warn"}">${p.is_builtin ? "Built-in" : "Custom"}</span>
        </div>
        <div>
          ${p.is_builtin ? `<span class="sub">Protected</span>` : `<button class="btn btn-danger" data-del-pack="${this._esc(p.pack_id)}">Delete</button>`}
        </div>
      </div>`
      )
      .join("")}</div>`;

    const roomOptions = st.rooms
      .map((r) => `<option value="${this._esc(r.code)}" ${st.tv.room_code === r.code ? "selected" : ""}>${this._esc(r.code)} — ${this._esc(r.name)}</option>`)
      .join("");

    body.innerHTML = `
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
      </section>

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
                <p class="sub"><strong>ZIP Layout:</strong> Put <code>pack.json</code> at the archive root (or inside one top-level folder) with referenced images under <code>media/</code> (e.g., <code>"image": "media/pic1.png"</code>).</p>
                <pre>{
  "pack_id": "custom_trivia",
  "title": "Family Movie Night",
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

    // Bind room action buttons
    body.querySelectorAll("[data-act]").forEach((btn) => {
      btn.addEventListener("click", () => this._roomAction(btn.dataset.code, btn.dataset.act));
    });
    body.querySelectorAll("[data-tv-follow]").forEach((btn) => {
      btn.addEventListener("click", () => this._setTVMode("follow", btn.dataset.tvFollow));
    });
    body.querySelectorAll("[data-del-pack]").forEach((btn) => {
      btn.addEventListener("click", () => this._deletePack(btn.dataset.delPack));
    });

    this.shadowRoot.getElementById("save-max-rooms-btn").addEventListener("click", () => {
      const val = this.shadowRoot.getElementById("max-rooms-input").value;
      this._setMaxRooms(val);
    });

    this.shadowRoot.getElementById("tv-mode-overview").addEventListener("click", () => {
      this._setTVMode("overview");
    });
    this.shadowRoot.getElementById("tv-mode-follow").addEventListener("click", () => {
      const code = this.shadowRoot.getElementById("tv-room-select").value;
      if (!code) return this._setBanner("Select an active room first.", true);
      this._setTVMode("follow", code);
    });
    body.querySelectorAll("[data-tv-theme]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._setTVMode(st.tv.mode, st.tv.room_code, btn.dataset.tvTheme);
      });
    });

    this.shadowRoot.getElementById("download-template-btn").addEventListener("click", () => this._downloadTemplate());
    this.shadowRoot.getElementById("reload-packs-btn").addEventListener("click", () => this._reloadPacks());
    this.shadowRoot.getElementById("toggle-docs-btn").addEventListener("click", () => {
      this._showDocs = !this._showDocs;
      this._renderContent();
    });

    const overwriteEl = this.shadowRoot.getElementById("overwrite-check");
    overwriteEl.addEventListener("change", () => { this._overwrite = overwriteEl.checked; });

    const dropzone = this.shadowRoot.getElementById("pack-dropzone");
    const fileInput = this.shadowRoot.getElementById("pack-file-input");
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
