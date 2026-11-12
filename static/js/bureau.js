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

  document.querySelectorAll("[data-confirm]").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      if (!window.confirm(btn.dataset.confirm)) {
        e.preventDefault();
      }
    });
  });
})();
