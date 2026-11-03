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

  // Look, then keep looking. On an older WebView the app injects its script in
  // onPageFinished — after this one has run — and sends no event to say so, so a single
  // check at load time finds nothing and the button never appears. Same poll as the push
  // handshake in hibiscus/user.js, which has been doing it correctly all along.
  //
  // What is NOT checked is a version: the app sets `window.SyncUp = Object.assign(
  // window.SyncUp || {}, { token: "..." })` and nothing else. An earlier draft of this file
  // required `SyncUp.v === 1`, which came from the SSO design that was removed from the app
  // in October. It is never set, so the test was always false and the button never showed on
  // any phone.
  var tries = 0;
  var timer = setInterval(function () {
    tries += 1;
    var token = window.SyncUp && window.SyncUp.token;
    if (typeof token === "string" && token) {
      clearInterval(timer);
      show(token);
    } else if (tries > 10) {
      clearInterval(timer);                 // not inside the app; there is nothing to offer
    }
  }, 500);
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
