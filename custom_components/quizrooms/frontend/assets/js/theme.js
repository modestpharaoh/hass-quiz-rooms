/* QuizRooms Theme Controller — auto (default), light, dark
   Tracks prefers-color-scheme live when in auto mode. */
(function () {
  const STORAGE_KEY = "quizrooms_theme";
  const VALID = new Set(["auto", "light", "dark"]);
  const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");

  function getSavedMode() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      return VALID.has(saved) ? saved : "auto";
    } catch (_) {
      return "auto";
    }
  }

  function resolveTheme(mode) {
    if (mode === "light" || mode === "dark") return mode;
    return mediaQuery.matches ? "dark" : "light";
  }

  function applyMode(mode, { persist = true } = {}) {
    const clean = VALID.has(mode) ? mode : "auto";
    if (persist) {
      try {
        localStorage.setItem(STORAGE_KEY, clean);
      } catch (_) {}
    }
    const resolved = resolveTheme(clean);
    document.documentElement.dataset.themeMode = clean;
    document.documentElement.dataset.theme = resolved;
    syncPills(clean);
    window.dispatchEvent(
      new CustomEvent("quizrooms-theme-change", { detail: { mode: clean, resolved } })
    );
  }

  function syncPills(activeMode) {
    document.querySelectorAll("[data-theme-pill]").forEach((pill) => {
      pill.querySelectorAll("button[data-mode]").forEach((btn) => {
        btn.setAttribute("aria-pressed", String(btn.dataset.mode === activeMode));
      });
    });
  }

  function mountPill(container) {
    if (!container) return;
    container.classList.add("theme-pill");
    container.setAttribute("data-theme-pill", "true");
    container.setAttribute("role", "group");
    container.setAttribute("aria-label", "Theme mode");
    const modes = [
      { id: "auto", label: "Auto" },
      { id: "light", label: "Light" },
      { id: "dark", label: "Dark" },
    ];
    container.innerHTML = "";
    const current = document.documentElement.dataset.themeMode || getSavedMode();
    modes.forEach((m) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.dataset.mode = m.id;
      btn.textContent = m.label;
      btn.setAttribute("aria-pressed", String(m.id === current));
      btn.addEventListener("click", () => applyMode(m.id, { persist: true }));
      container.appendChild(btn);
    });
  }

  const onMediaChange = () => {
    const mode = document.documentElement.dataset.themeMode || getSavedMode();
    if (mode === "auto") {
      document.documentElement.dataset.theme = resolveTheme("auto");
    }
  };
  if (mediaQuery.addEventListener) {
    mediaQuery.addEventListener("change", onMediaChange);
  } else if (mediaQuery.addListener) {
    mediaQuery.addListener(onMediaChange);
  }

  window.QuizRoomsTheme = {
    getMode: () => document.documentElement.dataset.themeMode || getSavedMode(),
    setMode: applyMode,
    mountPill,
  };
})();
