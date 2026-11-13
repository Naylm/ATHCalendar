(function () {
  "use strict";

  function rowFor(el) {
    return el.closest(".event-card");
  }

  function msgFor(form) {
    return rowFor(form).querySelector(".form-msg");
  }

  function showMsg(form, text, isError) {
    var el = msgFor(form);
    if (!el) return;
    el.textContent = text;
    el.setAttribute("role", isError ? "alert" : "status");
    el.hidden = false;
  }

  function clearMsg(form) {
    var el = msgFor(form);
    if (!el) return;
    el.hidden = true;
    el.textContent = "";
  }

  function errorText(err) {
    if (err && err.message) return err.message;
    return "Une erreur est survenue ; réessayez.";
  }

  async function findMyRegistration(organizerId, eventId) {
    var data = await window.ath.apiFetch(
      "/api/organizers/" + organizerId + "/registrations/?history=1"
    );
    var regs = (data && data.registrations) || [];
    var found = null;
    for (var i = 0; i < regs.length; i++) {
      if (regs[i].event_id === eventId) {
        found = regs[i];
        break;
      }
    }
    return { organizer: data.organizer, registration: found };
  }

  async function submitName(form) {
    var row = rowFor(form);
    var input = form.querySelector("input[name=name]");
    var button = form.querySelector("button[type=submit]");
    if (!row || !input || form.dataset.locked === "1") return;
    var name = input.value.trim();
    if (name.length < 2) {
      showMsg(form, "Indiquez votre nom (2 caractères minimum).", true);
      return;
    }
    if (!window.ath.live.begin(row)) return;
    button.disabled = true;
    input.disabled = true;
    clearMsg(form);
    try {
      var created = await window.ath.apiFetch("/api/organizers/create/", {
        method: "POST",
        body: JSON.stringify({ name: name }),
      });
      var result = await findMyRegistration(created.organizer.id, row.dataset.eventId);
      var mine = result.registration;
      await window.ath.apiFetch(
        "/api/events/" + row.dataset.eventId + "/registration/",
        {
          method: "PUT",
          body: JSON.stringify({
            organizer_id: result.organizer.id,
            state: "active",
            expected_event_version: parseInt(row.dataset.version, 10),
            expected_version: mine ? mine.version : 0,
          }),
        }
      );
      input.value = "";
      if (window.ath.toast) window.ath.toast("Ajouté", false);
      await window.ath.live.end(row, true);
    } catch (err) {
      showMsg(form, errorText(err), true);
      if (window.ath.toast) window.ath.toast(errorText(err), true);
      await window.ath.live.end(row, false);
    } finally {
      input.disabled = false;
      button.disabled = form.dataset.locked === "1";
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var list = document.getElementById("event-list");
    if (list) {
      list.addEventListener("submit", function (e) {
        var form = e.target.closest
          ? e.target.closest(".name-registration-form")
          : null;
        if (!form) return;
        e.preventDefault();
        submitName(form);
      });
    }

    if (window.location.hash) {
      try {
        var target = document.getElementById(
          decodeURIComponent(window.location.hash.slice(1))
        );
        if (target) {
          var group = target.closest(".month-group");
          if (group && window.ath.months) {
            window.ath.months.select(group.dataset.month, { scroll: false });
          }
          target.scrollIntoView();
        }
      } catch (err) {}
    }
  });
})();
