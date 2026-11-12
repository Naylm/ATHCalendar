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

  function getPseudo() {
    try {
      var raw = store.getItem("ath_pseudo");
      return raw ? JSON.parse(raw) : null;
    } catch (err) {
      return null;
    }
  }

  function setPseudo(pseudo) {
    store.setItem("ath_pseudo", JSON.stringify(pseudo));
    renderActingBar();
  }

  function clearPseudo() {
    store.removeItem("ath_pseudo");
    renderActingBar();
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

  function openPicker(onChosen) {
    var modal = document.getElementById("pseudo-modal");
    if (!modal) return Promise.resolve(null);
    var list = document.getElementById("pseudo-list");
    var error = document.getElementById("pseudo-error");
    error.hidden = true;
    list.innerHTML = "";
    modal.hidden = false;

    return window.ath.apiFetch("/api/organizers/").then(function (data) {
      data.organizers.forEach(function (o) {
        var li = document.createElement("li");
        var btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = o.name;
        btn.addEventListener("click", function () {
          modal.hidden = true;
          setPseudo(o);
          if (onChosen) onChosen(o);
        });
        li.appendChild(btn);
        list.appendChild(li);
      });
      return new Promise(function (resolve) {
        var form = document.getElementById("pseudo-create-form");
        form.onsubmit = function (e) {
          e.preventDefault();
          var name = document.getElementById("pseudo-new-name").value.trim();
          window.ath.apiFetch("/api/organizers/create/", {
            method: "POST",
            body: JSON.stringify({ name: name }),
          }).then(function (res) {
            modal.hidden = true;
            setPseudo(res.organizer);
            resolve(res.organizer);
            if (onChosen) onChosen(res.organizer);
          }).catch(function (err) {
            error.textContent = err.message || "Création impossible.";
            error.hidden = false;
          });
        };
        var closeBtn = document.getElementById("pseudo-close");
        closeBtn.onclick = function () {
          modal.hidden = true;
          resolve(getPseudo());
        };
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
    var changeBtn = document.getElementById("change-pseudo");
    if (changeBtn) {
      changeBtn.addEventListener("click", function () { openPicker(); });
    }
    var second = document.getElementById("change-pseudo-2");
    if (second) {
      second.addEventListener("click", function () { openPicker(); });
    }
    var opener = document.getElementById("open-picker");
    if (opener) {
      opener.addEventListener("click", function () {
        openPicker(function (o) {
          window.location.href = "/mes-inscriptions/?organizer=" + o.id;
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
