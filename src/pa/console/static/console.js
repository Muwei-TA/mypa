// Console interactions: CSRF-aware fetch helpers + double-confirm guards.
(function () {
  "use strict";

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)pa_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : null;
  }

  async function apiFetch(path, options) {
    options = options || {};
    options.headers = Object.assign(
      {"Content-Type": "application/json"},
      options.headers || {}
    );
    const token = csrfToken();
    if (token) { options.headers["X-CSRF-Token"] = token; }
    return fetch(path, options);
  }

  function confirmed(button) {
    const message = button.getAttribute("data-confirm");
    return !message || window.confirm(message);
  }

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-id]");
    if (!button) { return; }
    if (!confirmed(button)) { return; }

    if (button.classList.contains("act-cancel")) {
      const id = button.getAttribute("data-id");
      const response = await apiFetch(`/api/v1/tasks/${id}:cancel`, {method: "POST"});
      if (response.ok) { button.closest("tr").classList.add("done"); }
      else { window.alert("取消失败：" + (await response.text())); }
    }

    if (button.classList.contains("act-answer")) {
      const id = button.getAttribute("data-id");
      const option = button.getAttribute("data-option");
      const response = await apiFetch(`/api/v1/decisions/${id}:answer`, {
        method: "POST",
        body: JSON.stringify({option: option}),
      });
      if (response.ok) { window.location.reload(); }
      else { window.alert("提交失败：" + (await response.text())); }
    }
  });
})();
