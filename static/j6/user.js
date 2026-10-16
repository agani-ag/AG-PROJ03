/* J6 — the only JavaScript on the chat side, about 12 KB.
   Five jobs:
     1. the poll (list and thread, 304 when nothing changed),
     2. sending, typing and read marks,
     3. media by link — probe, draw, or leave it a link,
     4. notifications when the tab is not in front,
     5. the small conveniences: copy, reveal, search, autogrow.
   No framework, nothing loaded from outside. */
(function () {
  "use strict";

  var body = document.body;
  var POLL = body.getAttribute("data-poll");
  var SEND = body.getAttribute("data-send");
  var TYPING = body.getAttribute("data-typing");
  var CHAT = body.getAttribute("data-chat") || "";
  var MEDIA = {
    on: body.getAttribute("data-media") === "1",
    autoplay: parseInt(body.getAttribute("data-autoplay") || "15", 10),
    maxpx: parseInt(body.getAttribute("data-maxpx") || "240", 10),
    svg: body.getAttribute("data-svg") === "1"
  };

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
    }).then(function (r) { return r.json().catch(function () { return {}; }); });
  }
  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function timeOf(ms) {
    try { return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
    catch (e) { return ""; }
  }
  function listTime(ms) {
    if (!ms) return "";
    var age = Date.now() - ms, d = new Date(ms);
    if (age < 3600000) return Math.max(1, Math.floor(age / 60000)) + "m";
    if (age < 86400000) return timeOf(ms);
    return d.toLocaleDateString([], { day: "2-digit", month: "short" });
  }
  function dayLabel(ms) {
    var d = new Date(ms), t = new Date();
    var same = function (a, b) { return a.toDateString() === b.toDateString(); };
    if (same(d, t)) return "Today";
    if (same(d, new Date(t.getTime() - 86400000))) return "Yesterday";
    return d.toLocaleDateString([], { weekday: "short", day: "2-digit", month: "short" });
  }

  /* ---------- 5. small conveniences, on every page ---------- */
  document.addEventListener("click", function (e) {
    var copy = e.target.closest("[data-copy]");
    if (copy) {
      var text = copy.getAttribute("data-copy");
      (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject())
        .then(function () {
          copy.classList.add("done");
          setTimeout(function () { copy.classList.remove("done"); }, 1200);
        }).catch(function () { window.prompt("Copy this:", text); });
    }
    var reveal = e.target.closest("[data-reveal]");
    if (reveal) {
      var box = reveal.closest("[data-secret]").querySelector("code");
      box.textContent = box.getAttribute("data-mask");
    }
  });

  var search = document.getElementById("q");
  if (search) {
    search.addEventListener("input", function () {
      var q = search.value.toLowerCase();
      Array.prototype.forEach.call(document.querySelectorAll("#rows .row"), function (row) {
        var name = (row.getAttribute("data-name") || "").toLowerCase();
        row.style.display = !q || name.indexOf(q) >= 0 ? "" : "none";
      });
    });
  }

  /* ---------- 3. media by link ----------
     The browser decides: an Image() that loads is a picture, a <video> that reports metadata is
     a clip, an <audio> that does the same is audio. Anything else stays a link. Nothing is
     fetched until the bubble is in view, and no referrer is sent. */
  var PROBE_MS = 6000;
  function probe(kind, url) {
    return new Promise(function (resolve, reject) {
      var node = kind === "img" ? new Image() : document.createElement(kind);
      var done = false;
      var finish = function (ok) {
        if (done) return;
        done = true;
        ok ? resolve(node) : reject();
      };
      if (kind === "img") {
        node.referrerPolicy = "no-referrer";
        node.onload = function () { finish(true); };
        node.onerror = function () { finish(false); };
      } else {
        node.preload = "metadata";
        node.muted = true;
        node.addEventListener("loadedmetadata", function () { finish(true); });
        node.addEventListener("error", function () { finish(false); });
      }
      setTimeout(function () { finish(false); }, PROBE_MS);
      node.src = url;
    });
  }

  function lightbox(node) {
    var box = el("div", "lb");
    var close = el("button", "x", "✕");
    close.type = "button";
    box.appendChild(node);
    box.appendChild(close);
    var shut = function () { box.remove(); document.removeEventListener("keydown", onKey); };
    var onKey = function (e) { if (e.key === "Escape") shut(); };
    box.addEventListener("click", function (e) { if (e.target === box || e.target === close) shut(); });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(box);
  }

  function renderMedia(holder, url) {
    var slot = el("div", "media loading");
    slot.appendChild(el("span", "ring"));
    slot.appendChild(el("span", null, "Loading…"));
    holder.replaceChildren(slot);

    var asLink = function () {
      var a = el("a", null, url);
      a.href = url;
      a.rel = "noreferrer";
      holder.replaceChildren(a);
    };

    probe("img", url).then(function (img) {
      if (!MEDIA.svg && /\.svg(\?|$)/i.test(url)) { asLink(); return; }
      img.style.maxHeight = MEDIA.maxpx + "px";
      img.style.width = "auto";
      img.loading = "lazy";
      img.decoding = "async";
      slot.classList.remove("loading");
      slot.replaceChildren(img);
      slot.style.cursor = "zoom-in";
      slot.addEventListener("click", function () {
        var big = new Image();
        big.referrerPolicy = "no-referrer";
        big.src = url;
        lightbox(big);
      });
      holder.appendChild(labelFor(url));
    }).catch(function () {
      probe("video", url).then(function (v) {
        var short = v.duration && v.duration <= MEDIA.autoplay;
        v.style.maxHeight = MEDIA.maxpx + "px";
        v.playsInline = true;
        v.controls = !short;
        v.loop = short;
        v.muted = true;
        if (short) { v.autoplay = true; v.play().catch(function () {}); }
        slot.classList.remove("loading");
        slot.replaceChildren(v);
        var chip = el("span", "vchip", short ? "muted · looping" : clock(v.duration) + " · tap to play");
        slot.appendChild(chip);
        holder.appendChild(labelFor(url));
      }).catch(function () {
        probe("audio", url).then(function (a) {
          a.controls = false;
          var wrap = el("div", "audio");
          var play = el("button", "pl");
          play.type = "button";
          play.innerHTML = '<svg class="ic sm" viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15l13-7.5z"/></svg>';
          var bar = el("span", "bar2");
          var fill = el("i");
          bar.appendChild(fill);
          var time = el("span", "tiny", clock(a.duration));
          play.addEventListener("click", function () { a.paused ? a.play() : a.pause(); });
          a.addEventListener("timeupdate", function () {
            fill.style.width = (a.currentTime / (a.duration || 1) * 100) + "%";
            time.textContent = clock(a.duration - a.currentTime);
          });
          wrap.append(play, bar, time, a);
          holder.replaceChildren(wrap, labelFor(url));
        }).catch(asLink);
      });
    });
  }
  function clock(sec) {
    if (!sec || !isFinite(sec)) return "";
    var m = Math.floor(sec / 60), s = Math.floor(sec % 60);
    return m + ":" + (s < 10 ? "0" : "") + s;
  }
  function labelFor(url) {
    return el("span", "mlbl", url.replace(/^https?:\/\//, "").slice(0, 60));
  }

  var lazy = ("IntersectionObserver" in window) ? new IntersectionObserver(function (entries, obs) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      obs.unobserve(entry.target);
      renderMedia(entry.target, entry.target.getAttribute("data-url"));
    });
  }, { rootMargin: "200px" }) : null;

  function withLinks(text) {
    /* Returns a fragment: text with links made clickable, and media links marked for probing. */
    var frag = document.createDocumentFragment();
    var re = /(https?:\/\/[^\s<]+)/g, last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      var url = m[1].replace(/[.,;:!?)]+$/, "");
      var holder = el("span", "mediaslot");
      holder.setAttribute("data-url", url);
      if (MEDIA.on && /^https:\/\//i.test(url)) {
        var a = el("a", null, url);
        a.href = url;
        a.rel = "noreferrer";
        holder.appendChild(a);                       // until the probe answers
        if (lazy) lazy.observe(holder); else renderMedia(holder, url);
      } else {
        var plain = el("a", null, url);
        plain.href = url;
        plain.rel = "noreferrer";
        holder.appendChild(plain);
      }
      frag.appendChild(holder);
      last = m.index + m[1].length;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    return frag;
  }

  /* ---------- 1 + 2. the inbox ---------- */
  var inbox = document.getElementById("inbox");
  if (!inbox || !POLL) { wireNotifications(); return; }

  var boot = {};
  try { boot = JSON.parse(document.getElementById("boot").textContent); } catch (e) { boot = {}; }

  var msgs = document.getElementById("msgs");
  var input = document.getElementById("input");
  var rowsBox = document.getElementById("rows");
  var jump = document.getElementById("jump");
  var jumpN = document.getElementById("jump-n");
  var state = { seq: 0, lastDay: null, lastSender: null, readUpto: 0, news: 0, etag: null,
                typing: false, title: document.title };

  function atBottom() {
    return !msgs || msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 90;
  }
  function toBottom() { if (msgs) { msgs.scrollTop = msgs.scrollHeight; } state.news = 0; paintJump(); }
  function paintJump() {
    if (!jump) return;
    if (atBottom() || !state.news) { jump.hidden = true; jumpN.textContent = ""; }
    else { jump.hidden = false; jumpN.textContent = state.news; }
  }

  function addMessage(m) {
    if (m.seq <= state.seq) return;
    state.seq = m.seq;
    var day = new Date(m.at).toDateString();
    if (day !== state.lastDay) {
      msgs.appendChild(el("div", "dsep", dayLabel(m.at)));
      state.lastDay = day;
      state.lastSender = null;
    }
    var grouped = state.lastSender === (m.mine ? "me" : "them");
    var row = el("div", "msg " + (m.mine ? "me" : "them") + (grouped ? " grp" : ""));
    row.setAttribute("data-seq", m.seq);
    var col = el("div", "col");
    var bub = el("div", "bub");
    bub.appendChild(withLinks(m.body));
    var meta = el("div", "mta");
    meta.appendChild(el("span", null, timeOf(m.at)));
    if (m.mine) {
      var tick = el("span", "tick");
      tick.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
      meta.appendChild(tick);
    }
    col.append(bub, meta);
    row.appendChild(col);
    msgs.appendChild(row);
    state.lastSender = m.mine ? "me" : "them";
  }

  function paintTicks() {
    if (!msgs) return;
    var mine = msgs.querySelectorAll(".msg.me");
    for (var i = 0; i < mine.length; i++) {
      var tick = mine[i].querySelector(".tick");
      if (!tick) continue;
      if (i !== mine.length - 1) { tick.style.display = "none"; continue; }
      tick.style.display = "";
      var read = parseInt(mine[i].getAttribute("data-seq"), 10) <= state.readUpto;
      tick.className = read ? "tick read" : "tick";
      tick.innerHTML = read
        ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m2 13 4 4 8-10"/><path d="m11 16 1 1 9-11"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
    }
  }

  function paintTyping(on) {
    var existing = document.getElementById("typingrow");
    if (on && !existing) {
      var row = el("div", "msg them");
      row.id = "typingrow";
      row.innerHTML = '<div class="col"><div class="bub typing"><i></i><i></i><i></i></div></div>';
      msgs.appendChild(row);
      if (atBottom()) toBottom();
    } else if (!on && existing) {
      existing.remove();
    }
  }

  function paintHeader(w, typing, blocked) {
    var av = document.getElementById("th-av");
    var name = document.getElementById("th-name");
    var pres = document.getElementById("th-pres");
    var blockBtn = document.getElementById("th-block");
    if (!name) return;
    av.textContent = w.initials;
    av.style.background = w.colour;
    name.textContent = w.name;
    if (typing) { pres.textContent = "typing…"; pres.className = "pres on"; }
    else if (w.online) { pres.textContent = "Active now"; pres.className = "pres on"; }
    else { pres.textContent = "Last seen " + listTime(w.last_seen) + " ago"; pres.className = "pres"; }
    if (blockBtn) blockBtn.style.color = blocked ? "var(--bad)" : "";
  }

  function paintRows(rows) {
    var unread = 0;
    rows.forEach(function (r) { unread += r.unread; });
    document.title = (unread ? "(" + unread + ") " : "") + state.title;
    if (!rowsBox) return;
    rows.forEach(function (r) {
      var node = rowsBox.querySelector('[data-row="' + r.id + '"]');
      if (!node) { window.setTimeout(function () { location.reload(); }, 400); return; }
      node.classList.toggle("unread", !!r.unread);
      var txt = node.querySelector(".txt");
      if (txt) txt.textContent = (r.last_mine ? "You: " : "") + (r.last || "No messages yet");
      var tm = node.querySelector(".tm");
      if (tm) tm.textContent = listTime(r.last_at);
      var badge = node.querySelector(".badge");
      if (r.unread && !badge) {
        badge = el("span", "badge");
        node.querySelector(".pv").appendChild(badge);
      }
      if (badge) { badge.textContent = r.unread || ""; badge.style.display = r.unread ? "" : "none"; }
      var dot = node.querySelector(".dot");
      if (dot) dot.classList.toggle("on", !!r.online);
    });
  }

  function notify(title, text) {
    if (document.visibilityState === "visible") return;
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    try { new Notification(title, { body: text, tag: "j6", renotify: true }); } catch (e) {}
  }

  function apply(data) {
    if (data.chats) paintRows(data.chats);
    var t = data.thread;
    if (!t || !msgs) return;
    paintHeader(t["with"], t.typing, t.blocked);
    state.readUpto = t.read_upto || state.readUpto;
    var before = state.seq, added = 0;
    (t.messages || []).forEach(function (m) {
      if (m.seq > before) added++;
      addMessage(m);
    });
    paintTyping(t.typing);
    paintTicks();
    if (added) {
      var last = t.messages[t.messages.length - 1];
      if (!last.mine) notify(t["with"].name, last.body);
      if (atBottom()) toBottom(); else { state.news += added; paintJump(); }
    }
  }

  /* Reading is implicit. A poll from a visible tab carries `seen=1`, and the server moves the
     read mark itself — one rule in one place, rather than a second one in here. */
  var ticking = false;
  function tick() {
    if (ticking) return;
    ticking = true;
    var seen = document.visibilityState === "visible" ? "&seen=1" : "";
    var url = POLL + (CHAT ? "?chat=" + CHAT + "&since=" + state.seq + seen : "");
    var headers = state.etag ? { "If-None-Match": state.etag } : {};
    fetch(url, { credentials: "same-origin", headers: headers })
      .then(function (r) {
        state.etag = r.headers.get("ETag") || state.etag;
        if (r.status === 304) return null;
        return r.json();
      })
      .then(function (data) { if (data) apply(data); })
      .catch(function () {})
      .then(function () { ticking = false; });
  }

  function cadence() {
    if (document.visibilityState !== "visible") return 20000;
    return CHAT ? 3000 : 8000;
  }
  var timer = null;
  function loop() {
    clearTimeout(timer);
    tick();
    timer = setTimeout(loop, cadence());
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") { state.etag = null; loop(); }
  });

  // first paint from what the page was rendered with — no flash of an empty thread
  if (boot.thread) { apply({ chats: boot.rows, thread: boot.thread }); toBottom(); }
  loop();

  /* sending */
  var composer = document.getElementById("composer");
  if (composer) {
    var lastTyped = 0;
    var send = function () {
      var text = input.value.trim();
      if (!text) return;
      input.value = "";
      input.style.height = "auto";
      post(SEND, { chat: CHAT, body: text }).then(function (d) {
        if (d.error) { alert(d.error); return; }
        if (d.message) { addMessage(d.message); paintTicks(); toBottom(); }
      });
    };
    composer.addEventListener("submit", function (e) { e.preventDefault(); send(); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    });
    input.addEventListener("input", function () {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 120) + "px";
      var now = Date.now();
      if (input.value.trim() && now - lastTyped > 3000) {
        lastTyped = now;
        post(TYPING, { chat: CHAT }).catch(function () {});
      }
    });
  }
  if (jump) jump.addEventListener("click", toBottom);
  if (msgs) msgs.addEventListener("scroll", paintJump);

  /* ---------- 4. notifications, and the SyncUp handshake ---------- */
  function wireNotifications() {
    var row = document.querySelector("[data-notif]");
    if (row) {
      var stateEl = row.querySelector("[data-notif-state]");
      var ask = row.querySelector("[data-notif-ask]");
      var paint = function () {
        var p = ("Notification" in window) ? Notification.permission : "unsupported";
        stateEl.textContent = p === "granted" ? "On — new messages show on this device"
          : p === "denied" ? "Blocked in this browser's site settings"
          : p === "default" ? "Off — turn it on to be told about new messages"
          : "This browser cannot show notifications";
        ask.hidden = p !== "default";
      };
      row.hidden = false;
      paint();
      ask.addEventListener("click", function () { Notification.requestPermission().then(paint); });
    }

    /* Inside the Windows app the page is handed window.pywebview.api — the only difference
       between the frame and a browser tab. One button, revealed when the bridge exists. */
    var term = document.querySelector("[data-terminal]");
    if (term) {
      var reveal = function () {
        if (!(window.pywebview && window.pywebview.api)) return false;
        term.hidden = false;
        term.addEventListener("click", function () { window.pywebview.api.open_terminal(); });
        return true;
      };
      if (!reveal()) {
        window.addEventListener("pywebviewready", reveal);
      }
    }

    var tokenUrl = body.getAttribute("data-token-url");
    if (tokenUrl) {
      var tries = 0;
      var t = setInterval(function () {
        tries += 1;
        var token = window.SyncUp && window.SyncUp.token;
        if (token) { clearInterval(t); post(tokenUrl, { token: token }).catch(function () {}); }
        else if (tries > 10) { clearInterval(t); }
      }, 500);
    }
  }
  wireNotifications();
})();
