/* Hibiscus — the only JavaScript on the diary side, about 2 KB.
   Three jobs, each of which saves a page load:
     1. the day sheet on the calendar,
     2. the plus/minus buttons on stocks,
     3. the theme switch.
   Everything else is a plain form post. No framework, nothing loaded from outside. */
(function () {
  "use strict";

  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  function post(url, data) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(data || {}),
      credentials: "same-origin"
    }).then(function (r) {
      if (!r.ok) throw new Error("save failed");
      return r.json().catch(function () { return {}; });
    });
  }

  /* ---------- 1. the day sheet ---------- */
  var sheet = null;

  function closeSheet() {
    if (!sheet) return;
    sheet.parentNode.removeChild(sheet);
    sheet = null;
    document.removeEventListener("keydown", onKey);
  }

  function onKey(e) { if (e.key === "Escape") closeSheet(); }

  function openSheet(url) {
    fetch(url, { credentials: "same-origin", headers: { "X-Sheet": "1" } })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        closeSheet();
        sheet = document.createElement("div");
        sheet.innerHTML = html;
        document.body.appendChild(sheet);
        document.addEventListener("keydown", onKey);
        var first = sheet.querySelector("[data-autofocus]");
        if (first) first.focus();
      })
      .catch(function () { window.location.href = url; });   // no JS? the link still works
  }

  document.addEventListener("click", function (e) {
    var day = e.target.closest("[data-day]");
    if (day) { e.preventDefault(); openSheet(day.dataset.day); return; }
    if (e.target.closest("[data-close]")) { e.preventDefault(); closeSheet(); }
  });

  /* ---------- 2. stocks, plus and minus ---------- */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-move]");
    if (!btn) return;
    e.preventDefault();
    var row = btn.closest("[data-item]");
    var out = row.querySelector("[data-qty]");
    btn.disabled = true;
    post(btn.dataset.move, { change: Number(btn.dataset.change) })
      .then(function (data) {
        if (typeof data.in_stock === "number") out.textContent = data.in_stock;
        if (data.is_low !== undefined) row.classList.toggle("is-low", !!data.is_low);
        var pill = row.querySelector("[data-low-pill]");
        if (pill) pill.hidden = !data.is_low;
      })
      .catch(function () { window.location.reload(); })
      .finally(function () { btn.disabled = false; });
  });

  /* ---------- 3. theme ---------- */
  var order = ["system", "light", "dark"];
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-theme-next]");
    if (!btn) return;
    e.preventDefault();
    var now = document.documentElement.getAttribute("data-theme") || "system";
    var next = order[(order.indexOf(now) + 1) % order.length];
    if (next === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("hibiscus-theme", next); } catch (err) {}
    post(btn.dataset.themeNext, { theme: next }).catch(function () {});
  });

  /* ---------- the SyncUp push handshake ----------
     Inside the SyncUp app the page is handed window.SyncUp.token. Post it once so the server can
     send this person their daily message. On older WebViews it appears a moment after load. */
  var tokenUrl = document.body.getAttribute("data-token-url");
  if (tokenUrl) {
    var tries = 0;
    var timer = setInterval(function () {
      tries += 1;
      var token = window.SyncUp && window.SyncUp.token;
      if (token) {
        clearInterval(timer);
        post(tokenUrl, { token: token }).catch(function () {});
      } else if (tries > 10) {
        clearInterval(timer);                     // not in the app; nothing to do
      }
    }, 500);
  }
})();
