/* QuizRooms 16:9 TV Dashboard (/quizrooms/tv) — Overview & Follow Modes */
(function () {
  const state = {
    ws: null,
    wsConnected: false,
    lanBaseUrl: window.location.origin,
    tv: { mode: "overview", room_code: null, theme: null },
    lobby: { rooms: [], active_rooms: 0, max_rooms: 5 },
    packs: [],
    room: null,
    overscan: false,
    serverOffsetMs: 0,
    timerRaf: null,
    lastCountdownSec: -1,
    lastPhaseKey: "",
    lastLockedCount: 0,
    prevRanksByRoom: {},
    revealHighlights: null,
    lastRevealSig: "",
    deltaFadeTimer: null,
    deltaClearTimer: null,
    attractPage: 0,
    attractTimer: null,
  };

  function playerKey(p) {
    return (p && (p.player_id || p.session_token || p.nickname)) || "";
  }

  function clearDeltaTimers() {
    if (state.deltaFadeTimer) {
      clearTimeout(state.deltaFadeTimer);
      state.deltaFadeTimer = null;
    }
    if (state.deltaClearTimer) {
      clearTimeout(state.deltaClearTimer);
      state.deltaClearTimer = null;
    }
  }

  function stopAttractTimer() {
    if (state.attractTimer) {
      clearInterval(state.attractTimer);
      state.attractTimer = null;
    }
  }

  function getFeaturedPacks() {
    const list = Array.isArray(state.packs) ? state.packs : [];
    const withCovers = list.filter((p) => p && p.cover_url);
    return withCovers.length ? withCovers : list;
  }

  function buildAttractTilesHtml(packs, page) {
    const pageSize = 4;
    const totalPages = Math.max(1, Math.ceil(packs.length / pageSize));
    const safePage = ((page % totalPages) + totalPages) % totalPages;
    const start = safePage * pageSize;
    const slice = packs.slice(start, start + pageSize);
    return slice
      .map((p) => {
        const lang = p.open_ended
          ? (p.rtl ? "أتوبيس" : "Open")
          : String(p.language || "EN").toUpperCase();
        return `
          <div class="tv-attract-tile animate-in" dir="${p.rtl ? "rtl" : "auto"}">
            ${p.cover_url ? `<img src="${esc(p.cover_url)}" alt="" class="tv-attract-cover" width="240" height="150" />` : ""}
            <div class="tv-attract-title" dir="auto">${esc(p.title)}</div>
            <div class="tv-attract-meta">
              <span>${p.question_count || 0} Qs</span>
              <span class="badge" style="font-size:0.7rem;padding:2px 7px;">${esc(lang)}</span>
            </div>
          </div>`;
      })
      .join("");
  }

  function startAttractTimer() {
    const packs = getFeaturedPacks();
    if (packs.length <= 4) {
      stopAttractTimer();
      return;
    }
    if (state.attractTimer) return;
    state.attractTimer = setInterval(() => {
      const currentPacks = getFeaturedPacks();
      const rooms = (state.lobby && state.lobby.rooms) || [];
      if (state.tv.mode === "follow" || rooms.length || currentPacks.length <= 4) {
        stopAttractTimer();
        return;
      }
      const totalPages = Math.max(1, Math.ceil(currentPacks.length / 4));
      state.attractPage = (state.attractPage + 1) % totalPages;
      const grid = document.getElementById("tv-attract-grid");
      const pageEl = document.getElementById("tv-attract-page");
      if (grid) {
        grid.innerHTML = buildAttractTilesHtml(currentPacks, state.attractPage);
      }
      if (pageEl) {
        pageEl.textContent = `${state.attractPage + 1} / ${totalPages}`;
      }
    }, 6000);
  }

  function applyOverscan(enabled, persist = true) {
    state.overscan = Boolean(enabled);
    document.documentElement.dataset.overscan = state.overscan ? "on" : "off";
    const shell = document.querySelector(".tv-shell");
    if (shell) shell.classList.toggle("overscan-safe", state.overscan);
    const btn = document.getElementById("tv-overscan-btn");
    if (btn) {
      btn.textContent = state.overscan ? "Safe Zone: On" : "Safe Zone: Off";
      btn.setAttribute("aria-pressed", String(state.overscan));
    }
    if (persist) {
      try {
        localStorage.setItem("quizrooms_tv_overscan", state.overscan ? "on" : "off");
      } catch (_) { }
    }
  }

  function diffBadgeHtml(diff, extraStyle = "") {
    const d = String(diff || "").toLowerCase();
    if (!d || d === "random") return "";
    const cls = d === "easy" ? "badge-lobby" : d === "hard" ? "badge-paused" : "badge-live";
    const label = d.charAt(0).toUpperCase() + d.slice(1);
    return `<span class="badge ${cls}"${extraStyle ? ` style="${extraStyle}"` : ""}>${esc(label)}</span>`;
  }

  function esc(str) {
    return String(str == null ? "" : str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function joinUrl(code) {
    const base = state.lanBaseUrl || window.location.origin;
    return code ? `${base}/quizrooms/play?code=${encodeURIComponent(code)}` : `${base}/quizrooms/play`;
  }

    function updateConnBadge(text, live) {
      const el = document.getElementById("tv-conn-badge");
      if (!el) return;
      el.textContent = text;
      el.className = "badge " + (live ? "badge-lobby" : "badge-paused");
    }

    async function setTVMode(mode, roomCode = null, theme = null) {
      const payload = { mode };
      if (roomCode) payload.room_code = roomCode;
      if (theme) payload.theme = theme;

      // Fast path over open WebSocket
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify(Object.assign({ type: "tv_set_mode" }, payload)));
        return;
      }

      // Reliable HTTP fallback if WS is reconnecting or blocked
      try {
        const resp = await fetch("/quizrooms/api/public/tv_mode", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!resp.ok) return;
        const data = await resp.json();
        if (data.lobby) state.lobby = data.lobby;
        if (data.tv) applyTVConfig(data.tv);
        state.room = data.room || null;
        if (state.room) syncRoomEvents(state.room);
        else stopTimerLoop();
        render();
      } catch (err) {
        console.error("QuizRooms TV mode update failed", err);
      }
    }

    function connectWS() {
      if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) {
        return;
      }
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${window.location.host}/quizrooms/ws?role=tv`);
      state.ws = ws;

      ws.onopen = () => {
        state.wsConnected = true;
        updateConnBadge("Live", true);
      };

      ws.onclose = () => {
        state.wsConnected = false;
        updateConnBadge("Reconnecting…", false);
        setTimeout(connectWS, 1800);
      };

      ws.onerror = (err) => {
        console.error("QuizRooms TV WebSocket error", err);
        updateConnBadge("Reconnecting…", false);
      };

      ws.onmessage = (evt) => {
        try {
          handleMessage(JSON.parse(evt.data));
      } catch (err) {
        console.error("QuizRooms TV message handling error", err);
      }
    };
  }

  function handleMessage(msg) {
    if (msg.lan_base_url) {
      state.lanBaseUrl = msg.lan_base_url;
    }

    switch (msg.type) {
      case "hello":
        if (msg.lobby) state.lobby = msg.lobby;
        if (Array.isArray(msg.packs)) state.packs = msg.packs;
        if (msg.tv) applyTVConfig(msg.tv);
        state.room = msg.room || null;
        if (state.room) syncRoomEvents(state.room);
        else stopTimerLoop();
        render();
        break;

      case "lobby_update":
        if (msg.lobby) state.lobby = msg.lobby;
        if (Array.isArray(msg.packs)) state.packs = msg.packs;
        // Always refresh whenever Overview is being shown (even if tv.mode had no room)
        if (state.tv.mode !== "follow" || !state.room) {
          render();
        }
        break;

      case "tv_mode_changed":
        if (msg.lobby) state.lobby = msg.lobby;
        if (msg.tv) applyTVConfig(msg.tv);
        state.room = msg.room || null;
        if (state.room) {
          syncRoomEvents(state.room);
        } else {
          stopTimerLoop();
          clearDeltaTimers();
          state.revealHighlights = null;
          state.lastRevealSig = "";
        }
        render();
        break;

      case "room_state":
        state.room = msg.room;
        if (state.room && state.tv.mode !== "follow") {
          applyTVConfig({ mode: "follow", room_code: state.room.code, theme: state.tv.theme });
        }
        syncRoomEvents(msg.room);
        render();
        break;

      case "room_closed":
        delete state.prevRanksByRoom[msg.code];
        if (state.lobby && Array.isArray(state.lobby.rooms)) {
          state.lobby.rooms = state.lobby.rooms.filter((r) => r.code !== msg.code);
          state.lobby.active_rooms = Math.max(0, (state.lobby.active_rooms || 1) - 1);
        }
        if (state.room && state.room.code === msg.code) {
          state.room = null;
          applyTVConfig({ mode: "overview", room_code: null, theme: state.tv.theme });
          stopTimerLoop();
          clearDeltaTimers();
          state.revealHighlights = null;
          state.lastRevealSig = "";
        }
        render();
        break;

      case "error":
        console.warn("QuizRooms TV error:", msg.message);
        break;
    }
  }

  function applyTVConfig(tvCfg) {
    state.tv = Object.assign({ mode: "overview", room_code: null, theme: null }, tvCfg);
    if (tvCfg.theme && window.QuizRoomsTheme) {
      window.QuizRoomsTheme.setMode(tvCfg.theme, { persist: true });
    }
    const overviewBtn = document.getElementById("tv-overview-btn");
    if (overviewBtn) {
      overviewBtn.classList.toggle("hidden", state.tv.mode !== "follow");
    }
  }

  function scheduleRevealDeltaClear(sig) {
    clearDeltaTimers();
    state.deltaFadeTimer = setTimeout(() => {
      if (!state.revealHighlights || state.revealHighlights.sig !== sig) return;
      document.querySelectorAll(".tv-lb-delta-wrap, .tv-rank-arrow").forEach((el) => {
        el.classList.add("fading");
      });
      document.querySelectorAll(".tv-lb-row.moved-up, .tv-lb-row.moved-down").forEach((el) => {
        el.classList.remove("moved-up", "moved-down");
      });
    }, 3550);
    state.deltaClearTimer = setTimeout(() => {
      if (!state.revealHighlights || state.revealHighlights.sig !== sig) return;
      state.revealHighlights.visible = false;
      document.querySelectorAll(".tv-lb-delta-wrap, .tv-rank-arrow").forEach((el) => el.remove());
    }, 4050);
  }

  function syncRoomEvents(room) {
    if (!room) return;
    if (room.question && room.question.server_now_ms) {
      state.serverOffsetMs = room.question.server_now_ms - Date.now();
    }

    // Track pre-reveal ranks during non-reveal phases so Reveal can show ▲/▼ rank movement
    if (room.phase === "lobby" || room.phase === "question" || room.phase === "bonus_wager") {
      const rankSnap = {};
      (room.players || []).forEach((p) => {
        const k = playerKey(p);
        if (k) rankSnap[k] = p.rank;
      });
      state.prevRanksByRoom[room.code] = rankSnap;
      if (state.revealHighlights) {
        clearDeltaTimers();
        state.revealHighlights = null;
        state.lastRevealSig = "";
      }
    } else if (room.phase === "reveal" && room.reveal) {
      const outcomes = room.reveal.outcomes || [];
      const deltaMap = {};
      outcomes.forEach((o) => {
        const k = playerKey(o);
        if (k) deltaMap[k] = typeof o.delta === "number" ? o.delta : 0;
      });
      const sig = `${room.code}:${room.reveal.round_index}:${outcomes.map((o) => `${playerKey(o)}=${o.delta}`).join(",")}`;
      if (sig !== state.lastRevealSig) {
        state.lastRevealSig = sig;
        const prevRanks = state.prevRanksByRoom[room.code] || {};
        const byPlayer = {};
        (room.players || []).forEach((p) => {
          const k = playerKey(p);
          if (!k) return;
          const delta = deltaMap[k] != null ? deltaMap[k] : 0;
          const prevRank = prevRanks[k];
          const rankChange = typeof prevRank === "number" ? (prevRank - p.rank) : 0;
          byPlayer[k] = { delta, rankChange };
        });
        state.revealHighlights = {
          roomCode: room.code,
          roundIndex: room.reveal.round_index,
          sig,
          byPlayer,
          visible: true,
        };
        scheduleRevealDeltaClear(sig);
      }
    } else if (room.phase === "finale") {
      clearDeltaTimers();
      state.revealHighlights = null;
    }

    const phaseKey = `${room.code}:${room.phase}:${room.question ? room.question.index : ""}`;
    if (phaseKey !== state.lastPhaseKey) {
      const hadPrev = Boolean(state.lastPhaseKey);
      state.lastPhaseKey = phaseKey;
      state.lastCountdownSec = -1;
      state.lastLockedCount = room.locked_in_count || 0;
      if (hadPrev && (room.phase === "question" || room.phase === "bonus_wager") && window.QuizRoomsSFX) {
        window.QuizRoomsSFX.roundStart();
      } else if (room.phase === "reveal" && window.QuizRoomsSFX) {
        window.QuizRoomsSFX.reveal();
      } else if (room.phase === "finale") {
        if (window.QuizRoomsSFX) window.QuizRoomsSFX.finale();
        if (window.QuizRoomsConfetti) window.QuizRoomsConfetti.burst(1.8);
      }
    } else if ((room.phase === "question" || room.phase === "bonus_wager") && (room.locked_in_count || 0) > state.lastLockedCount) {
      state.lastLockedCount = room.locked_in_count;
      if (window.QuizRoomsSFX) window.QuizRoomsSFX.lockIn();
    }
  }

    function stopTimerLoop() {
      if (state.timerRaf) {
        cancelAnimationFrame(state.timerRaf);
        state.timerRaf = null;
      }
    }

    function startTimerLoop() {
      stopTimerLoop();
      function tick() {
        const room = state.room;
        if (!room || room.phase !== "question" || !room.question) return;
        const q = room.question;
        const fill = document.getElementById("tv-timer-fill");
        const num = document.getElementById("tv-timer-num");
        const stage = document.querySelector(".tv-stage");
        if (!fill || !num) return;

        let rem = q.remaining_seconds || 0;
        if (!room.paused && q.ends_at_ms) {
          rem = Math.max(0, (q.ends_at_ms - (Date.now() + state.serverOffsetMs)) / 1000);
        }
        const allLocked = !room.paused && room.connected_count > 0 && room.locked_in_count >= room.connected_count;
        const sec = Math.ceil(rem);
        const pct = Math.max(0, Math.min(100, (rem / (q.timer_seconds || 30)) * 100));
        const isUrgent = !room.paused && !allLocked && rem <= 5 && rem > 0;
        fill.style.width = pct.toFixed(1) + "%";
        fill.classList.toggle("warn", !allLocked && rem <= 10 && rem > 5);
        fill.classList.toggle("urgent", isUrgent);
        num.classList.toggle("urgent", isUrgent);
        if (stage) stage.classList.toggle("tv-stage-urgent", isUrgent);
        num.textContent = room.paused ? `PAUSED (${sec}s)` : `${sec}s`;

        if (!room.paused && !allLocked && sec > 0 && sec !== state.lastCountdownSec) {
          state.lastCountdownSec = sec;
          if (sec <= 5 && window.QuizRoomsSFX) {
            window.QuizRoomsSFX.tickUrgent(sec);
          } else if (sec <= 10 && window.QuizRoomsSFX) {
            window.QuizRoomsSFX.tickSoft();
          }
        }

        if (!room.paused && !allLocked && rem > 0) {
          state.timerRaf = requestAnimationFrame(tick);
        }
      }
      state.timerRaf = requestAnimationFrame(tick);
    }

      function render() {
        const main = document.getElementById("tv-main");
        const overviewBtn = document.getElementById("tv-overview-btn");
        if (overviewBtn) {
          overviewBtn.classList.toggle("hidden", !(state.tv.mode === "follow" && state.room));
        }

        // Preserve TV D-Pad focus on the currently focused Overview room card across refreshes
        const activeEl = document.activeElement;
        const focusedRoomCode =
          activeEl && activeEl.classList && activeEl.classList.contains("tv-room-card")
            ? activeEl.getAttribute("data-follow-code")
            : null;

        if (state.tv.mode === "follow" && state.room) {
          stopAttractTimer();
          renderFollowMode(main, state.room);
          if (state.room.phase === "question" && !state.room.paused) startTimerLoop();
          else stopTimerLoop();
        } else {
          stopTimerLoop();
          renderOverviewMode(main);
          if (focusedRoomCode) {
            const restored = main.querySelector(`.tv-room-card[data-follow-code="${CSS.escape ? CSS.escape(focusedRoomCode) : focusedRoomCode}"]`);
            if (restored) restored.focus({ preventScroll: true });
          }
        }
      }

      function renderOverviewMode(main) {
        const rooms = (state.lobby && state.lobby.rooms) || [];
        if (!rooms.length) {
          const qr = window.QuizRoomsQR ? window.QuizRoomsQR.toSVG(joinUrl()) : "";
          const packs = getFeaturedPacks();
          const totalPages = Math.max(1, Math.ceil(packs.length / 4));
          const safePage = ((state.attractPage % totalPages) + totalPages) % totalPages;
          const packShowcaseHtml = packs.length
            ? `
          <section class="tv-attract-packs" aria-label="Featured trivia packs">
            <div class="tv-attract-header">
              <span class="tv-attract-label">Featured Trivia Packs (${packs.length} Ready Offline)</span>
              ${totalPages > 1 ? `<span id="tv-attract-page" class="badge mono">${safePage + 1} / ${totalPages}</span>` : ""}
            </div>
            <div id="tv-attract-grid" class="tv-attract-grid">
              ${buildAttractTilesHtml(packs, safePage)}
            </div>
          </section>`
            : "";

          main.innerHTML = `
        <div class="tv-welcome">
          <div class="studio-card tv-welcome-card animate-in">
            <div>
              <span class="badge badge-accent">Local-First Party Trivia</span>
              <h2>Scan to Host or Join a Room</h2>
              <p>Scan the QR code with your phone to create a room, pick a trivia pack, and wager on your confidence.</p>
              <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
                <span class="badge">Active Rooms: ${state.lobby.active_rooms} / ${state.lobby.max_rooms}</span>
                ${packs.length ? `<span class="badge badge-lobby">${packs.length} Packs Installed</span>` : ""}
              </div>
            </div>
            <div style="display:flex;flex-direction:column;align-items:center;gap:8px;">
              <div class="qr-white-frame">${qr}</div>
              <span class="badge">Scan on Wi-Fi</span>
            </div>
          </div>
          ${packShowcaseHtml}
        </div>`;
          startAttractTimer();
          return;
        }

        stopAttractTimer();
        main.innerHTML = `
      <div class="tv-overview-grid">
        ${rooms
            .map((r) => {
              const qr = window.QuizRoomsQR ? window.QuizRoomsQR.toSVG(joinUrl(r.code)) : "";
              let statusClass = "badge-live";
              let statusText = `Round ${r.current_round} / ${r.total_rounds}`;
              if (r.status === "lobby") {
                statusClass = "badge-lobby";
                statusText = "Open Lobby";
              } else if (r.status === "finished") {
                statusClass = "badge-accent";
                statusText = "Finished · Podium";
              }

              const leaders = (r.top_players || [])
                .map(
                  (p) => `<div class="tv-mini-leader-row">
                  <span style="display:inline-flex;align-items:center;gap:8px;" dir="auto">
                    ${p.avatar_url ? `<img src="${esc(p.avatar_url)}" alt="" class="avatar-img avatar-xs" />` : ""}
                    #${p.rank} ${esc(p.nickname)}
                  </span>
                  <span class="mono" style="color:var(--accent-primary);">${p.score}</span>
                </div>`
                )
                .join("");

              return `
              <article class="studio-card tv-room-card animate-in" data-follow-code="${esc(r.code)}" tabindex="0" role="button" aria-label="Follow room ${esc(r.code)}" dir="${r.rtl ? "rtl" : "auto"}">
                <div>
                  <div style="display:flex;align-items:center;gap:14px;margin-bottom:6px;">
                    ${r.cover_url ? `<img src="${esc(r.cover_url)}" alt="" style="width:58px;height:58px;border-radius:14px;object-fit:cover;flex:none;border:1px solid var(--border-subtle);" width="58" height="58" />` : ""}
                    <div>
                      <div class="tv-room-code">${esc(r.code)}</div>
                      <h2 class="tv-room-title" dir="auto">${esc(r.name)}</h2>
                    </div>
                  </div>
                  <p class="tv-room-sub" dir="auto">${esc((r.pack_titles || []).join(" · "))}</p>
                  <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
                    <span class="badge ${statusClass}">${esc(statusText)}</span>
                    <span class="badge">${r.player_count} Player${r.player_count === 1 ? "" : "s"}</span>
                    ${diffBadgeHtml(r.difficulty)}
                    ${r.bonus_round ? `<span class="badge badge-accent">★ Bonus</span>` : ""}
                    ${r.unique_answers_only ? `<span class="badge badge-accent">No Repeats</span>` : ""}
                    <button type="button" class="btn btn-primary btn-sm" tabindex="-1" data-follow-code="${esc(r.code)}">Follow</button>
                  </div>
                  ${leaders ? `<div class="tv-mini-leaders">${leaders}</div>` : ""}
                </div>
                <div class="tv-room-qr">
                  <div class="qr-white-frame">${qr}</div>
                  <span style="font-size:0.8rem;color:var(--text-muted);font-weight:700;">Scan or select to follow</span>
                </div>
              </article>`;
            })
            .join("")}
      </div>`;
      }

      function renderFollowMode(main, room) {
        const qr = window.QuizRoomsQR ? window.QuizRoomsQR.toSVG(joinUrl(room.code)) : "";
        const showDeltas =
          room.phase === "reveal" &&
          state.revealHighlights &&
          state.revealHighlights.visible &&
          state.revealHighlights.roomCode === room.code;
        const deltaByPlayer = showDeltas ? state.revealHighlights.byPlayer : null;

        const leaderboardHtml = room.players
          .slice(0, 10)
          .map((p, idx) => {
            const info = deltaByPlayer ? deltaByPlayer[playerKey(p)] : null;
            const delta = info ? info.delta : 0;
            const rankChange = info ? info.rankChange : 0;
            const rowMoveCls =
              rankChange > 0 ? "moved-up" : rankChange < 0 ? "moved-down" : "";
            const arrowHtml =
              rankChange > 0
                ? `<span class="tv-rank-arrow up" aria-label="Up ${rankChange}">▲${rankChange > 1 ? rankChange : ""}</span>`
                : rankChange < 0
                ? `<span class="tv-rank-arrow down" aria-label="Down ${Math.abs(rankChange)}">▼${Math.abs(rankChange) > 1 ? Math.abs(rankChange) : ""}</span>`
                : "";
            const deltaHtml =
              delta !== 0
                ? `<span class="tv-lb-delta-wrap"><span class="tv-lb-delta ${delta > 0 ? "pos" : "neg"}">${delta > 0 ? `+${delta}` : `−${Math.abs(delta)}`}</span></span>`
                : "";
            return `
        <div class="tv-lb-row ${idx === 0 ? "top-1" : ""} ${rowMoveCls}">
          <span class="tv-lb-name-wrap" dir="auto">
            ${p.avatar_url ? `<img src="${esc(p.avatar_url)}" alt="" class="avatar-img avatar-sm" />` : ""}
            <span class="tv-lb-name-text">#${p.rank} ${esc(p.nickname)}</span>
            ${arrowHtml}
          </span>
          <span class="tv-lb-score-wrap">
            ${deltaHtml}
            <span class="mono" style="color:var(--accent-primary);">${p.score}</span>
          </span>
        </div>`;
          })
          .join("");

        let stageHtml = "";
        let stageUrgentClass = "";
        const stageDir =
          (room.question && room.question.rtl) || (room.reveal && room.reveal.rtl) || room.rtl
            ? "rtl"
            : "auto";
        const allLocked =
          !room.paused &&
          room.connected_count > 0 &&
          room.locked_in_count >= room.connected_count;

        if (room.phase === "lobby") {
          stageHtml = `
        <div class="tv-stage-meta">
          <span class="badge badge-lobby">Lobby Open</span>
          <span class="tv-room-code">${esc(room.code)}</span>
        </div>
        <div style="display:flex;align-items:center;gap:18px;text-align:start;">
          ${room.cover_url ? `<img src="${esc(room.cover_url)}" alt="" style="width:76px;height:76px;border-radius:18px;object-fit:cover;flex:none;border:2px solid var(--border-subtle);" width="76" height="76" />` : ""}
          <div>
            <h2 class="tv-question-text" dir="auto">${esc(room.settings.room_name)}</h2>
            <p style="font-size:1.3rem;color:var(--text-secondary);margin-top:6px;" dir="auto">
              ${esc(room.settings.pack_titles.join(" · "))} · ${room.settings.round_count} Rounds${room.settings.bonus_round ? " + ★ Bonus" : ""} · ${room.settings.timer_seconds}s · ${room.settings.difficulty && room.settings.difficulty !== "random" ? esc(room.settings.difficulty.toUpperCase()) : "All Levels"}${room.settings.unique_answers_only ? " · No Repeated Answers" : ""}
            </p>
          </div>
        </div>
        <div class="tv-lock-grid">
          ${room.players
              .map((p) => `<span class="tv-lock-badge" style="display:inline-flex;align-items:center;gap:8px;" dir="auto">
                ${p.avatar_url ? `<img src="${esc(p.avatar_url)}" alt="" class="avatar-img avatar-xs" />` : ""}
                ${esc(p.nickname)}${p.is_host ? " ★" : ""}
              </span>`)
              .join("")}
        </div>`;
        } else if (room.phase === "bonus_wager" && room.bonus_wager) {
          const bw = room.bonus_wager;
          const opts = bw.options || [5, 10, 15, 20, 25, 30];
          const lo = opts[0] || 5;
          const hi = opts[opts.length - 1] || 30;
          stageHtml = `
        <div class="tv-stage-meta">
          <span class="badge" style="font-size:1.05rem;padding:8px 18px;background:var(--accent-amber);color:#120d02;font-weight:900;">
            ★ FINAL BONUS ROUND · PLACE YOUR WAGERS
          </span>
          <span class="tv-room-code">${esc(room.code)}</span>
        </div>

        <div class="studio-card tv-reveal-box" style="border-color:var(--accent-amber);">
          <div style="font-size:0.9rem;font-weight:800;text-transform:uppercase;letter-spacing:0.08em;color:var(--accent-amber);">
            Blind High-Stakes Wager · ±${lo} to ±${hi} Points
          </div>
          <div class="tv-reveal-answer" style="font-size:clamp(1.6rem,3.4vh,2.6rem);">
            Choose Your Points on Your Phone!
          </div>
          <p style="margin:0;font-size:clamp(1rem,1.8vh,1.25rem);color:var(--text-secondary);">
            Stakes: <strong class="mono">${opts.map((v) => `±${v}`).join(" · ")}</strong> — Correct gains <strong>+Wager</strong>, wrong loses <strong>−Wager</strong>.
          </p>
        </div>

        ${allLocked ? `<div class="tv-all-locked-banner" role="status"><span>✓</span><span>All Wagers Locked In! Revealing bonus question…</span></div>` : ""}

        <div class="tv-lock-grid">
          ${room.players
              .map(
                (p) => `<span class="tv-lock-badge ${p.locked_in ? "locked" : ""}" style="display:inline-flex;align-items:center;gap:8px;" dir="auto">
                ${p.avatar_url ? `<img src="${esc(p.avatar_url)}" alt="" class="avatar-img avatar-xs" />` : ""}
                ${p.locked_in ? "✓ " : ""}${esc(p.nickname)}
              </span>`
              )
              .join("")}
        </div>`;
        } else if (room.phase === "question" && room.question) {
          const q = room.question;
          const letters = ["A", "B", "C", "D", "E", "F"];
          const remInit = q.remaining_seconds || 0;
          const isUrgentInit = !room.paused && !allLocked && remInit <= 5 && remInit > 0;
          if (isUrgentInit) stageUrgentClass = "tv-stage-urgent";
          stageHtml = `
        <div>
          <div class="tv-stage-meta">
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
              ${q.is_bonus
                ? (() => {
                  const opts = q.bonus_wager_options || [5, 10, 15, 20, 25, 30];
                  const lo = opts[0] || 5;
                  const hi = opts[opts.length - 1] || 30;
                  return `<span class="badge" style="font-size:1rem;padding:8px 16px;background:var(--accent-amber);color:#120d02;font-weight:900;">★ FINAL BONUS QUESTION · ±${lo}–${hi} PTS</span>`;
                })()
                : `<span class="badge badge-live" style="font-size:1rem;padding:8px 16px;">Round ${q.round_number} / ${q.regular_rounds || q.total_rounds}</span>`}
              ${diffBadgeHtml(q.difficulty, "font-size:0.95rem;padding:8px 14px;")}
              ${room.settings.unique_answers_only ? `<span class="badge badge-accent" style="font-size:0.95rem;padding:8px 14px;">Unique Answers Only</span>` : ""}
            </div>
            <div id="tv-timer-num" class="tv-timer-big ${isUrgentInit ? "urgent" : ""}">${Math.ceil(remInit)}s</div>
          </div>
          <div class="timer-track tv-timer-bar-wrap">
            <div id="tv-timer-fill" class="timer-fill ${isUrgentInit ? "urgent" : ""}"></div>
          </div>
        </div>

        ${q.media_url
              ? `<div class="tv-media-showcase"><div class="media-frame"><img src="${esc(q.media_url)}" alt="Question visual" width="640" height="480" /></div></div>`
              : ""
            }

        <h2 class="tv-question-text" dir="auto">${esc(q.prompt)}</h2>

        ${q.starts_with
              ? `<div style="display:inline-flex;align-items:center;gap:12px;padding:10px 20px;border-radius:999px;background:var(--accent-amber-soft);border:2px solid var(--accent-amber);align-self:flex-start;font-size:1.3rem;font-weight:800;">
                  <span>${q.rtl ? "يبدأ بحرف" : "Starts with"}</span>
                  <span style="display:inline-grid;place-items:center;min-width:44px;height:44px;padding-inline:10px;border-radius:999px;background:var(--accent-amber);color:#120d02;font-size:1.55rem;font-weight:900;">${esc(q.starts_with)}</span>
                </div>`
              : ""
            }

        ${q.choices && q.choices.length
              ? `<div class="tv-choices-grid">
                ${q.choices
                .map(
                  (c, i) => `<div class="tv-choice-tile">
                      <span class="tv-choice-key">${letters[i] || i + 1}</span>
                      <span dir="auto">${esc(c)}</span>
                    </div>`
                )
                .join("")}
              </div>`
              : `<div class="badge badge-accent" style="font-size:1.05rem;padding:10px 18px;align-self:flex-start;">
                ${q.open_ended ? (q.rtl ? "إجابة مفتوحة فريدة — اكتب إجابتك على هاتفك!" : "Open-Ended Mode — Submit a unique answer on your phone!") : "Free-Text Mode — Submit on your phone!"}
              </div>`
            }

        ${allLocked
              ? `<div class="tv-all-locked-banner" role="status"><span>✓</span><span>${q.rtl ? "الجميع أكّدوا الإجابة! جاري كشف الحل…" : "All Locked In! Revealing answer…"}</span></div>`
              : ""
            }

        <div class="tv-lock-grid">
          ${room.players
              .map(
                (p) => `<span class="tv-lock-badge ${p.locked_in ? "locked" : ""}" style="display:inline-flex;align-items:center;gap:8px;" dir="auto">
                ${p.avatar_url ? `<img src="${esc(p.avatar_url)}" alt="" class="avatar-img avatar-xs" />` : ""}
                ${p.locked_in ? "✓ " : ""}${esc(p.nickname)}
              </span>`
              )
              .join("")}
        </div>`;
        } else if (room.phase === "reveal" && room.reveal) {
          const rev = room.reveal;
          stageHtml = `
        <div class="tv-stage-meta">
          ${rev.is_bonus
            ? `<span class="badge" style="font-size:1rem;padding:8px 16px;background:var(--accent-amber);color:#120d02;font-weight:900;">★ Final Bonus Reveal</span>`
            : `<span class="badge badge-lobby" style="font-size:1rem;padding:8px 16px;">Answer Reveal · Round ${rev.round_index + 1} / ${room.settings.bonus_round ? rev.total_rounds - 1 : rev.total_rounds}</span>`}
          <span class="tv-room-code">${esc(room.code)}</span>
        </div>

        <div class="studio-card tv-reveal-box">
          <div style="font-size:0.86rem;font-weight:800;text-transform:uppercase;letter-spacing:0.08em;color:var(--accent-secondary);">
            ${rev.open_ended ? (rev.rtl ? "قاعدة الجولة المفتوحة" : "Open-Ended Round Rule") : "Correct Answer"}
          </div>
          <div class="tv-reveal-answer" dir="auto">
            ${esc(rev.correct_answer)}
          </div>
          ${rev.explanation ? `<p style="margin:0;font-size:clamp(0.95rem,1.65vh,1.2rem);color:var(--text-secondary);" dir="auto">${esc(rev.explanation)}</p>` : ""}
        </div>

        <div class="tv-reveal-outcomes">
          ${rev.outcomes
              .slice(0, 8)
              .map((o) => {
                const cls = o.delta > 0 ? "delta-pos" : o.delta < 0 ? "delta-neg" : "delta-zero";
                const sign = o.delta > 0 ? `+${o.delta}` : String(o.delta);
                return `
                <div class="tv-lb-row" ${o.outcome === "duplicate" ? 'style="border-color:var(--accent-amber);background:var(--accent-amber-soft);"' : ""}>
                  <div style="display:flex;align-items:center;gap:10px;text-align:start;">
                    ${o.avatar_url ? `<img src="${esc(o.avatar_url)}" alt="" class="avatar-img avatar-sm" />` : ""}
                    <div>
                      <div dir="auto">${esc(o.nickname)}${o.outcome === "duplicate" ? ` <span class="badge badge-live" style="font-size:0.68rem;padding:2px 6px;">Repeated</span>` : ""}</div>
                      <div style="font-size:0.85rem;color:var(--text-muted);" dir="auto">
                        ${o.answer ? esc(o.answer) : "Timed out"} · W${o.wager}${o.duplicate_reason ? ` · ${esc(o.duplicate_reason)}` : ""}
                      </div>
                    </div>
                  </div>
                  <span class="delta-pill ${cls}">${sign}</span>
                </div>`;
              })
              .join("")}
        </div>`;
        } else if (room.phase === "finale" && room.finale) {
          const fin = room.finale;
          stageHtml = `
        <div class="tv-stage-meta">
          <span class="badge badge-live" style="font-size:1rem;padding:8px 16px;">Final Podium</span>
          <span class="tv-room-code">${esc(room.code)}</span>
        </div>
        <div class="tv-podium">
          ${fin.podium
              .map(
                (p, i) => `
              <div class="tv-podium-slot rank-${i + 1}">
                ${p.avatar_url ? `<img src="${esc(p.avatar_url)}" alt="" class="avatar-img ${i === 0 ? "avatar-xl" : "avatar-lg"}" style="margin-bottom:8px;" />` : ""}
                <div style="font-size:1.7rem;font-weight:800;">#${i + 1}</div>
                <div style="font-size:1.5rem;font-weight:800;margin-block:6px;" dir="auto">${esc(p.nickname)}</div>
                <div class="mono" style="font-size:1.45rem;font-weight:800;color:var(--accent-primary);">${p.score} pts</div>
              </div>`
              )
              .join("")}
        </div>
        <div class="tv-awards-row">
          ${fin.awards
              .map(
                (a) => `
              <div class="tv-award-tile" style="display:flex;align-items:center;gap:12px;text-align:start;">
                ${a.avatar_url ? `<img src="${esc(a.avatar_url)}" alt="" class="avatar-img avatar-md" />` : ""}
                <div>
                  <div style="font-size:0.82rem;font-weight:800;text-transform:uppercase;color:var(--accent-amber);">${esc(a.title)}</div>
                  <div style="font-size:1.2rem;font-weight:800;margin-block:2px;" dir="auto">${esc(a.nickname)}</div>
                  <div style="font-size:0.88rem;color:var(--text-muted);">${esc(a.subtitle)}</div>
                </div>
              </div>`
              )
              .join("")}
        </div>`;
        }

        main.innerHTML = `
      <div class="tv-follow-layout">
        <section class="studio-card tv-stage animate-in ${stageUrgentClass}" dir="${stageDir}">
          ${stageHtml}
        </section>
        <aside class="studio-card tv-sidebar">
          <div style="display:flex;align-items:center;justify-content:space-between;">
            <strong style="font-size:1.1rem;">Leaderboard</strong>
            <span class="badge mono">${esc(room.code)}</span>
          </div>
          <div class="tv-leaderboard-list">${leaderboardHtml}</div>
          <div class="tv-sidebar-qr-wrap">
            <div class="qr-white-frame">${qr}</div>
            <span style="font-size:0.8rem;color:var(--text-muted);font-weight:700;">Scan to join room ${esc(room.code)}</span>
          </div>
        </aside>
      </div>`;
      }

      async function fetchBootstrap() {
        try {
          const resp = await fetch("/quizrooms/api/public/bootstrap", { cache: "no-store" });
          if (!resp.ok) return;
          const data = await resp.json();
          if (data.lan_base_url) {
            state.lanBaseUrl = data.lan_base_url;
          }
          if (data.lobby) state.lobby = data.lobby;
          if (Array.isArray(data.packs)) state.packs = data.packs;
          if (data.tv) applyTVConfig(data.tv);
          if (data.room !== undefined) {
            state.room = data.room;
            if (state.room) syncRoomEvents(state.room);
          }
          render();
        } catch (_) { }
      }

      function syncSoundButton(btn, enabled) {
        btn.textContent = enabled ? "Sound: On" : "Sound: Off";
        btn.setAttribute("aria-pressed", String(enabled));
      }

      function getVisibleHeaderControls() {
        return Array.from(document.querySelectorAll(".tv-controls button")).filter(
          (el) => !el.classList.contains("hidden") && el.offsetParent !== null
        );
      }

      function findSpatialNeighbor(currentEl, candidates, dir) {
        const cur = currentEl.getBoundingClientRect();
        const cx = cur.left + cur.width / 2;
        const cy = cur.top + cur.height / 2;
        let best = null;
        let bestScore = Infinity;

        for (const el of candidates) {
          if (el === currentEl) continue;
          const r = el.getBoundingClientRect();
          const ex = r.left + r.width / 2;
          const ey = r.top + r.height / 2;
          const dx = ex - cx;
          const dy = ey - cy;

          if (dir === "ArrowRight" && dx <= 8) continue;
          if (dir === "ArrowLeft" && dx >= -8) continue;
          if (dir === "ArrowDown" && dy <= 8) continue;
          if (dir === "ArrowUp" && dy >= -8) continue;

          const primary = (dir === "ArrowLeft" || dir === "ArrowRight") ? Math.abs(dx) : Math.abs(dy);
          const secondary = (dir === "ArrowLeft" || dir === "ArrowRight") ? Math.abs(dy) : Math.abs(dx);
          const score = primary + secondary * 2.4;
          if (score < bestScore) {
            bestScore = score;
            best = el;
          }
        }
        return best;
      }

      function setupRemoteNavigation() {
        document.addEventListener("keydown", (evt) => {
          const tag = (evt.target && evt.target.tagName) || "";
          if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

          // Back / Return key on Smart TV remotes (LG webOS 461, Tizen 10009, Escape, Backspace)
          if (
            evt.key === "Escape" ||
            evt.key === "Backspace" ||
            evt.keyCode === 461 ||
            evt.keyCode === 10009
          ) {
            if (state.tv.mode === "follow" && state.room) {
              evt.preventDefault();
              setTVMode("overview");
            }
            return;
          }

          if (
            evt.key !== "ArrowLeft" &&
            evt.key !== "ArrowRight" &&
            evt.key !== "ArrowUp" &&
            evt.key !== "ArrowDown"
          ) {
            return;
          }

          const headerBtns = getVisibleHeaderControls();
          const roomCards = Array.from(document.querySelectorAll(".tv-room-card"));
          const allTargets = headerBtns.concat(roomCards);
          if (!allTargets.length) return;

          evt.preventDefault();
          const active = document.activeElement;
          const inHeader = headerBtns.indexOf(active);
          const inCards = roomCards.indexOf(active);

          // First D-Pad press: focus first room card if in Overview, else first header control
          if (inHeader === -1 && inCards === -1) {
            const initial = roomCards[0] || headerBtns[0];
            if (initial) initial.focus();
            return;
          }

          let next = null;
          if (inHeader !== -1) {
            if (evt.key === "ArrowRight") {
              next = headerBtns[(inHeader + 1) % headerBtns.length];
            } else if (evt.key === "ArrowLeft") {
              next = headerBtns[(inHeader - 1 + headerBtns.length) % headerBtns.length];
            } else if (evt.key === "ArrowDown" && roomCards.length) {
              next = findSpatialNeighbor(active, roomCards, "ArrowDown") || roomCards[0];
            }
          } else if (inCards !== -1) {
            next = findSpatialNeighbor(active, roomCards, evt.key);
            if (!next && evt.key === "ArrowUp" && headerBtns.length) {
              next = findSpatialNeighbor(active, headerBtns, "ArrowUp") || headerBtns[0];
            } else if (!next && evt.key === "ArrowRight") {
              next = roomCards[(inCards + 1) % roomCards.length];
            } else if (!next && evt.key === "ArrowLeft") {
              next = roomCards[(inCards - 1 + roomCards.length) % roomCards.length];
            }
          }

          if (next) {
            next.focus();
            if (next.scrollIntoView) {
              next.scrollIntoView({ block: "nearest", inline: "nearest" });
            }
          }
        });
      }

      document.addEventListener("DOMContentLoaded", () => {
        if (window.QuizRoomsTheme) {
          window.QuizRoomsTheme.mountPill(document.getElementById("tv-theme-mount"));
        }

        // Restore TV overscan safe-zone setting
        let savedOverscan = false;
        try {
          savedOverscan = localStorage.getItem("quizrooms_tv_overscan") === "on";
        } catch (_) { }
        applyOverscan(savedOverscan, false);

        const overscanBtn = document.getElementById("tv-overscan-btn");
        if (overscanBtn) {
          overscanBtn.addEventListener("click", () => {
            applyOverscan(!state.overscan, true);
          });
        }

        setupRemoteNavigation();

        // Event delegation on #tv-main so click-to-follow works reliably across live re-renders
        const main = document.getElementById("tv-main");
        main.addEventListener("click", (evt) => {
          const target = evt.target.closest("[data-follow-code]");
          if (!target) return;
          evt.preventDefault();
          const code = target.getAttribute("data-follow-code");
          if (code) setTVMode("follow", code);
        });
        main.addEventListener("keydown", (evt) => {
          if (evt.key !== "Enter" && evt.key !== " ") return;
          const target = evt.target.closest("[data-follow-code]");
          if (!target) return;
          evt.preventDefault();
          const code = target.getAttribute("data-follow-code");
          if (code) setTVMode("follow", code);
        });

        document.getElementById("tv-overview-btn").addEventListener("click", () => {
          setTVMode("overview");
        });

        const soundBtn = document.getElementById("tv-sound-btn");
        if (window.QuizRoomsSFX) {
          syncSoundButton(soundBtn, window.QuizRoomsSFX.init("quizrooms_sound_tv"));
        }
        soundBtn.addEventListener("click", () => {
          if (!window.QuizRoomsSFX) return;
          syncSoundButton(soundBtn, window.QuizRoomsSFX.toggle());
        });

        render();
        fetchBootstrap();
        connectWS();

        // Resilient fallback sync if WS drops on a Smart TV or iframe
        setInterval(() => {
          if (!state.wsConnected) {
            fetchBootstrap();
          }
        }, 4500);
      });
    }) ();
