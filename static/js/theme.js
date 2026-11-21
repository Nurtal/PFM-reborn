// Loaded in <head> to avoid a flash of the wrong theme.
(function () {
  let stored = null;
  try {
    stored = localStorage.getItem("pfm-theme");
  } catch (e) {
    /* ignore */
  }
  const dark = stored ? stored === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.classList.toggle("dark", dark);
})();
