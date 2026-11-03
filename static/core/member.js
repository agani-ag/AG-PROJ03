/* The hub's public pages need almost nothing from JavaScript, and every screen works with this
   file blocked. */

/* The sign-in page, inside the SyncUp app.

   Three states ship in the document and this chooses between them. The app puts
   `window.SyncUp.token` on the page; that is not a credential and nothing here treats it as
   one — it is posted to our server, which asks SyncUp whose it is.

   What is NOT checked is a version. The app sets `window.SyncUp = Object.assign(
   window.SyncUp || {}, { token: "..." })` and nothing else. An earlier draft required
   `SyncUp.v === 1`, left over from the SSO design removed from the app in October, and since
   it is never set the button appeared on no phone at all.

   The poll is the same one hibiscus/user.js uses for the push handshake: an older WebView
   injects in onPageFinished, after this script has run, and sends no event to say so. */
(function () {
  "use strict";
  var form = document.getElementById("token-form");
  var page = document.getElementById("sign-in-page");
  if (!form || !page) { return; }

  var waiting = document.getElementById("signing-in");
  var oneButton = document.getElementById("one-button");
  var backLink = document.querySelector("[data-back-to-button]");
  var mayGoAlone = page.dataset.auto === "1";

  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  /* The name on the who-card. Fetched after the button is already on screen and never
     waited for: a name is a decoration, and the button works without one. */
  function fillName(token) {
    var body = new URLSearchParams({ token: token, csrfmiddlewaretoken: csrf() });
    fetch("/in/whose", { method: "POST", body: body, headers: { "X-CSRFToken": csrf() } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.name) { return; }
        document.querySelectorAll("[data-whose-name]").forEach(function (el) {
          el.textContent = d.name;
        });
        document.querySelectorAll("[data-whose-initials]").forEach(function (el) {
          var bits = d.name.split(" ").filter(Boolean);
          el.textContent = (bits.length > 1 ? bits[0][0] + bits[1][0] : d.name.slice(0, 2));
        });
        document.querySelectorAll("[data-whose-line]").forEach(function (el) {
          el.textContent = "as " + d.name;
        });
      })
      .catch(function () { /* the plain button is a complete answer on its own */ });
  }

  function goAlone(token) {
    page.hidden = true;
    if (waiting) { waiting.hidden = false; }
    fillName(token);
    form.submit();
  }

  function askFirst(token) {
    page.hidden = true;
    if (oneButton) { oneButton.hidden = false; }
    if (backLink) { backLink.hidden = false; }
    fillName(token);
  }

  // "Not you? Use another account", and the way back from it.
  document.querySelectorAll("[data-show-form]").forEach(function (b) {
    b.addEventListener("click", function () {
      if (oneButton) { oneButton.hidden = true; }
      page.hidden = false;
    });
  });
  document.querySelectorAll("[data-hide-form]").forEach(function (b) {
    b.addEventListener("click", function () {
      page.hidden = true;
      if (oneButton) { oneButton.hidden = false; }
    });
  });

  var tries = 0;
  var timer = setInterval(function () {
    tries += 1;
    var token = window.SyncUp && window.SyncUp.token;
    if (typeof token === "string" && token) {
      clearInterval(timer);
      form.querySelector("[name=token]").value = token;
      if (mayGoAlone) { goAlone(token); } else { askFirst(token); }
    } else if (tries > 10) {
      clearInterval(timer);               // an ordinary browser: the form is already right
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
