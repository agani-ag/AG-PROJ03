/* The hub's public pages need almost nothing from JavaScript, and every screen works with this
   file blocked. */

/* "Use my SyncUp sign-in", which only exists inside the SyncUp app.
   The app puts `window.SyncUp.token` on the page; this reveals the button and posts that
   token. It is not a credential and nothing here treats it as one — the server asks SyncUp
   whose it is, and a phone nobody is signed in on never signs anybody in. */
(function () {
  "use strict";
  var form = document.getElementById("token-form");
  var divider = document.querySelector("[data-token-wrap]");
  if (!form) { return; }

  function show(token) {
    form.querySelector("[name=token]").value = token;
    form.hidden = false;
    if (divider) { divider.hidden = false; }
  }

  var api = window.SyncUp;
  if (api && api.v === 1 && api.token) { show(api.token); }

  // The app can hand the token over after the page has loaded, so listen as well as look.
  window.addEventListener("syncuptoken", function (event) {
    var token = event && event.detail && event.detail.token;
    if (token) { show(token); }
  });
})();

/* The "Add a password?" card can be put away. Per browser, and it comes back on the next one —
   the card is a nudge, and the real answer is on /you/ until a password exists. */
(function () {
  "use strict";
  document.querySelectorAll("[data-dismiss]").forEach(function (button) {
    var id = button.dataset.dismiss, card = document.getElementById(id);
    if (!card) return;
    try { if (localStorage.getItem("synchub-hide-" + id) === "1") { card.hidden = true; } }
    catch (e) { /* private window: the card simply stays */ }
    button.addEventListener("click", function () {
      card.hidden = true;
      try { localStorage.setItem("synchub-hide-" + id, "1"); } catch (e) {}
    });
  });
})();
