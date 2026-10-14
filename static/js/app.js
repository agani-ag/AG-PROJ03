/* SyncUp console — app.js. Small vanilla helpers used by every admin page (no jQuery, Bootstrap JS,
 * DataTables or SweetAlert2). Public API on window.App: toast, confirm, fetchJSON, csrf, icon, openPalette.
 *
 * Declarative hooks (no page script needed):
 *   <form data-confirm="Delete this account?" data-confirm-text="…" data-confirm-button="Delete" data-danger>
 *   <button|a data-confirm="…">          confirm before the button submits / the link opens
 *   <button data-open-dialog="#id">      opens <dialog class="modal" id="id">; [data-close-dialog] closes it
 *   <div class="tabs" data-tabs><button data-tab="#panel" aria-selected="true">
 *   <button data-toggle="#id">           shows / hides #id
 *   <button data-copy="text"> or <button data-copy-target="#input">
 *   <button data-reveal="#password">     show / hide a password field
 *   <div data-table> [data-table-search] [data-table-count] [data-table-empty] <th data-sort="text|num">
 *   <tr data-href="/url">                whole row is a link
 *   <form data-track-changes> + <div class="savebar" hidden>
 *   <div class="alert"> … <button data-dismiss>
 *   <div class="seg" data-seg><button aria-pressed>   segmented control (one pressed at a time)
 */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var SPRITE = document.documentElement.getAttribute("data-icons") || "";

  function icon(name, cls) {
    return '<svg class="icon ' + (cls || "") + '" aria-hidden="true"><use href="' + SPRITE + "#i-" + name + '"></use></svg>';
  }

  /* ---- CSRF + JSON fetch ---- */
  function csrf() {
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    var input = $("[name=csrfmiddlewaretoken]");
    return input ? input.value : "";
  }
  function fetchJSON(url, opts) {
    opts = opts || {};
    var headers = Object.assign({ "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" }, opts.headers || {});
    var body = opts.body;
    if (body && !(body instanceof FormData) && typeof body !== "string") { body = JSON.stringify(body); headers["Content-Type"] = "application/json"; }
    return fetch(url, Object.assign({}, opts, { headers: headers, body: body, credentials: "same-origin" }))
      .then(function (r) {
        return r.text().then(function (t) {
          var data; try { data = t ? JSON.parse(t) : {}; } catch (e) { data = { error: t.slice(0, 200) }; }
          if (!r.ok) { var err = new Error(data.error || data.detail || ("Request failed (" + r.status + ")")); err.status = r.status; err.data = data; throw err; }
          return data;
        });
      });
  }

  /* ---- Toasts ---- */
  function toast(message, type) {
    type = type === "danger" ? "error" : (type || "info");
    var box = $("#toasts");
    if (!box) { box = document.createElement("div"); box.id = "toasts"; box.className = "toasts"; box.setAttribute("aria-live", "polite"); document.body.appendChild(box); }
    var icons = { success: "circle-check", error: "circle-alert", warning: "triangle-alert", info: "info", debug: "info" };
    var el = document.createElement("div");
    el.className = "toast " + type;
    el.setAttribute("role", type === "error" ? "alert" : "status");
    el.innerHTML = icon(icons[type] || "info") + '<div class="msg"></div><button type="button" aria-label="Dismiss">' + icon("x", "icon-sm") + "</button>";
    el.querySelector(".msg").textContent = message;
    el.querySelector("button").onclick = function () { el.remove(); };
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, type === "error" ? 9000 : 5000);
  }

  /* ---- Confirm dialog → Promise<boolean> ---- */
  function confirmDialog(opts) {
    if (typeof opts === "string") opts = { title: opts };
    opts = opts || {};
    return new Promise(function (resolve) {
      var d = document.createElement("dialog");
      d.className = "modal";
      d.innerHTML = '<form method="dialog"><div class="modal-body"><div class="modal-icon ' + (opts.danger ? "danger" : "") + '">' +
        icon(opts.danger ? "triangle-alert" : (opts.icon || "info")) + '</div><div><h2></h2><p></p></div></div><div class="modal-foot">' +
        '<button class="btn" value="cancel">' + (opts.cancelText ? "" : "Cancel") + '</button><button class="btn ' + (opts.danger ? "btn-danger" : "btn-primary") + '" value="ok"></button></div></form>';
      d.querySelector("h2").textContent = opts.title || "Are you sure?";
      var p = d.querySelector("p"); if (opts.text) p.textContent = opts.text; else p.remove();
      if (opts.cancelText) d.querySelector('[value="cancel"]').textContent = opts.cancelText;
      d.querySelector('[value="ok"]').textContent = opts.confirmText || "Confirm";
      d.addEventListener("close", function () { resolve(d.returnValue === "ok"); d.remove(); });
      d.addEventListener("click", function (e) { if (e.target === d) d.close("cancel"); });
      document.body.appendChild(d);
      d.showModal();
      d.querySelector('[value="ok"]').focus();
    });
  }
  function confirmOptions(el) {
    return { title: el.dataset.confirm, text: el.dataset.confirmText, confirmText: el.dataset.confirmButton || "Confirm", danger: el.hasAttribute("data-danger") };
  }

  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (f.dataset.confirm && !f.dataset.confirmed) {
      e.preventDefault();
      var submitter = e.submitter;
      confirmDialog(confirmOptions(f)).then(function (ok) {
        if (!ok) return;
        f.dataset.confirmed = "1";
        if (f.requestSubmit) f.requestSubmit(submitter && submitter.form === f ? submitter : undefined); else f.submit();
        delete f.dataset.confirmed;
      });
      return;
    }
    if (e.defaultPrevented || f.method === "dialog" || f.hasAttribute("data-no-busy")) return;
    // Stop double submits: spinner on the button that submitted (re-enabled if the user comes back).
    var btn = e.submitter || f.querySelector('[type="submit"], button:not([type])');
    if (btn) setTimeout(function () { btn.setAttribute("aria-busy", "true"); }, 0);
  });
  window.addEventListener("pageshow", function () { $$('[aria-busy="true"]').forEach(function (b) { b.removeAttribute("aria-busy"); }); });

  /* ---- Click delegation ---- */
  document.addEventListener("click", function (e) {
    var t = e.target;

    var confirmEl = t.closest("a[data-confirm], button[data-confirm]");
    if (confirmEl && !confirmEl.dataset.confirmed) {
      e.preventDefault();
      confirmDialog(confirmOptions(confirmEl)).then(function (ok) {
        if (!ok) return;
        if (confirmEl.tagName === "A") { window.location = confirmEl.href; return; }
        confirmEl.dataset.confirmed = "1"; confirmEl.click(); delete confirmEl.dataset.confirmed;
      });
      return;
    }

    if (t.closest("[data-nav-toggle], .scrim")) $(".shell").classList.toggle("nav-open");

    var dismiss = t.closest("[data-dismiss]");
    if (dismiss) { var target = dismiss.closest(dismiss.dataset.dismiss || ".alert"); if (target) target.remove(); }

    var opener = t.closest("[data-open-dialog]");
    if (opener) { var dlg = $(opener.dataset.openDialog); if (dlg) { e.preventDefault(); dlg.showModal(); } }
    var closer = t.closest("[data-close-dialog]");
    if (closer) { var cd = closer.closest("dialog"); if (cd) { e.preventDefault(); cd.close(); } }
    if (t.tagName === "DIALOG" && t.classList.contains("modal") && !t.hasAttribute("data-static")) t.close();

    var toggler = t.closest("[data-toggle]");
    if (toggler) {
      var panel = $(toggler.dataset.toggle);
      if (panel) { panel.hidden = !panel.hidden; toggler.setAttribute("aria-expanded", String(!panel.hidden)); }
    }

    var tab = t.closest("[data-tabs] [data-tab]");
    if (tab) {
      e.preventDefault();
      var group = tab.closest("[data-tabs]");
      $$("[data-tab]", group).forEach(function (b) {
        var on = b === tab, p = $(b.dataset.tab);
        b.setAttribute("aria-selected", String(on));
        if (p) p.hidden = !on;
      });
      group.dispatchEvent(new CustomEvent("tabchange", { detail: { tab: tab } }));
    }

    var copy = t.closest("[data-copy], [data-copy-target]");
    if (copy) {
      var src = copy.dataset.copyTarget ? $(copy.dataset.copyTarget) : null;
      var text = src ? (src.value != null ? src.value : src.textContent) : copy.dataset.copy;
      copyText(text).then(function () { toast("Copied to clipboard.", "success"); }, function () { toast("Couldn't copy. Select the text and copy it manually.", "error"); });
    }

    var reveal = t.closest("[data-reveal]");
    if (reveal) {
      var input = $(reveal.dataset.reveal), show = input.type === "password";
      input.type = show ? "text" : "password";
      reveal.innerHTML = icon(show ? "eye-off" : "eye");
      reveal.setAttribute("aria-label", show ? "Hide" : "Show");
    }

    var row = t.closest("tr[data-href]");
    if (row && !t.closest("a, button, input, label, select, textarea")) {
      if (e.ctrlKey || e.metaKey) window.open(row.dataset.href); else window.location = row.dataset.href;
    }

    var segBtn = t.closest("[data-seg] > button");
    if (segBtn) $$("button", segBtn.parentNode).forEach(function (b) { b.setAttribute("aria-pressed", String(b === segBtn)); });

    if (t.closest("[data-palette]")) openPalette();
    if (t.closest("[data-theme-toggle]")) cycleTheme();
  });

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea"); ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy") ? resolve() : reject(); } catch (err) { reject(err); }
      ta.remove();
    });
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { var s = $(".shell.nav-open"); if (s) s.classList.remove("nav-open"); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && $("#palette")) { e.preventDefault(); openPalette(); }
  });

  /* ---- Theme: system → light → dark (remembered per browser) ---- */
  function currentTheme() { return document.documentElement.getAttribute("data-theme") || "system"; }
  function paintThemeButton() {
    var b = $("[data-theme-toggle]"); if (!b) return;
    var th = currentTheme();
    b.innerHTML = icon(th === "dark" ? "moon" : th === "light" ? "sun" : "monitor");
    b.title = "Theme: " + th;
  }
  function cycleTheme() {
    var order = ["system", "light", "dark"], next = order[(order.indexOf(currentTheme()) + 1) % 3];
    if (next === "system") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (err) { /* private mode */ }
    paintThemeButton();
    syncThemeColor();
  }
  function syncThemeColor() {
    var meta = $('meta[name="theme-color"]:not([media])'); if (!meta) return;
    var th = currentTheme(), dark = th === "dark" || (th === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    meta.content = getComputedStyle(document.documentElement).getPropertyValue("--surface").trim() || (dark ? "#131a26" : "#ffffff");
  }

  /* ---- Tables ---- */
  function enhanceTable(root) {
    var table = $("table", root); if (!table || !table.tBodies[0]) return;
    var tbody = table.tBodies[0];
    var rows = $$("tr", tbody).filter(function (r) { return !r.hasAttribute("data-empty"); });
    var placeholder = $("tr[data-empty]", tbody);
    var search = $("[data-table-search]", root), count = $("[data-table-count]", root), empty = $("[data-table-empty]", root);
    var filterFn = null;
    function apply() {
      var q = (search && search.value || "").trim().toLowerCase(), shown = 0;
      rows.forEach(function (r) {
        var ok = (!q || r.textContent.toLowerCase().indexOf(q) !== -1) && (!filterFn || filterFn(r));
        r.hidden = !ok; if (ok) shown++;
      });
      if (count) count.textContent = shown === rows.length ? rows.length + " total" : shown + " of " + rows.length;
      if (empty) { empty.hidden = !(rows.length && shown === 0); var qEl = $("[data-query]", empty); if (qEl) qEl.textContent = q; }
      if (placeholder) placeholder.hidden = rows.length > 0;
    }
    if (search) search.addEventListener("input", apply);
    $$("th[data-sort]", table).forEach(function (th) {
      var idx = th.cellIndex, type = th.dataset.sort;
      th.innerHTML = '<button type="button" class="th-sort">' + th.innerHTML + icon("chevrons-up-down") + "</button>";
      th.firstChild.addEventListener("click", function () {
        var dir = th.getAttribute("aria-sort") === "ascending" ? "descending" : "ascending";
        $$("th[aria-sort]", table).forEach(function (o) { o.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", dir);
        var key = function (r) {
          var c = r.cells[idx]; if (!c) return "";
          var v = c.dataset.value != null ? c.dataset.value : c.textContent.trim();
          return type === "num" ? (parseFloat(v) || 0) : v.toLowerCase();
        };
        rows.sort(function (a, b) { var x = key(a), y = key(b); return (x > y ? 1 : x < y ? -1 : 0) * (dir === "ascending" ? 1 : -1); });
        rows.forEach(function (r) { tbody.appendChild(r); });
      });
    });
    root.setFilter = function (fn) { filterFn = fn; apply(); };
    root.refresh = apply;
    apply();
  }

  /* ---- Jump-to palette (Ctrl/⌘ K): searches the sidebar links ---- */
  function openPalette() {
    var d = $("#palette"); if (!d) return;
    var input = $("input", d), list = $(".palette-list", d);
    var links = $$(".side-nav .nav-link").map(function (a) {
      var grp = a.closest(".nav-group");
      return { a: a, text: (a.querySelector(".nav-text") || a).textContent.trim(), grp: grp ? $(".nav-label", grp).textContent.trim() : "" };
    });
    function render() {
      var q = input.value.trim().toLowerCase();
      var hits = links.filter(function (l) { return !q || (l.text + " " + l.grp).toLowerCase().indexOf(q) !== -1; });
      list.innerHTML = "";
      hits.forEach(function (l, i) {
        var li = document.createElement("li"), a = document.createElement("a");
        a.href = l.a.getAttribute("href"); if (l.a.target) a.target = l.a.target;
        var svg = l.a.querySelector("svg");
        a.innerHTML = (svg ? svg.outerHTML : "") + '<span></span><span class="grp"></span>';
        a.children[svg ? 1 : 0].textContent = l.text; a.querySelector(".grp").textContent = l.grp;
        if (i === 0) a.className = "active";
        li.appendChild(a); list.appendChild(li);
      });
      if (!hits.length) list.innerHTML = '<li class="empty small">No page matches.</li>';
    }
    input.value = ""; render();
    input.oninput = render;
    input.onkeydown = function (e) {
      var items = $$("a", list), cur = items.indexOf($("a.active", list));
      if ((e.key === "ArrowDown" || e.key === "ArrowUp") && items.length) {
        e.preventDefault(); if (cur >= 0) items[cur].classList.remove("active");
        cur = (cur + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items[cur].classList.add("active"); items[cur].scrollIntoView({ block: "nearest" });
      } else if (e.key === "Enter" && cur >= 0) { e.preventDefault(); items[cur].click(); }
    };
    d.onclick = function (e) { if (e.target === d) d.close(); };
    d.showModal(); input.focus();
  }

  /* ---- Unsaved-changes bar ---- */
  function trackChanges(form) {
    var bar = $(".savebar", form); if (!bar) return;
    var snapshot = function () { return new URLSearchParams(new FormData(form)).toString(); };
    var initial = snapshot();
    var check = function () { bar.hidden = snapshot() === initial; };
    form.addEventListener("input", check); form.addEventListener("change", check);
    form.addEventListener("reset", function () { setTimeout(check, 0); });
  }

  function init() {
    // Top-bar breadcrumb from the active sidebar link and the page's <h1>:
    // "Accounts / Chat Tester" on a detail page, "Notify / Reminders" on a section page.
    var crumb = $("#crumb");
    if (crumb && !crumb.childNodes.length) {
      var active = $(".side-nav [aria-current='page']"), h1 = $(".page h1");
      var navText = active ? (active.querySelector(".nav-text") || active).textContent.trim() : "", title = h1 ? h1.textContent.trim() : "";
      var group = active && active.closest(".nav-group") ? $(".nav-label", active.closest(".nav-group")).textContent.trim() : "";
      var parts = title && navText && title.toLowerCase() !== navText.toLowerCase() ? [navText, title]
                : group && navText && group.toLowerCase() !== navText.toLowerCase() ? [group, navText]
                : [navText || title];
      if (parts.length === 2) crumb.appendChild(document.createTextNode(parts[0] + " / "));
      var b = document.createElement("b"); b.textContent = parts[parts.length - 1]; crumb.appendChild(b);
    }
    $$("[data-table]").forEach(enhanceTable);
    $$("form[data-track-changes]").forEach(trackChanges);
    // Django messages arrive as JSON (see base.html) and show as toasts; the server-rendered copy is for no-JS.
    var msgs = $("#django-messages");
    if (msgs) {
      var flash = $(".flash"); if (flash) flash.remove();
      JSON.parse(msgs.textContent).forEach(function (m) { toast(m.text, m.level); });
    }
    paintThemeButton();
    syncThemeColor();
  }
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", init) : init();

  window.App = { toast: toast, confirm: confirmDialog, fetchJSON: fetchJSON, csrf: csrf, icon: icon, openPalette: openPalette, enhanceTable: enhanceTable, copy: copyText };
})();
