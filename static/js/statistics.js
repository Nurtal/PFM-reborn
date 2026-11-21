// Statistics charts. Data comes from a json_script tag; colours from CSS tokens.
document.addEventListener("DOMContentLoaded", () => {
  const dataEl = document.getElementById("charts-data");
  if (!dataEl || !window.Chart) return;
  const data = JSON.parse(dataEl.textContent);
  const css = getComputedStyle(document.documentElement);
  const accent = css.getPropertyValue("--color-accent").trim() || "#4f46e5";
  const dark = document.documentElement.classList.contains("dark");
  const grid = dark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)";
  const text = dark ? "#a1a1aa" : "#52525b";
  window.Chart.defaults.color = text;
  window.Chart.defaults.font.family = css.getPropertyValue("--font-sans");

  const bar = (id, series, horizontal) => {
    const el = document.getElementById(id);
    if (!el) return;
    new window.Chart(el, {
      type: "bar",
      data: {
        labels: series.labels,
        datasets: [{ data: series.values, backgroundColor: accent, borderRadius: 4, maxBarThickness: 32 }],
      },
      options: {
        indexAxis: horizontal ? "y" : "x",
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { grid: { color: grid } }, y: { grid: { color: grid }, beginAtZero: true } },
      },
    });
  };
  bar("chart-usage", data.usage, true);
  bar("chart-revenue", data.revenue, false);
});
