(function () {
  "use strict";

  window.ath = window.ath || {};
  var notes = {};
  window.ath.notes = notes;

  function formOf(row) {
    return row.querySelector(":scope > .event-note-form");
  }

  function noteOf(row) {
    return row.querySelector(":scope > .event-note");
  }

  function area(form) {
    return form.querySelector("textarea");
  }

  function saveButton(form) {
    return form.querySelector(".note-save");
  }

  function cancelButton(form) {
    return form.querySelector(".note-cancel");
  }

  function msgEl(form) {
    return form.querySelector(".note-msg");
  }

  function rowOf(form) {
    return form.closest(".event-card");
  }

  function locked(form) {
    var row = rowOf(form);
    return (
      form.dataset.locked === "1" ||
      (row && row.dataset.removed === "1")
    );
  }

  function updateControls(form) {
    var dirty = form.dataset.dirty === "1";
    var off = locked(form);
    area(form).readOnly = off;
    saveButton(form).disabled = off || !dirty;
    cancelButton(form).hidden = !dirty;
  }

  function setMsg(form, text, alert) {
    var el = msgEl(form);
    el.textContent = text;
    el.setAttribute("role", alert ? "alert" : "status");
    el.hidden = !text;
  }

  function errorText(err) {
    if (err && err.message) return err.message;
    return "Une erreur est survenue ; réessayez.";
  }

  notes.lock = function (row, message) {
    var form = formOf(row);
    if (!form) return;
    form.dataset.locked = "1";
    updateControls(form);
    if (message) setMsg(form, message, false);
  };

  notes.patch = function (dst, src) {
    var dform = formOf(dst);
    var sform = formOf(src);
    var dnote = noteOf(dst);
    var snote = noteOf(src);

    if (dform && dform.dataset.dirty === "1") {
      // Brouillon local : ne jamais écraser ni réaligner la version de base.
      if (!sform) {
        notes.lock(dst, "Date verrouillée ; brouillon conservé.");
      } else {
        delete dform.dataset.locked;
        var el = msgEl(dform);
        var hasAlert =
          el.getAttribute("role") === "alert" && el.textContent !== "";
        if (sform.dataset.noteVersion !== dform.dataset.noteVersion && !hasAlert) {
          setMsg(dform, "La date a changé ailleurs ; brouillon conservé.", false);
        }
        updateControls(dform);
      }
      if (dnote) dnote.remove();
      return;
    }

    if (dform && sform) {
      var da = area(dform);
      var sa = area(sform);
      if (da.value !== sa.value) da.value = sa.value;
      da.defaultValue = sa.defaultValue;
      dform.dataset.noteVersion = sform.dataset.noteVersion;
      delete dform.dataset.locked;
      delete dform.dataset.dirty;
      setMsg(dform, "", false);
      updateControls(dform);
      if (dnote) dnote.remove();
      return;
    }

    var dnode = dform || dnote;
    var snode = sform || snote;
    if (dnode && snode) {
      if (dnode.outerHTML !== snode.outerHTML) dnode.replaceWith(snode.cloneNode(true));
    } else if (!dnode && snode) {
      dst.appendChild(snode.cloneNode(true));
    } else if (dnode && !snode) {
      dnode.remove();
    }
  };

  async function save(form) {
    var row = rowOf(form);
    if (!row || form.dataset.dirty !== "1" || locked(form)) return;
    if (!window.ath.live.begin(row)) return;
    var ta = area(form);
    ta.readOnly = true;
    saveButton(form).disabled = true;
    cancelButton(form).disabled = true;
    setMsg(form, "", false);
    try {
      var data = await window.ath.apiFetch(
        "/api/events/" + row.dataset.eventId + "/note/",
        {
          method: "PUT",
          body: JSON.stringify({
            public_note: ta.value,
            expected_event_version: parseInt(form.dataset.noteVersion, 10),
          }),
        }
      );
      ta.defaultValue = data.event.public_note;
      form.dataset.noteVersion = data.event.version;
      delete form.dataset.dirty;
      setMsg(form, "Note enregistrée.", false);
      await window.ath.live.end(row, true);
    } catch (err) {
      var text = errorText(err);
      if (err && err.httpStatus === 409) {
        text +=
          " Votre brouillon est conservé ; « Annuler » recharge la note actuelle.";
      }
      setMsg(form, text, true);
      if (window.ath.toast) window.ath.toast(errorText(err), true);
      await window.ath.live.end(row, false);
    } finally {
      cancelButton(form).disabled = false;
      updateControls(form);
    }
  }

  function cancel(form) {
    var row = rowOf(form);
    var ta = area(form);
    ta.value = ta.defaultValue;
    delete form.dataset.dirty;
    setMsg(form, "", false);
    if (
      row &&
      row.dataset.removed === "1" &&
      row.contains(document.activeElement)
    ) {
      document.activeElement.blur();
    }
    updateControls(form);
    window.ath.live.refresh(true).catch(function () {});
  }

  document.addEventListener("input", function (e) {
    var ta = e.target && e.target.closest ? e.target.closest(".event-note-form textarea") : null;
    if (!ta) return;
    var form = ta.closest(".event-note-form");
    if (ta.value !== ta.defaultValue) form.dataset.dirty = "1";
    else delete form.dataset.dirty;
    updateControls(form);
  });

  document.addEventListener("submit", function (e) {
    var form = e.target && e.target.closest ? e.target.closest(".event-note-form") : null;
    if (!form) return;
    e.preventDefault();
    save(form);
  });

  document.addEventListener("click", function (e) {
    var btn = e.target && e.target.closest ? e.target.closest(".event-note-form .note-cancel") : null;
    if (!btn) return;
    e.preventDefault();
    cancel(btn.closest(".event-note-form"));
  });
})();
