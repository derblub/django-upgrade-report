// The script of the HTML report. The page is complete without it: it only ticks, filters
// and searches the rows the page already has. No framework, no build step, under 8 KB.
(function () {
  "use strict";
  var main = document.querySelector("main");
  function all(selector) {
    return Array.prototype.slice.call(document.querySelectorAll(selector));
  }

  // --- the checklist: ticks kept in localStorage, per plan. Without it (a file:// page in
  // some browsers, private mode), the boxes still tick, they are just not remembered.
  var key = main.getAttribute("data-checklist");
  var boxes = all("input[data-todo]");
  var ticks = {};
  try { ticks = JSON.parse(window.localStorage.getItem(key) || "{}"); } catch (e) { ticks = {}; }
  function count() {
    var done = 0;
    boxes.forEach(function (box) {
      if (box.checked) { done += 1; }
      box.closest("tr").classList.toggle("done", box.checked);
    });
    var counter = document.getElementById("done");
    if (counter) { counter.textContent = done; }
  }
  boxes.forEach(function (box) {
    box.checked = ticks[box.getAttribute("data-todo")] === true;
    box.addEventListener("change", function () {
      ticks[box.getAttribute("data-todo")] = box.checked;
      try { window.localStorage.setItem(key, JSON.stringify(ticks)); } catch (e) {}
      count();
    });
  });
  count();

  // --- filters: kept in the URL fragment, e.g. #status=blocked,check&q=allauth&notes=1,
  // so a filtered view can be passed on.
  var bar = document.getElementById("toolbar");
  if (!bar) { return; }
  var items = all("[data-filter]");
  var search = document.getElementById("q");
  var notes = document.getElementById("notes");
  var chips = all("button[data-chip]");
  var tiles = all(".tile[data-tile]");
  var shown = document.getElementById("shown");
  var state = { status: [], q: "", notes: false };

  function read() {
    var hash = window.location.hash.slice(1);
    state = { status: [], q: "", notes: false };
    if (hash.indexOf("=") < 0) { return; } // a plain anchor such as #blocked
    hash.split("&").forEach(function (part) {
      var pair = part.split("=");
      var value = decodeURIComponent((pair[1] || "").replace(/\+/g, " "));
      if (pair[0] === "status" && value) { state.status = value.split(","); }
      if (pair[0] === "q") { state.q = value; }
      if (pair[0] === "notes") { state.notes = value === "1"; }
    });
  }

  function write() {
    var parts = [];
    if (state.status.length) { parts.push("status=" + state.status.join(",")); }
    if (state.q) { parts.push("q=" + encodeURIComponent(state.q)); }
    if (state.notes) { parts.push("notes=1"); }
    var url = window.location.pathname + window.location.search;
    try {
      window.history.replaceState(null, "", parts.length ? url + "#" + parts.join("&") : url);
    } catch (e) {} // some browsers refuse it on file:// pages
  }

  function same(a, b) {
    return a.length === b.length && a.every(function (x) { return b.indexOf(x) >= 0; });
  }

  function apply() {
    var words = state.q.toLowerCase().split(/\s+/).filter(Boolean);
    var visible = 0;
    items.forEach(function (item) {
      var text = item.getAttribute("data-search") || "";
      var match = (!state.status.length || state.status.indexOf(item.getAttribute("data-filter")) >= 0) &&
        (!state.notes || item.hasAttribute("data-notes")) &&
        words.every(function (word) { return text.indexOf(word) >= 0; });
      item.classList.toggle("filtered", !match);
      if (match) { visible += 1; }
    });
    all("section[data-rows]").forEach(function (section) {
      var any = section.querySelector("[data-filter]:not(.filtered)");
      section.classList.toggle("filtered", !any);
    });
    chips.forEach(function (chip) {
      var on = state.status.indexOf(chip.getAttribute("data-chip")) >= 0;
      chip.setAttribute("aria-pressed", on ? "true" : "false");
    });
    tiles.forEach(function (tile) {
      var on = same(tile.getAttribute("data-tile").split(","), state.status);
      tile.setAttribute("aria-pressed", on ? "true" : "false");
    });
    if (search.value !== state.q) { search.value = state.q; }
    notes.checked = state.notes;
    var active = state.status.length || state.q || state.notes;
    shown.textContent = active ? visible + " of " + items.length + " shown" : "";
  }

  function change() { write(); apply(); }

  function reset() {
    state = { status: [], q: "", notes: false };
    change();
  }

  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      var value = chip.getAttribute("data-chip");
      var at = state.status.indexOf(value);
      if (at >= 0) { state.status.splice(at, 1); } else { state.status.push(value); }
      change();
    });
  });
  tiles.forEach(function (tile) {
    tile.setAttribute("role", "button");
    tile.setAttribute("tabindex", "0");
    function toggle() {
      var values = tile.getAttribute("data-tile").split(",");
      state.status = same(values, state.status) ? [] : values;
      change();
    }
    tile.addEventListener("click", toggle);
    tile.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); }
    });
  });
  search.addEventListener("input", function () { state.q = search.value; change(); });
  notes.addEventListener("change", function () { state.notes = notes.checked; change(); });
  document.getElementById("reset").addEventListener("click", reset);
  document.addEventListener("keydown", function (event) {
    var field = event.target;
    var typing = field.tagName === "TEXTAREA" || field.isContentEditable ||
      (field.tagName === "INPUT" && !/^(checkbox|radio|button)$/.test(field.type));
    if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      search.focus();
    } else if (event.key === "Escape") {
      reset();
      if (event.target === search) { search.blur(); }
    }
  });
  window.addEventListener("hashchange", function () { read(); apply(); });

  bar.hidden = false;
  read();
  apply();
})();
