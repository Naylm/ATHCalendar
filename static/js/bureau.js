(function () {
  "use strict";

  var list = document.getElementById("preview-list");
  if (list) {
    function sync() {
      var chosen = Array.prototype.slice
        .call(document.querySelectorAll(".preview-check:checked"))
        .map(function (box) { return box.value; })
        .join(",");
      var field = document.getElementById("id_selected_dates");
      if (field) field.value = chosen;
    }
    list.addEventListener("change", sync);
    document.querySelectorAll("form").forEach(function (form) {
      form.addEventListener("submit", sync);
    });
    sync();
  }

  var batchForm = document.getElementById("batch-form");
  if (batchForm) {
    batchForm.addEventListener("submit", function () {
      var versions = {};
      batchForm.querySelectorAll("input[name='event_ids']:checked")
        .forEach(function (box) {
          versions[box.value] = parseInt(box.dataset.version, 10);
        });
      var field = document.getElementById("id_versions_json");
      if (field) field.value = JSON.stringify(versions);
    });
  }

  var visibilityForms = document.querySelectorAll(".visibility-form");
  if (visibilityForms.length) {
    var pending = false;
    function revisionFields() {
      return document.querySelectorAll(
        ".visibility-form input[name='expected_revision']"
      );
    }
    function rowOf(form) {
      return form.closest(".bureau-event");
    }
    function applyState(form, data) {
      var row = rowOf(form);
      var box = form.querySelector("input[name='visible']");
      var ev = data.event;
      box.checked = !ev.is_hidden;
      row.classList.toggle("is-hidden", ev.is_hidden);
      var masked = row.querySelector(".visibility-status");
      if (masked) masked.hidden = !ev.is_hidden;
      form.querySelector("input[name='expected_version']").value = ev.version;
      if (typeof data.revision === "number") {
        revisionFields().forEach(function (field) {
          field.value = data.revision;
        });
      }
    }
    function setMsg(form, text) {
      var msg = form.querySelector(".visibility-msg");
      if (!msg) return;
      if (text) {
        msg.textContent = text;
        msg.hidden = false;
      } else {
        msg.textContent = "";
        msg.hidden = true;
      }
    }
    function setLocked(locked) {
      visibilityForms.forEach(function (form) {
        var box = form.querySelector("input[name='visible']");
        if (box && !box.dataset.archived) box.disabled = locked;
        form.setAttribute("aria-busy", locked ? "true" : "false");
      });
    }
    visibilityForms.forEach(function (form) {
      var box = form.querySelector("input[name='visible']");
      if (!box) return;
      if (box.disabled) box.dataset.archived = "1";
      box.addEventListener("change", function () {
        if (pending) {
          box.checked = !box.checked;
          return;
        }
        var desired = box.checked;
        var previous = !desired;
        var body = new FormData(form);
        pending = true;
        setLocked(true);
        setMsg(form, "");
        fetch(form.getAttribute("action") || window.location.pathname, {
          method: "POST",
          credentials: "same-origin",
          headers: { Accept: "application/json" },
          body: body,
        })
          .then(function (response) {
            if (response.redirected) {
              setMsg(form, "Reconnectez-vous au Bureau.");
              box.checked = previous;
              return null;
            }
            return response
              .json()
              .then(function (data) {
                return { response: response, data: data };
              })
              .catch(function () {
                box.checked = previous;
                setMsg(form, "Actualisez la page avant de réessayer.");
                return null;
              });
          })
          .then(function (result) {
            if (!result) return;
            var data = result.data;
            if (result.response.ok && data.event) {
              applyState(form, data);
              setMsg(form, "");
            } else if (data.event) {
              applyState(form, data);
              setMsg(form, data.error && data.error.message
                ? data.error.message
                : "La date a été modifiée entre-temps.");
            } else {
              box.checked = previous;
              setMsg(form, data.error && data.error.message
                ? data.error.message
                : "Impossible de modifier la date. Réessayez.");
            }
          })
          .catch(function () {
            box.checked = previous;
            setMsg(form, "Impossible de modifier la date. Réessayez.");
          })
          .finally(function () {
            pending = false;
            setLocked(false);
          });
      });
    });
  }

  var autoFilter = document.querySelector("form[data-auto-filter]");
  if (autoFilter) {
    var select = autoFilter.querySelector("select");
    if (select) {
      select.addEventListener("change", function () {
        autoFilter.requestSubmit();
      });
      var fallback = autoFilter.querySelector("[data-filter-submit]");
      if (fallback) fallback.hidden = true;
    }
  }

  document.querySelectorAll("[data-confirm]").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      if (!window.confirm(btn.dataset.confirm)) {
        e.preventDefault();
      }
    });
  });
})();
