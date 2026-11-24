/* QuizRooms Guest Lobby & Player SPA (/quizrooms/play) */
(function () {
  const LS_SESSION = "quizrooms_session_token";
  const LS_NICK = "quizrooms_nickname";
  const LS_AVATAR = "quizrooms_avatar";
  const LS_HOSTS = "quizrooms_host_tokens";
  const LS_ACTIVE_ROOM = "quizrooms_active_room";

  const state = {
    ws: null,
    connected: false,
    lanBaseUrl: window.location.origin,
    packs: [],
    avatars: [],
    lobby: { rooms: [], active_rooms: 0, max_rooms: 5, can_create_room: true, taken_presets: [] },
    room: null,
    sessionToken: loadStr(LS_SESSION) || makeToken(),
    nickname: loadStr(LS_NICK) || "",
    avatar: loadStr(LS_AVATAR) || "",
    hostTokens: loadJSON(LS_HOSTS, {}),
    activeRoomCode: loadStr(LS_ACTIVE_ROOM) || "",
    selectedAnswer: "",
    selectedWager: 5,
    lastRoundKey: "",
    lastRevealKey: "",
    lastFinaleKey: "",
    lastCountdownSec: -1,
    timerRaf: null,
    serverOffsetMs: 0,
    showCreateModal: false,
    showAvatarModal: false,
    createPackFilter: "all",
    createPackSearch: "",
    createSelectedPacks: new Set(),
    packsLoaded: false,
    packsError: "",
  };

  saveStr(LS_SESSION, state.sessionToken);

  function makeToken() {
    const arr = new Uint8Array(16);
    if (window.crypto && crypto.getRandomValues) {
      crypto.getRandomValues(arr);
    } else {
      for (let i = 0; i < arr.length; i++) arr[i] = Math.floor(Math.random() * 256);
    }
    return Array.from(arr, (b) => b.toString(16).padStart(2, "0")).join("");
  }

  function loadStr(key) {
    try { return localStorage.getItem(key) || ""; } catch (_) { return ""; }
  }
  function saveStr(key, val) {
    try {
      if (val) localStorage.setItem(key, val);
      else localStorage.removeItem(key);
    } catch (_) { }
  }
  function loadJSON(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (_) { return fallback; }
  }
  function saveJSON(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); } catch (_) { }
  }

  function diffBadgeHtml(diff, prefix = "") {
    const d = String(diff || "").toLowerCase();
    if (!d || d === "random") return "";
    const cls = d === "easy" ? "badge-lobby" : d === "hard" ? "badge-paused" : "badge-live";
    const label = d.charAt(0).toUpperCase() + d.slice(1);
    return `<span class="badge ${cls}">${esc(prefix + label)}</span>`;
  }

  function esc(str) {
    return String(str == null ? "" : str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  let toastTimer = null;
  function showToast(msg, isError = false) {
    const el = document.getElementById("toast-root");
    el.textContent = msg;
    el.className = "toast" + (isError ? " error" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), 3400);
  }

  function getHostToken(code) {
    return state.hostTokens[(code || "").toUpperCase()] || "";
  }

  function setHostToken(code, token) {
    const upper = (code || "").toUpperCase();
    if (!upper) return;
    if (token) state.hostTokens[upper] = token;
    else delete state.hostTokens[upper];
    saveJSON(LS_HOSTS, state.hostTokens);
  }

  function sendWS(payload) {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
      showToast("Reconnecting to server…", true);
      return false;
    }
    state.ws.send(JSON.stringify(payload));
    return true;
  }

  function connectWS() {
    if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const role = state.activeRoomCode ? "room" : "lobby";
    const url = `${proto}//${window.location.host}/quizrooms/ws?role=${role}`;
    const ws = new WebSocket(url);
    state.ws = ws;

    ws.onopen = () => {
      state.connected = true;
      updateConnStatus("Live · Local Network");
      updateReconnectBanner(false);
      // Auto-rejoin previous room if still active
      if (state.activeRoomCode) {
        sendWS({
          type: "join_room",
          code: state.activeRoomCode,
          session_token: state.sessionToken,
          nickname: state.nickname || undefined,
          avatar: state.avatar || undefined,
        });
      }
    };

    ws.onclose = () => {
      state.connected = false;
      updateConnStatus("Reconnecting…");
      updateReconnectBanner(true);
      setTimeout(connectWS, 1800);
    };

    ws.onerror = () => {
      updateConnStatus("Connection interrupted");
      updateReconnectBanner(true);
    };

    ws.onmessage = (evt) => {
      try {
        handleWSMessage(JSON.parse(evt.data));
      } catch (err) {
        console.error("Invalid WS message", err);
      }
    };
  }

  function updateConnStatus(text) {
    const el = document.getElementById("connection-status");
    if (el) el.textContent = text;
  }

  function updateReconnectBanner(disconnected) {
    const el = document.getElementById("reconnect-banner");
    if (!el) return;
    const roomCode = (state.room && state.room.code) || state.activeRoomCode;
    if (disconnected && roomCode) {
      el.innerHTML = `<span class="reconnect-dot" aria-hidden="true"></span><span>Reconnecting to room <strong class="mono">${esc(roomCode)}</strong>… Your draft answer &amp; wager are preserved.</span>`;
      el.classList.remove("hidden");
    } else {
      el.classList.add("hidden");
      el.innerHTML = "";
    }
  }

  function avatarUrlFromRef(ref) {
    if (!ref) return "/quizrooms/avatar/preset/fox";
    if (ref.startsWith("preset:")) return `/quizrooms/avatar/preset/${encodeURIComponent(ref.slice(7))}`;
    if (ref.startsWith("custom:")) return `/quizrooms/avatar/custom/${encodeURIComponent(ref.slice(7))}`;
    return "/quizrooms/avatar/preset/fox";
  }

  function ensureAvailableAvatar() {
    if (!state.avatars.length) return;
    if (state.avatar && state.avatar.startsWith("custom:")) return;
    const myCurrentRef = state.room && state.room.me ? state.room.me.avatar : "";
    const cur = state.avatars.find((a) => a.ref === state.avatar);
    if (cur && (!cur.taken || cur.ref === myCurrentRef)) return;
    const free = state.avatars.find((a) => !a.taken);
    if (free) {
      state.avatar = free.ref;
      saveStr(LS_AVATAR, state.avatar);
    }
  }

  async function compressAndUploadAvatar(file) {
    const imgUrl = URL.createObjectURL(file);
    try {
      const img = await new Promise((resolve, reject) => {
        const el = new Image();
        el.onload = () => resolve(el);
        el.onerror = () => reject(new Error("Invalid image file"));
        el.src = imgUrl;
      });
      const size = 160;
      const canvas = document.createElement("canvas");
      canvas.width = size;
      canvas.height = size;
      const ctx = canvas.getContext("2d");
      const minSide = Math.min(img.naturalWidth || img.width, img.naturalHeight || img.height);
      const sx = ((img.naturalWidth || img.width) - minSide) / 2;
      const sy = ((img.naturalHeight || img.height) - minSide) / 2;
      ctx.drawImage(img, sx, sy, minSide, minSide, 0, 0, size, size);

      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
      if (!blob) throw new Error("Unable to encode profile photo");

      const fd = new FormData();
      fd.append("file", blob, "avatar.jpg");
      const resp = await fetch("/quizrooms/api/public/avatar", { method: "POST", body: fd });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.message || "Avatar upload failed");

      state.avatar = data.avatar;
      saveStr(LS_AVATAR, state.avatar);
      if (state.room) {
        sendWS({
          type: "update_avatar",
          code: state.room.code,
          session_token: state.sessionToken,
          avatar: state.avatar,
        });
      }
      showToast("Profile photo updated!");
      closeModal();
      render();
    } finally {
      URL.revokeObjectURL(imgUrl);
    }
  }

  function renderAvatarModalGrid() {
    const grid = document.getElementById("avatar-modal-grid");
    if (!grid) return;
    const myRoomAvatar = state.room && state.room.me ? state.room.me.avatar : "";
    grid.innerHTML = state.avatars
      .map((a) => {
        const isMine = a.ref === state.avatar || a.ref === myRoomAvatar;
        const disabled = a.taken && !isMine;
        return `
          <button type="button" class="avatar-choice ${isMine ? "selected" : ""}" data-pick-avatar="${esc(a.ref)}" ${disabled ? "disabled" : ""} title="${esc(a.label)}">
            ${disabled ? `<span class="avatar-taken-pill">Taken</span>` : ""}
            <img src="${esc(a.url)}" alt="${esc(a.label)}" class="avatar-img avatar-md" />
            <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;">${esc(a.label)}</span>
          </button>`;
      })
      .join("");

    grid.querySelectorAll("[data-pick-avatar]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const chosen = btn.dataset.pickAvatar;
        state.avatar = chosen;
        saveStr(LS_AVATAR, chosen);
        if (state.room) {
          sendWS({
            type: "update_avatar",
            code: state.room.code,
            session_token: state.sessionToken,
            avatar: chosen,
          });
        }
        closeModal();
        render();
      });
    });
  }

  function openAvatarModal() {
    state.showAvatarModal = true;
    state.showCreateModal = false;
    const modalRoot = document.getElementById("modal-root");
    modalRoot.innerHTML = `
      <div class="modal-backdrop" id="avatar-backdrop">
        <div class="studio-card modal-panel animate-in" role="dialog" aria-modal="true" aria-label="Choose Profile Avatar">
          <div class="modal-header">
            <h2>Choose Your Profile Avatar</h2>
            <button type="button" class="btn btn-secondary btn-sm" id="close-avatar-modal">Close</button>
          </div>

          <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px;border-radius:var(--radius-md);background:var(--bg-surface-2);border:1px solid var(--border-subtle);margin-block-end:14px;">
            <div style="display:flex;align-items:center;gap:12px;">
              <img src="${esc(avatarUrlFromRef(state.avatar))}" alt="Current avatar" class="avatar-img avatar-md" />
              <div style="text-align:start;">
                <div style="font-weight:800;font-size:0.92rem;">Use Your Own Photo</div>
                <div style="font-size:0.78rem;color:var(--text-muted);">Auto-cropped square · PNG / JPG / WebP</div>
              </div>
            </div>
            <label class="btn btn-primary btn-sm" style="cursor:pointer;">
              Upload Photo
              <input id="avatar-file-input" type="file" accept="image/png,image/jpeg,image/webp,image/gif" style="display:none;" />
            </label>
          </div>

          <div style="display:flex;align-items:center;justify-content:space-between;margin-block-end:8px;">
            <span class="field-label" style="margin:0;">Or Pick a Unique Studio SVG Character</span>
            <span class="badge">${state.avatars.filter((a) => !a.taken).length} free</span>
          </div>
          <div id="avatar-modal-grid" class="avatar-grid"></div>
        </div>
      </div>
    `;

    renderAvatarModalGrid();
    document.getElementById("close-avatar-modal").addEventListener("click", closeModal);
    document.getElementById("avatar-backdrop").addEventListener("click", (e) => {
      if (e.target.id === "avatar-backdrop") closeModal();
    });
    const fileInput = document.getElementById("avatar-file-input");
    fileInput.addEventListener("change", async () => {
      const f = fileInput.files && fileInput.files[0];
      if (!f) return;
      try {
        await compressAndUploadAvatar(f);
      } catch (err) {
        showToast(err.message || "Could not upload photo.", true);
      }
      fileInput.value = "";
    });
  }

  async function fetchBootstrap() {
    try {
      const resp = await fetch("/quizrooms/api/public/bootstrap", { cache: "no-store" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (data.lan_base_url) state.lanBaseUrl = data.lan_base_url;
      if (data.lobby) state.lobby = data.lobby;
      if (Array.isArray(data.avatars)) {
        state.avatars = data.avatars;
        ensureAvailableAvatar();
      }
      if (Array.isArray(data.packs)) {
        state.packs = data.packs;
        state.packsLoaded = true;
        state.packsError = "";
      }
      if (!state.room) render();
      if (state.showCreateModal) renderModalPackList();
      if (state.showAvatarModal) renderAvatarModalGrid();
    } catch (err) {
      state.packsError = "Unable to load trivia packs. Tap Retry.";
      if (state.showCreateModal) renderModalPackList();
    }
  }

  function handleWSMessage(msg) {
    switch (msg.type) {
      case "hello":
        if (msg.lan_base_url) state.lanBaseUrl = msg.lan_base_url;
        if (msg.lobby) state.lobby = msg.lobby;
        if (Array.isArray(msg.avatars)) {
          state.avatars = msg.avatars;
          ensureAvailableAvatar();
        }
        if (Array.isArray(msg.packs)) {
          state.packs = msg.packs;
          state.packsLoaded = true;
          state.packsError = "";
        }
        if (!state.room) render();
        if (state.showCreateModal) renderModalPackList();
        if (state.showAvatarModal) renderAvatarModalGrid();
        break;

      case "lobby_update":
        if (msg.lobby) state.lobby = msg.lobby;
        if (Array.isArray(msg.avatars)) {
          state.avatars = msg.avatars;
          ensureAvailableAvatar();
        }
        if (Array.isArray(msg.packs)) {
          state.packs = msg.packs;
          state.packsLoaded = true;
          state.packsError = "";
        }
        if (!state.room) render();
        if (state.showCreateModal) renderModalPackList();
        if (state.showAvatarModal) renderAvatarModalGrid();
        break;

      case "room_created":
        if (msg.session_token) {
          state.sessionToken = msg.session_token;
          saveStr(LS_SESSION, state.sessionToken);
        }
        setHostToken(msg.code, msg.host_token);
        state.activeRoomCode = msg.code;
        saveStr(LS_ACTIVE_ROOM, msg.code);
        state.showCreateModal = false;
        closeModal();
        applyRoomState(msg.room);
        break;

      case "joined_room":
        if (msg.session_token) {
          state.sessionToken = msg.session_token;
          saveStr(LS_SESSION, state.sessionToken);
        }
        state.activeRoomCode = msg.code;
        saveStr(LS_ACTIVE_ROOM, msg.code);
        applyRoomState(msg.room);
        break;

      case "room_state":
        applyRoomState(msg.room);
        break;

      case "room_closed":
        showToast(msg.reason || "Room closed.", true);
        if (state.room && state.room.code === msg.code) {
          setHostToken(msg.code, "");
          state.room = null;
          state.activeRoomCode = "";
          saveStr(LS_ACTIVE_ROOM, "");
          stopTimerLoop();
          sendWS({ type: "subscribe_lobby" });
          render();
        }
        break;

      case "error":
        if (msg.code === "room_not_found" && !state.room && state.activeRoomCode) {
          // Stale saved room from an earlier session; silently fall back to open lobby
          setHostToken(state.activeRoomCode, "");
          state.activeRoomCode = "";
          saveStr(LS_ACTIVE_ROOM, "");
          sendWS({ type: "subscribe_lobby" });
          render();
          return;
        }
        showToast(msg.message || "Action failed.", true);
        if (msg.code === "avatar_taken") {
          fetchBootstrap().then(() => openAvatarModal());
        }
        if (msg.code === "already_hosting" && msg.existing_room_code) {
          state.showCreateModal = false;
          closeModal();
          sendWS({
            type: "join_room",
            code: msg.existing_room_code,
            session_token: state.sessionToken,
          });
        }
        break;
    }
  }

  function applyRoomState(room) {
    if (!room) return;
    // Preserve active free-text input focus & cursor if user is mid-typing on the same question
    const activeInput = document.activeElement;
    const preserveFreeText =
      activeInput &&
      activeInput.id === "free-text-input" &&
      room.phase === "question" &&
      room.question &&
      state.lastRoundKey === `${room.code}:${room.question.index}`;
    const prevSelStart = preserveFreeText ? activeInput.selectionStart : null;
    const prevSelEnd = preserveFreeText ? activeInput.selectionEnd : null;
    if (preserveFreeText) {
      state.selectedAnswer = activeInput.value;
    }

    state.room = room;
    if (state.connected) updateReconnectBanner(false);

    if (room.question && room.question.server_now_ms) {
      state.serverOffsetMs = room.question.server_now_ms - Date.now();
    }

    if (room.phase === "bonus_wager" && room.bonus_wager) {
      const bwKey = `${room.code}:bonus_wager`;
      if (state.lastRoundKey !== bwKey) {
        state.lastRoundKey = bwKey;
        const opts = room.bonus_wager.options || [5, 10, 15, 20, 25, 30];
        state.selectedWager = opts[0] || 5;
        if (window.QuizRoomsSFX) window.QuizRoomsSFX.roundStart();
      }
    }

    // Reset local answer/wager selection when moving to a new question (default to lowest available number)
    if (room.phase === "question" && room.question) {
      const roundKey = `${room.code}:${room.question.index}`;
      if (state.lastRoundKey !== roundKey) {
        state.lastRoundKey = roundKey;
        state.lastCountdownSec = -1;
        state.selectedAnswer = "";
        const avail = (room.me && room.me.available_wagers) || [];
        state.selectedWager = avail.length ? Math.min(...avail) : 1;
        if (window.QuizRoomsSFX) window.QuizRoomsSFX.roundStart();
      }
    }

    // Celebratory burst + SFX on reveal (correct / wrong / timeout)
    if (room.phase === "reveal" && room.reveal) {
      const revKey = `${room.code}:${room.reveal.round_index}`;
      if (state.lastRevealKey !== revKey) {
        state.lastRevealKey = revKey;
        const myOutcome = room.reveal.outcomes.find((o) => o.session_token === state.sessionToken);
          if (myOutcome) {
            if (myOutcome.is_correct) {
              if (window.QuizRoomsConfetti) window.QuizRoomsConfetti.burst(0.9);
              if (window.QuizRoomsSFX) window.QuizRoomsSFX.correct();
            } else if (myOutcome.outcome === "wrong" || myOutcome.outcome === "duplicate") {
              if (window.QuizRoomsSFX) window.QuizRoomsSFX.wrong();
            } else if (myOutcome.outcome === "timeout") {
              if (window.QuizRoomsSFX) window.QuizRoomsSFX.timeout();
            }
          } else if (window.QuizRoomsSFX) {
            window.QuizRoomsSFX.reveal();
          }
        }
      }

      // Celebratory burst + fanfare on finale podium
      if (room.phase === "finale") {
        const finKey = `${room.code}:finale:${room.finale ? room.finale.podium.length : 0}`;
        if (state.lastFinaleKey !== finKey) {
          state.lastFinaleKey = finKey;
          if (window.QuizRoomsConfetti) window.QuizRoomsConfetti.burst(1.5);
          if (window.QuizRoomsSFX) window.QuizRoomsSFX.finale();
        }
      }

      render();
      if (preserveFreeText) {
        const nextInput = document.getElementById("free-text-input");
        if (nextInput) {
          nextInput.focus();
          if (typeof prevSelStart === "number" && typeof prevSelEnd === "number") {
            try { nextInput.setSelectionRange(prevSelStart, prevSelEnd); } catch (_) { }
          }
        }
      }
      if (room.phase === "question" && !room.paused) startTimerLoop();
      else stopTimerLoop();
    }

    function stopTimerLoop() {
      if (state.timerRaf) {
        cancelAnimationFrame(state.timerRaf);
        state.timerRaf = null;
      }
    }

    function startTimerLoop() {
      stopTimerLoop();
      function frame() {
        const room = state.room;
        if (!room || room.phase !== "question" || !room.question) return;
        const q = room.question;
        const fill = document.getElementById("timer-fill");
        const readout = document.getElementById("timer-readout");
        if (!fill || !readout) return;

        let rem = q.remaining_seconds || 0;
        if (!room.paused && q.ends_at_ms) {
          rem = Math.max(0, (q.ends_at_ms - (Date.now() + state.serverOffsetMs)) / 1000);
        }
        const sec = Math.ceil(rem);
        const pct = Math.max(0, Math.min(100, (rem / (q.timer_seconds || 30)) * 100));
        fill.style.width = pct.toFixed(1) + "%";
        fill.classList.toggle("warn", rem <= 10 && rem > 5);
        fill.classList.toggle("urgent", rem <= 5);
        readout.classList.toggle("urgent", rem <= 5);
        readout.textContent = room.paused ? `PAUSED (${sec}s)` : `${sec}s`;

        const alreadyLocked = Boolean(room.me && room.me.submitted);
        if (!room.paused && sec > 0 && sec !== state.lastCountdownSec) {
          state.lastCountdownSec = sec;
          if (sec <= 5 && window.QuizRoomsSFX) {
            window.QuizRoomsSFX.tickUrgent(sec);
          } else if (sec <= 10 && !alreadyLocked && window.QuizRoomsSFX) {
            window.QuizRoomsSFX.tickSoft();
          }
        }

        if (!room.paused && rem > 0) {
          state.timerRaf = requestAnimationFrame(frame);
        }
      }
      state.timerRaf = requestAnimationFrame(frame);
    }

    function getJoinUrl(code) {
      const base = state.lanBaseUrl || window.location.origin;
      return `${base}/quizrooms/play?code=${encodeURIComponent(code)}`;
    }

    function requireNickname() {
      const input = document.getElementById("lobby-nickname");
      const nick = (input ? input.value : state.nickname).trim();
      if (!nick) {
        showToast("Enter your nickname first!", true);
        if (input) input.focus();
        return null;
      }
      state.nickname = nick;
      saveStr(LS_NICK, nick);
      return nick;
    }

    function render() {
      const root = document.getElementById("app-root");
      if (!state.room) {
        renderOpenLobby(root);
      } else if (state.room.phase === "lobby") {
        renderRoomLobby(root, state.room);
      } else if (state.room.phase === "bonus_wager") {
        renderBonusWagerPhase(root, state.room);
      } else if (state.room.phase === "question") {
        renderQuestionPhase(root, state.room);
      } else if (state.room.phase === "reveal") {
        renderRevealPhase(root, state.room);
      } else if (state.room.phase === "finale") {
        renderFinalePhase(root, state.room);
      }
    }

    function renderOpenLobby(root) {
      const urlCode = new URLSearchParams(window.location.search).get("code") || "";
      const { rooms, active_rooms, max_rooms, can_create_room } = state.lobby;

      const roomsHtml = rooms.length
        ? `<div class="rooms-grid">${rooms
          .map((r) => {
            const statusClass = r.status === "lobby" ? "badge-lobby" : "badge-live";
            const statusText = r.status === "lobby" ? "Lobby" : `In Progress · R${r.current_round}/${r.total_rounds}`;
            const joinDisabled = r.status === "in_progress" && !r.allow_late_join;
            return `
              <article class="studio-card room-card animate-in" dir="${r.rtl ? "rtl" : "auto"}">
                <div class="room-card-top">
                  <div style="display:flex;align-items:center;gap:10px;min-width:0;">
                    ${r.cover_url ? `<img src="${esc(r.cover_url)}" alt="" class="pack-cover-thumb" />` : ""}
                    <div style="min-width:0;">
                      <h3 class="room-card-name" dir="auto">${esc(r.name)}</h3>
                      <p class="room-card-packs" dir="auto">${esc((r.pack_titles || []).join(" · "))}</p>
                    </div>
                  </div>
                  <span class="room-code-chip">${esc(r.code)}</span>
                </div>
                <div class="room-card-foot">
                  <div style="display:flex;gap:6px;flex-wrap:wrap;">
                    <span class="badge ${statusClass}">${esc(statusText)}</span>
                    <span class="badge">${r.player_count} player${r.player_count === 1 ? "" : "s"}</span>
                    ${diffBadgeHtml(r.difficulty)}
                    ${r.unique_answers_only ? `<span class="badge badge-accent">Unique Answers</span>` : ""}
                  </div>
                  <button class="btn btn-primary btn-sm" data-join-code="${esc(r.code)}" ${joinDisabled ? "disabled" : ""}>
                    ${joinDisabled ? "Closed" : "Join"}
                  </button>
                </div>
              </article>`;
          })
          .join("")}</div>`
        : `<div class="studio-card empty-state">
          <p style="margin:0 0 6px;font-weight:700;color:var(--text-primary);">No open public rooms yet</p>
          <p style="margin:0;font-size:0.9rem;">Create a new trivia room above or enter a private room code to jump in.</p>
        </div>`;

      root.innerHTML = `
      <section class="studio-card hero-card animate-in">
        <div class="hero-grid">
          <div>
            <label class="field-label" for="lobby-nickname">Your Profile &amp; Nickname</label>
            <div class="profile-input-row">
              <button type="button" id="open-avatar-btn" class="avatar-picker-btn" title="Change avatar or upload photo">
                <img src="${esc(avatarUrlFromRef(state.avatar))}" alt="Your avatar" />
                <span class="avatar-picker-tag">Edit</span>
              </button>
              <input id="lobby-nickname" class="text-input" dir="auto" maxlength="24"
                placeholder="e.g., Alex or سارة" value="${esc(state.nickname)}" />
            </div>
          </div>
          <div>
            <label class="field-label" for="join-code-input">Join Private / QR Room</label>
            <div class="join-code-row">
              <input id="join-code-input" class="text-input join-code-input" maxlength="6"
                placeholder="CODE" value="${esc(urlCode.toUpperCase())}" />
              <button id="join-code-btn" class="btn btn-secondary">Join</button>
            </div>
          </div>
        </div>
        <div class="capacity-bar">
          <div>
            <span class="badge ${can_create_room ? "badge-accent" : "badge-paused"}">
              Rooms: ${active_rooms} / ${max_rooms}
            </span>
            ${!can_create_room ? `<span style="margin-inline-start:8px;font-size:0.84rem;color:var(--accent-coral);font-weight:700;">Room limit reached</span>` : ""}
          </div>
          <button id="open-create-btn" class="btn btn-primary" ${can_create_room ? "" : "disabled"}>
            + Create New Room
          </button>
        </div>
      </section>

      <div class="section-header">
        <h2>Open Public Rooms</h2>
        <span class="badge">${rooms.length} open</span>
      </div>
      ${roomsHtml}
    `;

      document.getElementById("open-avatar-btn").addEventListener("click", () => {
        openAvatarModal();
      });

      const nickEl = document.getElementById("lobby-nickname");
      nickEl.addEventListener("input", () => {
        state.nickname = nickEl.value;
        saveStr(LS_NICK, nickEl.value.trim());
      });

      document.getElementById("join-code-btn").addEventListener("click", () => {
        const nick = requireNickname();
        if (!nick) return;
        const code = (document.getElementById("join-code-input").value || "").trim().toUpperCase();
        if (!code) {
          showToast("Enter a 4-letter room code.", true);
          return;
        }
        sendWS({ type: "join_room", code, session_token: state.sessionToken, nickname: nick, avatar: state.avatar || undefined });
      });

      document.getElementById("open-create-btn").addEventListener("click", () => {
        const nick = requireNickname();
        if (!nick) return;
        openCreateModal();
      });

      root.querySelectorAll("[data-join-code]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const nick = requireNickname();
          if (!nick) return;
          sendWS({
            type: "join_room",
            code: btn.dataset.joinCode,
            session_token: state.sessionToken,
            nickname: nick,
            avatar: state.avatar || undefined,
          });
        });
      });
    }

    function isLanguagePackFilter(filter) {
      return Boolean(filter && filter !== "all" && filter !== "open_ended");
    }

    function syncRandomPacksButton() {
      const randomBtn = document.getElementById("cr-random-packs-btn");
      if (!randomBtn) return;
      const filter = state.createPackFilter || "all";
      const show = isLanguagePackFilter(filter);
      randomBtn.classList.toggle("hidden", !show);
      if (show) {
        const langLabel = filter.toUpperCase();
        randomBtn.textContent = `🎲 Random ${langLabel} Packs`;
        randomBtn.setAttribute("aria-label", `Select random ${langLabel} packs`);
      }
    }

    function renderSelectedPacksSummary() {
      const summaryEl = document.getElementById("cr-selected-summary");
      if (!summaryEl) return;
      const selectedList = state.packs.filter((p) => state.createSelectedPacks.has(p.pack_id));
      if (!selectedList.length) {
        summaryEl.classList.add("hidden");
        summaryEl.innerHTML = "";
        return;
      }
      summaryEl.classList.remove("hidden");
      summaryEl.innerHTML = `
        ${selectedList
          .map(
            (p) => `
          <span class="pack-selected-chip animate-in" dir="${p.rtl ? "rtl" : "auto"}">
            ${p.cover_url ? `<img src="${esc(p.cover_url)}" alt="" width="18" height="18" />` : ""}
            <span class="pack-selected-chip-title" title="${esc(p.title)}">${esc(p.title)}</span>
            <button type="button" class="pack-selected-chip-remove" data-unselect-pack="${esc(p.pack_id)}" aria-label="Remove ${esc(p.title)}">×</button>
          </span>`
          )
          .join("")}
        <button type="button" id="cr-clear-packs-btn" class="pack-clear-btn">Clear all</button>
      `;

      summaryEl.querySelectorAll("[data-unselect-pack]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.getAttribute("data-unselect-pack");
          if (id) state.createSelectedPacks.delete(id);
          renderModalPackList({ syncFromDom: false });
        });
      });
      const clearBtn = document.getElementById("cr-clear-packs-btn");
      if (clearBtn) {
        clearBtn.addEventListener("click", () => {
          state.createSelectedPacks.clear();
          renderModalPackList({ syncFromDom: false });
        });
      }
    }

    function selectRandomPacksForActiveLanguage() {
      const filter = state.createPackFilter || "all";
      if (!isLanguagePackFilter(filter)) return;

      const langPacks = state.packs.filter(
        (p) => String(p.language || "").toLowerCase() === filter
      );
      if (!langPacks.length) {
        showToast(`No ${filter.toUpperCase()} packs available.`, true);
        return;
      }

      // Prefer standard (non-open-ended) packs for balanced random trivia mixes
      const standardPacks = langPacks.filter((p) => !p.open_ended);
      const pool = standardPacks.length ? standardPacks : langPacks;
      const pickCount = Math.min(3, pool.length);

      function shuffle(arr) {
        const copy = arr.slice();
        for (let i = copy.length - 1; i > 0; i -= 1) {
          const j = Math.floor(Math.random() * (i + 1));
          const tmp = copy[i];
          copy[i] = copy[j];
          copy[j] = tmp;
        }
        return copy;
      }

      // Always deselect all previously selected packs first, then draw fresh random ones
      const prevIds = new Set(state.createSelectedPacks);
      state.createSelectedPacks.clear();

      const unseenPool = pool.filter((p) => !prevIds.has(p.pack_id));
      let picked = [];
      if (unseenPool.length >= pickCount) {
        picked = shuffle(unseenPool).slice(0, pickCount);
      } else {
        const seenPool = pool.filter((p) => prevIds.has(p.pack_id));
        picked = shuffle(unseenPool).concat(shuffle(seenPool)).slice(0, pickCount);
      }

      picked.forEach((p) => state.createSelectedPacks.add(p.pack_id));
      state.createPackSearch = "";
      const searchEl = document.getElementById("cr-pack-search");
      if (searchEl) searchEl.value = "";
      if (window.QuizRoomsSFX && window.QuizRoomsSFX.choiceTap) window.QuizRoomsSFX.choiceTap();
      renderModalPackList({ syncFromDom: false, sortSelectedFirst: true });

      const listEl = document.getElementById("cr-pack-list");
      if (listEl) listEl.scrollTop = 0;

      const titles = picked.map((p) => p.title).join(", ");
      showToast(`Selected ${picked.length} random ${filter.toUpperCase()} pack${picked.length === 1 ? "" : "s"}: ${titles}`);
    }

    function renderModalPackList(opts) {
      const syncFromDom = !opts || opts.syncFromDom !== false;
      const sortSelectedFirst = Boolean(opts && opts.sortSelectedFirst);
      const listEl = document.getElementById("cr-pack-list");
      const countBadge = document.getElementById("cr-pack-count");
      if (!listEl) return;

      // Sync any currently rendered checkboxes into persistent Set before re-filtering
      if (syncFromDom) {
        listEl.querySelectorAll('input[name="pack_id"]').forEach((el) => {
          if (el.checked) state.createSelectedPacks.add(el.value);
          else state.createSelectedPacks.delete(el.value);
        });
      }

      syncRandomPacksButton();
      renderSelectedPacksSummary();

      if (!state.packs.length) {
        if (countBadge) countBadge.textContent = "0 available";
        if (state.packsError) {
          listEl.innerHTML = `
          <div class="pack-empty-box">
            <span>${esc(state.packsError)}</span>
            <button type="button" class="btn btn-secondary btn-sm" id="retry-packs-btn">Retry</button>
          </div>`;
          const retryBtn = document.getElementById("retry-packs-btn");
          if (retryBtn) retryBtn.addEventListener("click", () => fetchBootstrap());
        } else if (!state.packsLoaded) {
          listEl.innerHTML = `<div class="pack-empty-box">Loading trivia packs…</div>`;
        } else {
          listEl.innerHTML = `
          <div class="pack-empty-box">
            <span>No trivia packs loaded on the server yet.</span>
            <button type="button" class="btn btn-secondary btn-sm" id="retry-packs-btn">Refresh</button>
          </div>`;
          const retryBtn = document.getElementById("retry-packs-btn");
          if (retryBtn) retryBtn.addEventListener("click", () => fetchBootstrap());
        }
        return;
      }

      const term = (state.createPackSearch || "").trim().toLowerCase();
      const filter = state.createPackFilter || "all";
      const filtered = state.packs.filter((p) => {
        if (filter === "open_ended") {
          if (!p.open_ended) return false;
        } else if (filter !== "all") {
          if (String(p.language || "").toLowerCase() !== filter) return false;
        }
        if (!term) return true;
        return (
          String(p.title || "").toLowerCase().includes(term) ||
          String(p.pack_id || "").toLowerCase().includes(term) ||
          String(p.category || "").toLowerCase().includes(term)
        );
      });

      const ordered = sortSelectedFirst
        ? filtered
            .filter((p) => state.createSelectedPacks.has(p.pack_id))
            .concat(filtered.filter((p) => !state.createSelectedPacks.has(p.pack_id)))
        : filtered;

      if (countBadge) {
        countBadge.textContent = `${state.createSelectedPacks.size} selected · ${filtered.length}/${state.packs.length}`;
      }

      if (!ordered.length) {
        listEl.innerHTML = `<div class="pack-empty-box">No packs match "${esc(state.createPackSearch || filter)}".</div>`;
        return;
      }

      listEl.innerHTML = ordered
        .map((p) => {
          const checked = state.createSelectedPacks.has(p.pack_id);
          return `
        <label class="pack-check-item ${checked ? "is-selected" : ""}" dir="${p.rtl ? "rtl" : "auto"}">
          <input type="checkbox" name="pack_id" value="${esc(p.pack_id)}" ${checked ? "checked" : ""} />
          ${p.cover_url ? `<img src="${esc(p.cover_url)}" alt="" class="pack-cover-thumb" />` : ""}
          <div style="flex:1;min-width:0;text-align:start;">
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-weight:800;">
              <span>${esc(p.title)}</span>
              ${p.open_ended ? `<span class="badge badge-live" style="font-size:0.68rem;padding:2px 7px;">Open-Ended</span>` : ""}
            </div>
            <div style="font-size:0.78rem;color:var(--text-muted);">
              ${p.question_count} Qs · ${esc(p.language.toUpperCase())}${p.has_media ? " · Visual" : ""}
            </div>
          </div>
        </label>`;
        })
        .join("");

      function syncOpenEndedDefaults() {
        listEl.querySelectorAll('input[name="pack_id"]').forEach((el) => {
          if (el.checked) state.createSelectedPacks.add(el.value);
          else state.createSelectedPacks.delete(el.value);
          const row = el.closest(".pack-check-item");
          if (row) row.classList.toggle("is-selected", el.checked);
        });
        if (countBadge) {
          countBadge.textContent = `${state.createSelectedPacks.size} selected · ${filtered.length}/${state.packs.length}`;
        }
        renderSelectedPacksSummary();
        const hasOpenEnded = state.packs.some((p) => state.createSelectedPacks.has(p.pack_id) && p.open_ended);
        const modeSelect = document.getElementById("cr-answer-mode");
        const uniqueCheck = document.getElementById("cr-unique-answers");
        if (hasOpenEnded) {
          if (modeSelect) modeSelect.value = "free_text";
          if (uniqueCheck && uniqueCheck.dataset.userTouched !== "true") {
            uniqueCheck.checked = true;
          }
        } else if (uniqueCheck && uniqueCheck.dataset.userTouched !== "true") {
          uniqueCheck.checked = false;
        }
      }

      listEl.querySelectorAll('input[name="pack_id"]').forEach((cb) => {
        cb.addEventListener("change", syncOpenEndedDefaults);
      });
      syncOpenEndedDefaults();
    }

    function openCreateModal() {
      state.showCreateModal = true;
      state.createPackFilter = "all";
      state.createPackSearch = "";
      state.createSelectedPacks = new Set();
      const modalRoot = document.getElementById("modal-root");

      // Collect any additional custom pack languages beyond EN / AR
      const extraLangs = Array.from(
        new Set(
          state.packs
            .map((p) => String(p.language || "").trim().toLowerCase())
            .filter((lang) => lang && lang !== "en" && lang !== "ar")
        )
      ).sort();

      modalRoot.innerHTML = `
      <div class="modal-backdrop" id="create-backdrop">
        <div class="studio-card modal-panel animate-in" role="dialog" aria-modal="true" aria-label="Create New Room">
          <div class="modal-header">
            <h2>Create New Room</h2>
            <button type="button" class="btn btn-secondary btn-sm" id="close-modal-btn">Close</button>
          </div>
          <form id="create-room-form" class="form-grid">
            <div>
              <label class="field-label" for="cr-name">Room Name (optional)</label>
              <input id="cr-name" class="text-input" dir="auto" maxlength="48" placeholder="Friday Trivia Night" />
            </div>

            <div>
              <div style="display:flex;align-items:center;justify-content:space-between;margin-block-end:6px;">
                <label class="field-label" style="margin:0;">Trivia Pack(s)</label>
                <span class="badge" id="cr-pack-count">0 selected · ${state.packs.length} available</span>
              </div>
              <div class="pack-filter-bar">
                <input id="cr-pack-search" type="search" class="text-input" style="padding-block:8px;font-size:0.9rem;" placeholder="Search packs by title or category…" />
                <div class="pack-filter-pills" role="group" aria-label="Filter packs by language or mode">
                  <button type="button" class="pack-filter-pill active" data-cr-filter="all">All</button>
                  <button type="button" class="pack-filter-pill" data-cr-filter="en">EN</button>
                  <button type="button" class="pack-filter-pill" data-cr-filter="ar">AR (عربي)</button>
                  ${extraLangs.map((lang) => `<button type="button" class="pack-filter-pill" data-cr-filter="${esc(lang)}">${esc(lang.toUpperCase())}</button>`).join("")}
                  <button type="button" class="pack-filter-pill" data-cr-filter="open_ended">Open-Ended</button>
                  <button type="button" id="cr-random-packs-btn" class="pack-random-btn hidden">🎲 Random Packs</button>
                </div>
              </div>
              <div id="cr-selected-summary" class="pack-selected-summary hidden" aria-live="polite"></div>
              <div id="cr-pack-list" class="pack-checklist"></div>
            </div>

            <div class="form-row-2">
              <div>
                <label class="field-label" for="cr-rounds">Rounds (5–25)</label>
                <select id="cr-rounds" class="select-input mono">
                  <option value="5">5 rounds (Wagers 1–5)</option>
                  <option value="10" selected>10 rounds (Wagers 1–10)</option>
                  <option value="15">15 rounds (Wagers 1–15)</option>
                  <option value="20">20 rounds (Wagers 1–20)</option>
                  <option value="25">25 rounds (Wagers 1–25)</option>
                </select>
              </div>
              <div>
                <label class="field-label" for="cr-timer">Question Timer</label>
                <select id="cr-timer" class="select-input">
                  <option value="20">20 seconds</option>
                  <option value="30" selected>30 seconds</option>
                  <option value="45">45 seconds</option>
                  <option value="60">60 seconds</option>
                </select>
              </div>
            </div>

            <div class="form-row-2">
              <div>
                <label class="field-label" for="cr-answer-mode">Answer Mode</label>
                <select id="cr-answer-mode" class="select-input">
                  <option value="multiple_choice" selected>Multiple Choice</option>
                  <option value="free_text">Free-Text (Fuzzy + RTL)</option>
                </select>
              </div>
              <div>
                <label class="field-label" for="cr-difficulty">Difficulty</label>
                <select id="cr-difficulty" class="select-input">
                  <option value="random" selected>Random (All Levels)</option>
                  <option value="easy">Easy</option>
                  <option value="medium">Medium</option>
                  <option value="hard">Hard</option>
                </select>
              </div>
            </div>

            <div class="form-row-2">
              <div>
                <label class="field-label" for="cr-visibility">Visibility</label>
                <select id="cr-visibility" class="select-input">
                  <option value="public" selected>Public (Open Lobby)</option>
                  <option value="private">Private (Code / QR only)</option>
                </select>
              </div>
              <div style="display:flex;align-items:end;">
                <label class="toggle-line" style="width:100%;cursor:pointer;" title="When enabled, wrong or unanswered questions deduct the selected confidence number.">
                  <span style="font-size:0.86rem;font-weight:700;">Deduct Wager if Wrong</span>
                  <input id="cr-deduct-wrong" type="checkbox" checked style="width:18px;height:18px;accent-color:var(--accent-primary);" />
                </label>
              </div>
            </div>

            <div class="form-row-2">
              <label class="toggle-line" style="cursor:pointer;">
                <span style="font-size:0.86rem;font-weight:700;">Allow Late Joiners</span>
                <input id="cr-late-join" type="checkbox" checked style="width:18px;height:18px;accent-color:var(--accent-primary);" />
              </label>
              <label class="toggle-line" style="cursor:pointer;" title="Disallow duplicate answers submitted by multiple players in the same round or reused from earlier rounds (auto-enabled for open-ended packs like Autobus Complete).">
                <span style="font-size:0.86rem;font-weight:700;">No Repeated Answers</span>
                <input id="cr-unique-answers" type="checkbox" style="width:18px;height:18px;accent-color:var(--accent-primary);" />
              </label>
            </div>

            <div>
              <label class="toggle-line" style="width:100%;cursor:pointer;" title="Adds one high-stakes blind-wager bonus question at the end. Players choose their points before seeing the question (+score if correct, −score if wrong).">
                <span id="cr-bonus-label" style="font-size:0.86rem;font-weight:700;">★ Final Bonus Question (blind wager ±5–30 pts)</span>
                <input id="cr-bonus-round" type="checkbox" style="width:18px;height:18px;accent-color:var(--accent-amber);" />
              </label>
            </div>

            <button type="submit" class="btn btn-primary" style="width:100%;">Create &amp; Open Lobby</button>
          </form>
        </div>
      </div>
    `;

      const uniqueEl = document.getElementById("cr-unique-answers");
      if (uniqueEl) {
        uniqueEl.addEventListener("change", () => {
          uniqueEl.dataset.userTouched = "true";
        });
      }

      const roundsEl = document.getElementById("cr-rounds");
      const bonusLabelEl = document.getElementById("cr-bonus-label");
      function syncBonusLabel() {
        if (!roundsEl || !bonusLabelEl) return;
        const r = parseInt(roundsEl.value, 10) || 10;
        const maxBonus = r <= 5 ? 15 : 30;
        bonusLabelEl.textContent = `★ Final Bonus Question (blind wager ±5–${maxBonus} pts)`;
      }
      if (roundsEl) {
        roundsEl.addEventListener("change", syncBonusLabel);
        syncBonusLabel();
      }

      const packSearchEl = document.getElementById("cr-pack-search");
      if (packSearchEl) {
        packSearchEl.addEventListener("input", () => {
          state.createPackSearch = packSearchEl.value;
          renderModalPackList();
        });
      }
      modalRoot.querySelectorAll("[data-cr-filter]").forEach((btn) => {
        btn.addEventListener("click", () => {
          state.createPackFilter = btn.dataset.crFilter;
          modalRoot.querySelectorAll("[data-cr-filter]").forEach((b) => {
            b.classList.toggle("active", b.dataset.crFilter === state.createPackFilter);
          });
          syncRandomPacksButton();
          renderModalPackList();
        });
      });

      const randomPacksBtn = document.getElementById("cr-random-packs-btn");
      if (randomPacksBtn) {
        randomPacksBtn.addEventListener("click", selectRandomPacksForActiveLanguage);
      }

      renderModalPackList();
      if (!state.packs.length) {
        fetchBootstrap();
      }

      document.getElementById("close-modal-btn").addEventListener("click", closeModal);
      document.getElementById("create-backdrop").addEventListener("click", (e) => {
        if (e.target.id === "create-backdrop") closeModal();
      });

      document.getElementById("create-room-form").addEventListener("submit", (e) => {
        e.preventDefault();
        const selectedPacks = Array.from(state.createSelectedPacks);
        if (!selectedPacks.length) {
          showToast("Select at least one trivia pack.", true);
          return;
        }
        sendWS({
          type: "create_room",
          session_token: state.sessionToken,
          nickname: state.nickname,
          avatar: state.avatar || undefined,
          room_name: document.getElementById("cr-name").value.trim(),
          pack_ids: selectedPacks,
          round_count: parseInt(document.getElementById("cr-rounds").value, 10) || 10,
          timer_seconds: parseInt(document.getElementById("cr-timer").value, 10) || 30,
          answer_mode: document.getElementById("cr-answer-mode").value,
          wager_mode: "pool",
          deduct_wrong_wager: document.getElementById("cr-deduct-wrong").checked,
          unique_answers_only: document.getElementById("cr-unique-answers").checked,
          difficulty: document.getElementById("cr-difficulty").value,
          visibility: document.getElementById("cr-visibility").value,
          allow_late_join: document.getElementById("cr-late-join").checked,
          bonus_round: document.getElementById("cr-bonus-round").checked,
        });
      });
    }

    function closeModal() {
      state.showCreateModal = false;
      state.showAvatarModal = false;
      const modalRoot = document.getElementById("modal-root");
      if (modalRoot) modalRoot.innerHTML = "";
    }

    function renderRoomHeader(room) {
      const isHost = Boolean(getHostToken(room.code) || (room.me && room.me.is_host));
      return `
      <div class="studio-card room-header-card">
        <div class="room-meta">
          <span class="room-code-chip">${esc(room.code)}</span>
          <strong dir="auto">${esc(room.settings.room_name)}</strong>
          <span class="badge">${room.players.length} players</span>
          ${room.me ? `<span class="badge badge-accent mono">${room.me.score} pts</span>` : ""}
          ${room.paused ? `<span class="badge badge-paused">PAUSED</span>` : ""}
        </div>
        <div style="display:flex;gap:8px;">
          ${isHost ? `<span class="badge badge-lobby">Host</span>` : ""}
          <button id="leave-room-btn" class="btn btn-secondary btn-sm">Lobby</button>
        </div>
      </div>
    `;
    }

    function wireLeaveButton() {
      const btn = document.getElementById("leave-room-btn");
      if (btn) {
        btn.addEventListener("click", () => {
          state.room = null;
          state.activeRoomCode = "";
          saveStr(LS_ACTIVE_ROOM, "");
          updateReconnectBanner(false);
          stopTimerLoop();
          sendWS({ type: "leave_room" });
          render();
        });
      }
    }

    function renderHostDangerFooter() {
      return `
        <div class="host-danger-row">
          <button type="button" id="host-close-room-btn" class="btn btn-danger-subtle">Close Room…</button>
        </div>
      `;
    }

    function openCloseRoomConfirm(room, hostToken) {
      const modalRoot = document.getElementById("modal-root");
      if (!modalRoot) return;
      modalRoot.innerHTML = `
        <div class="modal-backdrop" id="confirm-close-backdrop">
          <div class="studio-card modal-panel animate-in" role="dialog" aria-modal="true" aria-label="Confirm Close Room" style="max-width:400px;">
            <div class="modal-header">
              <h2 style="margin:0;">Close Room ${esc(room.code)}?</h2>
            </div>
            <p style="margin:0 0 18px;color:var(--text-secondary);font-size:0.94rem;">
              Are you sure you want to close <strong dir="auto">${esc(room.settings.room_name)}</strong>? All connected players will be disconnected and current scores will end.
            </p>
            <div style="display:flex;gap:10px;justify-content:flex-end;flex-wrap:wrap;">
              <button type="button" id="cancel-close-room-btn" class="btn btn-secondary">Cancel</button>
              <button type="button" id="confirm-close-room-btn" class="btn btn-danger">Yes, Close Room</button>
            </div>
          </div>
        </div>
      `;
      const cancelBtn = document.getElementById("cancel-close-room-btn");
      const confirmBtn = document.getElementById("confirm-close-room-btn");
      const backdrop = document.getElementById("confirm-close-backdrop");
      if (cancelBtn) {
        cancelBtn.focus();
        cancelBtn.addEventListener("click", closeModal);
      }
      if (backdrop) {
        backdrop.addEventListener("click", (e) => {
          if (e.target.id === "confirm-close-backdrop") closeModal();
        });
      }
      if (confirmBtn) {
        confirmBtn.addEventListener("click", () => {
          closeModal();
          sendWS({ type: "close_room", code: room.code, host_token: hostToken });
        });
      }
    }

    function wireHostCloseButton(room, hostToken) {
      const btn = document.getElementById("host-close-room-btn");
      if (btn) {
        btn.addEventListener("click", () => openCloseRoomConfirm(room, hostToken));
      }
    }

    function renderRoomLobby(root, room) {
      const hostToken = getHostToken(room.code);
      const isHost = Boolean(hostToken);
      const joinUrl = getJoinUrl(room.code);
      const qrSvg = window.QuizRoomsQR ? window.QuizRoomsQR.toSVG(joinUrl) : "";

      root.innerHTML = `
      ${renderRoomHeader(room)}
      <section class="studio-card question-card animate-in" dir="${room.rtl ? "rtl" : "auto"}">
        <div class="lobby-share-grid">
          <div class="qr-box">
            <div class="qr-white-frame">${qrSvg}</div>
            <span class="badge mono">${esc(room.code)}</span>
          </div>
          <div style="text-align:start;">
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
              ${room.cover_url ? `<img src="${esc(room.cover_url)}" alt="" class="pack-cover-thumb lg" />` : ""}
              <div>
                <h2 style="margin:0 0 4px;" dir="auto">${esc(room.settings.room_name)}</h2>
                <p style="margin:0;color:var(--text-secondary);" dir="auto">
                  ${esc(room.settings.pack_titles.join(" · "))}
                </p>
              </div>
            </div>
            <div style="display:flex;flex-wrap:wrap;gap:8px;margin-block-end:14px;">
              <span class="badge">${room.settings.round_count} Rounds</span>
              <span class="badge">${room.settings.timer_seconds}s Timer</span>
              <span class="badge">${room.settings.answer_mode === "free_text" ? "Free-Text" : "Multiple Choice"}</span>
              ${room.settings.difficulty && room.settings.difficulty !== "random" ? diffBadgeHtml(room.settings.difficulty, "Difficulty: ") : `<span class="badge">Difficulty: Random</span>`}
              <span class="badge">Wagers 1–${room.settings.round_count} (1× each)</span>
              <span class="badge">${room.settings.deduct_wrong_wager === false ? "Wrong: 0 pts" : "Wrong: −Wager"}</span>
              ${room.settings.bonus_round ? (() => {
                const opts = room.settings.bonus_wager_options || [5, 10, 15, 20, 25, 30];
                const lo = opts[0] || 5;
                const hi = opts[opts.length - 1] || 30;
                return `<span class="badge badge-accent">★ Final Bonus (±${lo}–${hi})</span>`;
              })() : ""}
              ${room.settings.unique_answers_only ? `<span class="badge badge-live">No Repeated Answers</span>` : ""}
              <span class="badge">${room.settings.visibility === "private" ? "Private" : "Public"}</span>
            </div>

            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
              <div style="font-size:0.84rem;font-weight:700;color:var(--text-secondary);">Players Ready (${room.players.length})</div>
              <button type="button" id="room-change-avatar-btn" class="btn btn-secondary btn-sm">Change Avatar</button>
            </div>
            <div class="player-roster">
              ${room.players
          .map(
            (p) => `<span class="player-chip ${p.connected ? "" : "offline"}" dir="auto">
                    ${p.avatar_url ? `<img src="${esc(p.avatar_url)}" alt="" class="avatar-img avatar-xs" />` : ""}
                    ${esc(p.nickname)}${p.is_host ? " ★" : ""}
                  </span>`
          )
          .join("")}
            </div>
          </div>
        </div>

        ${isHost
          ? `<div class="host-bar">
                <button id="start-game-btn" class="btn btn-primary" style="flex:1;">Start Game</button>
              </div>
              ${renderHostDangerFooter()}`
          : `<p style="margin-block:18px 0;text-align:center;color:var(--text-secondary);font-weight:700;">
                Waiting for the host to start the game…
              </p>`
        }
      </section>
    `;

      wireLeaveButton();
      const chAvatarBtn = document.getElementById("room-change-avatar-btn");
      if (chAvatarBtn) chAvatarBtn.addEventListener("click", () => openAvatarModal());
      if (isHost) {
        document.getElementById("start-game-btn").addEventListener("click", () => {
          sendWS({ type: "start_game", code: room.code, host_token: hostToken });
        });
        wireHostCloseButton(room, hostToken);
      }
    }

    function renderBonusWagerPhase(root, room) {
      const bw = room.bonus_wager || {};
      const me = room.me;
      const hostToken = getHostToken(room.code);
      const isHost = Boolean(hostToken);
      const options = bw.options || (me && me.available_wagers) || [5, 10, 15, 20, 25, 30];
      const lockedWager = me && typeof me.bonus_wager === "number" ? me.bonus_wager : null;
      if (!options.includes(state.selectedWager)) {
        state.selectedWager = lockedWager && options.includes(lockedWager) ? lockedWager : options[0];
      }

      const standingsHtml = (room.players || [])
        .map(
          (p) => `
          <div class="outcome-row">
            <div style="display:inline-flex;align-items:center;gap:8px;font-weight:800;" dir="auto">
              ${p.avatar_url ? `<img src="${esc(p.avatar_url)}" alt="" class="avatar-img avatar-xs" />` : ""}
              #${p.rank} ${esc(p.nickname)}
              ${p.locked_in ? `<span class="badge badge-live" style="font-size:0.68rem;padding:2px 7px;">✓ Ready</span>` : ""}
            </div>
            <div class="mono" style="font-weight:800;color:var(--accent-primary);">${p.score} pts</div>
          </div>`
        )
        .join("");

      const wagerControlsHtml =
        lockedWager === null
          ? `<div class="wager-section">
              <div class="wager-header">
                <span>★ Choose Your Blind Bonus Stake</span>
                <span class="wager-risk-pill" id="bonus-risk-pill">Risking +${state.selectedWager} / −${state.selectedWager}</span>
              </div>
              <div class="wager-chips">
                ${options
                  .map(
                    (val) => `<button type="button" class="wager-chip ${state.selectedWager === val ? "selected" : ""}" data-bonus-wager="${val}">${val}</button>`
                  )
                  .join("")}
              </div>
              <div style="font-size:0.74rem;color:var(--text-muted);margin-top:8px;text-align:center;">
                Tip: Tap an already-selected stake again to lock in immediately.
              </div>
            </div>
            <div class="sticky-action-bar">
              <button id="lock-bonus-wager-btn" class="btn btn-primary" style="width:100%;">
                Lock In Bonus Wager (±${state.selectedWager} pts)
              </button>
            </div>`
          : `<div class="locked-banner">
              ★ Bonus Wager Locked In: <strong>±${lockedWager} pts</strong>
              <div style="font-size:0.84rem;margin-top:4px;opacity:0.9;">
                ${bw.locked_count || 0} / ${bw.connected_count || room.connected_count || 0} players ready · Waiting for host to reveal the bonus question…
              </div>
            </div>`;

      root.innerHTML = `
      ${renderRoomHeader(room)}
      <section class="studio-card question-card animate-in">
        <div class="question-top">
          <span class="badge" style="background:var(--accent-amber);color:#120d02;font-weight:900;">★ FINAL BONUS WAGER</span>
          <span class="badge mono">${bw.locked_count || 0} / ${bw.connected_count || room.connected_count || 0} ready</span>
        </div>

        <h2 class="question-prompt" style="margin-block:6px 4px;">Place Your Secret Bonus Wager!</h2>
        <p style="margin:0 0 14px;color:var(--text-secondary);font-size:0.94rem;">
          Pick how many points to risk <strong>before</strong> seeing the final question. Answer right to gain <strong>+Wager</strong>, or miss it to lose <strong>−Wager</strong>.
        </p>

        ${wagerControlsHtml}

        <div style="margin-top:18px;">
          <div style="font-size:0.84rem;font-weight:700;color:var(--text-secondary);margin-bottom:8px;">Current Standings</div>
          <div class="outcomes-list">${standingsHtml}</div>
        </div>

        ${isHost
          ? `<div class="host-bar">
                <button id="host-start-bonus-q-btn" class="btn btn-primary" style="flex:1;">
                  Reveal Bonus Question ★
                </button>
              </div>
              ${renderHostDangerFooter()}`
          : ""
        }
      </section>
    `;

      wireLeaveButton();

      if (lockedWager === null) {
        function submitBonusWagerNow() {
          if (window.QuizRoomsSFX) window.QuizRoomsSFX.lockIn();
          sendWS({
            type: "submit_bonus_wager",
            code: room.code,
            session_token: state.sessionToken,
            wager: state.selectedWager,
          });
        }

        root.querySelectorAll("[data-bonus-wager]").forEach((chip) => {
          chip.addEventListener("click", () => {
            const clickedVal = parseInt(chip.dataset.bonusWager, 10);
            if (clickedVal === state.selectedWager) {
              submitBonusWagerNow();
              return;
            }
            state.selectedWager = clickedVal;
            if (window.QuizRoomsSFX) window.QuizRoomsSFX.wagerClick(state.selectedWager);
            root.querySelectorAll("[data-bonus-wager]").forEach((c) => c.classList.remove("selected"));
            chip.classList.add("selected");
            const btn = document.getElementById("lock-bonus-wager-btn");
            if (btn) btn.textContent = `Lock In Bonus Wager (±${state.selectedWager} pts)`;
            const pill = document.getElementById("bonus-risk-pill");
            if (pill) pill.textContent = `Risking +${state.selectedWager} / −${state.selectedWager}`;
          });
        });

        const lockBtn = document.getElementById("lock-bonus-wager-btn");
        if (lockBtn) {
          lockBtn.addEventListener("click", submitBonusWagerNow);
        }
      }

      if (isHost) {
        document.getElementById("host-start-bonus-q-btn").addEventListener("click", () => {
          sendWS({ type: "next_step", code: room.code, host_token: hostToken });
        });
        wireHostCloseButton(room, hostToken);
      }
    }

    function renderQuestionPhase(root, room) {
      const q = room.question;
      const me = room.me;
      const hostToken = getHostToken(room.code);
      const isHost = Boolean(hostToken);
      const locked = Boolean(me && me.submitted);
      const isBonus = Boolean(q.is_bonus);
      const bonusOptions = q.bonus_wager_options || [5, 10, 15, 20, 25, 30];
      const lockedBonusWager = isBonus && me && typeof me.bonus_wager === "number" ? me.bonus_wager : null;
      const poolSize = (me && me.wager_pool_size) || q.regular_rounds || room.settings.round_count || 10;
      const allChips = isBonus ? bonusOptions : Array.from({ length: poolSize }, (_, i) => i + 1);
      const availWagers = isBonus ? bonusOptions : ((me && me.available_wagers) || allChips);
      if (lockedBonusWager !== null) {
        state.selectedWager = lockedBonusWager;
      } else if (!availWagers.includes(state.selectedWager)) {
        state.selectedWager = availWagers.length ? Math.min(...availWagers) : (isBonus ? bonusOptions[0] : 1);
      }
      const letters = ["A", "B", "C", "D", "E", "F"];
      const dir = q.rtl ? "rtl" : "auto";

      const mediaHtml = q.media_url
        ? `<div class="question-media-wrap">
          <div class="media-frame" id="zoom-media-trigger" title="Tap to zoom">
            <img src="${esc(q.media_url)}" alt="Question visual" />
          </div>
        </div>`
        : "";

      const startsWithHtml = q.starts_with
        ? `<div class="starts-with-banner" dir="${dir}">
            <span>${q.rtl ? "يبدأ بحرف" : "Starts with"}</span>
            <span class="starts-with-letter">${esc(q.starts_with)}</span>
          </div>`
        : "";

      let answerInputHtml = "";
      if (!locked) {
        if (room.settings.answer_mode === "multiple_choice" && !q.open_ended) {
          answerInputHtml = `
          <div class="choices-grid">
            ${q.choices
              .map(
                (choice, i) => `
                <button type="button" class="choice-btn ${state.selectedAnswer === choice ? "selected" : ""}" data-choice-idx="${i}">
                  <span class="choice-letter">${letters[i] || i + 1}</span>
                  <span dir="auto">${esc(choice)}</span>
                </button>`
              )
              .join("")}
          </div>`;
        } else {
          answerInputHtml = `
          <div style="margin-block-end:16px;">
            <label class="field-label" for="free-text-input">${q.rtl ? "اكتب إجابتك" : "Type your answer"}</label>
            <input id="free-text-input" class="text-input" dir="auto" autocomplete="off"
              placeholder="${q.rtl ? "اكتب إجابتك هنا…" : "Type your answer here…"}"
              value="${esc(state.selectedAnswer)}" />
          </div>`;
        }
      }

      const deductsOnWrong = isBonus || room.settings.deduct_wrong_wager !== false;
      function riskText(val) {
        return deductsOnWrong ? `Risking +${val} / −${val}` : `Risking +${val} / 0`;
      }

      function renderChipBtn(val) {
        const available = availWagers.includes(val);
        const selected = state.selectedWager === val && available;
        return `<button type="button" class="wager-chip ${selected ? "selected" : ""}"
              data-wager="${val}" title="${esc(riskText(val))}" ${available ? "" : "disabled"}>${val}</button>`;
      }

      let chipsGridHtml = "";
      if (!isBonus && poolSize >= 15) {
        const lowEnd = Math.ceil(poolSize / 3);
        const midEnd = Math.ceil((poolSize * 2) / 3);
        const tiers = [
          { cls: "tier-low", label: `Low · 1–${lowEnd}`, vals: allChips.filter((v) => v <= lowEnd) },
          { cls: "tier-mid", label: `Mid · ${lowEnd + 1}–${midEnd}`, vals: allChips.filter((v) => v > lowEnd && v <= midEnd) },
          { cls: "tier-high", label: `High · ${midEnd + 1}–${poolSize}`, vals: allChips.filter((v) => v > midEnd) },
        ];
        chipsGridHtml = `<div class="wager-tiers">${tiers
          .map((t) => {
            const left = t.vals.filter((v) => availWagers.includes(v)).length;
            return `<div class="wager-tier ${t.cls}">
              <div class="wager-tier-head">
                <span>${t.label}</span>
                <span>${left} left</span>
              </div>
              <div class="wager-chips">${t.vals.map(renderChipBtn).join("")}</div>
            </div>`;
          })
          .join("")}</div>`;
      } else {
        chipsGridHtml = `<div class="wager-chips">${allChips.map(renderChipBtn).join("")}</div>`;
      }

      const wagerHeaderLabel = isBonus
        ? "★ Bonus Score Wager"
        : `Confidence Wager (1–${poolSize} · use each once)`;
      const lockInLabel = isBonus
        ? `Lock In Bonus Answer (±${state.selectedWager} pts)`
        : `Lock In Answer & Wager (${state.selectedWager} pts)`;

      const stickySubmitBar = `
        <div class="sticky-action-bar">
          <div class="sticky-risk-row">
            <span id="sticky-risk-pill" class="wager-risk-pill">${riskText(state.selectedWager)}</span>
            <span style="font-size:0.74rem;color:var(--text-muted);">Tap selected choice/chip again for instant lock-in</span>
          </div>
          <button id="lock-in-btn" class="btn btn-primary" style="width:100%;">
            ${lockInLabel}
          </button>
        </div>`;

      const wagerHtml = !locked
        ? isBonus && lockedBonusWager !== null
          ? `<div class="wager-section" style="border-color:var(--accent-amber);background:var(--accent-amber-soft);">
              <div class="wager-header" style="margin:0;">
                <span>★ Your Locked Bonus Wager (+${lockedBonusWager} if right · −${lockedBonusWager} if wrong)</span>
                <span class="badge mono" style="background:var(--accent-amber);color:#120d02;font-weight:900;">±${lockedBonusWager} pts</span>
              </div>
            </div>
            ${stickySubmitBar}`
          : `<div class="wager-section">
          <div class="wager-header">
            <span>${wagerHeaderLabel}</span>
            <span id="wager-risk-badge" class="wager-risk-pill">${riskText(state.selectedWager)}</span>
          </div>
          ${chipsGridHtml}
        </div>
        ${stickySubmitBar}`
        : `<div class="locked-banner">
          Locked in: <span dir="auto">${esc(me.my_submission.answer)}</span> · ${isBonus ? `Bonus Wager ±${me.my_submission.wager} pts` : `Wager ${me.my_submission.wager} pts`}
          <div style="font-size:0.84rem;margin-top:4px;opacity:0.9;">
            ${room.locked_in_count} / ${room.connected_count} players locked in
          </div>
        </div>`;

      const roundBadgeHtml = isBonus
        ? `<span class="badge" style="background:var(--accent-amber);color:#120d02;font-weight:900;">★ FINAL BONUS QUESTION</span>`
        : `<span class="badge badge-live">Round ${q.round_number} / ${q.regular_rounds || q.total_rounds}</span>`;

      root.innerHTML = `
      ${renderRoomHeader(room)}
      <section class="studio-card question-card animate-in" dir="${dir}">
        <div class="question-top">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            ${roundBadgeHtml}
            ${diffBadgeHtml(q.difficulty)}
            ${room.settings.unique_answers_only ? `<span class="badge badge-accent">Unique Answers Only</span>` : ""}
          </div>
          <span id="timer-readout" class="timer-readout">${Math.ceil(q.remaining_seconds || 0)}s</span>
        </div>
        <div class="timer-track"><div id="timer-fill" class="timer-fill"></div></div>
        ${mediaHtml}
        <h2 class="question-prompt" dir="auto">${esc(q.prompt)}</h2>
        ${startsWithHtml}
        ${answerInputHtml}
        ${wagerHtml}

        ${isHost
          ? `<div class="host-bar">
                <button id="host-pause-btn" class="btn btn-secondary btn-sm" style="flex:1;">${room.paused ? "Resume Timer" : "Pause Timer"}</button>
                <button id="host-reveal-btn" class="btn btn-secondary btn-sm" style="flex:1;">Reveal Now</button>
              </div>
              ${renderHostDangerFooter()}`
          : ""
        }
      </section>
    `;

      wireLeaveButton();

      const zoomTrigger = document.getElementById("zoom-media-trigger");
      if (zoomTrigger) {
        zoomTrigger.addEventListener("click", () => openLightbox(q.media_url));
      }

      if (!locked) {
        function submitAnswerNow() {
          if (!state.selectedAnswer.trim()) {
            showToast("Choose or enter an answer before locking in!", true);
            return;
          }
          if (window.QuizRoomsSFX) window.QuizRoomsSFX.lockIn();
          sendWS({
            type: "submit_answer",
            code: room.code,
            session_token: state.sessionToken,
            answer: state.selectedAnswer,
            wager: state.selectedWager,
          });
        }

        root.querySelectorAll("[data-choice-idx]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const picked = q.choices[parseInt(btn.dataset.choiceIdx, 10)];
            if (state.selectedAnswer === picked) {
              submitAnswerNow();
              return;
            }
            state.selectedAnswer = picked;
            if (window.QuizRoomsSFX && window.QuizRoomsSFX.choiceTap) window.QuizRoomsSFX.choiceTap();
            root.querySelectorAll("[data-choice-idx]").forEach((b) => b.classList.remove("selected"));
            btn.classList.add("selected");
          });
        });

        const freeInput = document.getElementById("free-text-input");
        if (freeInput) {
          freeInput.addEventListener("input", () => {
            state.selectedAnswer = freeInput.value;
          });
          freeInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              submitAnswerNow();
            }
          });
        }

        root.querySelectorAll("[data-wager]").forEach((chip) => {
          chip.addEventListener("click", () => {
            const pickedVal = parseInt(chip.dataset.wager, 10);
            if (pickedVal === state.selectedWager && state.selectedAnswer.trim()) {
              submitAnswerNow();
              return;
            }
            state.selectedWager = pickedVal;
            if (window.QuizRoomsSFX) window.QuizRoomsSFX.wagerClick(state.selectedWager);
            root.querySelectorAll("[data-wager]").forEach((c) => c.classList.remove("selected"));
            chip.classList.add("selected");
            const lockBtn = document.getElementById("lock-in-btn");
            if (lockBtn) {
              lockBtn.textContent = isBonus
                ? `Lock In Bonus Answer (±${state.selectedWager} pts)`
                : `Lock In Answer & Wager (${state.selectedWager} pts)`;
            }
            const riskLabel = riskText(state.selectedWager);
            const riskBadge = document.getElementById("wager-risk-badge");
            if (riskBadge) riskBadge.textContent = riskLabel;
            const stickyRisk = document.getElementById("sticky-risk-pill");
            if (stickyRisk) stickyRisk.textContent = riskLabel;
          });
        });

        document.getElementById("lock-in-btn").addEventListener("click", submitAnswerNow);
      }

      if (isHost) {
        document.getElementById("host-pause-btn").addEventListener("click", () => {
          sendWS({ type: "pause_room", code: room.code, host_token: hostToken });
        });
        document.getElementById("host-reveal-btn").addEventListener("click", () => {
          sendWS({ type: "next_step", code: room.code, host_token: hostToken });
        });
        wireHostCloseButton(room, hostToken);
      }
    }

    function renderRevealPhase(root, room) {
      const rev = room.reveal;
      const hostToken = getHostToken(room.code);
      const isHost = Boolean(hostToken);
      const isFreeText = room.settings.answer_mode === "free_text" || Boolean(rev.open_ended);
      const isLast = rev.round_index + 1 >= rev.total_rounds;
      const nextIsBonus = !isLast && Boolean(room.settings.bonus_round) && (rev.round_index + 2 === rev.total_rounds);

      const outcomesHtml = rev.outcomes
        .map((o) => {
          const deltaClass = o.delta > 0 ? "delta-pos" : o.delta < 0 ? "delta-neg" : "delta-zero";
          const deltaLabel = o.delta > 0 ? `+${o.delta}` : String(o.delta);
          const dupTag =
            o.outcome === "duplicate"
              ? ` <span class="badge badge-live" style="font-size:0.68rem;padding:2px 7px;" title="${esc(o.duplicate_reason || "Repeated answer")}">Repeated</span>`
              : "";
          const overrideBtn =
            isHost && isFreeText && o.outcome !== "timeout"
              ? `<button class="btn btn-sm ${o.is_correct ? "btn-danger" : "btn-success"}"
                data-override-token="${esc(o.session_token)}" data-override-approve="${!o.is_correct}">
                ${o.is_correct ? "Mark Wrong" : "Approve"}
              </button>`
              : "";
          return `
          <div class="outcome-row ${esc(o.outcome)}">
            <div style="display:flex;align-items:center;gap:10px;text-align:start;min-width:0;flex:1;">
              ${o.avatar_url ? `<img src="${esc(o.avatar_url)}" alt="" class="avatar-img avatar-sm" />` : ""}
              <div style="min-width:0;">
                <div style="font-weight:800;" dir="auto">${esc(o.nickname)}${dupTag}</div>
                <div style="font-size:0.84rem;color:var(--text-secondary);" dir="auto">
                  ${o.answer ? esc(o.answer) : "<em>No answer</em>"} · Wager ${o.wager}
                  ${o.duplicate_reason ? ` · <em>${esc(o.duplicate_reason)}</em>` : ""}
                  ${o.overridden ? " · <em>Host override</em>" : ""}
                </div>
              </div>
            </div>
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end;">
              ${overrideBtn}
              <span class="delta-pill ${deltaClass}">${deltaLabel}</span>
              <span class="mono" style="font-weight:800;min-width:46px;text-align:end;">${o.score}</span>
            </div>
          </div>`;
        })
        .join("");

      root.innerHTML = `
      ${renderRoomHeader(room)}
      <section class="studio-card question-card animate-in" dir="${rev.rtl ? "rtl" : "auto"}">
        <div class="question-top">
          ${rev.is_bonus
            ? `<span class="badge" style="background:var(--accent-amber);color:#120d02;font-weight:900;">★ Final Bonus Reveal</span>`
            : `<span class="badge badge-lobby">Answer Reveal · Round ${rev.round_index + 1}/${room.settings.bonus_round ? rev.total_rounds - 1 : rev.total_rounds}</span>`}
        </div>
        <div class="reveal-answer-box">
          <div class="reveal-answer-label">${rev.open_ended ? (rev.rtl ? "قاعدة الجولة المفتوحة" : "Open-Ended Round Rule") : "Correct Answer"}</div>
          <div class="reveal-answer-text" dir="auto">${esc(rev.correct_answer)}</div>
          ${rev.explanation ? `<p class="reveal-explanation" dir="auto">${esc(rev.explanation)}</p>` : ""}
        </div>

        <div class="outcomes-list">${outcomesHtml}</div>

        ${isHost
          ? `<div class="host-bar">
                <button id="host-next-btn" class="btn btn-primary" style="flex:1;">
                  ${isLast ? "Show Finale Podium" : nextIsBonus ? "Place Final Bonus Wagers ★" : "Next Question"}
                </button>
              </div>
              ${renderHostDangerFooter()}`
          : `<p style="margin:0;text-align:center;color:var(--text-muted);font-weight:700;">
                Waiting for host to continue…
              </p>`
        }
      </section>
    `;

      wireLeaveButton();
      const chAvatarBtn = document.getElementById("room-change-avatar-btn");
      if (chAvatarBtn) chAvatarBtn.addEventListener("click", () => openAvatarModal());
      if (isHost) {
        root.querySelectorAll("[data-override-token]").forEach((btn) => {
          btn.addEventListener("click", () => {
            sendWS({
              type: "override_answer",
              code: room.code,
              host_token: hostToken,
              target_session_token: btn.dataset.overrideToken,
              approve: btn.dataset.overrideApprove === "true",
            });
          });
        });
        document.getElementById("host-next-btn").addEventListener("click", () => {
          sendWS({ type: "next_step", code: room.code, host_token: hostToken });
        });
        wireHostCloseButton(room, hostToken);
      }
    }

    function renderFinalePhase(root, room) {
      const fin = room.finale;
      const hostToken = getHostToken(room.code);
      const isHost = Boolean(hostToken);
      const medals = ["#1", "#2", "#3"];

      const podiumHtml = fin.podium
        .map(
          (p, idx) => `
        <div class="podium-slot rank-${idx + 1}">
          ${p.avatar_url ? `<img src="${esc(p.avatar_url)}" alt="" class="avatar-img ${idx === 0 ? "avatar-lg" : "avatar-md"}" style="margin-bottom:6px;" />` : ""}
          <div class="podium-medal">${medals[idx] || "#" + (idx + 1)}</div>
          <div class="podium-name" dir="auto">${esc(p.nickname)}</div>
          <div class="podium-score">${p.score} pts</div>
        </div>`
        )
        .join("");

      const awardsHtml = fin.awards
        .map(
          (a) => `
        <div class="award-card" style="display:flex;align-items:center;gap:10px;">
          ${a.avatar_url ? `<img src="${esc(a.avatar_url)}" alt="" class="avatar-img avatar-sm" />` : ""}
          <div>
            <div class="award-title">${esc(a.title)}</div>
            <div class="award-winner" dir="auto">${esc(a.nickname)}</div>
            <div class="award-sub">${esc(a.subtitle)}</div>
          </div>
        </div>`
        )
        .join("");

      root.innerHTML = `
      ${renderRoomHeader(room)}
      <section class="studio-card question-card animate-in">
        <div style="text-align:center;">
          <span class="badge badge-live">Game Complete</span>
          <h2 style="margin:10px 0 4px;font-size:1.7rem;">Final Podium</h2>
        </div>

        <div class="podium-grid">${podiumHtml}</div>
        ${awardsHtml ? `<div class="awards-grid">${awardsHtml}</div>` : ""}

        <div class="outcomes-list">
          ${fin.leaderboard
          .map(
            (row) => `
              <div class="outcome-row">
                <div style="display:inline-flex;align-items:center;gap:8px;font-weight:800;" dir="auto">
                  ${row.avatar_url ? `<img src="${esc(row.avatar_url)}" alt="" class="avatar-img avatar-xs" />` : ""}
                  #${row.rank} ${esc(row.nickname)}
                </div>
                <div class="mono" style="font-weight:800;color:var(--accent-primary);">${row.score} pts</div>
              </div>`
          )
          .join("")}
        </div>

        ${isHost
          ? `<div class="host-bar">
                <button id="play-again-btn" class="btn btn-primary" style="flex:1;">Play Again</button>
              </div>
              ${renderHostDangerFooter()}`
          : ""
        }
      </section>
    `;

      wireLeaveButton();
      const chAvatarBtn = document.getElementById("room-change-avatar-btn");
      if (chAvatarBtn) chAvatarBtn.addEventListener("click", () => openAvatarModal());
      if (isHost) {
        document.getElementById("play-again-btn").addEventListener("click", () => {
          sendWS({ type: "play_again", code: room.code, host_token: hostToken });
        });
        wireHostCloseButton(room, hostToken);
      }
    }

    function openLightbox(src) {
      const box = document.getElementById("lightbox-root");
      box.innerHTML = `
      <div class="lightbox" id="lightbox-close">
        <div class="media-frame">
          <img src="${esc(src)}" alt="Zoomed visual" />
        </div>
      </div>`;
      document.getElementById("lightbox-close").addEventListener("click", () => {
        box.innerHTML = "";
      });
    }

    function syncSoundButton(btn, enabled) {
      if (!btn) return;
      btn.textContent = enabled ? "Sound: On" : "Sound: Off";
      btn.setAttribute("aria-pressed", String(enabled));
    }

    document.addEventListener("DOMContentLoaded", () => {
      if (window.QuizRoomsTheme) {
        window.QuizRoomsTheme.mountPill(document.getElementById("theme-pill-mount"));
      }
      const soundBtn = document.getElementById("play-sound-btn");
      if (soundBtn && window.QuizRoomsSFX) {
        syncSoundButton(soundBtn, window.QuizRoomsSFX.init("quizrooms_sound_play"));
        soundBtn.addEventListener("click", () => {
          syncSoundButton(soundBtn, window.QuizRoomsSFX.toggle());
        });
      }
      render();
      fetchBootstrap();
      connectWS();
    });
  }) ();
