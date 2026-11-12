(function () {
  "use strict";

  var page = document.body.dataset.page;

  function send(eventId, state, note, expectedEventVersion, expectedVersion) {
    return window.ath.pseudo.ensure().then(function (pseudo) {
      if (!pseudo) return null;
      return window.ath.apiFetch("/api/events/" + eventId + "/registration/", {
        method: "PUT",
        body: JSON.stringify({
          organizer_id: pseudo.id,
          state: state,
          note: note,
          expected_event_version: expectedEventVersion,
          expected_version: expectedVersion,
        }),
      });
    });
  }

  function showError(el, err) {
    if (!el) { window.ath.toast(err.message || "Erreur.", true); return; }
    el.textContent = err.message || "Erreur.";
    el.hidden = false;
  }

  if (page === "event-detail") {
    var card = document.getElementById("event-card");
    var joinBtn = document.getElementById("join-btn");
    var form = document.getElementById("registration-form");
    var noteInput = document.getElementById("note-input");
    var errorEl = document.getElementById("registration-error");
    var unavailable = document.getElementById("unavailable-reason");
    var myVersion = null;

    var locked = card.dataset.status === "cancelled" ||
      card.dataset.open === "false" ||
      card.dataset.past === "true" ||
      card.dataset.archived === "true";

    if (locked) {
      joinBtn.hidden = true;
      unavailable.hidden = false;
      var reasons = [];
      if (card.dataset.status === "cancelled") reasons.push("date annulée");
      if (card.dataset.open === "false") reasons.push("inscriptions fermées");
      if (card.dataset.past === "true") reasons.push("date passée");
      if (card.dataset.archived === "true") reasons.push("période archivée");
      unavailable.textContent = "Actions indisponibles : " + reasons.join(", ") + ".";
    }

    var params = new URLSearchParams(window.location.search);
    var pseudoParam = params.get("organizer");
    var myRegs = null;
    if (pseudoParam) {
      window.ath.apiFetch("/api/organizers/" + pseudoParam + "/registrations/")
        .then(function (data) {
          myRegs = data.registrations.filter(function (r) {
            return r.event_id === card.dataset.eventId;
          })[0] || null;
          if (myRegs && myRegs.state === "active") {
            myVersion = myRegs.version;
            noteInput.value = myRegs.note || "";
            form.hidden = false;
            joinBtn.hidden = true;
          }
        });
    }

    joinBtn.addEventListener("click", function () {
      send(card.dataset.eventId, "active", noteInput.value || null,
           parseInt(card.dataset.version, 10), myVersion)
        .then(function (res) {
          if (res) { window.ath.toast("Inscription enregistrée."); window.location.reload(); }
        })
        .catch(function (err) { showError(errorEl, err); });
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      send(card.dataset.eventId, "active", noteInput.value || null,
           parseInt(card.dataset.version, 10), myVersion)
        .then(function () {
          window.ath.toast("Note enregistrée.");
          form.dataset.dirty = "false";
        })
        .catch(function (err) { showError(errorEl, err); });
    });
    noteInput.addEventListener("input", function () {
      form.dataset.dirty = "true";
    });

    var withdrawBtn = document.getElementById("withdraw-btn");
    var withdrawModal = document.getElementById("withdraw-modal");
    var withdrawSummary = document.getElementById("withdraw-summary");
    withdrawBtn.addEventListener("click", function () {
      var pseudo = window.ath.pseudo.get();
      withdrawSummary.textContent =
        "Désinscription de " + (pseudo ? pseudo.name : "ce pseudo") +
        " pour le " + document.querySelector("h1").textContent + ".";
      withdrawModal.hidden = false;
    });
    document.getElementById("withdraw-cancel").addEventListener("click", function () {
      withdrawModal.hidden = true;
    });
    document.getElementById("withdraw-confirm").addEventListener("click", function () {
      send(card.dataset.eventId, "withdrawn", null,
           parseInt(card.dataset.version, 10), myVersion)
        .then(function () {
          withdrawModal.hidden = true;
          window.ath.toast("Désinscription enregistrée.");
          window.location.reload();
        })
        .catch(function (err) {
          withdrawModal.hidden = true;
          showError(errorEl, err);
        });
    });
  }

  if (page === "planning") {
    var list = document.getElementById("event-list");
    var revision = list ? parseInt(list.dataset.revision, 10) : null;
    var periodId = list ? list.dataset.periodId : null;

    document.querySelectorAll("[data-action='quick-register']").forEach(function (btn) {
      var cardEl = btn.closest(".event-card");
      var usable = cardEl.dataset.status === "scheduled" &&
        cardEl.dataset.open === "true" &&
        cardEl.dataset.past === "false";
      btn.hidden = !usable;
      btn.addEventListener("click", function () {
        send(cardEl.dataset.eventId, "active", null,
             parseInt(cardEl.dataset.version, 10), null)
          .then(function () {
            window.ath.toast("Inscription enregistrée.");
            window.location.reload();
          })
          .catch(function (err) { window.ath.toast(err.message, true); });
      });
    });

    var toggle = document.getElementById("toggle-batch");
    var actions = document.getElementById("batch-actions");
    if (toggle) {
      toggle.addEventListener("click", function () {
        var on = actions.hidden;
        actions.hidden = !on;
        document.querySelectorAll(".batch-check").forEach(function (el) {
          el.hidden = !on;
        });
      });
    }

    function batch(state) {
      var checked = Array.prototype.slice.call(
        document.querySelectorAll(".batch-select:checked")
      );
      if (!checked.length) {
        window.ath.toast("Cochez au moins une date.", true);
        return;
      }
      window.ath.pseudo.ensure().then(function (pseudo) {
        if (!pseudo) return;
        var versions = {};
        checked.forEach(function (box) {
          var el = box.closest(".event-card");
          versions[el.dataset.eventId] = { event: parseInt(el.dataset.version, 10) };
        });
        window.ath.apiFetch("/api/registrations/batch/", {
          method: "POST",
          body: JSON.stringify({
            organizer_id: pseudo.id,
            event_ids: checked.map(function (b) { return b.value; }),
            state: state,
            versions: versions,
          }),
        }).then(function () {
          window.ath.toast("Opération appliquée.");
          window.location.reload();
        }).catch(function (err) {
          window.ath.toast(err.message, true);
        });
      });
    }

    var joinBtn = document.getElementById("batch-join");
    var leaveBtn = document.getElementById("batch-leave");
    if (joinBtn) joinBtn.addEventListener("click", function () { batch("active"); });
    if (leaveBtn) leaveBtn.addEventListener("click", function () { batch("withdrawn"); });

    if (periodId && revision !== null) {
      var dirtyNote = function () {
        var f = document.getElementById("registration-form");
        return f && f.dataset.dirty === "true";
      };
      setInterval(function () {
        if (document.hidden) return;
        window.ath.apiFetch("/api/periods/" + periodId + "/revision/")
          .then(function (data) {
            if (data.revision !== revision && !dirtyNote()) {
              var note = document.getElementById("refresh-note");
              if (note) note.hidden = false;
            }
          }).catch(function () {});
      }, 30000);
      var reload = document.getElementById("reload-page");
      if (reload) reload.addEventListener("click", function () { window.location.reload(); });
      document.addEventListener("visibilitychange", function () {
        if (!document.hidden && periodId) {
          window.ath.apiFetch("/api/periods/" + periodId + "/revision/")
            .then(function (data) {
              if (data.revision !== revision && !dirtyNote()) {
                var note = document.getElementById("refresh-note");
                if (note) note.hidden = false;
              }
            }).catch(function () {});
        }
      });
    }
  }
})();
