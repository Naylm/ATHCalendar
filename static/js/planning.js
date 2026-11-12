(function () {
  "use strict";

  var page = document.body.dataset.page;

  function sendFor(organizerId, eventId, state, note,
                   expectedEventVersion, expectedVersion) {
    return window.ath.apiFetch("/api/events/" + eventId + "/registration/", {
      method: "PUT",
      body: JSON.stringify({
        organizer_id: organizerId,
        state: state,
        note: note,
        expected_event_version: expectedEventVersion,
        expected_version: expectedVersion,
      }),
    });
  }

  function showError(el, err) {
    if (!el) { window.ath.toast(err.message || "Erreur.", true); return; }
    el.textContent = err.message || "Erreur.";
    el.hidden = false;
  }

  var myRegsCache = null;
  function myRegs(pseudoId, history) {
    if (myRegsCache && myRegsCache.id === pseudoId) {
      return Promise.resolve(myRegsCache.data);
    }
    var url = "/api/organizers/" + pseudoId + "/registrations/" +
      (history ? "?history=1" : "");
    return window.ath.apiFetch(url).then(function (data) {
      myRegsCache = { id: data.organizer.id, data: data };
      return data;
    }).catch(function (err) {
      if (err.httpStatus === 404) {
        window.ath.pseudo.clear();
      }
      throw err;
    });
  }

  function stashToast(message) {
    try {
      sessionStorage.setItem("ath_toast", message);
    } catch (err) {}
  }

  function popToast() {
    try {
      var message = sessionStorage.getItem("ath_toast");
      if (message) {
        sessionStorage.removeItem("ath_toast");
        window.ath.toast(message);
      }
    } catch (err) {}
  }

  function pollRevision(periodId, known, onChange) {
    if (!periodId || known === null || isNaN(known)) return;
    function check() {
      window.ath.apiFetch("/api/periods/" + periodId + "/revision/")
        .then(function (data) {
          if (data.revision !== known) onChange(data.revision);
        }).catch(function () {});
    }
    setInterval(function () {
      if (!document.hidden) check();
    }, 30000);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) check();
    });
  }

  function refreshNotice() {
    var note = document.getElementById("refresh-note");
    if (note) note.hidden = false;
  }

  var reloadBtn = document.getElementById("reload-page");
  if (reloadBtn) {
    reloadBtn.addEventListener("click", function () {
      window.location.reload();
    });
  }

  function trapModal(modal) {
    function items() {
      return Array.prototype.slice.call(
        modal.querySelectorAll("button, input, a[href]")
      ).filter(function (el) { return !el.disabled; });
    }
    modal.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        e.preventDefault();
        modal.hidden = true;
      } else if (e.key === "Tab") {
        var list = items();
        if (!list.length) return;
        var first = list[0];
        var last = list[list.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    });
  }

  document.addEventListener("DOMContentLoaded", popToast);

  if (page === "event-detail") {
    var card = document.getElementById("event-card");
    var joinBtn = document.getElementById("join-btn");
    var form = document.getElementById("registration-form");
    var noteInput = document.getElementById("note-input");
    var errorEl = document.getElementById("registration-error");
    var unavailable = document.getElementById("unavailable-reason");
    var eventVersion = parseInt(card.dataset.version, 10);
    var myVersion = 0;
    var inFlight = false;
    var selected = null;
    var suppressNav = false;

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
    var stored = window.ath.pseudo.get();
    var candidateId = pseudoParam || (stored && stored.id);

    function loadSelectedPseudo(id) {
      return myRegs(id, true).then(function (data) {
        var current = window.ath.pseudo.get();
        if (!current || current.id !== data.organizer.id ||
            current.name !== data.organizer.name) {
          suppressNav = true;
          window.ath.pseudo.set(data.organizer);
          suppressNav = false;
        }
        selected = data.organizer;
        var mine = data.registrations.filter(function (r) {
          return r.event_id === card.dataset.eventId;
        })[0] || null;
        if (mine) {
          myVersion = mine.version;
          if (mine.state === "active") {
            noteInput.value = mine.note || "";
            if (!locked) {
              form.hidden = false;
              joinBtn.hidden = true;
            }
          }
        }
        return selected;
      }).catch(function () {
        selected = null;
        return null;
      });
    }

    var selectedReady = candidateId ? loadSelectedPseudo(candidateId)
      : Promise.resolve(null);

    document.addEventListener("ath:pseudo-changed", function (e) {
      if (suppressNav || inFlight) return;
      var next = e.detail;
      var nextId = next && next.id;
      var currentId = selected && selected.id;
      if (nextId && nextId !== currentId) {
        params.set("organizer", nextId);
        window.location.href =
          window.location.pathname + "?" + params.toString();
      }
    });

    function run(work) {
      if (inFlight) return Promise.resolve(null);
      inFlight = true;
      [joinBtn, document.getElementById("save-note-btn"),
       document.getElementById("withdraw-btn")].forEach(function (b) {
        if (b) b.disabled = true;
      });
      return work().finally(function () {
        inFlight = false;
        [joinBtn, document.getElementById("save-note-btn"),
         document.getElementById("withdraw-btn")].forEach(function (b) {
          if (b) b.disabled = false;
        });
      });
    }

    function handleConflict(err) {
      if (err.httpStatus === 409) {
        showError(errorEl, {
          message: (err.message || "Conflit.") +
            " Rechargez la page avant de réessayer ; votre note saisie est conservée."
        });
      } else {
        showError(errorEl, err);
      }
    }

    function reloadWithToast(message) {
      stashToast(message);
      if (selected) params.set("organizer", selected.id);
      window.location.href =
        window.location.pathname + "?" + params.toString();
    }

    function updateListedNote(note) {
      document.querySelectorAll(".organizer-list li").forEach(function (li) {
        var name = li.querySelector("strong");
        if (selected && name && name.textContent === selected.name) {
          var span = li.querySelector(".muted");
          if (note) {
            if (!span) {
              span = document.createElement("span");
              span.className = "muted";
              li.appendChild(document.createTextNode(" "));
              li.appendChild(span);
            }
            span.textContent = "— " + note;
          } else if (span) {
            span.remove();
          }
        }
      });
    }

    joinBtn.addEventListener("click", function () {
      run(function () {
        return selectedReady.then(function (current) {
          var pick = current ? Promise.resolve(current)
            : window.ath.pseudo.openPicker().then(function (o) {
                if (!o) return null;
                suppressNav = true;
                return loadSelectedPseudo(o.id).finally(function () {
                  suppressNav = false;
                });
              });
          return pick.then(function (org) {
            if (!org) return null;
            return sendFor(org.id, card.dataset.eventId, "active", null,
                           eventVersion, myVersion)
              .then(function (res) {
                reloadWithToast("Inscription enregistrée.");
              })
              .catch(handleConflict);
          });
        });
      });
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      run(function () {
        return selectedReady.then(function (current) {
          if (!current) return null;
          return sendFor(current.id, card.dataset.eventId, "active",
                         noteInput.value, eventVersion, myVersion)
            .then(function (res) {
              myVersion = res.registration.version;
              form.dataset.dirty = "false";
              updateListedNote(res.registration.note);
              window.ath.toast("Note enregistrée.");
            })
            .catch(handleConflict);
        });
      });
    });
    noteInput.addEventListener("input", function () {
      form.dataset.dirty = "true";
    });

    var withdrawBtn = document.getElementById("withdraw-btn");
    var withdrawModal = document.getElementById("withdraw-modal");
    var withdrawSummary = document.getElementById("withdraw-summary");
    trapModal(withdrawModal);
    withdrawBtn.addEventListener("click", function () {
      withdrawSummary.textContent =
        "Désinscription de " + (selected ? selected.name : "ce pseudo") +
        " pour le " + document.querySelector("h1").textContent + ".";
      withdrawModal.hidden = false;
      document.getElementById("withdraw-cancel").focus();
    });
    document.getElementById("withdraw-cancel").addEventListener("click", function () {
      withdrawModal.hidden = true;
      withdrawBtn.focus();
    });
    document.getElementById("withdraw-confirm").addEventListener("click", function () {
      run(function () {
        return selectedReady.then(function (current) {
          if (!current) { withdrawModal.hidden = true; return null; }
          return sendFor(current.id, card.dataset.eventId, "withdrawn", null,
                         eventVersion, myVersion)
            .then(function () {
              reloadWithToast("Désinscription enregistrée.");
            })
            .catch(function (err) {
              withdrawModal.hidden = true;
              handleConflict(err);
            });
        });
      });
    });

    pollRevision(card.dataset.periodId, parseInt(card.dataset.revision, 10),
      refreshNotice);
  }

  if (page === "planning") {
    var list = document.getElementById("event-list");
    var calRoot = document.getElementById("calendar-root");
    var source = list || calRoot;
    var revision = source ? parseInt(source.dataset.revision, 10) : null;
    var periodId = source ? source.dataset.periodId : null;
    pollRevision(periodId, revision, refreshNotice);

    document.querySelectorAll("[data-action='quick-register']").forEach(function (btn) {
      var cardEl = btn.closest(".event-card");
      var usable = cardEl.dataset.status === "scheduled" &&
        cardEl.dataset.open === "true" &&
        cardEl.dataset.past === "false" &&
        cardEl.dataset.archived !== "true";
      btn.hidden = !usable;
      if (usable) {
        btn.addEventListener("click", function () {
          btn.disabled = true;
          var done = function () { btn.disabled = false; };
          window.ath.pseudo.ensure().then(function (pseudo) {
            if (!pseudo) { done(); return; }
            myRegs(pseudo.id, true).then(function (data) {
              var mine = data.registrations.filter(function (r) {
                return r.event_id === cardEl.dataset.eventId;
              })[0];
              return sendFor(data.organizer.id, cardEl.dataset.eventId,
                "active", null, parseInt(cardEl.dataset.version, 10),
                mine ? mine.version : 0);
            }).then(function (res) {
              stashToast("Inscription enregistrée.");
              window.location.reload();
            }).catch(function (err) {
              window.ath.toast(err.message, true);
            }).finally(done);
          }).catch(done);
        });
      }
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

    var batchInFlight = false;
    function batch(state) {
      if (batchInFlight) return;
      var checked = Array.prototype.slice.call(
        document.querySelectorAll(".batch-select:checked")
      );
      if (!checked.length) {
        window.ath.toast("Cochez au moins une date.", true);
        return;
      }
      window.ath.pseudo.ensure().then(function (pseudo) {
        if (!pseudo) return;
        if (state === "withdrawn" &&
            !window.confirm(
              "Retirer " + pseudo.name + " de " + checked.length +
              " date(s) cochée(s) ?")) {
          return;
        }
        myRegs(pseudo.id, true).then(function (data) {
          var byEvent = {};
          data.registrations.forEach(function (r) { byEvent[r.event_id] = r; });
          var versions = {};
          var blocked = null;
          checked.forEach(function (box) {
            var el = box.closest(".event-card");
            var usable = el.dataset.status === "scheduled" &&
              el.dataset.open === "true" &&
              el.dataset.past === "false" &&
              el.dataset.archived !== "true";
            if (!usable && state === "active") {
              blocked = el;
            }
            var mine = byEvent[el.dataset.eventId];
            versions[el.dataset.eventId] = {
              event: parseInt(el.dataset.version, 10),
              registration: mine ? mine.version : 0,
            };
          });
          if (blocked) {
            window.ath.toast(
              "Une date cochée n'est pas ouverte aux inscriptions.", true
            );
            return;
          }
          batchInFlight = true;
          window.ath.apiFetch("/api/registrations/batch/", {
            method: "POST",
            body: JSON.stringify({
              organizer_id: data.organizer.id,
              event_ids: checked.map(function (b) { return b.value; }),
              state: state,
              versions: versions,
            }),
          }).then(function () {
            stashToast("Opération appliquée.");
            window.location.reload();
          }).catch(function (err) {
            window.ath.toast(err.message, true);
          }).finally(function () {
            batchInFlight = false;
          });
        }).catch(function (err) {
          window.ath.toast(err.message || "Chargement impossible.", true);
        });
      });
    }

    var joinBatchBtn = document.getElementById("batch-join");
    var leaveBatchBtn = document.getElementById("batch-leave");
    if (joinBatchBtn) joinBatchBtn.addEventListener("click", function () { batch("active"); });
    if (leaveBatchBtn) leaveBatchBtn.addEventListener("click", function () { batch("withdrawn"); });
  }

  if (page === "my-registrations") {
    var params2 = new URLSearchParams(window.location.search);
    var explicitId = params2.get("organizer");
    var pseudo2 = window.ath.pseudo.get();
    if (!explicitId && pseudo2 && pseudo2.id) {
      params2.set("organizer", pseudo2.id);
      window.location.replace("/mes-inscriptions/?" + params2.toString());
    } else if (explicitId) {
      myRegs(explicitId, true).then(function (data) {
        var cur = window.ath.pseudo.get();
        if (!cur || cur.id !== data.organizer.id ||
            cur.name !== data.organizer.name) {
          window.ath.pseudo.set(data.organizer);
        }
      }).catch(function () {});
      document.addEventListener("ath:pseudo-changed", function (e) {
        var next = e.detail;
        if (next && next.id && next.id !== explicitId) {
          params2.set("organizer", next.id);
          window.location.href = "/mes-inscriptions/?" + params2.toString();
        }
      });
    }
  }
})();
