/* Games — the table, and the theme switch. Every page works with this file blocked except the
   table itself, which is a card game and cannot be one without it.

   One poll and one move, the same arrangement J6 uses, because this host cannot do WebSockets.
   UNO suits it better than chat does: you only need fresh state when it is NOT your turn, so
   the cadence drops while you are the one thinking.

   The page is a renderer and nothing else. It decides nothing — what is playable, whose turn
   it is and what a card does all come from the server, because a browser is a thing anybody
   can edit. */
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
  var etag = null, busy = false, picking = null;

  var LETTER = { skip: "skip", reverse: "turn", draw2: "+2", wild: "wild", draw4: "+4" };
  var SHORT = { red: "r", yellow: "y", green: "g", blue: "b" };

  function el(id) { return document.getElementById(id); }
  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  /* A card. The value is written on every one of them — colour is never the only signal,
     because red and green are two of the four and plenty of people cannot separate those. */
  function paint(node, index) {
    var card = CARDS[index];
    node.className = "uc " + (SHORT[card.c] || "");
    node.innerHTML = "";
    var corner = document.createElement("span");
    corner.className = "corner";
    corner.textContent = card.c ? card.c.charAt(0).toUpperCase() : "W";
    var face = document.createElement("span");
    var word = LETTER[card.v];
    if (word) { face.className = "cv"; face.textContent = word; }
    else { face.textContent = card.v; }
    node.appendChild(corner);
    node.appendChild(face);
    return node;
  }

  function seatRow(seat, game, index) {
    var box = document.createElement("span");
    box.className = "who" + (game && game.turn === index ? " now" : "");
    var name = document.createElement("span");
    name.className = "n";
    name.textContent = seat.name;
    box.appendChild(name);
    if (game) {
      var count = document.createElement("span");
      count.className = "c";
      count.textContent = game.counts[index] + (game.counts[index] === 1 ? " card" : " cards");
      box.appendChild(count);
      if (game.counts[index] === 1 && game.said_uno[index]) {
        var flag = document.createElement("span");
        flag.className = "uno";
        flag.textContent = "UNO";
        box.appendChild(flag);
      }
    } else {
      var score = document.createElement("span");
      score.className = "c";
      score.textContent = seat.score;
      box.appendChild(score);
    }
    return box;
  }

  function listSeats(into, data) {
    into.innerHTML = "";
    data.seats.forEach(function (seat) {
      var row = document.createElement("div");
      row.className = "item";
      var who = document.createElement("div");
      who.className = "grow";
      var b = document.createElement("b");
      b.textContent = seat.name + (seat.position === data.you ? " (you)" : "");
      who.appendChild(b);
      row.appendChild(who);
      var pill = document.createElement("span");
      pill.className = "pill";
      pill.textContent = seat.score;
      row.appendChild(pill);
      into.appendChild(row);
    });
  }

  function rulesLine(options) {
    var on = [];
    if (options.stacking) { on.push("stacking +2 and +4"); }
    if (options.draw_until) { on.push("draw until you can play"); }
    if (options.seven_zero) { on.push("7 swaps, 0 passes round"); }
    if (options.free_wild_four) { on.push("wild +4 any time"); }
    return on.length ? on.join(" · ") : "The ones on the box.";
  }

  function render() {
    var game = state.game;
    var lobby = el("lobby"), play = el("play"), over = el("over");
    el("lobby-rules").textContent = rulesLine(state.options);

    if (!game || state.phase === "lobby") {
      lobby.hidden = false; play.hidden = true; over.hidden = true;
      listSeats(el("lobby-seats"), state);
      return;
    }
    if (game.phase !== "playing") {
      lobby.hidden = true; play.hidden = true; over.hidden = false;
      var winner = game.winner === null ? null : state.seats[game.winner];
      el("over-who").textContent = winner
        ? (game.winner === state.you ? "You went out" : winner.name + " went out")
        : "Round over";
      el("over-score").textContent = state.target
        ? "Playing to " + state.target + " points."
        : "";
      listSeats(el("over-seats"), state);
      return;
    }

    lobby.hidden = true; play.hidden = false; over.hidden = true;
    var mine = game.turn === state.you;

    var seats = el("seats");
    seats.innerHTML = "";
    state.seats.forEach(function (seat, index) { seats.appendChild(seatRow(seat, game, index)); });

    if (game.top !== null) { paint(el("top-card"), game.top); }
    el("draw-count").textContent = game.draw_count;
    el("colour-dot").className = "dot " + (SHORT[game.colour] || "");
    el("colour-word").textContent = game.colour ? game.colour : "no colour yet";
    el("pending").textContent = game.pending ? " · " + game.pending + " to draw" : "";

    var log = el("log");
    log.innerHTML = "";
    game.log.slice(-4).forEach(function (line) {
      var row = document.createElement("div");
      row.textContent = line;
      log.appendChild(row);
    });

    var hand = el("hand");
    hand.innerHTML = "";
    game.hand.forEach(function (index) {
      var node = document.createElement("button");
      node.type = "button";
      paint(node, index);
      var can = mine && game.playable.indexOf(index) !== -1;
      node.classList.add(can ? "on" : "off");
      if (can) {
        node.addEventListener("click", function () { send({ what: "play", card: index }); });
      }
      hand.appendChild(node);
    });

    el("colour-picker").hidden = !(mine && game.need_colour);
    el("swap-picker").hidden = !(mine && game.need_swap);
    if (mine && game.need_swap) {
      var list = el("swap-list");
      list.innerHTML = "";
      state.seats.forEach(function (seat, index) {
        if (index === state.you) { return; }
        var button = document.createElement("button");
        button.className = "btn ghost";
        button.type = "button";
        button.textContent = seat.name + " · " + game.counts[index] + " cards";
        button.addEventListener("click", function () { send({ what: "swap", seat: index }); });
        list.appendChild(button);
      });
    }

    el("pass-btn").hidden = !(mine && game.drew !== null);
    el("uno-btn").hidden = !(game.hand.length === 2 || (game.uno_open && game.uno_seat === state.you));
    el("catch-btn").hidden = !(game.uno_open && game.uno_seat !== state.you);
    el("draw-pile").disabled = !mine || game.need_colour || game.need_swap || game.drew !== null;

    el("hint").textContent = !mine
      ? "Waiting for " + state.seats[game.turn].name
      : game.need_colour ? "Name a colour."
      : game.need_swap ? "Pick whose hand to take."
      : game.playable.length ? "Your turn."
      : "Nothing to play — take one from the pile.";
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
  el("pass-btn").addEventListener("click", function () { send({ what: "pass" }); });
  el("uno-btn").addEventListener("click", function () { send({ what: "uno" }); });
  el("catch-btn").addEventListener("click", function () { send({ what: "catch" }); });
  document.querySelectorAll("#colour-picker [data-colour]").forEach(function (button) {
    button.addEventListener("click", function () {
      send({ what: "colour", colour: button.dataset.colour });
    });
  });

  /* The cadence inverts the obvious one: fast while you are waiting on somebody else, slow
     while the table is waiting on you — you already have the state you are acting on. */
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
