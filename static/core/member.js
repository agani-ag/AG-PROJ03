/* The hub's public pages need almost nothing from JavaScript, and every screen works with this
   file blocked. */

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
