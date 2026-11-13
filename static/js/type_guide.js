(function () {
  "use strict";

  var guide = document.getElementById("type-guide");
  if (!guide) return;

  document.addEventListener("click", function (event) {
    if (guide.open && !guide.contains(event.target)) guide.open = false;
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && guide.open) {
      event.preventDefault();
      guide.open = false;
      guide.querySelector("summary").focus();
    }
  });
})();
