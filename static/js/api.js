(function () {
  "use strict";

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  async function apiFetch(url, options) {
    const opts = options || {};
    opts.headers = Object.assign(
      { "Content-Type": "application/json" },
      opts.headers || {}
    );
    if (opts.method && opts.method !== "GET") {
      opts.headers["X-CSRFToken"] = csrfToken();
    }
    let response;
    try {
      response = await fetch(url, opts);
    } catch (err) {
      throw { code: "network", message: "Réseau indisponible ; réessayez.", fields: {} };
    }
    let body = null;
    try {
      body = await response.json();
    } catch (err) {
      body = null;
    }
    if (!response.ok) {
      const error = (body && body.error) || {
        code: "error",
        message: "Une erreur est survenue (" + response.status + ").",
        fields: {},
      };
      error.httpStatus = response.status;
      throw error;
    }
    return body;
  }

  function toast(message, isError) {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = message;
    el.className = "toast" + (isError ? " error" : "");
    el.setAttribute("role", isError ? "alert" : "status");
    el.hidden = false;
    clearTimeout(el._timer);
    el._timer = setTimeout(function () { el.hidden = true; }, 5000);
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".js-auto-submit").forEach(function (el) {
      el.addEventListener("change", function () {
        if (el.form) el.form.submit();
      });
    });
  });

  window.ath = window.ath || {};
  window.ath.apiFetch = apiFetch;
  window.ath.toast = toast;
})();
