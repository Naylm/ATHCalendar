(function () {
  "use strict";

  var list = document.getElementById("event-list");
  if (!list) return;

  function publicOpen(row) {
    return (
      row.dataset.status === "scheduled" &&
      row.dataset.open === "true" &&
      row.dataset.past === "false" &&
      row.dataset.archived === "false" &&
      row.dataset.removed !== "1"
    );
  }

  function errorText(err) {
    if (err && err.message) return err.message;
    return "Une erreur est survenue ; réessayez.";
  }

  list.addEventListener("click", function (e) {
    var btn =
      e.target && e.target.closest
        ? e.target.closest("button.remove-registration")
        : null;
    if (!btn || !list.contains(btn) || btn.disabled) return;
    var row = btn.closest(".event-card");
    if (!row || row.dataset.writing === "1" || !publicOpen(row)) return;
    var chip = btn.closest(".organizer-name");
    var label = chip ? chip.querySelector(".organizer-label") : null;
    var name = label ? label.textContent.trim() : "ce nom";
    if (!window.confirm("Retirer " + name + " de cette journée ?")) return;
    if (!window.ath.live.begin(row)) return;
    var hadFocus = document.activeElement === btn;
    btn.disabled = true;
    window.ath
      .apiFetch("/api/events/" + row.dataset.eventId + "/registration/", {
        method: "PUT",
        body: JSON.stringify({
          organizer_id: btn.dataset.organizerId,
          state: "withdrawn",
          expected_event_version: parseInt(row.dataset.version, 10),
          expected_version: parseInt(
            btn.dataset.registrationVersion || "0",
            10
          ),
        }),
      })
      .then(function () {
        if (window.ath.toast) window.ath.toast("Nom retiré", false);
        return window.ath.live.end(row, true);
      })
      .catch(function (err) {
        if (window.ath.toast) window.ath.toast(errorText(err), true);
        return window.ath.live.end(row, false);
      })
      .finally(function () {
        var active = document.activeElement;
        var focusFree =
          !active ||
          active === document.body ||
          active === document.documentElement;
        if (hadFocus && !btn.isConnected && focusFree) {
          var input = row.querySelector(
            ".name-registration-form input[name=name]"
          );
          if (input && !input.disabled) {
            input.focus({ preventScroll: true });
          }
        }
        if (btn.isConnected && publicOpen(row)) {
          btn.disabled = false;
        }
      });
  });
})();
