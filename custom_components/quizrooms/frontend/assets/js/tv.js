/* QuizRooms 16:9 TV Dashboard (/quizrooms/tv) — Overview & Follow Modes */
(function () {
  const state = {
    ws: null,
    wsConnected: false,
    lanBaseUrl: window.location.origin,
    tv: { mode: "overview", room_code: null, theme: null },
    lobby: { rooms: [], active_rooms: 0, max_rooms: 5 },
    room: null,
    serverOffsetMs: 0,
    timerRaf: null,
    lastCountdownSec: -1,
    lastPhaseKey: "",
    lastLockedCount: 0,
  };

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
        if (msg.tv) applyTVConfig(msg.tv);
        state.room = msg.room || null;
        if (state.room) syncRoomEvents(state.room);
        else stopTimerLoop();
        render();
        break;

      case "lobby_update":
        if (msg.lobby) state.lobby = msg.lobby;
        // Always refresh whenever Overview is being shown (even if tv.mode had no room)
        if (state.tv.mode !== "follow" || !state.room) {
          render();
        }
        break;

      case "tv_mode_changed":
        if (msg.lobby) state.lobby = msg.lobby;
        if (msg.tv) applyTVConfig(msg.tv);
        state.room = msg.room || null;
        if (state.room) syncRoomEvents(state.room);
        else stopTimerLoop();
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
        if (state.lobby && Array.isArray(state.lobby.rooms)) {
          state.lobby.rooms = state.lobby.rooms.filter((r) => r.code !== msg.code);
          state.lobby.active_rooms = Math.max(0, (state.lobby.active_rooms || 1) - 1);
        }
        if (state.room && state.room.code === msg.code) {
          state.room = null;
          applyTVConfig({ mode: "overview", room_code: null, theme: state.tv.theme });
          stopTimerLoop();
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

  function syncRoomEvents(room) {
    if (!room) return;
    if (room.question && room.question.server_now_ms) {
      state.serverOffsetMs = room.question.server_now_ms - Date.now();
    }
    const phaseKey = `${room.code}:${room.phase}:${room.question ? room.question.index : ""}`;
    if (phaseKey !== state.lastPhaseKey) {
      const hadPrev = Boolean(state.lastPhaseKey);
      state.lastPhaseKey = phaseKey;
      state.lastCountdownSec = -1;
      state.lastLockedCount = room.locked_in_count || 0;
        if (hadPrev && room.phase === "question" && window.QuizRoomsSFX) {
          window.QuizRoomsSFX.roundStart();
        } else if (room.phase === "reveal" && window.QuizRoomsSFX) {
          window.QuizRoomsSFX.reveal();
        } else if (room.phase === "finale") {
          if (window.QuizRoomsSFX) window.QuizRoomsSFX.finale();
          if (window.QuizRoomsConfetti) window.QuizRoomsConfetti.burst(1.8);
        }
      } else if (room.phase === "question" && (room.locked_in_count || 0) > state.lastLockedCount) {
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
        if (!fill || !num) return;

        let rem = q.remaining_seconds || 0;
        if (!room.paused && q.ends_at_ms) {
          rem = Math.max(0, (q.ends_at_ms - (Date.now() + state.serverOffsetMs)) / 1000);
        }
        const sec = Math.ceil(rem);
        const pct = Math.max(0, Math.min(100, (rem / (q.timer_seconds || 30)) * 100));
        fill.style.width = pct.toFixed(1) + "%";
        fill.classList.toggle("warn", rem <= 10 && rem > 5);
        fill.classList.toggle("urgent", rem <= 5);
        num.classList.toggle("urgent", rem <= 5);
        num.textContent = room.paused ? `PAUSED (${sec}s)` : `${sec}s`;

          if (!room.paused && sec > 0 && sec !== state.lastCountdownSec) {
            state.lastCountdownSec = sec;
            if (sec <= 5 && window.QuizRoomsSFX) {
              window.QuizRoomsSFX.tickUrgent(sec);
            } else if (sec <= 10 && window.QuizRoomsSFX) {
              window.QuizRoomsSFX.tickSoft();
            }
          }

          if (!room.paused && rem > 0) {
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

        if (state.tv.mode === "follow" && state.room) {
          renderFollowMode(main, state.room);
          if (state.room.phase === "question" && !state.room.paused) startTimerLoop();
          else stopTimerLoop();
        } else {
          stopTimerLoop();
          renderOverviewMode(main);
        }
      }

      function renderOverviewMode(main) {
        const rooms = (state.lobby && state.lobby.rooms) || [];
        if (!rooms.length) {
          const qr = window.QuizRoomsQR ? window.QuizRoomsQR.toSVG(joinUrl()) : "";
          main.innerHTML = `
        <div class="tv-welcome">
          <div class="studio-card tv-welcome-card animate-in">
            <div>
              <span class="badge badge-accent">Local-First Party Trivia</span>
              <h2>Scan to Host or Join a Room</h2>
              <p>Scan the QR code with your phone to create a room, pick a trivia pack, and wager on your confidence.</p>
              <span class="badge">Active Rooms: ${state.lobby.active_rooms} / ${state.lobby.max_rooms}</span>
            </div>
            <div style="display:flex;flex-direction:column;align-items:center;gap:8px;">
              <div class="qr-white-frame">${qr}</div>
              <span class="badge">Scan on Wi-Fi</span>
            </div>
          </div>
        </div>`;
          return;
        }

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
                    ${r.cover_url ? `<img src="${esc(r.cover_url)}" alt="" style="width:58px;height:58px;border-radius:14px;object-fit:cover;flex:none;border:1px solid var(--border-subtle);" />` : ""}
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
                    ${r.unique_answers_only ? `<span class="badge badge-accent">No Repeats</span>` : ""}
                    <button type="button" class="btn btn-primary btn-sm" data-follow-code="${esc(r.code)}">Follow</button>
                  </div>
                  ${leaders ? `<div class="tv-mini-leaders">${leaders}</div>` : ""}
                </div>
                <div class="tv-room-qr">
                  <div class="qr-white-frame">${qr}</div>
                  <span style="font-size:0.8rem;color:var(--text-muted);font-weight:700;">Scan or click to follow</span>
                </div>
              </article>`;
            })
            .join("")}
      </div>`;
      }

      function renderFollowMode(main, room) {
        const qr = window.QuizRoomsQR ? window.QuizRoomsQR.toSVG(joinUrl(room.code)) : "";
        const leaderboardHtml = room.players
          .slice(0, 10)
          .map(
            (p, idx) => `
        <div class="tv-lb-row ${idx === 0 ? "top-1" : ""}">
          <span style="display:inline-flex;align-items:center;gap:8px;min-width:0;" dir="auto">
            ${p.avatar_url ? `<img src="${esc(p.avatar_url)}" alt="" class="avatar-img avatar-sm" />` : ""}
            <span>#${p.rank} ${esc(p.nickname)}</span>
          </span>
          <span class="mono" style="color:var(--accent-primary);">${p.score}</span>
        </div>`
          )
          .join("");

        let stageHtml = "";
        const stageDir =
          (room.question && room.question.rtl) || (room.reveal && room.reveal.rtl) || room.rtl
            ? "rtl"
            : "auto";

        if (room.phase === "lobby") {
          stageHtml = `
        <div class="tv-stage-meta">
          <span class="badge badge-lobby">Lobby Open</span>
          <span class="tv-room-code">${esc(room.code)}</span>
        </div>
        <div style="display:flex;align-items:center;gap:18px;text-align:start;">
          ${room.cover_url ? `<img src="${esc(room.cover_url)}" alt="" style="width:76px;height:76px;border-radius:18px;object-fit:cover;flex:none;border:2px solid var(--border-subtle);" />` : ""}
          <div>
            <h2 class="tv-question-text" dir="auto">${esc(room.settings.room_name)}</h2>
            <p style="font-size:1.3rem;color:var(--text-secondary);margin-top:6px;" dir="auto">
              ${esc(room.settings.pack_titles.join(" · "))} · ${room.settings.round_count} Rounds · ${room.settings.timer_seconds}s · ${room.settings.difficulty && room.settings.difficulty !== "random" ? esc(room.settings.difficulty.toUpperCase()) : "All Levels"}${room.settings.unique_answers_only ? " · No Repeated Answers" : ""}
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
        } else if (room.phase === "question" && room.question) {
          const q = room.question;
          const letters = ["A", "B", "C", "D", "E", "F"];
          stageHtml = `
        <div>
          <div class="tv-stage-meta">
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
              <span class="badge badge-live" style="font-size:1rem;padding:8px 16px;">
                Round ${q.round_number} / ${q.total_rounds}
              </span>
              ${diffBadgeHtml(q.difficulty, "font-size:0.95rem;padding:8px 14px;")}
              ${room.settings.unique_answers_only ? `<span class="badge badge-accent" style="font-size:0.95rem;padding:8px 14px;">Unique Answers Only</span>` : ""}
            </div>
            <div id="tv-timer-num" class="tv-timer-big">${Math.ceil(q.remaining_seconds || 0)}s</div>
          </div>
          <div class="timer-track tv-timer-bar-wrap">
            <div id="tv-timer-fill" class="timer-fill"></div>
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
          <span class="badge badge-lobby" style="font-size:1rem;padding:8px 16px;">
            Answer Reveal · Round ${rev.round_index + 1} / ${rev.total_rounds}
          </span>
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
        <section class="studio-card tv-stage animate-in" dir="${stageDir}">
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

      document.addEventListener("DOMContentLoaded", () => {
        if (window.QuizRoomsTheme) {
          window.QuizRoomsTheme.mountPill(document.getElementById("tv-theme-mount"));
        }

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
