(function () {
  "use strict";

  var memoryStore = {};

  function storage() {
    try {
      var key = "__ath_test__";
      window.localStorage.setItem(key, "1");
      window.localStorage.removeItem(key);
      return window.localStorage;
    } catch (err) {
      return {
        getItem: function (k) { return memoryStore[k] || null; },
        setItem: function (k, v) { memoryStore[k] = v; },
        removeItem: function (k) { delete memoryStore[k]; },
      };
    }
  }

  var store = storage();
  var openRequest = null;

  function getPseudo() {
    try {
      var raw = store.getItem("ath_pseudo");
      return raw ? JSON.parse(raw) : null;
    } catch (err) {
      return null;
    }
  }

  function notify() {
    document.dispatchEvent(
      new CustomEvent("ath:pseudo-changed", { detail: getPseudo() })
    );
  }

  function setPseudo(pseudo) {
    try {
      store.setItem("ath_pseudo", JSON.stringify(pseudo));
    } catch (err) {}
    renderActingBar();
    notify();
  }

  function clearPseudo() {
    try {
      store.removeItem("ath_pseudo");
    } catch (err) {}
    renderActingBar();
    notify();
  }

  function renderActingBar() {
    var bar = document.getElementById("acting-bar");
    if (!bar) return;
    var pseudo = getPseudo();
    if (pseudo) {
      bar.hidden = false;
      document.getElementById("acting-name").textContent = pseudo.name;
    } else {
      bar.hidden = true;
    }
  }

  function focusables(modal) {
    return Array.prototype.slice.call(
      modal.querySelectorAll(
        "button, input, select, textarea, a[href], [tabindex]"
      )
    ).filter(function (el) { return !el.disabled && !el.hidden; });
  }

  function openPicker() {
    var modal = document.getElementById("pseudo-modal");
    if (!modal) return Promise.resolve(null);
    if (openRequest) {
      openRequest.finish(null);
    }
    return new Promise(function (resolve) {
      var list = document.getElementById("pseudo-list");
      var error = document.getElementById("pseudo-error");
      var form = document.getElementById("pseudo-create-form");
      var closeBtn = document.getElementById("pseudo-close");
      var previousFocus = document.activeElement;
      var done = false;

      function finish(value) {
        if (done) return;
        done = true;
        openRequest = null;
        modal.hidden = true;
        modal.removeEventListener("keydown", onKeydown);
        form.removeEventListener("submit", onSubmit);
        closeBtn.removeEventListener("click", onCancel);
        if (previousFocus && previousFocus.focus) previousFocus.focus();
        resolve(value);
      }

      function choose(organizer) {
        setPseudo(organizer);
        finish(organizer);
      }

      function onCancel() { finish(null); }

      function onSubmit(e) {
        e.preventDefault();
        error.hidden = true;
        var name = document.getElementById("pseudo-new-name").value.trim();
        window.ath.apiFetch("/api/organizers/create/", {
          method: "POST",
          body: JSON.stringify({ name: name }),
        }).then(function (res) {
          choose(res.organizer);
        }).catch(function (err) {
          error.textContent = err.message || "Création impossible.";
          error.hidden = false;
        });
      }

      function onKeydown(e) {
        if (e.key === "Escape") {
          e.preventDefault();
          finish(null);
          return;
        }
        if (e.key !== "Tab") return;
        var items = focusables(modal);
        if (!items.length) return;
        var first = items[0];
        var last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }

      error.hidden = true;
      list.innerHTML = "";
      modal.hidden = false;
      form.addEventListener("submit", onSubmit);
      closeBtn.addEventListener("click", onCancel);
      modal.addEventListener("keydown", onKeydown);
      openRequest = { finish: finish };

      window.ath.apiFetch("/api/organizers/").then(function (data) {
        if (done) return;
        data.organizers.forEach(function (o) {
          var li = document.createElement("li");
          var btn = document.createElement("button");
          btn.type = "button";
          btn.textContent = o.name;
          btn.addEventListener("click", function () { choose(o); });
          li.appendChild(btn);
          list.appendChild(li);
        });
        var focusTarget = list.querySelector("button") ||
          document.getElementById("pseudo-new-name") || closeBtn;
        if (focusTarget) focusTarget.focus();
      }).catch(function (err) {
        if (done) return;
        error.textContent = err.message || "Chargement impossible.";
        error.hidden = false;
        var input = document.getElementById("pseudo-new-name");
        if (input) input.focus();
      });
    });
  }

  function ensurePseudo() {
    var pseudo = getPseudo();
    if (pseudo) return Promise.resolve(pseudo);
    return openPicker();
  }

  document.addEventListener("DOMContentLoaded", function () {
    renderActingBar();
    ["change-pseudo", "change-pseudo-2"].forEach(function (id) {
      var btn = document.getElementById(id);
      if (btn) btn.addEventListener("click", function () { openPicker(); });
    });
    var opener = document.getElementById("open-picker");
    if (opener) {
      opener.addEventListener("click", function () {
        openPicker().then(function (o) {
          if (o) {
            window.location.href = "/mes-inscriptions/?organizer=" + o.id;
          }
        });
      });
    }
  });

  window.ath = window.ath || {};
  window.ath.pseudo = {
    get: getPseudo,
    set: setPseudo,
    clear: clearPseudo,
    ensure: ensurePseudo,
    openPicker: openPicker,
  };
})();
