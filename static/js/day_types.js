(function () {
  "use strict";

  function typeMsg(row) {
    return row.querySelector(":scope > .type-msg");
  }

  function showError(row, text) {
    var el = typeMsg(row);
    if (!el) return;
    el.textContent = text;
    el.setAttribute("role", "alert");
    el.hidden = false;
  }

  function clearMsg(row) {
    var el = typeMsg(row);
    if (!el) return;
    el.hidden = true;
    el.textContent = "";
  }

  function errorText(err) {
    if (err && err.message) return err.message;
    return "Une erreur est survenue ; réessayez.";
  }

  function rowLocked(row) {
    return (
      row.dataset.status !== "scheduled" ||
      row.dataset.open !== "true" ||
      row.dataset.past === "true" ||
      row.dataset.archived === "true"
    );
  }

  function paintType(select, colorClass) {
    ["type-c1", "type-c2", "type-c3", "type-c4", "type-c5", "type-unset"]
      .forEach(function (name) { select.classList.remove(name); });
    var option = select.options[select.selectedIndex];
    select.classList.add(colorClass ||
      (option && option.dataset.typeClass) || "type-unset");
  }

  async function saveType(select) {
    var row = select.closest(".event-card");
    if (!row) return;
    var value = select.value || null;
    paintType(select);
    if (value === (select.dataset.savedValue || null)) {
      select.value = select.dataset.savedValue || "";
      paintType(select);
      return;
    }
    if (!window.ath.live.begin(row)) {
      select.value = select.dataset.savedValue || "";
      paintType(select);
      return;
    }
    select.disabled = true;
    clearMsg(row);
    try {
      var data = await window.ath.apiFetch(
        "/api/events/" + row.dataset.eventId + "/type/",
        {
          method: "PUT",
          body: JSON.stringify({
            game_type_id: value,
            expected_event_version: parseInt(row.dataset.version, 10),
          }),
        }
      );
      paintType(select, data.event.type_class);
      select.dataset.savedValue = select.value;
      await window.ath.live.end(row, true);
    } catch (err) {
      select.value = select.dataset.savedValue || "";
      paintType(select);
      showError(row, errorText(err));
      if (window.ath.toast) window.ath.toast(errorText(err), true);
      await window.ath.live.end(row, false);
    } finally {
      if (document.contains(select)) select.disabled = rowLocked(row);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var list = document.getElementById("event-list");
    if (!list) return;
    list.addEventListener("change", function (e) {
      var select = e.target.closest
        ? e.target.closest(".event-type-select")
        : null;
      if (!select) return;
      saveType(select);
    });
  });
})();
