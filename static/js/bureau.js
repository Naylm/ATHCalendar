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
})();
