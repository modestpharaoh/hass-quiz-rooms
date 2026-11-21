/* QuizRooms Guest Lobby & Player SPA (/quizrooms/play) */
(function () {
  const LS_SESSION = "quizrooms_session_token";
  const LS_NICK = "quizrooms_nickname";
  const LS_HOSTS = "quizrooms_host_tokens";
  const LS_ACTIVE_ROOM = "quizrooms_active_room";

  const state = {
    ws: null,
    connected: false,
    lanBaseUrl: window.location.origin,
    packs: [],
    lobby: { rooms: [], active_rooms: 0, max_rooms: 5, can_create_room: true },
    room: null,
    sessionToken: loadStr(LS_SESSION) || makeToken(),
    nickname: loadStr(LS_NICK) || "",
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
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const role = state.activeRoomCode ? "room" : "lobby";
    const url = `${proto}//${window.location.host}/quizrooms/ws?role=${role}`;
    const ws = new WebSocket(url);
    state.ws = ws;

    ws.onopen = () => {
      state.connected = true;
      updateConnStatus("Live · Local Network");
      // Auto-rejoin previous room if still active
      if (state.activeRoomCode) {
        sendWS({
          type: "join_room",
          code: state.activeRoomCode,
          session_token: state.sessionToken,
          nickname: state.nickname || undefined,
        });
      }
    };

    ws.onclose = () => {
      state.connected = false;
      updateConnStatus("Reconnecting…");
      setTimeout(connectWS, 1800);
    };

    ws.onerror = () => {
      updateConnStatus("Connection interrupted");
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

  async function fetchBootstrap() {
    try {
      const resp = await fetch("/quizrooms/api/public/bootstrap", { cache: "no-store" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (data.lan_base_url) state.lanBaseUrl = data.lan_base_url;
      if (data.lobby) state.lobby = data.lobby;
      if (Array.isArray(data.packs)) {
        state.packs = data.packs;
        state.packsLoaded = true;
        state.packsError = "";
      }
      if (!state.room) render();
      if (state.showCreateModal) renderModalPackList();
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
        if (Array.isArray(msg.packs)) {
          state.packs = msg.packs;
          state.packsLoaded = true;
          state.packsError = "";
        }
        if (!state.room) render();
        if (state.showCreateModal) renderModalPackList();
        break;

      case "lobby_update":
        if (msg.lobby) state.lobby = msg.lobby;
        if (Array.isArray(msg.packs)) {
          state.packs = msg.packs;
          state.packsLoaded = true;
          state.packsError = "";
        }
        if (!state.room) render();
        if (state.showCreateModal) renderModalPackList();
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
    state.room = room;

    if (room.question && room.question.server_now_ms) {
      state.serverOffsetMs = room.question.server_now_ms - Date.now();
    }

    // Reset local answer/wager selection when moving to a new question
    if (room.phase === "question" && room.question) {
      const roundKey = `${room.code}:${room.question.index}`;
      if (state.lastRoundKey !== roundKey) {
        state.lastRoundKey = roundKey;
        state.lastCountdownSec = -1;
        state.selectedAnswer = "";
        const avail = (room.me && room.me.available_wagers) || [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
        if (!avail.includes(state.selectedWager)) {
          state.selectedWager = avail[Math.floor(avail.length / 2)] || avail[0] || 1;
        }
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
            } else if (myOutcome.outcome === "wrong") {
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
                  <div>
                    <h3 class="room-card-name" dir="auto">${esc(r.name)}</h3>
                    <p class="room-card-packs" dir="auto">${esc((r.pack_titles || []).join(" · "))}</p>
                  </div>
                  <span class="room-code-chip">${esc(r.code)}</span>
                </div>
                <div class="room-card-foot">
                  <div style="display:flex;gap:6px;flex-wrap:wrap;">
                    <span class="badge ${statusClass}">${esc(statusText)}</span>
                    <span class="badge">${r.player_count} player${r.player_count === 1 ? "" : "s"}</span>
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
            <label class="field-label" for="lobby-nickname">Your Nickname</label>
            <input id="lobby-nickname" class="text-input" dir="auto" maxlength="24"
              placeholder="e.g., Alex or سارة" value="${esc(state.nickname)}" />
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
        sendWS({ type: "join_room", code, session_token: state.sessionToken, nickname: nick });
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
          });
        });
      });
    }

    function renderModalPackList() {
      const listEl = document.getElementById("cr-pack-list");
      const countBadge = document.getElementById("cr-pack-count");
      if (!listEl) return;

      const prevChecked = new Set(
        Array.from(listEl.querySelectorAll('input[name="pack_id"]:checked')).map((el) => el.value)
      );

      if (countBadge) {
        countBadge.textContent = `${state.packs.length} available`;
      }

      if (!state.packs.length) {
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

      listEl.innerHTML = state.packs
        .map((p, idx) => {
          const checked = prevChecked.size > 0 ? prevChecked.has(p.pack_id) : idx === 0;
          return `
        <label class="pack-check-item" dir="${p.rtl ? "rtl" : "auto"}">
          <input type="checkbox" name="pack_id" value="${esc(p.pack_id)}" ${checked ? "checked" : ""} />
          <div style="flex:1;text-align:start;">
            <div style="font-weight:800;">${esc(p.title)}</div>
            <div style="font-size:0.78rem;color:var(--text-muted);">
              ${p.question_count} Qs · ${esc(p.language.toUpperCase())}${p.has_media ? " · Visual" : ""}
            </div>
          </div>
        </label>`;
        })
        .join("");
    }

    function openCreateModal() {
      state.showCreateModal = true;
      const modalRoot = document.getElementById("modal-root");

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
                <span class="badge" id="cr-pack-count">${state.packs.length} available</span>
              </div>
              <div id="cr-pack-list" class="pack-checklist"></div>
            </div>

            <div class="form-row-2">
              <div>
                <label class="field-label" for="cr-rounds">Rounds</label>
                <input id="cr-rounds" type="number" min="1" max="50" value="10" class="text-input mono" />
              </div>
              <div>
                <label class="field-label" for="cr-timer">Question Timer</label>
                <select id="cr-timer" class="select-input">
                  <option value="20">20 seconds</option>
                  <option value="30" selected>30 seconds</option>
                  <option value="45">45 seconds</option>
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
                <label class="field-label" for="cr-wager-mode">Confidence Wager</label>
                <select id="cr-wager-mode" class="select-input">
                  <option value="free_1_to_10" selected>1–10 Any Round</option>
                  <option value="pool">One-Time Chip Pool</option>
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
                <label class="toggle-line" style="width:100%;cursor:pointer;">
                  <span style="font-size:0.86rem;font-weight:700;">Allow Late Joiners</span>
                  <input id="cr-late-join" type="checkbox" checked style="width:18px;height:18px;accent-color:var(--accent-primary);" />
                </label>
              </div>
            </div>

            <button type="submit" class="btn btn-primary" style="width:100%;">Create &amp; Open Lobby</button>
          </form>
        </div>
      </div>
    `;

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
        const selectedPacks = Array.from(
          modalRoot.querySelectorAll('input[name="pack_id"]:checked')
        ).map((el) => el.value);
        if (!selectedPacks.length) {
          showToast("Select at least one trivia pack.", true);
          return;
        }
        sendWS({
          type: "create_room",
          session_token: state.sessionToken,
          nickname: state.nickname,
          room_name: document.getElementById("cr-name").value.trim(),
          pack_ids: selectedPacks,
          round_count: parseInt(document.getElementById("cr-rounds").value, 10) || 10,
          timer_seconds: parseInt(document.getElementById("cr-timer").value, 10) || 30,
          answer_mode: document.getElementById("cr-answer-mode").value,
          wager_mode: document.getElementById("cr-wager-mode").value,
          visibility: document.getElementById("cr-visibility").value,
          allow_late_join: document.getElementById("cr-late-join").checked,
        });
      });
    }

    function closeModal() {
      state.showCreateModal = false;
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
          stopTimerLoop();
          sendWS({ type: "leave_room" });
          render();
        });
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
            <h2 style="margin:0 0 8px;" dir="auto">${esc(room.settings.room_name)}</h2>
            <p style="margin:0 0 12px;color:var(--text-secondary);" dir="auto">
              ${esc(room.settings.pack_titles.join(" · "))}
            </p>
            <div style="display:flex;flex-wrap:wrap;gap:8px;margin-block-end:14px;">
              <span class="badge">${room.settings.round_count} Rounds</span>
              <span class="badge">${room.settings.timer_seconds}s Timer</span>
              <span class="badge">${room.settings.answer_mode === "free_text" ? "Free-Text" : "Multiple Choice"}</span>
              <span class="badge">${room.settings.wager_mode === "pool" ? "Wager Chip Pool" : "1–10 Wagers"}</span>
              <span class="badge">${room.settings.visibility === "private" ? "Private" : "Public"}</span>
            </div>

            <div style="font-size:0.84rem;font-weight:700;color:var(--text-secondary);">Players Ready (${room.players.length})</div>
            <div class="player-roster">
              ${room.players
          .map(
            (p) => `<span class="player-chip ${p.connected ? "" : "offline"}" dir="auto">
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
                <button id="close-room-btn" class="btn btn-danger">Close Room</button>
              </div>`
          : `<p style="margin-block:18px 0;text-align:center;color:var(--text-secondary);font-weight:700;">
                Waiting for the host to start the game…
              </p>`
        }
      </section>
    `;

      wireLeaveButton();
      if (isHost) {
        document.getElementById("start-game-btn").addEventListener("click", () => {
          sendWS({ type: "start_game", code: room.code, host_token: hostToken });
        });
        document.getElementById("close-room-btn").addEventListener("click", () => {
          sendWS({ type: "close_room", code: room.code, host_token: hostToken });
        });
      }
    }

    function renderQuestionPhase(root, room) {
      const q = room.question;
      const me = room.me;
      const hostToken = getHostToken(room.code);
      const isHost = Boolean(hostToken);
      const locked = Boolean(me && me.submitted);
      const availWagers = (me && me.available_wagers) || [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
      const allChips = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
      const letters = ["A", "B", "C", "D", "E", "F"];
      const dir = q.rtl ? "rtl" : "auto";

      const mediaHtml = q.media_url
        ? `<div class="question-media-wrap">
          <div class="media-frame" id="zoom-media-trigger" title="Tap to zoom">
            <img src="${esc(q.media_url)}" alt="Question visual" />
          </div>
        </div>`
        : "";

      let answerInputHtml = "";
      if (!locked) {
        if (room.settings.answer_mode === "multiple_choice") {
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
            <label class="field-label" for="free-text-input">Type your answer</label>
            <input id="free-text-input" class="text-input" dir="auto" autocomplete="off"
              placeholder="${q.rtl ? "اكتب إجابتك هنا…" : "Type your answer here…"}"
              value="${esc(state.selectedAnswer)}" />
          </div>`;
        }
      }

      const wagerHtml = !locked
        ? `<div class="wager-section">
          <div class="wager-header">
            <span>Confidence Wager ${room.settings.wager_mode === "pool" ? "(One-Time Chips)" : "(1–10 pts)"}</span>
            <span class="badge badge-accent mono">${state.selectedWager} pts</span>
          </div>
          <div class="wager-chips">
            ${allChips
          .map((val) => {
            const available = availWagers.includes(val);
            const selected = state.selectedWager === val && available;
            return `<button type="button" class="wager-chip ${selected ? "selected" : ""}"
                  data-wager="${val}" ${available ? "" : "disabled"}>${val}</button>`;
          })
          .join("")}
          </div>
        </div>
        <button id="lock-in-btn" class="btn btn-primary" style="width:100%;">
          Lock In Answer &amp; Wager (${state.selectedWager} pts)
        </button>`
        : `<div class="locked-banner">
          Locked in: <span dir="auto">${esc(me.my_submission.answer)}</span> · Wager ${me.my_submission.wager} pts
          <div style="font-size:0.84rem;margin-top:4px;opacity:0.9;">
            ${room.locked_in_count} / ${room.connected_count} players locked in
          </div>
        </div>`;

      root.innerHTML = `
      ${renderRoomHeader(room)}
      <section class="studio-card question-card animate-in" dir="${dir}">
        <div class="question-top">
          <span class="badge badge-live">Round ${q.round_number} / ${q.total_rounds}</span>
          <span id="timer-readout" class="timer-readout">${Math.ceil(q.remaining_seconds || 0)}s</span>
        </div>
        <div class="timer-track"><div id="timer-fill" class="timer-fill"></div></div>
        ${mediaHtml}
        <h2 class="question-prompt" dir="auto">${esc(q.prompt)}</h2>
        ${answerInputHtml}
        ${wagerHtml}

        ${isHost
          ? `<div class="host-bar">
                <button id="host-pause-btn" class="btn btn-secondary btn-sm">${room.paused ? "Resume Timer" : "Pause Timer"}</button>
                <button id="host-reveal-btn" class="btn btn-secondary btn-sm">Reveal Now</button>
                <button id="host-close-btn" class="btn btn-danger btn-sm">Close Room</button>
              </div>`
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
        root.querySelectorAll("[data-choice-idx]").forEach((btn) => {
          btn.addEventListener("click", () => {
            state.selectedAnswer = q.choices[parseInt(btn.dataset.choiceIdx, 10)];
            root.querySelectorAll("[data-choice-idx]").forEach((b) => b.classList.remove("selected"));
            btn.classList.add("selected");
          });
        });

        const freeInput = document.getElementById("free-text-input");
        if (freeInput) {
          freeInput.addEventListener("input", () => {
            state.selectedAnswer = freeInput.value;
          });
        }

        root.querySelectorAll("[data-wager]").forEach((chip) => {
          chip.addEventListener("click", () => {
            state.selectedWager = parseInt(chip.dataset.wager, 10);
            if (window.QuizRoomsSFX) window.QuizRoomsSFX.wagerClick(state.selectedWager);
            root.querySelectorAll("[data-wager]").forEach((c) => c.classList.remove("selected"));
            chip.classList.add("selected");
            const lockBtn = document.getElementById("lock-in-btn");
            if (lockBtn) lockBtn.textContent = `Lock In Answer & Wager (${state.selectedWager} pts)`;
            const badge = root.querySelector(".wager-header .badge");
            if (badge) badge.textContent = `${state.selectedWager} pts`;
          });
        });

        document.getElementById("lock-in-btn").addEventListener("click", () => {
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
        });
      }

      if (isHost) {
        document.getElementById("host-pause-btn").addEventListener("click", () => {
          sendWS({ type: "pause_room", code: room.code, host_token: hostToken });
        });
        document.getElementById("host-reveal-btn").addEventListener("click", () => {
          sendWS({ type: "next_step", code: room.code, host_token: hostToken });
        });
        document.getElementById("host-close-btn").addEventListener("click", () => {
          sendWS({ type: "close_room", code: room.code, host_token: hostToken });
        });
      }
    }

    function renderRevealPhase(root, room) {
      const rev = room.reveal;
      const hostToken = getHostToken(room.code);
      const isHost = Boolean(hostToken);
      const isFreeText = room.settings.answer_mode === "free_text";
      const isLast = rev.round_index + 1 >= rev.total_rounds;

      const outcomesHtml = rev.outcomes
        .map((o) => {
          const deltaClass = o.delta > 0 ? "delta-pos" : o.delta < 0 ? "delta-neg" : "delta-zero";
          const deltaLabel = o.delta > 0 ? `+${o.delta}` : String(o.delta);
          const overrideBtn =
            isHost && isFreeText && o.outcome !== "timeout"
              ? `<button class="btn btn-sm ${o.is_correct ? "btn-danger" : "btn-success"}"
                data-override-token="${esc(o.session_token)}" data-override-approve="${!o.is_correct}">
                ${o.is_correct ? "Mark Wrong" : "Approve"}
              </button>`
              : "";
          return `
          <div class="outcome-row ${esc(o.outcome)}">
            <div style="text-align:start;min-width:0;flex:1;">
              <div style="font-weight:800;" dir="auto">${esc(o.nickname)}</div>
              <div style="font-size:0.84rem;color:var(--text-secondary);" dir="auto">
                ${o.answer ? esc(o.answer) : "<em>No answer</em>"} · Wager ${o.wager}
                ${o.overridden ? " · <em>Host override</em>" : ""}
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
          <span class="badge badge-lobby">Answer Reveal · Round ${rev.round_index + 1}/${rev.total_rounds}</span>
        </div>
        <div class="reveal-answer-box">
          <div class="reveal-answer-label">Correct Answer</div>
          <div class="reveal-answer-text" dir="auto">${esc(rev.correct_answer)}</div>
          ${rev.explanation ? `<p class="reveal-explanation" dir="auto">${esc(rev.explanation)}</p>` : ""}
        </div>

        <div class="outcomes-list">${outcomesHtml}</div>

        ${isHost
          ? `<div class="host-bar">
                <button id="host-next-btn" class="btn btn-primary" style="flex:1;">
                  ${isLast ? "Show Finale Podium" : "Next Question"}
                </button>
                <button id="host-close-btn" class="btn btn-danger">Close Room</button>
              </div>`
          : `<p style="margin:0;text-align:center;color:var(--text-muted);font-weight:700;">
                Waiting for host to continue…
              </p>`
        }
      </section>
    `;

      wireLeaveButton();
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
        document.getElementById("host-close-btn").addEventListener("click", () => {
          sendWS({ type: "close_room", code: room.code, host_token: hostToken });
        });
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
          <div class="podium-medal">${medals[idx] || "#" + (idx + 1)}</div>
          <div class="podium-name" dir="auto">${esc(p.nickname)}</div>
          <div class="podium-score">${p.score} pts</div>
        </div>`
        )
        .join("");

      const awardsHtml = fin.awards
        .map(
          (a) => `
        <div class="award-card">
          <div class="award-title">${esc(a.title)}</div>
          <div class="award-winner" dir="auto">${esc(a.nickname)}</div>
          <div class="award-sub">${esc(a.subtitle)}</div>
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
                <div style="font-weight:800;" dir="auto">#${row.rank} ${esc(row.nickname)}</div>
                <div class="mono" style="font-weight:800;color:var(--accent-primary);">${row.score} pts</div>
              </div>`
          )
          .join("")}
        </div>

        ${isHost
          ? `<div class="host-bar">
                <button id="play-again-btn" class="btn btn-primary" style="flex:1;">Play Again</button>
                <button id="close-room-btn" class="btn btn-danger">Close Room</button>
              </div>`
          : ""
        }
      </section>
    `;

      wireLeaveButton();
      if (isHost) {
        document.getElementById("play-again-btn").addEventListener("click", () => {
          sendWS({ type: "play_again", code: room.code, host_token: hostToken });
        });
        document.getElementById("close-room-btn").addEventListener("click", () => {
          sendWS({ type: "close_room", code: room.code, host_token: hostToken });
        });
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
