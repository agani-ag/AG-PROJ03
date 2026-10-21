/* The hub's public pages need almost nothing from JavaScript. Three small jobs, all optional:
   every screen works with this file blocked.

   1. Offer "Continue with SyncUp" — but only when there is a real SyncUp to continue with AND
      somebody is signed in to it. A button that resolves null when tapped is a dead button, so
      it appears and disappears with the account, live.
   2. Try a quiet sign-in once the person has already said yes in the app. Null means they
      haven't, or they declined: show the button instead and say nothing.
   3. Notice when the SyncUp account changes under us. A different person always ends the
      session; a plain sign-out ends only sessions that began through SyncUp and have no
      password behind them. The server decides which — this only reports the change. */
(function () {
  "use strict";

  var ssoUrl = document.body.dataset.ssoUrl;        // present only when a key is configured
  var changedUrl = document.body.dataset.changedUrl;
  var api = window.SyncUp;
  var usable = api && api.v === 1 && typeof api.signIn === "function";
  var button = document.querySelector("[data-sso]");
  var divider = document.querySelector("[data-sso-wrap]");

  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  function signedIn() {
    return !!(usable && api.account && api.account.signedIn);
  }

  function show(on) {
    if (button) { button.hidden = !on; }
    if (divider) { divider.hidden = !on; }
  }

  // ---- 3. the account changed while this page was open
  if (usable) {
    window.addEventListener("syncupaccount", function (event) {
      var detail = (event && event.detail) || {};
      // Keep the button honest: it follows the account in real time, on this page and any
      // other that offers it.
      show(ssoUrl && !!detail.signedIn);
      if (!changedUrl) { return; }                  // nobody is signed in here to sign out
      fetch(changedUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        body: JSON.stringify({ id: detail.signedIn ? detail.id : "" })
      }).then(function (r) { return r.json(); }).then(function (data) {
        if (data.signed_out) { window.location.reload(); }
      }).catch(function () { /* a missed hand-off is not worth breaking the page over */ });
    });
  }

  if (!button || !ssoUrl || !usable) { return; }

  function hand(code) {
    var form = document.getElementById("sso-form");
    form.querySelector("[name=code]").value = code;
    form.submit();
  }

  // ---- 1. Wire the button FIRST, before anything below can decide to stop.
  // Everything after this is about whether to attempt a *quiet* sign-in; none of it should
  // leave a visible button that does nothing. Two of those early exits used to sit above this
  // line, which made the button dead exactly when it was most needed — straight after Sign
  // out, and when SyncUp signed in while the page was already open.
  button.addEventListener("click", function () {
    button.disabled = true;
    Promise.resolve(api.signIn()).then(function (answer) {
      if (!answer || !answer.code) { button.disabled = false; return; }   // cancelled
      hand(answer.code);
    }).catch(function () { button.disabled = false; });
  });

  // ---- 2. Nothing is attempted while SyncUp itself is signed out: a silent call would only
  // resolve null. The 'syncupaccount' listener above reveals the button when they sign in.
  if (!signedIn()) { return; }

  // Somebody who has just pressed Sign out must not be signed straight back in. The server
  // says whether a quiet attempt is allowed; without permission the button still shows — and
  // now works — so getting back in is one deliberate tap rather than impossible.
  if (document.body.dataset.autoSso !== "1") { show(true); return; }

  Promise.resolve(api.signIn({ silent: true })).then(function (answer) {
    if (answer && answer.code) { hand(answer.code); return; }
    show(true);                                     // they have not said yes yet: ask plainly
  }).catch(function () { show(true); });
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
