/* Pocket Money — the little the pages need from JavaScript.
   Four things, all optional: every screen works with this file blocked.

   1. The SyncUp device token, handed over once so notifications can reach the phone.
   2. Copy buttons.
   3. The UPI hand-off: tapping Pay opens the UPI app, and when you come back the
      "did it go through?" panel is open and waiting. Nothing is recorded without a tap.
   4. A QR scanner for UPI ID fields — native BarcodeDetector, no library. */
(function () {
  "use strict";

  var body = document.body;

  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  // ---- 1. the device token, only ever available inside the SyncUp app
  (function token() {
    var url = body.dataset.tokenUrl;
    if (!url || !window.SyncUp || !window.SyncUp.token) return;
    try {
      fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        body: JSON.stringify({ token: window.SyncUp.token })
      });
    } catch (e) { /* a missing notification is a nuisance, not a broken page */ }
  })();

  // ---- 2. copy
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy]");
    if (!button) return;
    event.preventDefault();
    var text = button.dataset.copy;
    var done = function () {
      var was = button.innerHTML;
      button.textContent = "✓";
      setTimeout(function () { button.innerHTML = was; }, 1200);
    };
    if (navigator.clipboard) { navigator.clipboard.writeText(text).then(done, function () {}); }
    else {
      var box = document.createElement("textarea");
      box.value = text; document.body.appendChild(box); box.select();
      try { document.execCommand("copy"); done(); } catch (e) {}
      document.body.removeChild(box);
    }
  });

  // ---- 3. the UPI hand-off
  // The link is a redirect and nothing more: no amount goes out, and nothing comes back. So the
  // only honest thing to do is ask when the person returns.
  var paid = false;
  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-pay]")) { paid = true; }
  });
  function askOnReturn() {
    if (!paid) return;
    paid = false;
    var panel = document.getElementById("record");
    if (!panel) return;
    panel.open = true;
    panel.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  window.addEventListener("pageshow", askOnReturn);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) askOnReturn();
  });

  // ---- 4. the QR scanner
  // Android's WebView is Chromium, so BarcodeDetector reads the code with no library at all,
  // and SyncUp already grants the WebView its camera. Where the API is missing — iOS Safari,
  // Firefox — the button is never shown and typing works as it always did.
  var canScan = ("BarcodeDetector" in window) && navigator.mediaDevices
                && navigator.mediaDevices.getUserMedia;
  var scanButtons = document.querySelectorAll("[data-scan]");
  if (!canScan || !scanButtons.length) return;

  var target = null, stream = null, detector = null, timer = null;
  var overlay = document.createElement("div");
  overlay.className = "scan";
  overlay.innerHTML =
    '<div class="bar"><button type="button" class="rb" data-scan-close aria-label="Close">' +
    '<svg class="ic sm" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round">' +
    '<path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button>' +
    '<b style="font-size:16px">Scan a UPI QR</b></div>' +
    '<video playsinline muted></video>' +
    '<div class="foot"><div data-scan-msg>Point at any UPI QR — printed, on a screen, or in a ' +
    'screenshot.</div><div class="tiny">Only the UPI ID is taken. An amount printed in the code ' +
    'is ignored — it is somebody else\'s figure, not what you owe.</div></div>';
  document.body.appendChild(overlay);
  var video = overlay.querySelector("video");
  var message = overlay.querySelector("[data-scan-msg]");

  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
    overlay.classList.remove("open");
  }

  overlay.addEventListener("click", function (event) {
    if (event.target.closest("[data-scan-close]")) stop();
  });

  function accept(text) {
    var url = body.dataset.scanUrl;
    if (!url) return;
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ text: text })
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (!data.ok) { message.textContent = data.error || "That QR is not a UPI code."; return; }
      if (target) {
        target.value = data.upi;
        target.dispatchEvent(new Event("input", { bubbles: true }));
      }
      stop();
    }).catch(function () { message.textContent = "Could not read that one. Try again."; });
  }

  function start(input) {
    target = input;
    message.textContent = "Point at any UPI QR — printed, on a screen, or in a screenshot.";
    overlay.classList.add("open");
    detector = detector || new window.BarcodeDetector({ formats: ["qr_code"] });
    navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } })
      .then(function (s) {
        stream = s;
        video.srcObject = s;
        return video.play();
      })
      .then(function () {
        timer = setInterval(function () {
          detector.detect(video).then(function (codes) {
            if (codes && codes.length) { clearInterval(timer); timer = null; accept(codes[0].rawValue); }
          }).catch(function () {});
        }, 400);
      })
      .catch(function () {
        message.textContent = "No camera here. Type the ID instead — it is only ever a shortcut.";
      });
  }

  scanButtons.forEach(function (button) {
    button.hidden = false;
    button.addEventListener("click", function () {
      var field = button.closest(".field");
      start(field ? field.querySelector("[data-upi]") : document.querySelector("[data-upi]"));
    });
  });
})();
