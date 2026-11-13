(function () {
  "use strict";

  var list = document.getElementById("event-list");
  var POLL_MS = 2000;
  var MAX_BACKOFF_MS = 15000;

  var live = {};
  window.ath = window.ath || {};
  window.ath.live = live;
  if (!list) return;

  var known = parseInt(list.dataset.revision || "0", 10);
  if (isNaN(known)) known = null;
  var epoch = 0;
  var failures = 0;
  var timer = null;
  var currentFetch = null;
  var nextForce = false;
  var nextResolvers = [];
  var deferred = new Map();

  function liveUrl(force) {
    var url =
      "/api/periods/" + list.dataset.periodId + "/live/?filtre=" +
      encodeURIComponent(list.dataset.filtre || "");
    if (!force && known !== null) url += "&revision=" + known;
    return url;
  }

  live.refresh = function (force) {
    nextForce = nextForce || !!force;
    if (currentFetch) {
      return new Promise(function (resolve, reject) {
        nextResolvers.push({ resolve: resolve, reject: reject });
      });
    }
    return run();
  };

  function run() {
    var force = nextForce;
    nextForce = false;
    var p = fetchSnapshot(force);
    currentFetch = p;
    return p.then(
      function (r) { currentFetch = null; drain(); return r; },
      function (e) { currentFetch = null; drain(); throw e; }
    );
  }

  function drain() {
    if (!nextResolvers.length) return;
    var resolvers = nextResolvers;
    nextResolvers = [];
    var q = run();
    resolvers.forEach(function (r) { q.then(r.resolve, r.reject); });
  }

  function fetchSnapshot(force) {
    var startEpoch = epoch;
    return window.ath.apiFetch(liveUrl(force), { method: "GET" }).then(function (data) {
      if (!data || typeof data.revision !== "number") return data;
      if (startEpoch !== epoch) return data;
      if (!data.changed) {
        if (known === null || data.revision > known) known = data.revision;
        failures = 0;
        return data;
      }
      if (known !== null && data.revision < known) return data;
      applySnapshot(data.html || "");
      known = data.revision;
      failures = 0;
      return data;
    });
  }

  live.begin = function (row) {
    if (!row || row.dataset.writing === "1") return false;
    row.dataset.writing = "1";
    return true;
  };

  live.end = function (row, success) {
    if (!row) return Promise.resolve();
    delete row.dataset.writing;
    var pending = deferred.get(row);
    deferred.delete(row);
    if (!success && pending) applyRowSafe(row, pending);
    if (success) epoch += 1;
    return live.refresh(true).catch(function () { return null; });
  };

  function findAnchor() {
    var rows = list.querySelectorAll("li.event-card");
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i].getBoundingClientRect();
      if (r.bottom > 0 && r.height > 0) return { el: rows[i], top: r.top };
    }
    return null;
  }

  function restoreScroll(anchor) {
    if (!anchor) return;
    var now = document.getElementById(anchor.el.id);
    if (!now || now !== anchor.el) return;
    var dy = now.getBoundingClientRect().top - anchor.top;
    if (dy) window.scrollBy(0, dy);
  }

  function applyRowSafe(dst, src) {
    var anchor = findAnchor();
    applyRow(dst, src);
    restoreScroll(anchor);
  }

  function applySnapshot(html) {
    var tpl = document.createElement("template");
    tpl.innerHTML = html;
    var anchor = findAnchor();
    reconcileGroups(tpl.content);
    if (window.ath.months && window.ath.months.refresh) {
      window.ath.months.refresh();
    }
    restoreScroll(anchor);
  }

  function place(node, parent, cursor) {
    if (cursor) {
      if (cursor.nextElementSibling !== node) cursor.after(node);
    } else if (parent.firstElementChild !== node) {
      parent.prepend(node);
    }
  }

  function rowUnsafe(li) {
    if (li.dataset.writing === "1") return true;
    if (li.querySelector(".event-note-form[data-dirty='1']")) return true;
    if (li.contains(document.activeElement)) return true;
    var input = li.querySelector(".name-registration-form input[name=name]");
    return !!(input && input.value.trim());
  }

  function reconcileGroups(frag) {
    var existing = new Map();
    list.querySelectorAll(":scope > section.month-group").forEach(function (g) {
      existing.set(g.dataset.month, g);
    });
    var seen = new Set();
    var seenEmpty = false;
    var cursor = null;
    Array.prototype.forEach.call(frag.children, function (g) {
      if (!g.matches) return;
      if (g.matches(".planning-empty")) {
        seenEmpty = true;
        if (!list.querySelector(":scope > .planning-empty")) {
          list.appendChild(g.cloneNode(true));
        }
        return;
      }
      if (!g.matches("section.month-group")) return;
      var key = g.dataset.month;
      var node = existing.get(key);
      if (node) {
        reconcileGroup(node, g);
      } else {
        node = g.cloneNode(true);
        node.hidden = true;
      }
      seen.add(key);
      place(node, list, cursor);
      cursor = node;
    });
    var emptyEl = list.querySelector(":scope > .planning-empty");
    if (emptyEl && !seenEmpty) emptyEl.remove();
    existing.forEach(function (g, key) {
      if (seen.has(key)) return;
      g.querySelectorAll(":scope > ul > li.event-card").forEach(removeRow);
      var rows = g.querySelectorAll("li.event-card");
      var unsafe = g.contains(document.activeElement);
      for (var i = 0; i < rows.length && !unsafe; i++) unsafe = rowUnsafe(rows[i]);
      if (!unsafe) g.remove();
    });
  }

  function reconcileGroup(dst, src) {
    if (src.dataset.label && dst.dataset.label !== src.dataset.label) {
      dst.dataset.label = src.dataset.label;
    }
    var ul = dst.querySelector(":scope > ul.event-list");
    var sul = src.querySelector(":scope > ul.event-list");
    if (!ul || !sul) return;
    var rows = new Map();
    ul.querySelectorAll(":scope > li.event-card").forEach(function (li) {
      rows.set(li.id, li);
    });
    var seen = new Set();
    var cursor = null;
    Array.prototype.forEach.call(sul.children, function (s) {
      if (!s.matches || !s.matches("li.event-card")) return;
      var node = rows.get(s.id);
      if (node) {
        patchRow(node, s);
      } else {
        node = s.cloneNode(true);
      }
      seen.add(s.id);
      place(node, ul, cursor);
      cursor = node;
    });
    rows.forEach(function (li, id) {
      if (seen.has(id)) return;
      removeRow(li);
    });
  }

  function removeRow(li) {
    if (rowUnsafe(li)) {
      li.dataset.removed = "1";
      li.querySelectorAll("button.remove-registration").forEach(function (b) {
        b.disabled = true;
      });
      var form = li.querySelector(".name-registration-form");
      if (form) {
        form.dataset.locked = "1";
        var btn = form.querySelector("button[type=submit]");
        if (btn) btn.disabled = true;
      }
      var msg = li.querySelector(".form-msg");
      if (msg) {
        msg.hidden = false;
        msg.setAttribute("role", "status");
        msg.textContent = "Date retirée";
      }
      if (window.ath.notes && window.ath.notes.lock) {
        window.ath.notes.lock(li, null);
      }
      return;
    }
    li.remove();
  }

  function patchRow(dst, src) {
    if (dst.dataset.writing === "1") {
      deferred.set(dst, src);
      return;
    }
    var focused = dst.contains(document.activeElement) ? document.activeElement : null;
    if (focused && focused.classList.contains("event-type-select")) {
      deferred.set(dst, src);
      if (!dst._deferFocusout) {
        dst._deferFocusout = true;
        focused.addEventListener("focusout", function once() {
          focused.removeEventListener("focusout", once);
          dst._deferFocusout = false;
          var pending = deferred.get(dst);
          if (pending) {
            deferred.delete(dst);
            patchRow(dst, pending);
          }
        });
      }
      return;
    }
    applyRow(dst, src);
  }

  function applyRow(dst, src) {
    var wasRemoved = dst.dataset.removed === "1";
    delete dst.dataset.removed;
    if (wasRemoved) {
      var rmsg = dst.querySelector(".form-msg");
      if (rmsg && rmsg.textContent === "Date retirée") {
        rmsg.hidden = true;
        rmsg.removeAttribute("role");
        rmsg.textContent = "";
      }
    }
    ["version", "status", "open", "past", "archived"].forEach(function (k) {
      if (src.dataset[k] !== undefined) dst.dataset[k] = src.dataset[k];
    });
    dst.className = src.className;

    var dwhen = dst.querySelector(":scope > .event-when");
    var swhen = src.querySelector(":scope > .event-when");
    if (dwhen && swhen) {
      var dcell = dwhen.querySelector(":scope > .event-date-cell");
      var scell = swhen.querySelector(":scope > .event-date-cell");
      if (dcell && scell) {
        patchInner(dcell, scell, ".event-dateline");
        patchInner(dcell, scell, ".event-title");
        patchInner(dcell, scell, ".event-count");
      }
      patchTypeControl(dwhen, swhen);
    }

    var dorg = dst.querySelector(":scope > .event-organizers");
    var sorg = src.querySelector(":scope > .event-organizers");
    if (dorg && sorg) {
      var dnames = dorg.querySelector(":scope > .event-names");
      var snames = sorg.querySelector(":scope > .event-names");
      if (dnames && snames && dnames.innerHTML !== snames.innerHTML) {
        dnames.innerHTML = snames.innerHTML;
      } else if (!dnames && snames) {
        dorg.insertBefore(snames.cloneNode(true), dorg.firstElementChild);
      }
      patchAction(dorg, sorg);
    }

    var dmsg = dst.querySelector(":scope > .type-msg");
    var smsg = src.querySelector(":scope > .type-msg");
    if (!dmsg && smsg) {
      dst.insertBefore(
        smsg.cloneNode(true),
        dst.querySelector(":scope > .event-note-form, :scope > .event-note")
      );
    }

    if (window.ath.notes && window.ath.notes.patch) {
      window.ath.notes.patch(dst, src);
    } else {
      var dnote = dst.querySelector(":scope > .event-note");
      var snote = src.querySelector(":scope > .event-note");
      if (dnote && snote) {
        if (dnote.innerHTML !== snote.innerHTML) dnote.innerHTML = snote.innerHTML;
      } else if (!dnote && snote) {
        dst.appendChild(snote.cloneNode(true));
      } else if (dnote && !snote) {
        dnote.remove();
      }
    }
  }

  function patchInner(dwhen, swhen, sel) {
    var d = dwhen.querySelector(":scope > " + sel);
    var s = swhen.querySelector(":scope > " + sel);
    if (d && s) {
      if (d.innerHTML !== s.innerHTML) d.innerHTML = s.innerHTML;
    } else if (!d && s) {
      var before = dwhen.querySelector(":scope > .event-count, :scope > .event-type-select, :scope > .tag");
      if (before) dwhen.insertBefore(s.cloneNode(true), before);
      else dwhen.appendChild(s.cloneNode(true));
    } else if (d && !s) {
      d.remove();
    }
  }

  function patchSelect(dsel, ssel) {
    function sig(el) {
      var out = [];
      for (var i = 0; i < el.options.length; i++) {
        var o = el.options[i];
        out.push(o.value + "|" + o.textContent + "|" + (o.disabled ? "d" : ""));
      }
      return out.join("");
    }
    if (sig(dsel) !== sig(ssel)) dsel.innerHTML = ssel.innerHTML;
    dsel.dataset.savedValue = ssel.dataset.savedValue || "";
    if (dsel.value !== ssel.value) dsel.value = ssel.value;
    if (dsel.disabled !== ssel.disabled) dsel.disabled = ssel.disabled;
  }

  function patchTypeControl(dwhen, swhen) {
    var dsel = dwhen.querySelector(":scope > .event-type-select");
    var ssel = swhen.querySelector(":scope > .event-type-select");
    var dtag = dwhen.querySelector(":scope > .tag");
    var stag = swhen.querySelector(":scope > .tag");
    if (dsel && ssel) {
      patchSelect(dsel, ssel);
    } else if (dsel && stag) {
      dsel.replaceWith(stag.cloneNode(true));
    } else if (dtag && ssel) {
      dtag.replaceWith(ssel.cloneNode(true));
    } else if (dtag && stag) {
      if (dtag.outerHTML !== stag.outerHTML) dtag.replaceWith(stag.cloneNode(true));
    } else if (!dsel && !dtag && (ssel || stag)) {
      dwhen.appendChild((ssel || stag).cloneNode(true));
    } else if ((dsel || dtag) && !ssel && !stag) {
      (dsel || dtag).remove();
    }
  }

  function insertAction(dst, node, src) {
    var before = dst.querySelector(
      ":scope > .type-msg, :scope > .event-note-form, :scope > .event-note"
    );
    if (before) dst.insertBefore(node, before);
    else dst.appendChild(node);
    if (node.classList.contains("name-registration-form")) {
      var smsg = src.querySelector(":scope > .form-msg");
      if (smsg && !dst.querySelector(":scope > .form-msg")) {
        node.insertAdjacentElement("afterend", smsg.cloneNode(true));
      }
    }
  }

  function patchAction(dst, src) {
    var sform = src.querySelector(":scope > .name-registration-form");
    var stag = src.querySelector(":scope > .event-lock-status");
    var dform = dst.querySelector(":scope > .name-registration-form");
    var dtag = dst.querySelector(":scope > .event-lock-status");
    if (sform) {
      if (dform) {
        if (dform.dataset.locked === "1") {
          delete dform.dataset.locked;
          var btn = dform.querySelector("button[type=submit]");
          var input = dform.querySelector("input[name=name]");
          if (btn) btn.disabled = false;
          if (input) input.disabled = false;
          if (dtag) dtag.remove();
        }
      } else if (dtag) {
        var clone = sform.cloneNode(true);
        dtag.replaceWith(clone);
        var smsg = src.querySelector(":scope > .form-msg");
        if (smsg) clone.insertAdjacentElement("afterend", smsg.cloneNode(true));
      } else {
        insertAction(dst, sform.cloneNode(true), src);
      }
    } else if (stag) {
      if (dform) {
        dform.dataset.locked = "1";
        var sbtn = dform.querySelector("button[type=submit]");
        if (sbtn) sbtn.disabled = true;
        if (dtag) {
          if (dtag.outerHTML !== stag.outerHTML) dtag.replaceWith(stag.cloneNode(true));
        } else {
          dform.insertAdjacentElement("afterend", stag.cloneNode(true));
        }
      } else if (dtag) {
        if (dtag.outerHTML !== stag.outerHTML) dtag.replaceWith(stag.cloneNode(true));
      } else {
        insertAction(dst, stag.cloneNode(true), src);
      }
    }
  }

  list.addEventListener("focusout", function (e) {
    var input = e.target && e.target.closest
      ? e.target.closest(".name-registration-form input[name=name]")
      : null;
    if (!input) return;
    var row = input.closest(".event-card");
    if (
      row &&
      row.dataset.removed === "1" &&
      !input.value.trim() &&
      !row.querySelector(".event-note-form[data-dirty='1']") &&
      !row.contains(document.activeElement)
    ) {
      row.remove();
      live.refresh(true).catch(function () {});
    }
  });

  function schedule(delay) {
    clearTimeout(timer);
    timer = setTimeout(tick, delay);
  }

  function tick() {
    if (document.hidden) return;
    live.refresh(false).then(
      function () { failures = 0; },
      function () { failures += 1; }
    ).finally(function () {
      if (!document.hidden) {
        schedule(Math.min(POLL_MS * Math.pow(2, failures), MAX_BACKOFF_MS));
      }
    });
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) tick();
  });

  schedule(POLL_MS + Math.random() * 300);
})();
