(function () {
  "use strict";

  var root = document.getElementById("calendar-root");
  if (!root) return;
  var detail = document.getElementById("day-detail");
  var periodId = root.dataset.periodId;
  var filtre = root.dataset.filtre || "avenir";
  var events = [];
  var monthCursor = null;
  var selectedDay = null;

  var MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre"];
  var DOW = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];

  function civil(y, m, d) {
    return new Date(Date.UTC(y, m, d, 12));
  }

  function isoOf(d) {
    return d.getUTCFullYear() + "-" +
      String(d.getUTCMonth() + 1).padStart(2, "0") + "-" +
      String(d.getUTCDate()).padStart(2, "0");
  }

  function parisToday() {
    var parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Europe/Paris",
      year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(new Date());
    var map = {};
    parts.forEach(function (p) { map[p.type] = p.value; });
    return map.year + "-" + map.month + "-" + map.day;
  }

  function fmtDate(d) {
    return new Intl.DateTimeFormat("fr-FR", {
      timeZone: "UTC", weekday: "long", day: "numeric",
      month: "long", year: "numeric",
    }).format(d);
  }

  function statusLabel(ev) {
    if (ev.status === "cancelled") return "annulée";
    if (!ev.registrations_open) return "inscriptions fermées";
    if (ev.fill === "needs_more") return "renfort nécessaire";
    if (ev.fill === "sufficient") return "équipe suffisante";
    return "aucun organisateur";
  }

  function render() {
    root.innerHTML = "";
    var nav = document.createElement("div");
    nav.className = "cal-month-nav";
    var prev = document.createElement("button");
    prev.className = "btn"; prev.type = "button";
    prev.textContent = "<";
    prev.setAttribute("aria-label", "Mois précédent");
    var next = document.createElement("button");
    next.className = "btn"; next.type = "button";
    next.textContent = ">";
    next.setAttribute("aria-label", "Mois suivant");
    var title = document.createElement("strong");
    title.textContent =
      MONTHS[monthCursor.getUTCMonth()] + " " + monthCursor.getUTCFullYear();
    prev.addEventListener("click", function () {
      monthCursor = civil(
        monthCursor.getUTCFullYear(), monthCursor.getUTCMonth() - 1, 1
      );
      render();
    });
    next.addEventListener("click", function () {
      monthCursor = civil(
        monthCursor.getUTCFullYear(), monthCursor.getUTCMonth() + 1, 1
      );
      render();
    });
    nav.appendChild(prev); nav.appendChild(title); nav.appendChild(next);
    root.appendChild(nav);

    var grid = document.createElement("div");
    grid.className = "calendar-grid";
    grid.setAttribute("role", "grid");
    DOW.forEach(function (d) {
      var el = document.createElement("div");
      el.className = "dow"; el.textContent = d;
      grid.appendChild(el);
    });
    var first = civil(monthCursor.getUTCFullYear(), monthCursor.getUTCMonth(), 1);
    var startOffset = (first.getUTCDay() + 6) % 7;
    var daysInMonth = new Date(Date.UTC(
      monthCursor.getUTCFullYear(), monthCursor.getUTCMonth() + 1, 0
    )).getUTCDate();
    for (var i = 0; i < startOffset; i++) {
      var empty = document.createElement("div");
      empty.className = "cal-day empty";
      empty.setAttribute("aria-hidden", "true");
      grid.appendChild(empty);
    }
    var today = parisToday();
    var _loop = function (day) {
      var cellDate = civil(
        monthCursor.getUTCFullYear(), monthCursor.getUTCMonth(), day
      );
      var iso = isoOf(cellDate);
      var cell = document.createElement("button");
      cell.type = "button";
      cell.className = "cal-day";
      cell.dataset.date = iso;
      cell.textContent = String(day);
      var ev = events.filter(function (e) { return e.date === iso; })[0];
      var label = fmtDate(cellDate);
      if (ev) {
        cell.classList.add(ev.fill);
        if (ev.status === "cancelled") cell.classList.add("cancelled");
        else if (!ev.registrations_open) cell.classList.add("closed");
        var count = document.createElement("span");
        count.className = "count";
        count.textContent = ev.active_count + "/" + ev.min_organizers;
        cell.appendChild(count);
        var flag = document.createElement("span");
        flag.className = "flag";
        flag.textContent = ev.status === "cancelled" ? "Annulée" :
          (!ev.registrations_open ? "Fermée" : "");
        cell.appendChild(flag);
        cell.setAttribute("aria-label",
          label + " — " + ev.active_count + " organisateur(s), " +
          statusLabel(ev));
        cell.addEventListener("click", function () {
          if (selectedDay) selectedDay.classList.remove("selected");
          selectedDay = cell;
          cell.classList.add("selected");
          showDay(ev);
        });
      } else {
        cell.disabled = true;
        cell.setAttribute("aria-label", label + " — aucune date planifiée");
      }
      if (iso === today) cell.classList.add("today");
      grid.appendChild(cell);
    };
    for (var day = 1; day <= daysInMonth; day++) _loop(day);
    root.appendChild(grid);
  }

  function showDay(ev) {
    detail.hidden = false;
    var parts = ev.date.split("-");
    var d = civil(
      parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10)
    );
    var names = ev.organizers.map(function (o) { return o.organizer_name; }).join(", ");
    detail.innerHTML = "";
    var h = document.createElement("h2");
    h.textContent = fmtDate(d);
    var info = document.createElement("p");
    info.textContent = ev.type_label + " · " + ev.active_count + " organisateur(s)" +
      (names ? " · " + names : "") + " · " + statusLabel(ev);
    var link = document.createElement("a");
    link.className = "btn"; link.href = "/dates/" + ev.id + "/";
    link.textContent = "Voir la partie";
    detail.appendChild(h); detail.appendChild(info); detail.appendChild(link);
    h.setAttribute("tabindex", "-1");
    h.focus();
  }

  window.ath.apiFetch("/api/periods/" + periodId + "/planning/?filtre=" + filtre).then(function (data) {
    events = data.events;
    var today = parisToday();
    var firstEvent = events.filter(function (e) {
      return e.date >= today && e.status === "scheduled";
    })[0] || events[0];
    var base = firstEvent ? firstEvent.date : today;
    var parts = base.split("-");
    monthCursor = civil(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, 1);
    render();
  }).catch(function () {
    root.textContent = "Impossible de charger le calendrier.";
  });
})();
