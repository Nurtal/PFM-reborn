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

// --- Dialog, toasts and small behaviours (no inline JS: CSP) -----------------------
(function () {
  const modal = () => document.getElementById("modal");

  function toast(message) {
    const box = document.getElementById("toasts");
    if (!box || !message) return;
    const el = document.createElement("div");
    el.setAttribute("role", "status");
    el.className =
      "pointer-events-auto w-full max-w-sm rounded-lg border border-emerald-300 bg-white p-4 text-sm shadow-lg dark:border-emerald-800 dark:bg-zinc-900";
    el.textContent = message;
    box.appendChild(el);
    setTimeout(() => el.remove(), 5000);
  }

  function applyColors(root) {
    root.querySelectorAll("[data-color]").forEach((el) => {
      el.style.backgroundColor = el.dataset.color;
    });
  }

  document.addEventListener("DOMContentLoaded", () => applyColors(document));

  document.addEventListener("htmx:afterSwap", (e) => {
    applyColors(e.target);
    const dialog = modal();
    if (dialog && e.target.id === "modal-body" && !dialog.open) dialog.showModal();
  });

  document.addEventListener("toast", (e) => toast(e.detail.value));
  document.addEventListener("bookingChanged", () => {
    const dialog = modal();
    if (dialog && dialog.open) dialog.close();
  });

  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-close-dialog]")) {
      const dialog = modal();
      if (dialog) dialog.close();
    }
  });

  document.addEventListener("change", (e) => {
    const select = e.target.closest("select[data-navigate]");
    if (select && select.value) window.location.assign(select.value);
  });

  document.addEventListener("focusin", (e) => {
    if (e.target.matches("[data-select-on-focus]")) e.target.select();
  });

  window.PFM = { toast, modal };
})();
