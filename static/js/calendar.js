(function () {
  "use strict";

  var root = document.getElementById("calendar-root");
  if (!root) return;
  var detail = document.getElementById("day-detail");
  var periodId = root.dataset.periodId;
  var events = [];
  var monthCursor = null;

  var MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre"];
  var DOW = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];

  function parisToday() {
    var fmt = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Europe/Paris", year: "numeric", month: "2-digit", day: "2-digit",
    });
    return fmt.format(new Date());
  }

  function fmtDate(d) {
    return new Intl.DateTimeFormat("fr-FR", {
      timeZone: "Europe/Paris", weekday: "long", day: "numeric",
      month: "long", year: "numeric",
    }).format(d);
  }

  function render() {
    root.innerHTML = "";
    var nav = document.createElement("div");
    nav.className = "cal-month-nav";
    var prev = document.createElement("button");
    prev.className = "btn"; prev.type = "button"; prev.textContent = "<";
    var next = document.createElement("button");
    next.className = "btn"; next.type = "button"; next.textContent = ">";
    var title = document.createElement("strong");
    title.textContent = MONTHS[monthCursor.getMonth()] + " " + monthCursor.getFullYear();
    title.style.textTransform = "capitalize";
    prev.addEventListener("click", function () {
      monthCursor = new Date(monthCursor.getFullYear(), monthCursor.getMonth() - 1, 1);
      render();
    });
    next.addEventListener("click", function () {
      monthCursor = new Date(monthCursor.getFullYear(), monthCursor.getMonth() + 1, 1);
      render();
    });
    nav.appendChild(prev); nav.appendChild(title); nav.appendChild(next);
    root.appendChild(nav);

    var grid = document.createElement("div");
    grid.className = "calendar-grid";
    DOW.forEach(function (d) {
      var el = document.createElement("div");
      el.className = "dow"; el.textContent = d;
      grid.appendChild(el);
    });
    var first = new Date(monthCursor.getFullYear(), monthCursor.getMonth(), 1);
    var startOffset = (first.getDay() + 6) % 7;
    var daysInMonth = new Date(monthCursor.getFullYear(), monthCursor.getMonth() + 1, 0).getDate();
    for (var i = 0; i < startOffset; i++) {
      var empty = document.createElement("div");
      empty.className = "cal-day empty";
      grid.appendChild(empty);
    }
    var today = parisToday();
    var _loop = function (day) {
      var cellDate = new Date(monthCursor.getFullYear(), monthCursor.getMonth(), day);
      var iso = cellDate.getFullYear() + "-" +
        String(cellDate.getMonth() + 1).padStart(2, "0") + "-" +
        String(day).padStart(2, "0");
      var cell = document.createElement("button");
      cell.type = "button";
      cell.className = "cal-day";
      cell.textContent = String(day);
      var ev = events.filter(function (e) { return e.date === iso; })[0];
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
        cell.addEventListener("click", function () { showDay(ev); });
      } else if (iso === today) {
        cell.style.borderColor = "var(--amber)";
      }
      grid.appendChild(cell);
    };
    for (var day = 1; day <= daysInMonth; day++) _loop(day);
    root.appendChild(grid);
  }

  function showDay(ev) {
    detail.hidden = false;
    var parts = ev.date.split("-");
    var d = new Date(parts[0], parts[1] - 1, parts[2]);
    var names = ev.organizers.map(function (o) { return o.organizer_name; }).join(", ");
    detail.innerHTML = "";
    var h = document.createElement("h2");
    h.textContent = fmtDate(d);
    var info = document.createElement("p");
    info.textContent = ev.type_label + " · " + ev.active_count + " organisateur(s)" +
      (names ? " · " + names : "");
    var link = document.createElement("a");
    link.className = "btn"; link.href = "/dates/" + ev.id + "/";
    link.textContent = "Voir la partie";
    detail.appendChild(h); detail.appendChild(info); detail.appendChild(link);
  }

  window.ath.apiFetch("/api/periods/" + periodId + "/planning/").then(function (data) {
    events = data.events;
    var today = parisToday();
    var firstEvent = events.filter(function (e) {
      return e.date >= today && e.status === "scheduled";
    })[0] || events[0];
    var base = firstEvent ? firstEvent.date : today;
    var parts = base.split("-");
    monthCursor = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, 1);
    render();
  }).catch(function () {
    root.textContent = "Impossible de charger le calendrier.";
  });
})();
