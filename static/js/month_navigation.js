(function () {
  "use strict";

  var list = document.getElementById("event-list");
  var months = {};
  window.ath = window.ath || {};
  window.ath.months = months;
  if (!list) return;

  var nav = document.querySelector(".month-navigation");
  var titleEl = nav ? nav.querySelector("#month-title") : null;
  var prevBtn = nav ? nav.querySelector("[data-month-step='-1']") : null;
  var nextBtn = nav ? nav.querySelector("[data-month-step='1']") : null;
  var active = null;

  function groups() {
    return list.querySelectorAll(":scope > section.month-group");
  }

  function keys() {
    var out = [];
    groups().forEach(function (g) { out.push(g.dataset.month); });
    return out.sort();
  }

  function groupOf(key) {
    var found = null;
    groups().forEach(function (g) {
      if (g.dataset.month === key) found = g;
    });
    return found;
  }

  function apply() {
    groups().forEach(function (g) {
      g.hidden = g.dataset.month !== active;
    });
    var ks = keys();
    if (nav) nav.hidden = ks.length === 0;
    if (titleEl) {
      var g = active !== null ? groupOf(active) : null;
      var label = g ? g.dataset.label || "" : "";
      if (titleEl.textContent !== label) titleEl.textContent = label;
    }
    var idx = active === null ? -1 : ks.indexOf(active);
    if (prevBtn) prevBtn.disabled = idx <= 0;
    if (nextBtn) nextBtn.disabled = idx < 0 || idx >= ks.length - 1;
  }

  months.refresh = function () {
    var ks = keys();
    if (active !== null && ks.indexOf(active) !== -1) {
      apply();
      return;
    }
    var old = active;
    active = null;
    for (var i = 0; i < ks.length; i++) {
      if (old === null || ks[i] > old) {
        active = ks[i];
        break;
      }
    }
    if (active === null && ks.length) active = ks[ks.length - 1];
    apply();
  };

  months.select = function (key, options) {
    var ks = keys();
    if (ks.indexOf(key) === -1 || key === active) return key === active;
    var opts = options || {};
    if (opts.scroll !== false && nav) {
      var header = document.querySelector(".site-header");
      var headerBottom = header
        ? header.getBoundingClientRect().bottom
        : 0;
      var rect = nav.getBoundingClientRect();
      if (rect.top < headerBottom) {
        nav.scrollIntoView({ block: "start", behavior: "auto" });
      }
    }
    var focused = document.activeElement;
    var outgoing = active !== null ? groupOf(active) : null;
    if (outgoing && focused && outgoing.contains(focused)) {
      focused.blur();
    }
    active = key;
    apply();
    return true;
  };

  months.activeKey = function () {
    return active;
  };

  function stepTo(step) {
    var ks = keys();
    var idx = active === null ? -1 : ks.indexOf(active);
    var next = idx + step;
    if (next < 0 || next >= ks.length) return;
    months.select(ks[next]);
  }

  if (nav) {
    nav.addEventListener("click", function (e) {
      var btn = e.target.closest
        ? e.target.closest("[data-month-step]")
        : null;
      if (!btn || btn.disabled) return;
      stepTo(parseInt(btn.dataset.monthStep, 10));
    });
    nav.addEventListener("keydown", function (e) {
      if (e.key === "ArrowLeft") {
        stepTo(-1);
        e.preventDefault();
      } else if (e.key === "ArrowRight") {
        stepTo(1);
        e.preventDefault();
      }
    });
  }

  var tracking = null;

  list.addEventListener("pointerdown", function (e) {
    if (e.pointerType !== "touch" && e.pointerType !== "pen") return;
    if (e.button !== 0) return;
    if (tracking) {
      tracking = null;
      return;
    }
    var vw = document.documentElement.clientWidth;
    if (e.clientX < 24 || e.clientX > vw - 24) return;
    var t = e.target;
    if (
      t.closest &&
      t.closest("input,select,textarea,button,a,label,[contenteditable]")
    ) {
      return;
    }
    if (
      window.getSelection &&
      !window.getSelection().isCollapsed
    ) {
      return;
    }
    tracking = { id: e.pointerId, x: e.clientX, y: e.clientY, t: Date.now() };
  });

  list.addEventListener("pointerup", function (e) {
    if (!tracking || e.pointerId !== tracking.id) return;
    var dx = e.clientX - tracking.x;
    var dy = e.clientY - tracking.y;
    var dt = Date.now() - tracking.t;
    tracking = null;
    var threshold = Math.max(48, document.documentElement.clientWidth * 0.16);
    if (Math.abs(dx) <= threshold) return;
    if (Math.abs(dx) <= 1.5 * Math.abs(dy)) return;
    if (dt > 900) return;
    stepTo(dx < 0 ? 1 : -1);
  });

  list.addEventListener("pointercancel", function (e) {
    if (tracking && e.pointerId === tracking.id) tracking = null;
  });

  var initial = keys();
  active = initial.length ? initial[0] : null;
  apply();
})();
