/* Optional Web Audio API Studio Sound Effects for QuizRooms (/tv and /play).
   Zero external audio files. Independent per-page mute toggle (muted by default). */
(function () {
  let muted = true;
  let storageKey = "";
  let audioCtx = null;

  function ensureContext() {
    if (!audioCtx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) audioCtx = new Ctx();
    }
    if (audioCtx && audioCtx.state === "suspended") {
      audioCtx.resume().catch(() => { });
    }
    return audioCtx;
  }

    function unlockOnGesture() {
      if (!muted) ensureContext();
    }
    document.addEventListener("pointerdown", unlockOnGesture, { passive: true });
    document.addEventListener("keydown", unlockOnGesture, { passive: true });

    function playTone(
      freq,
      duration,
      { type = "sine", gain = 0.08, delay = 0, freqEnd = null } = {}
    ) {
      if (muted) return;
      const ctx = ensureContext();
      if (!ctx) return;
      const now = ctx.currentTime + delay;
      const osc = ctx.createOscillator();
      const amp = ctx.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freq, now);
      if (freqEnd !== null) {
        osc.frequency.exponentialRampToValueAtTime(freqEnd, now + duration);
      }
      amp.gain.setValueAtTime(0.0001, now);
      amp.gain.exponentialRampToValueAtTime(gain, now + 0.014);
      amp.gain.exponentialRampToValueAtTime(0.0001, now + duration);
      osc.connect(amp);
      amp.connect(ctx.destination);
      osc.start(now);
      osc.stop(now + duration + 0.025);
    }

    function init(key) {
      storageKey = key || "";
      if (storageKey) {
        try {
          muted = localStorage.getItem(storageKey) !== "on";
        } catch (_) {
          muted = true;
        }
      }
      return !muted;
    }

    function savePref() {
      if (!storageKey) return;
      try {
        localStorage.setItem(storageKey, muted ? "off" : "on");
      } catch (_) { }
    }

    window.QuizRoomsSFX = {
      init,
      isMuted: () => muted,
      isEnabled: () => !muted,
      toggle() {
        muted = !muted;
        savePref();
        if (!muted) {
          ensureContext();
          playTone(587.33, 0.12, { type: "triangle", gain: 0.07 });
          playTone(880.0, 0.18, { type: "triangle", gain: 0.07, delay: 0.08 });
        }
        return !muted;
      },
        /* Upbeat 3-beat count-in chime when a new question begins */
        roundStart() {
          playTone(392.0, 0.09, { type: "triangle", gain: 0.06, delay: 0.0 });
          playTone(523.25, 0.09, { type: "triangle", gain: 0.065, delay: 0.11 });
          playTone(659.25, 0.18, { type: "triangle", gain: 0.08, delay: 0.22 });
        },
        /* Tactile chip click pitched gently by wager (1-10) */
        wagerClick(wager) {
          const w = Math.max(1, Math.min(10, Number(wager) || 5));
          const freq = 320 + w * 38;
          playTone(freq, 0.055, { type: "triangle", gain: 0.055 });
        },
        /* Satisfying lock-in confirmation */
        lockIn() {
          playTone(523.25, 0.08, { type: "triangle", gain: 0.065, delay: 0 });
          playTone(783.99, 0.12, { type: "triangle", gain: 0.065, delay: 0.06 });
        },
        /* Gentle tick during warning window (10..6s) */
        tickSoft() {
          playTone(520, 0.045, { type: "sine", gain: 0.03 });
        },
        /* Crisp urgent countdown pulse for final 5..1 seconds */
        tickUrgent(secondsLeft) {
          const freq = secondsLeft === 1 ? 988 : 784;
          playTone(freq, 0.08, { type: "square", gain: 0.032 });
        },
        /* Bright celebratory arpeggio when player answers correctly */
        correct() {
          playTone(523.25, 0.12, { type: "triangle", gain: 0.08, delay: 0.0 });
          playTone(659.25, 0.12, { type: "triangle", gain: 0.08, delay: 0.08 });
          playTone(783.99, 0.14, { type: "triangle", gain: 0.085, delay: 0.16 });
          playTone(1046.5, 0.28, { type: "triangle", gain: 0.09, delay: 0.24 });
        },
        /* Playful descending buzzer when player answers wrong */
        wrong() {
          playTone(311.13, 0.16, { type: "sawtooth", gain: 0.045, delay: 0.0, freqEnd: 277.18 });
          playTone(233.08, 0.28, { type: "sawtooth", gain: 0.045, delay: 0.15, freqEnd: 185.0 });
        },
        /* Soft neutral cue on unanswered timeout */
        timeout() {
          playTone(392.0, 0.11, { type: "sine", gain: 0.055, delay: 0.0 });
          playTone(329.63, 0.18, { type: "sine", gain: 0.05, delay: 0.12 });
        },
        /* Neutral studio reveal chime (TV / spectator) */
        reveal() {
          playTone(440, 0.14, { type: "triangle", gain: 0.07, delay: 0 });
          playTone(554.37, 0.14, { type: "triangle", gain: 0.07, delay: 0.09 });
          playTone(659.25, 0.24, { type: "triangle", gain: 0.08, delay: 0.18 });
        },
        /* Finale podium fanfare */
        finale() {
          [523.25, 659.25, 783.99, 1046.5].forEach((f, idx) => {
            playTone(f, 0.35, { type: "triangle", gain: 0.08, delay: idx * 0.1 });
          });
        },
      };
    }) ();
