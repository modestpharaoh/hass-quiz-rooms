/* Lightweight Canvas Celebratory Burst for QuizRooms (honors prefers-reduced-motion) */
(function () {
  const COLORS = ["#22d3ee", "#fbbf24", "#fb7185", "#34d399", "#818cf8", "#f97316"];
  let activeRaf = null;

  function burst(intensity = 1) {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      return;
    }
    if (activeRaf) {
      cancelAnimationFrame(activeRaf);
      activeRaf = null;
    }
    let canvas = document.getElementById("confetti-canvas");
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.id = "confetti-canvas";
      document.body.appendChild(canvas);
    }
    const isLiteTV =
      document.documentElement.dataset.tvPerf === "lite" ||
      /Web0S|webOS|Tizen|SMART-TV|SmartTV/i.test(navigator.userAgent || "");
    const isLiteMobile =
      document.documentElement.dataset.mobilePerf === "lite" ||
      (typeof navigator.deviceMemory === "number" && navigator.deviceMemory <= 3) ||
      (typeof navigator.hardwareConcurrency === "number" && navigator.hardwareConcurrency <= 4) ||
      Boolean(navigator.connection && navigator.connection.saveData);
    const isLite = isLiteTV || isLiteMobile;
    const ctx = canvas.getContext("2d");
    const dpr = isLite ? 1 : Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = window.innerWidth * dpr;
    canvas.height = window.innerHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const maxParticles = isLite ? 28 : 140;
    const maxFrames = isLite ? 65 : 140;
    const count = Math.min(maxParticles, Math.round((isLite ? 20 : 55) * intensity));
    const particles = [];
    const cx = window.innerWidth * 0.5;
    const cy = window.innerHeight * 0.38;

    for (let i = 0; i < count; i++) {
      const angle = Math.random() * Math.PI * 2;
      const speed = 3 + Math.random() * (9 * intensity);
      particles.push({
        x: cx + (Math.random() - 0.5) * 120,
        y: cy + (Math.random() - 0.5) * 40,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed - 4,
        w: 6 + Math.random() * 6,
        h: 4 + Math.random() * 4,
        rot: Math.random() * Math.PI * 2,
        vrot: (Math.random() - 0.5) * 0.25,
        color: COLORS[i % COLORS.length],
        life: 1,
      });
    }

    let frame = 0;
    function tick() {
      frame++;
      ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
      let alive = 0;
      for (const p of particles) {
        p.x += p.vx;
        p.y += p.vy;
        p.vy += 0.24;
        p.vx *= 0.99;
        p.rot += p.vrot;
        p.life -= 0.014;
        if (p.life <= 0) continue;
        alive++;
        ctx.save();
        ctx.globalAlpha = Math.max(0, p.life);
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot);
        ctx.fillStyle = p.color;
        ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
        ctx.restore();
      }
      if (alive > 0 && frame < maxFrames) {
        activeRaf = requestAnimationFrame(tick);
      } else {
        activeRaf = null;
        ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
        canvas.width = 0;
        canvas.height = 0;
        if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
      }
    }
    activeRaf = requestAnimationFrame(tick);
  }

  window.QuizRoomsConfetti = { burst };
})();
