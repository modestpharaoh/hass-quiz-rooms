/* Optional Web Audio API Studio Sound Effects for TV Dashboard (muted by default) */
(function () {
  let muted = true;
  let audioCtx = null;

  function ensureContext() {
    if (!audioCtx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) audioCtx = new Ctx();
    }
    if (audioCtx && audioCtx.state === "suspended") {
      audioCtx.resume().catch(() => {});
    }
    return audioCtx;
  }

  function playTone(freq, duration, { type = "sine", gain = 0.08, delay = 0 } = {}) {
    if (muted) return;
    const ctx = ensureContext();
    if (!ctx) return;
    const now = ctx.currentTime + delay;
    const osc = ctx.createOscillator();
    const amp = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, now);
    amp.gain.setValueAtTime(0.0001, now);
    amp.gain.exponentialRampToValueAtTime(gain, now + 0.015);
    amp.gain.exponentialRampToValueAtTime(0.0001, now + duration);
    osc.connect(amp);
    amp.connect(ctx.destination);
    osc.start(now);
    osc.stop(now + duration + 0.02);
  }

  window.QuizRoomsSFX = {
    isMuted: () => muted,
    toggle() {
      muted = !muted;
      if (!muted) {
        ensureContext();
        playTone(587.33, 0.14, { type: "triangle", gain: 0.07 });
        playTone(880.0, 0.18, { type: "triangle", gain: 0.07, delay: 0.08 });
      }
      return !muted;
    },
    tickUrgent() {
      playTone(740, 0.07, { type: "square", gain: 0.03 });
    },
    lockIn() {
      playTone(523.25, 0.09, { type: "triangle", gain: 0.06 });
    },
    reveal() {
      playTone(440, 0.14, { type: "triangle", gain: 0.07, delay: 0 });
      playTone(554.37, 0.14, { type: "triangle", gain: 0.07, delay: 0.09 });
      playTone(659.25, 0.24, { type: "triangle", gain: 0.08, delay: 0.18 });
    },
    finale() {
      [523.25, 659.25, 783.99, 1046.5].forEach((f, idx) => {
        playTone(f, 0.35, { type: "triangle", gain: 0.08, delay: idx * 0.1 });
      });
    },
  };
})();
