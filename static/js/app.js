// Alpine components, registered for the CSP build (no inline JS expressions).
document.addEventListener("alpine:init", () => {
  window.Alpine.data("themeToggle", () => ({
    toggle() {
      const dark = !document.documentElement.classList.contains("dark");
      document.documentElement.classList.toggle("dark", dark);
      try {
        localStorage.setItem("pfm-theme", dark ? "dark" : "light");
      } catch (e) {
        /* storage unavailable: the choice lasts for this page only */
      }
    },
  }));

  window.Alpine.data("sidebar", () => ({
    open: false,
    toggle() {
      this.open = !this.open;
    },
    close() {
      this.open = false;
    },
  }));

  window.Alpine.data("toast", () => ({
    visible: true,
    init() {
      setTimeout(() => {
        this.visible = false;
      }, 6000);
    },
    dismiss() {
      this.visible = false;
    },
  }));
});
