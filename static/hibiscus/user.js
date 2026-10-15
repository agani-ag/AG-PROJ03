/* Hibiscus — the only JavaScript on the diary side, about 3 KB.
   Four jobs, each of which saves a page load or a trip to the admin:
     1. the day sheet on the calendar,
     2. the plus/minus buttons on stocks,
     3. the theme switch,
     4. the reminder shown once when somebody arrives.
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


  /* ---------- the arrival reminder ----------
     Once when somebody arrives — after signing in, after opening a magic link, or on their first
     page of the day with a session still running — and never again while they move around the
     diary. The server decides which of those it is; this only asks and delivers.

     Delivery is the plain Notification API. Inside the SyncUp app that same call becomes a real
     Android notification, because the app answers Notification.permission and new Notification()
     for the page, so there is nothing app-specific to write here. When permission has not been
     given, the line is shown in the page instead — a notification nobody sees is worse than a
     banner, and the browser will not let us ask without a tap. */
  var reminderUrl = document.body.getAttribute("data-reminder-url");

  function banner(title, body, askable) {
    var box = document.createElement("div");
    box.className = "greet";
    box.innerHTML =
      '<b></b><span></span>' +
      (askable ? '<button type="button" class="btn sm ghost" data-ask>Turn on notifications</button>' : '') +
      '<button type="button" class="x" aria-label="Dismiss">&times;</button>';
    box.querySelector("b").textContent = title;
    box.querySelector("span").textContent = body;
    box.querySelector(".x").addEventListener("click", function () { box.remove(); });
    var ask = box.querySelector("[data-ask]");
    if (ask) {
      ask.addEventListener("click", function () {
        Notification.requestPermission().then(function (p) {
          if (p === "granted") { show(title, body); box.remove(); }
          else { ask.remove(); }
        });
      });
    }
    var main = document.querySelector("main .wrap");
    if (main) main.insertBefore(box, main.firstChild);
  }

  function show(title, body) {
    try {
      new Notification(title, { body: body, tag: "hibiscus", icon: "/static/hibiscus/flower.svg" });
      return true;
    } catch (err) {
      return false;
    }
  }

  if (reminderUrl) {
    fetch(reminderUrl, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.show) return;
        var can = ("Notification" in window);
        if (can && Notification.permission === "granted" && show(d.title, d.body)) return;
        banner(d.title, d.body, can && Notification.permission === "default");
      })
      .catch(function () {});
  }

  /* The same thing from the settings page, where it can be turned on deliberately rather than
     the one moment the arrival line offers it. */
  var notifRow = document.querySelector("[data-notif]");
  if (notifRow) {
    var state = notifRow.querySelector("[data-notif-state]");
    var askBtn = notifRow.querySelector("[data-notif-ask]");
    var paint = function () {
      var p = ("Notification" in window) ? Notification.permission : "unsupported";
      state.textContent = p === "granted" ? "On — reminders show on this device."
        : p === "denied" ? "Blocked in this browser's site settings."
        : p === "default" ? "Off — turn it on to get the daily line here."
        : "This browser cannot show notifications.";
      askBtn.hidden = p !== "default";
    };
    notifRow.hidden = false;
    paint();
    askBtn.addEventListener("click", function () {
      Notification.requestPermission().then(paint);
    });
  }

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
