/* Games — the table, and the theme switch. Built from docs/mockup/games-uno-mockup.html.

   Every page works with this file blocked except the table itself, which is a card game and
   cannot be one without it.

   One poll and one move, the same arrangement J6 uses, because this host cannot do WebSockets.
   The cadence inverts the obvious one: two seconds while you are waiting on somebody, nine
   while the table is waiting on YOU — you already have the state you are acting on.

   The page is a renderer and nothing else. It decides nothing: what is playable, whose turn it
   is and what a card does all come from the server, because a browser is a thing anybody can
   edit. The one thing worked out here is the sentence under the pile, and that is worked out
   from the same payload everybody gets. */
(function () {
  "use strict";

  /* ---------- theme ---------- */
  document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      var root = document.documentElement;
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("games-theme", next); } catch (e) { /* private window */ }
    });
  });

  var table = document.getElementById("table");
  if (!table) { return; }

  var POLL = table.dataset.poll, MOVE = table.dataset.move;
  var CARDS = JSON.parse(table.dataset.cards);
  var state = JSON.parse(table.dataset.boot);
  var etag = null, busy = false, clockTimer = null;

  var SHORT = { red: "c-r", yellow: "c-y", green: "c-g", blue: "c-b" };

  function el(id) { return document.getElementById(id); }
  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }
  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }
  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }
  function initials(name) {
    var bits = (name || "").trim().split(/\s+/).filter(Boolean);
    if (!bits.length) { return "?"; }
    return bits.length > 1 ? bits[0][0] + bits[1][0] : bits[0].slice(0, 2);
  }

  /* ============================================================
     ONE OF THE DECK

     There are no cards. Each of the hundred and eight is somebody in a coloured kit with
     their value across their chest, and the value is the content — the colour only agrees
     with it, which is the same rule the cards were drawn under and matters for the same
     reason: red and green are two of the four.

     Moulded rather than drawn flat, and that is four separate things:
       the volume     a gradient with a real range, nearly white to nearly black
       the rim light  every form drawn TWICE, the second copy nudged up and left, which
                      leaves a crescent of a lighter tint along the shaded side. It is the
                      one that matters most: without it a shaded ball is a flat disc
       the contact    dark where the head meets the shoulders, where the eyes sit, and two
                      shadows on the floor rather than one
       hands and feet a sphere on the end of each limb, or it reads as a stick

     The gradients are in the page once (see room.html) and every figure points at them.
     ============================================================ */
  var SVGNS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs) {
    var node = document.createElementNS(SVGNS, tag);
    Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }

  var KEY = { red: "r", yellow: "y", green: "g", blue: "b" };
  var FLAT = { r: "#e0243f", y: "#efa900", g: "#18a05a", b: "#1667cc", n: "#9aa2b1" };
  /* The bounce light along the shaded edge: a lighter tint of the thing itself, which is
     what light off a nearby surface actually does to a colour. */
  var RIM = { r: "#ff93a0", y: "#ffdd8a", g: "#73ebab", b: "#86c2ff", n: "#dde3ec" };
  var CHEST = { draw2: "+2", draw4: "+4", wild: "W" };
  var BADGE = { skip: "g-skip", reverse: "g-rev" };

  /* A pose for each of them, worked out from the colour and the value, so the red 7 that
     comes back next round stands the way the red 7 always stands. Nothing about the game
     depends on it; it is there so a line of seven looks like seven people. */
  function poseOf(card) {
    var v = String(card.v);
    var n = (card.c || "w").charCodeAt(0) * 31 + v.charCodeAt(0) * 13 + v.length * 5;
    return { tilt: ((n % 5) - 2) * 2.2, grin: n % 3, brow: (n >> 3) % 3,
             armL: 8 + (n % 4) * 7, armR: 8 + ((n >> 2) % 4) * 7 };
  }

  /* Nudge a shape up and left over a copy of itself. The shrink is the part to be careful
     with: shrinking a ten-pixel arm by a tenth leaves rim on all four sides, which is an
     outline and not a light, so small forms take none and the offset alone does it. */
  function inset(s, cx, cy, dx, dy) {
    return "translate(" + (cx * (1 - s) + dx).toFixed(2) + ","
         + (cy * (1 - s) + dy).toFixed(2) + ") scale(" + s + ")";
  }

  function figure(index, back) {
    var card = index === null ? { c: null, v: "wild" } : CARDS[index];
    var pose = poseOf(card);
    var wild = !card.c;
    var key = back ? "n" : wild ? "r" : KEY[card.c];
    var g = svgEl("svg", { viewBox: "0 0 64 114" });
    if (!back && index !== null) { g.setAttribute("aria-label", name(index)); g.setAttribute("role", "img"); }
    var clipId = "cut" + (clipCount += 1);
    var body = svgEl("g", { "class": "body" });

    /* Two shadows on the floor: tight and dark where it touches, wide and soft for the
       rest. One ellipse on its own reads as a grey smudge. */
    g.appendChild(svgEl("ellipse", { cx: "32", cy: "106", rx: "22", ry: "5.5",
                                     fill: "url(#floorshade)", "class": "shadow" }));
    g.appendChild(svgEl("ellipse", { cx: "32", cy: "104.5", rx: "11", ry: "2.6",
                                     fill: "url(#floorshade)", "class": "shadow" }));
    g.appendChild(body);

    function moulded(into, build, cx, cy, tight, dx, dy) {
      into.appendChild(build(RIM[key]));
      var over = svgEl("g", { transform: inset(tight, cx, cy, dx, dy) });
      over.appendChild(build("url(#k-" + key + ")"));
      into.appendChild(over);
    }

    function tube(x, y, w, h, turn, pivot, end) {
      var part = svgEl("g", turn ? { "class": "limb",
                                     transform: "rotate(" + turn + " " + pivot + ")" }
                                 : { "class": "limb" });
      moulded(part, function (fill) {
        return svgEl("rect", { x: x, y: y, width: w, height: h, rx: w / 2, fill: fill });
      }, x + w / 2, y + h / 2, 1, -0.8, -0.9);
      moulded(part, function (fill) {
        return svgEl("circle", { cx: x + w / 2, cy: y + h - 1, r: end, fill: fill });
      }, x + w / 2, y + h - 1, 0.93, -0.7, -0.8);
      part.appendChild(svgEl("ellipse", { cx: x + w * 0.34, cy: y + h * 0.22,
                                          rx: w * 0.19, ry: h * 0.16, fill: "url(#gloss)" }));
      return part;
    }

    body.appendChild(tube(18, 72, 11, 26, 0, null, 6.4));
    body.appendChild(tube(35, 72, 11, 26, 0, null, 6.4));

    /* A hand in the air is how a skip says stop and how a draw four looks pleased with
       itself — a second signal for the two that have no number of their own. */
    var up = card.v === "skip" || card.v === "draw4";
    var both = card.v === "draw4";
    var tuck = back ? 3 : 0;
    var rest = back ? -4 : 2;
    body.appendChild(tube(8 + tuck, 45, 10, 27, both ? -140 : -(rest + pose.armL * 0.2),
                          (13 + tuck) + " 49", 6));
    body.appendChild(tube(46 - tuck, 45, 10, 27, up ? 140 : rest + pose.armR * 0.2,
                          (51 - tuck) + " 49", 6));

    var shape = "M32,41 C45,41 47.5,50 48.5,59 C49.6,71 43,81 32,81 "
              + "C21,81 14.4,71 15.5,59 C16.5,50 19,41 32,41 Z";
    if (wild && !back) {
      var clip = svgEl("clipPath", { id: clipId });
      clip.appendChild(svgEl("path", { d: shape }));
      g.appendChild(clip);
      var quarters = svgEl("g", { "clip-path": "url(#" + clipId + ")" });
      [["r", 14, 39], ["y", 32, 39], ["g", 14, 60], ["b", 32, 60]].forEach(function (q) {
        quarters.appendChild(svgEl("rect", { x: q[1], y: q[2], width: "18", height: "21",
                                             fill: "url(#k-" + q[0] + ")" }));
      });
      body.appendChild(quarters);
    } else {
      moulded(body, function (fill) { return svgEl("path", { d: shape, fill: fill }); },
              32, 61, 0.96, -0.9, -1.3);
    }
    body.appendChild(svgEl("ellipse", { cx: "23.5", cy: "51", rx: "7", ry: "8.5",
                                        fill: "url(#gloss)" }));
    body.appendChild(svgEl("ellipse", { cx: "32", cy: "43.5", rx: "14", ry: "6",
                                        fill: "url(#ao)" }));

    /* The value, in the one place it is on every single one of them. The two that are a
       shape rather than a word reuse the marks the whole table is already drawn with. */
    if (!back) {
      if (BADGE[card.v]) {
        var use = document.createElementNS(SVGNS, "use");
        use.setAttribute("href", "#" + BADGE[card.v]);
        use.setAttribute("x", "19"); use.setAttribute("y", "48");
        use.setAttribute("width", "26"); use.setAttribute("height", "26");
        use.style.color = card.c === "yellow" ? "#4a3300" : "#ffffff";
        body.appendChild(use);
      } else {
        var text = CHEST[card.v] || card.v;
        var value = svgEl("text", { x: "32", y: "70", "text-anchor": "middle",
                                    "font-family": "inherit", "font-weight": "800",
                                    "font-style": "italic",
                                    "font-size": text.length > 1 ? "20" : "26",
                                    fill: card.c === "yellow" ? "#4a3300" : "#ffffff",
                                    stroke: card.c === "yellow" ? "rgba(255,255,255,.6)"
                                                                : "rgba(0,0,0,.34)",
                                    "stroke-width": "3", "paint-order": "stroke",
                                    "stroke-linejoin": "round" });
        value.textContent = text;
        body.appendChild(value);
      }
    }

    /* The head, sitting straight on the shoulders. The missing neck is most of what makes
       this shape read as a toy rather than as a small man. */
    var head = svgEl("g", { transform: "rotate(" + pose.tilt + " 32 28)" });
    body.appendChild(head);
    moulded(head, function (fill) {
      return svgEl("circle", { cx: "32", cy: "26", r: "19", fill: fill });
    }, 32, 26, 0.955, -0.9, -1.3);
    head.appendChild(svgEl("ellipse", { cx: "24", cy: "15", rx: "8", ry: "6.6",
                                        fill: "url(#gloss)",
                                        transform: "rotate(-24 24 15)" }));
    head.appendChild(svgEl("circle", { cx: "20.5", cy: "12.5", r: "2.2", fill: "#fff",
                                       opacity: ".75" }));

    if (!back) {
      /* A reverse is glancing back over its shoulder, which is the joke and the clue. */
      var gaze = card.v === "reverse" ? 3 : 0;
      [25.5, 38.5].forEach(function (x) {
        head.appendChild(svgEl("ellipse", { cx: x, cy: "26.8", rx: "7", ry: "7.9",
                                            fill: "url(#ao)", opacity: ".55" }));
        head.appendChild(svgEl("ellipse", { cx: x, cy: "26", rx: "6.2", ry: "7.4",
                                            fill: "url(#eyeball)" }));
        head.appendChild(svgEl("ellipse", { cx: x + gaze, cy: "27", rx: "3.1", ry: "4",
                                            fill: "#17191f" }));
        head.appendChild(svgEl("circle", { cx: x + gaze - 1.2, cy: "24.4", r: "1.35",
                                           fill: "#fff", opacity: ".95" }));
        var lift = card.v === "skip" ? -2.2
                 : pose.brow === 0 ? 0 : pose.brow === 1 ? -1.4 : 1;
        head.appendChild(svgEl("path", {
          d: "M" + (x - 5) + "," + (16.5 + lift) + " Q" + x + "," + (12.5 + lift)
             + " " + (x + 5) + "," + (16.5 + lift),
          fill: "none", stroke: "#17191f", "stroke-width": "2.6",
          "stroke-linecap": "round" }));
      });
      var mouth = pose.grin === 0 ? "M25,36 Q32,45 39,36 Q32,39.5 25,36 Z"
                : pose.grin === 1 ? "M24,35.5 Q32,47 40,35.5 Q32,40 24,35.5 Z"
                                  : "M26,36.5 Q32,43 38,36.5 Q32,38.8 26,36.5 Z";
      head.appendChild(svgEl("path", {
        d: card.v === "skip" ? "M26,39 Q32,34.5 38,39" : mouth,
        fill: card.v === "skip" ? "none" : "#17191f",
        stroke: card.v === "skip" ? "#17191f" : "none",
        "stroke-width": "2.6", "stroke-linecap": "round" }));
    }
    return g;
  }

  var clipCount = 0;

  function who(index, opts) {
    opts = opts || {};
    var node = make(opts.button ? "button" : "span", "who" + (opts.cls ? " " + opts.cls : ""));
    if (opts.button) { node.type = "button"; }
    node.appendChild(figure(index, opts.back));
    return node;
  }

  /* ---------- getting from one place on the table to another ----------
     Measured off the page rather than written down: the line is laid out by flexbox and
     the queue sits wherever the screen left room for it, so the only honest source for the
     distance between them is the two boxes themselves. */
  function travel(node, to) {
    if (!to || !to.getBoundingClientRect) { return null; }
    var a = to.getBoundingClientRect(), b = node.getBoundingClientRect();
    if (!a.width || !b.width) { return null; }
    return { x: a.left - b.left + (a.width - b.width) / 2,
             y: a.top - b.top + (a.height - b.height) / 2 };
  }

  function once(node, cls) {
    node.classList.add(cls);
    var off = function () {
      node.classList.remove(cls);
      node.removeEventListener("animationend", off);
    };
    node.addEventListener("animationend", off);
  }

  /* ---------- the one sentence ----------
     Worked out from the payload rather than read off a log, so it says what to do rather than
     what happened. The catch is the case that earns it: being told somebody is one card from
     out, in the five seconds you can do something about it. */
  function sentence(game) {
    var mine = game.turn === state.you;
    var seats = state.seats;
    if (game.uno_open && game.uno_seat !== state.you && seats[game.uno_seat]) {
      return [seats[game.uno_seat].name, " is down to one card — and said nothing."];
    }
    if (!mine) { return ["Waiting for ", seats[game.turn] ? seats[game.turn].name : "somebody"]; }
    if (game.need_colour) { return [null, "Name a colour."]; }
    if (game.need_swap) { return [null, "Pick whose hand to take."]; }
    if (game.pending) { return [null, game.pending + " to draw, or answer it."]; }
    // Naming the card you just drew is most of what made drawing feel like a punishment:
    // something appeared somewhere in a fan of seven and you had to find it.
    if (game.drew !== null && game.playable.indexOf(game.drew) !== -1) {
      return [null, "You drew the " + name(game.drew) + " — play it, or keep it and pass."];
    }
    if (game.drew !== null) { return [null, "Nothing in that one — keep it and pass."]; }
    if (game.playable.length) { return [null, "Your turn."]; }
    // Nothing matches, so one is already on the way: the table draws for you rather than
    // asking you to tap to be told there was nothing to decide.
    return [null, "Nothing matches — calling one over for you."];
  }

  function say(game) {
    var parts = sentence(game);
    var node = el("said");
    clear(node);
    if (parts[0]) { node.appendChild(make("b", null, parts[0])); }
    node.appendChild(document.createTextNode(parts[1]));
  }

  /* ---------- the one button ----------
     Take one, Keep it and pass, UNO and Caught! are never all true at once, so only the one
     that is shows. */
  function action(game) {
    var button = el("act");
    var mine = game.turn === state.you;
    button.className = "btn ghost";
    button.disabled = false;
    if (game.uno_open && game.uno_seat !== state.you) {
      var who = state.seats[game.uno_seat];
      button.className = "btn uno";
      button.textContent = "Caught " + (who ? who.name : "them") + "!";
      button.dataset.what = "catch";
      return;
    }
    if (mine && game.hand.length === 2) {
      button.className = "btn uno";
      button.textContent = "UNO!";
      button.dataset.what = "uno";
      return;
    }
    if (mine && game.drew !== null) {
      button.textContent = "Keep it and pass";
      button.dataset.what = "pass";
      return;
    }
    button.textContent = "Take one";
    button.dataset.what = "draw";
    button.disabled = !mine || game.need_colour || game.need_swap;
  }

  /* ---------- the clock ----------
     A width transition driven off the deadline the server already sends. One interval for the
     whole table, not one per render. */
  function clock(game) {
    var bar = el("clock");
    var fill = bar.firstElementChild;
    if (!game.turn_until) { bar.hidden = true; return; }
    bar.hidden = false;
    var ends = Date.parse(game.turn_until);
    var tick = function () {
      var left = Math.max(0, ends - Date.now()) / 1000;
      var share = Math.max(0, Math.min(1, left / 90));
      fill.style.width = (share * 100).toFixed(1) + "%";
      bar.classList.toggle("low", share < 0.18);
    };
    clearInterval(clockTimer);
    tick();
    clockTimer = setInterval(tick, 1000);
  }

  function seatRow(into, seat, index, game) {
    var row = make("div", "item");
    if (game) { row.appendChild(make("span", "rank", String(index + 1))); }
    var face = make("span", "av" + (seat.position === state.you ? "" : " dim"),
                    initials(seat.name));
    row.appendChild(face);
    var who = make("div", "grow");
    who.appendChild(make("b", null, seat.name + (seat.position === state.you ? " (you)" : "")));
    row.appendChild(who);
    row.appendChild(make("span", game ? "sc" : "pill", String(seat.score)));
    into.appendChild(row);
  }

  function render() {
    var game = state.game;
    var lobby = el("lobby"), play = el("play"), over = el("over");

    if (!game) {
      lobby.hidden = false; play.hidden = true; over.hidden = true;
      clear(el("lobby-seats"));
      state.seats.forEach(function (seat, i) { seatRow(el("lobby-seats"), seat, i, null); });
      el("lobby-rules").textContent = rulesLine();
      return;
    }

    if (game.phase !== "playing") {
      lobby.hidden = true; play.hidden = true; over.hidden = false;
      clearInterval(clockTimer);
      var pile = el("over-card");
      clear(pile);
      if (game.top !== null) { pile.appendChild(who(game.top)); }
      var winner = game.winner === null ? null : state.seats[game.winner];
      el("over-who").textContent = winner
        ? (game.winner === state.you ? "You went out" : winner.name + " went out")
        : "Round over";
      el("over-score").textContent = "Round " + state.round;
      var board = el("over-seats");
      clear(board);
      state.seats.slice().sort(function (a, b) { return b.score - a.score; })
        .forEach(function (seat, i) { seatRow(board, seat, i, game); });
      el("over-note").textContent = state.target
        ? "Playing to " + state.target + ". The score carries between rounds."
        : "The score carries between rounds.";
      return;
    }

    lobby.hidden = true; play.hidden = true; over.hidden = true;
    play.hidden = false;
    var mine = game.turn === state.you;

    var opps = el("opps");
    clear(opps);
    state.seats.forEach(function (seat, index) {
      if (index === state.you) { return; }
      var chip = make("span", "opp" + (game.turn === index ? " turn" : ""));
      chip.appendChild(make("span", "av", initials(seat.name)));
      chip.appendChild(make("span", "nm", seat.name));
      if (game.counts[index] === 1) { chip.appendChild(make("span", "uno", "UNO")); }
      else { chip.appendChild(make("span", "ct", String(game.counts[index]))); }
      opps.appendChild(chip);
    });

    el("wash").className = "wash " + (SHORT[game.colour] || "c-w");
    el("colour-word").textContent = game.colour || "your choice";
    el("draw-count").textContent = game.draw_count + " waiting";
    el("draw-pile").disabled = !mine || game.drew !== null || game.need_colour || game.need_swap;

    renderTable(game, mine);

    say(game);
    action(game);
    clock(game);

    var wild = mine && game.need_colour, swap = mine && game.need_swap;
    el("colour-picker").hidden = !wild;
    el("swap-picker").hidden = !swap;
    el("scrim").hidden = !(wild || swap);
    if (wild) { el("colour-hint").textContent = mostOf(game.hand); }
    if (swap) {
      var list = el("swap-list");
      clear(list);
      state.seats.forEach(function (seat, index) {
        if (index === state.you) { return; }
        var button = make("button", "btn ghost",
                          seat.name + " · " + game.counts[index] + " cards");
        button.type = "button";
        button.addEventListener("click", function () { send({ what: "swap", seat: index }); });
        list.appendChild(button);
      });
    }
  }

  /* ---------- the queue, and the one in the middle ----------
     Redrawn only when they have actually changed. There is no animation of theirs to
     protect, but they are rebuilt every two seconds for the whole length of a game, and a
     figure is thirty-odd nodes — there is no reason for that to cost anything.

     The ones already out stand behind with their backs turned, exactly as the two face-down
     cards did before: the server never has to say who they were, and nobody has to decide
     whether remembering it for you is an advantage. */
  var tableAt = null;

  function renderTable(game, mine) {
    var now = [game.top, state.round].join(":");
    if (now !== tableAt) {
      tableAt = now;
      var queue = el("draw-pile");
      clear(queue);
      /* Two of them. Each has hands and feet, and three overlapping read as one animal
         with a lot of limbs; the number underneath says how many there really are. */
      for (var q = 0; q < 2; q += 1) {
        var waiting = who(null, { back: true });
        var far = 1 - q;                                  // 0 is the one at the front
        waiting.style.transform = "translate3d(" + (far * 7) + "px," + (-far * 5) + "px,"
          + (-far * 16) + "px)";
        waiting.style.zIndex = String(10 - far);
        queue.appendChild(waiting);
      }

      /* Just the one. Two faint figures behind them was the two face-down cards the pile
         used to show, carried over without being looked at: at that size and that opacity
         they did not read as people standing further back, they read as a smudge around
         the one in front. What they were there to say — how played-on the table is — the
         number under the queue already says. */
      var played = el("disc");
      clear(played);
      if (game.top !== null) { played.appendChild(who(game.top)); }
    }
    renderLine(game, mine);
  }

  /* ---------- your line ----------
     KEYED BY CARD, and that is the whole reason any of this moves. Rebuilding it on every
     poll — twice a second — meant nobody could step forward, step out or be handed over,
     because the element doing it was thrown away before it finished. It is also thirty
     nodes a figure, rebuilt for nothing. Now somebody still in your hand is still the same
     element, and the browser moves them from wherever they were to wherever they go.

     Each index is one physical card of the 108, so no hand holds the same key twice. */
  var lineNodes = {};

  function renderLine(game, mine) {
    var hand = el("hand"), want = game.hand, arrived = [];

    /* Gone from your hand. Whoever is now standing in the middle is the one you just sent
       out, so they walk there; anybody who left another way — the seven swap takes a whole
       hand at once — simply goes, because walking them to the middle would be a lie. */
    Object.keys(lineNodes).forEach(function (key) {
      var index = Number(key);
      if (want.indexOf(index) !== -1) { return; }
      var node = lineNodes[index];
      delete lineNodes[index];
      if (!node.parentNode) { return; }
      if (index === game.top) { stepOut(node); }
      else { node.parentNode.removeChild(node); }
    });

    want.forEach(function (index, i) {
      var node = lineNodes[index];
      if (!node) {
        node = who(index, { button: true });
        node.addEventListener("click", function () {
          if (!node.classList.contains("on")) { return; }
          /* Stand them up the moment they are tapped, and leave them standing until the
             answer comes back. The server still decides everything — this does not play
             the card, it only says the tap landed. Without it there is a whole round trip
             on a free host over mobile data where you have touched somebody and the table
             has not moved, which reads as a dropped tap and gets tapped again.
             :active only lasts while the finger is down, so it covers none of that. */
          node.style.transform = "translateY(-20px) scale(1.04)";
          send({ what: "play", card: index });
        });
        lineNodes[index] = node;
        arrived.push(node);
      }
      var can = mine && game.playable.indexOf(index) !== -1;
      node.classList.toggle("on", can);
      node.classList.toggle("off", !can);
      node.classList.toggle("just", index === game.drew);
      node.disabled = !can;
      /* The ones you can send out stand forward, and the line curves so its ends are
         turned towards you rather than edge on. */
      var span = want.length > 1 ? (i / (want.length - 1)) * 2 - 1 : 0;
      node.style.transform = "translateY(" + (can ? -9 : 0) + "px) rotateY("
        + (-span * 13).toFixed(1) + "deg)";
      /* Only move it if it is not already there. Taking an element out of the DOM and
         putting it back is a no-op in the markup but not in CSS: it restarts whatever it
         was in the middle of. */
      if (hand.children[i] !== node) { hand.insertBefore(node, hand.children[i] || null); }
    });

    fitLine();
    joinIn(arrived);
  }

  /* How close they stand is worked out from the room there actually is, not fixed in the
     stylesheet: four should stand apart and twelve have to squeeze up. Never so close that
     the value is covered — on this table that is the one thing that must stay whole. */
  function fitLine() {
    var hand = el("hand"), kids = [].slice.call(hand.children);
    if (!kids.length) { return; }
    var w = kids[0].offsetWidth || 56;
    var room = (hand.clientWidth || 360) - 18;
    var over = kids.length > 1 ? (kids.length * w - room) / (kids.length - 1) : 0;
    over = Math.max(-2, Math.min(w * 0.4, over));
    kids.forEach(function (k, i) { k.style.marginLeft = i ? (-over).toFixed(1) + "px" : "0px"; });
  }
  window.addEventListener("resize", fitLine);

  /* Called over from the ones waiting. A whole hand arrives together and is staggered; one
     drawn mid-turn is not, because there is nothing to stagger it against. */
  function joinIn(arrived) {
    if (!arrived.length) { return; }
    var from = el("draw-pile"), many = arrived.length > 2;
    arrived.forEach(function (node, i) {
      var move = travel(node, from);
      if (!move) { return; }
      node.style.setProperty("--from", "translate(" + move.x.toFixed(0) + "px,"
        + move.y.toFixed(0) + "px) scale(.8)");
      node.style.setProperty("--to", node.style.transform);
      node.style.animationDelay = many ? (i * 55) + "ms" : "0ms";
      once(node, "joining");
    });
  }

  /* Sent out: they walk to the middle and are gone. Whoever is standing there has already
     changed by the time this runs, so it plays over the top of the new state rather than
     holding it up — the game is never waiting on an animation. */
  function stepOut(node) {
    var move = travel(node, el("disc"));
    if (!move) { node.parentNode.removeChild(node); return; }
    node.classList.remove("on", "off", "just");
    node.style.setProperty("--from", node.style.transform);
    node.style.setProperty("--to", "translate(" + move.x.toFixed(0) + "px,"
      + move.y.toFixed(0) + "px) scale(1.14)");
    node.classList.add("leaving");
    node.addEventListener("animationend", function () {
      if (node.parentNode) { node.parentNode.removeChild(node); }
    });
  }


  var WORDS = { skip: "skip", reverse: "reverse", draw2: "draw two",
                wild: "wild", draw4: "wild draw four" };
  function name(index) {
    var card = CARDS[index];
    var what = WORDS[card.v] || card.v;
    return card.c ? card.c + " " + what : what;
  }

  function mostOf(hand) {
    var counts = {};
    hand.forEach(function (i) { if (CARDS[i].c) { counts[CARDS[i].c] = (counts[CARDS[i].c] || 0) + 1; } });
    var best = null;
    Object.keys(counts).forEach(function (c) { if (!best || counts[c] > counts[best]) { best = c; } });
    return best ? "You hold " + counts[best] + " " + best + "." : "";
  }

  function rulesLine() {
    var on = [], o = state.options || {};
    if (o.stacking) { on.push("stacking +2 and +4"); }
    if (o.draw_until) { on.push("draw until you can play"); }
    if (o.seven_zero) { on.push("7 swaps, 0 passes round"); }
    if (o.free_wild_four) { on.push("wild +4 any time"); }
    return on.length ? on.join(" · ") : "The rules on the box.";
  }

  function apply(data) {
    if (!data) { return; }
    /* A new round is a new cast. Without this the last round's people are still keyed into
       the line, and the new hand slides out of the old one instead of being dealt. */
    if (data.round !== state.round || !data.game) {
      lineNodes = {};
      tableAt = null;
      clear(el("hand"));
    }
    state = data;
    var oops = el("oops");
    if (data.error) { oops.textContent = data.error; oops.hidden = false; }
    else { oops.hidden = true; }
    render();
  }

  function send(move) {
    if (busy) { return; }
    busy = true;
    el("table").classList.add("sending");
    fetch(MOVE, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(move)
    })
      .then(function (r) { return r.json(); })
      .then(apply)
      .catch(function () { /* the next poll puts it right */ })
      .then(function () {
        busy = false;
        etag = null;
        el("table").classList.remove("sending");
      });
  }

  el("draw-pile").addEventListener("click", function () {
    once(el("draw-pile"), "calling");
    send({ what: "draw" });
  });
  el("act").addEventListener("click", function () {
    send({ what: el("act").dataset.what || "draw" });
  });
  document.querySelectorAll("#colour-picker [data-colour]").forEach(function (button) {
    button.addEventListener("click", function () {
      send({ what: "colour", colour: button.dataset.colour });
    });
  });

  function cadence() {
    if (document.visibilityState !== "visible") { return 12000; }
    var game = state.game;
    if (!game || game.phase !== "playing") { return 5000; }
    return game.turn === state.you ? 9000 : 2000;
  }

  var timer = null, ticking = false;
  function tick() {
    if (ticking || busy) { return; }
    ticking = true;
    var headers = etag ? { "If-None-Match": etag } : {};
    fetch(POLL, { credentials: "same-origin", headers: headers })
      .then(function (r) {
        etag = r.headers.get("ETag") || etag;
        return r.status === 304 ? null : r.json();
      })
      .then(function (data) { if (data) { apply(data); } })
      .catch(function () {})
      .then(function () { ticking = false; });
  }
  function loop() {
    clearTimeout(timer);
    tick();
    timer = setTimeout(loop, cadence());
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") { etag = null; loop(); }
  });

  render();
  loop();
})();
