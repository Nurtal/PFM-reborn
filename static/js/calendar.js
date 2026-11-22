// FullCalendar wiring. Config comes from a json_script tag (no inline JS: CSP).
document.addEventListener("DOMContentLoaded", () => {
  const el = document.getElementById("calendar");
  const cfgEl = document.getElementById("calendar-config");
  if (!el || !cfgEl || !window.FullCalendar) return;
  const cfg = JSON.parse(cfgEl.textContent);
  const small = window.matchMedia("(max-width: 640px)").matches;

  const openInModal = (url) =>
    window.htmx.ajax("GET", url, { target: "#modal-body", swap: "innerHTML" });

  const calendar = new window.FullCalendar.Calendar(el, {
    locale: el.dataset.lang || "fr",
    initialView: small ? "timeGridDay" : "timeGridWeek",
    headerToolbar: {
      left: "prev,next today",
      center: "title",
      right: small ? "timeGridDay,listWeek" : "dayGridMonth,timeGridWeek,timeGridDay",
    },
    firstDay: 1,
    nowIndicator: true,
    allDaySlot: false,
    height: "auto",
    slotDuration: { minutes: cfg.slotMinutes },
    snapDuration: { minutes: cfg.slotMinutes },
    slotMinTime: cfg.slotMinTime,
    slotMaxTime: cfg.slotMaxTime,
    hiddenDays: cfg.hiddenDays,
    eventColor: cfg.color,
    selectable: cfg.canBook,
    selectMirror: true,
    selectOverlap: false,
    longPressDelay: 300,
    events: { url: cfg.eventsUrl },
    select(info) {
      const params = new URLSearchParams({ start: info.startStr, end: info.endStr });
      openInModal(`${cfg.newUrl}?${params}`);
      calendar.unselect();
    },
    eventClick(info) {
      info.jsEvent.preventDefault();
      const url = info.event.extendedProps.detailUrl;
      if (url) openInModal(url);
    },
  });
  calendar.render();

  document.addEventListener("bookingChanged", () => calendar.refetchEvents());
});
