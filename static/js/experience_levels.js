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

  list.addEventListener("change", function (e) {
    var select =
      e.target && e.target.closest
        ? e.target.closest("select.experience-edit")
        : null;
    if (!select || !list.contains(select) || select.disabled) return;
    var row = select.closest(".event-card");
    if (!row || row.dataset.writing === "1" || !publicOpen(row)) {
      select.value = "";
      return;
    }
    var level = select.value;
    if (level !== "organizer" && level !== "beginner") {
      select.value = "";
      return;
    }
    var organizerId = select.dataset.organizerId;
    if (!window.ath.live.begin(row)) {
      select.value = "";
      return;
    }
    select.disabled = true;
    window.ath
      .apiFetch("/api/events/" + row.dataset.eventId + "/registration/", {
        method: "PUT",
        body: JSON.stringify({
          organizer_id: organizerId,
          state: "active",
          experience_level: level,
          expected_event_version: parseInt(row.dataset.version, 10),
          expected_version: parseInt(
            select.dataset.registrationVersion || "0",
            10
          ),
        }),
      })
      .then(function () {
        if (window.ath.toast) window.ath.toast("Niveau enregistré", false);
        select.blur();
        return window.ath.live.end(row, true).then(function () {
          var active = document.activeElement;
          var focusFree =
            !active ||
            active === document.body ||
            active === document.documentElement;
          if (!select.isConnected && focusFree) {
            var next = row.querySelector(
              'button.remove-registration[data-organizer-id="' +
                organizerId +
                '"]'
            );
            if (next && !next.disabled) next.focus({ preventScroll: true });
          }
        });
      })
      .catch(function (err) {
        select.value = "";
        if (window.ath.toast) window.ath.toast(errorText(err), true);
        select.blur();
        return window.ath.live.end(row, false);
      })
      .finally(function () {
        if (select.isConnected && publicOpen(row)) {
          select.disabled = false;
        }
      });
  });
})();
