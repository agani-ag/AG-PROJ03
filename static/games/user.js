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

  var MARK = { skip: "g-skip", reverse: "g-rev", draw2: "g-d2" };
  var CORNER = { skip: "g-skip", reverse: "g-rev", draw2: "+2", wild: "W", draw4: "+4" };
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
  function mark(name) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "ic");
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#" + name);
    svg.appendChild(use);
    return svg;
  }
  function initials(name) {
    var bits = (name || "").trim().split(/\s+/).filter(Boolean);
    if (!bits.length) { return "?"; }
    return bits.length > 1 ? bits[0][0] + bits[1][0] : bits[0].slice(0, 2);
  }

  /* A card: a coloured face, a tilted oval, and the value in the middle and both corners.
     Four nodes plus the marks, and no image at any size. */
  function paint(node, index, size) {
    var card = CARDS[index];
    var colour = SHORT[card.c] || (card.v === "wild" || card.v === "draw4" ? "c-w" : "");
    node.className = "uc " + (size ? size + " " : "") + colour;
    clear(node);
    var face = make("span", "face");
    face.appendChild(make("span", "oval"));
    var middle = make("span", "mid");
    if (MARK[card.v]) { middle.appendChild(mark(MARK[card.v])); }
    else { middle.textContent = card.v === "wild" ? "W" : card.v === "draw4" ? "+4" : card.v; }
    face.appendChild(middle);
    if (card.c) {
      ["tl", "br"].forEach(function (where) {
        var corner = make("span", "cn " + where);
        var what = CORNER[card.v];
        if (MARK[card.v]) { corner.appendChild(mark(MARK[card.v])); }
        else { corner.textContent = what || card.v; }
        face.appendChild(corner);
      });
    }
    node.appendChild(face);
    return node;
  }

  function cardBack(size) {
    var node = make("span", "uc " + (size || "") + " back");
    var face = make("span", "face");
    face.appendChild(make("span", "oval"));
    face.appendChild(make("span", "mid", "UNO"));
    node.appendChild(face);
    return node;
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
    if (game.playable.length) { return [null, "Your turn."]; }
    if (game.drew !== null) { return [null, "Nothing in that one — keep it and pass."]; }
    return [null, "Nothing to play — take one from the pile."];
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
      if (game.top !== null) { pile.appendChild(paint(make("span"), game.top, "lg u3")); }
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
    el("draw-count").textContent = game.draw_count + " left";
    el("draw-pile").disabled = !mine || game.drew !== null || game.need_colour || game.need_swap;

    // Two backs under the top card, so the pile looks played-on rather than reset. Backs
    // rather than the faces the mockup drew: the server never has to send what is under
    // there, and nobody has to decide whether remembering it for you is an advantage.
    var disc = el("disc");
    clear(disc);
    var depth = Math.min(2, Math.max(0, 108 - game.draw_count - 8));
    for (var d = 0; d < depth; d += 1) { disc.appendChild(cardBack("sm u" + (d + 1))); }
    if (game.top !== null) { disc.appendChild(paint(make("span"), game.top, "lg u3")); }

    var hand = el("hand");
    clear(hand);
    var spread = [-11, -6, -2, 2, 6, 11];
    game.hand.forEach(function (index, i) {
      var node = make("button", "uc");
      node.type = "button";
      paint(node, index, null);
      var can = mine && game.playable.indexOf(index) !== -1;
      node.classList.add(can ? "on" : "off");
      if (index === game.drew) { node.classList.add("just"); }
      var tilt = spread[Math.min(i, spread.length - 1)];
      if (game.hand.length > 1) {
        var span = (i / (game.hand.length - 1)) * 2 - 1;      // -1 .. 1 across the fan
        tilt = span * 11;
      } else { tilt = 0; }
      var lift = can ? -16 : Math.abs(tilt) * 0.55;
      node.style.transform = "rotate(" + tilt.toFixed(1) + "deg) translateY(" +
                             lift.toFixed(1) + "px)";
      if (can) {
        node.addEventListener("click", function () { send({ what: "play", card: index }); });
      }
      hand.appendChild(node);
    });

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
    state = data;
    var oops = el("oops");
    if (data.error) { oops.textContent = data.error; oops.hidden = false; }
    else { oops.hidden = true; }
    render();
  }

  function send(move) {
    if (busy) { return; }
    busy = true;
    fetch(MOVE, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(move)
    })
      .then(function (r) { return r.json(); })
      .then(apply)
      .catch(function () { /* the next poll puts it right */ })
      .then(function () { busy = false; etag = null; });
  }

  el("draw-pile").addEventListener("click", function () { send({ what: "draw" }); });
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
