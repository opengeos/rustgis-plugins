// The plugin catalog (docs/catalog.md, a shell written by scripts/generate_catalog.py): cards
// built from registry.json, with a search box and category filters. Cards are added in
// batches as you scroll, so the page stays fast however many plugins the registry lists.
//
// Registry text comes from contributors, so it only ever goes into the page as text
// (textContent), never as HTML; links must be http(s).
//
// Instant navigation swaps pages without a reload, so this runs on each page change through
// `document$` when it exists, and once the DOM is ready otherwise.

(function () {
  const BATCH = 48;
  const PERMISSIONS = {
    edit: ["Edits the project", "Can change the project: run commands that edit it and add layers."],
    files: ["Reads and writes files", "Can run commands that read or write files on your computer."],
  };
  // The puzzle-piece icon (Material Design Icons "puzzle").
  const PUZZLE =
    "M20.5 11H19V7a2 2 0 0 0-2-2h-4V3.5A2.5 2.5 0 0 0 10.5 1 2.5 2.5 0 0 0 8 3.5V5H4a2 2 0 0 0-2 2v3.8h1.5c1.5 0 2.7 1.2 2.7 2.7S5 16.2 3.5 16.2H2V20a2 2 0 0 0 2 2h3.8v-1.5c0-1.5 1.2-2.7 2.7-2.7s2.7 1.2 2.7 2.7V22H17a2 2 0 0 0 2-2v-4h1.5a2.5 2.5 0 0 0 2.5-2.5 2.5 2.5 0 0 0-2.5-2.5Z";

  let registry; // the fetch, shared by page changes

  // A list field, whatever the registry holds there.
  function arr(x) {
    return Array.isArray(x) ? x.filter((v) => typeof v === "string") : [];
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function safeUrl(u) {
    try {
      const url = new URL(String(u || ""), location.href);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch (e) {
      return null;
    }
  }

  function link(text, href, external) {
    const url = safeUrl(href);
    if (!url) return null;
    const a = el("a", null, text);
    a.href = url;
    if (external) {
      a.target = "_blank";
      a.rel = "noopener";
    }
    return a;
  }

  // The catalog page's folder (…/catalog/), with or without a trailing slash in the address:
  // plugin pages and registry.json are found relative to it.
  function base() {
    const u = new URL(location.href);
    u.search = "";
    u.hash = "";
    if (!u.pathname.endsWith("/")) u.pathname += "/";
    return u.href;
  }

  // A plugin's page, beside the catalog page.
  function page(id) {
    return new URL(`${encodeURIComponent(id)}/`, base()).href;
  }

  function size(n) {
    return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
  }

  function card(p) {
    const li = el("li");
    const title = el("p", "plugin-card-title");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "plugin-card-icon");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", PUZZLE);
    svg.append(path);
    // Plugin pages are beside the catalog page (catalog/<id>/), wherever the site is served.
    const strong = el("strong");
    strong.append(link(p.name || p.id, page(p.id)) || (p.name || p.id));
    title.append(svg, strong);
    li.append(title, el("hr"));
    if (p.description) li.append(el("p", null, p.description));
    const meta = p.version ? [p.version] : [];
    if (p.author) meta.push(`by ${p.author}`);
    if (p.tab) meta.push(`its own ${p.tab} tab`);
    const nc = arr(p.commands).length;
    const nt = arr(p.tools).length;
    if (nc) meta.push(`${nc} ${nc === 1 ? "command" : "commands"}`);
    if (nt) meta.push(`${nt} ${nt === 1 ? "tool" : "tools"}`);
    li.append(el("span", "plugin-card-meta", meta.join(" · ")));
    const perms = arr(p.permissions).filter((x) => PERMISSIONS[x]);
    const permLine = el("span", "plugin-card-perms");
    if (perms.length) {
      perms.forEach((x, i) => {
        const s = el("span", "plugin-perm", PERMISSIONS[x][0]);
        s.title = PERMISSIONS[x][1];
        if (i) permLine.append(" · ");
        permLine.append(s);
      });
    } else {
      const s = el("span", "plugin-perm plugin-perm-read", "Reads the map only");
      s.title = "Only reads the map.";
      permLine.append(s);
    }
    li.append(permLine);
    const tags = el("p", "plugin-card-tags");
    for (const c of arr(p.categories)) tags.append(el("code", null, c), " ");
    if (tags.childNodes.length) li.append(tags);
    const links = el("p", "plugin-card-links");
    const parts = [
      link("Details", page(p.id)),
      link(Number.isFinite(p.size) && p.size > 0 ? `Download (${size(p.size)})` : "Download", p.package),
      link("Homepage", p.homepage, true),
    ].filter(Boolean);
    parts.forEach((a, i) => {
      if (i) links.append(" · ");
      links.append(a);
    });
    li.append(links);
    return li;
  }

  function setUp() {
    const filters = document.querySelector(".plugin-filters");
    const catalog = document.querySelector(".plugin-catalog");
    if (!filters || !catalog || catalog.dataset.ready) return;
    catalog.dataset.ready = "true";
    const list = catalog.querySelector("ul");
    const more = document.querySelector(".plugin-more");
    const search = filters.querySelector(".plugin-search");
    const boxes = filters.querySelector(".plugin-categories");
    const count = filters.querySelector(".plugin-count");

    // registry.json is at the site's root, given relative to this page.
    registry ??= fetch(new URL(catalog.dataset.registry || "../registry.json", base()), { cache: "no-cache" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .catch((e) => {
        registry = undefined;
        throw e;
      });
    registry.then(
      (data) => {
        const plugins = (Array.isArray(data && data.plugins) ? data.plugins : []).filter((p) => p && typeof p.id === "string" && p.id);
        // Searchable text, made once.
        const text = plugins.map((p) =>
          [p.name, p.id, p.description, p.author, p.tab, ...arr(p.categories), ...arr(p.commands), ...arr(p.tools)]
            .filter(Boolean)
            .join(" ")
            .toLowerCase(),
        );
        const selected = new Set();
        const categories = [...new Set(plugins.flatMap((p) => arr(p.categories)))].sort();
        boxes.replaceChildren();
        for (const c of categories) {
          const b = el("button", null, c);
          b.type = "button";
          b.setAttribute("aria-pressed", "false");
          b.addEventListener("click", () => {
            if (selected.has(c)) selected.delete(c);
            else selected.add(c);
            b.setAttribute("aria-pressed", String(selected.has(c)));
            apply();
          });
          boxes.append(b);
        }

        let shown = []; // indexes of the plugins that match
        let rendered = 0;

        // The next batch of cards.
        function fill() {
          const end = Math.min(rendered + BATCH, shown.length);
          const frag = document.createDocumentFragment();
          for (; rendered < end; rendered += 1) frag.append(card(plugins[shown[rendered]]));
          list.append(frag);
          more.textContent = rendered < shown.length ? "Loading more…" : "";
        }

        // Batches until the end of the list is out of reach (a tall or zoomed-out window can
        // show more than one batch, and the observer only calls back when that changes).
        function topUp() {
          while (rendered < shown.length && more.getBoundingClientRect().top < innerHeight + 800) fill();
        }

        function apply() {
          const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
          shown = [];
          plugins.forEach((p, i) => {
            if (!words.every((w) => text[i].includes(w))) return;
            if (selected.size && !arr(p.categories).some((c) => selected.has(c))) return;
            shown.push(i);
          });
          const noun = plugins.length === 1 ? "plugin" : "plugins";
          count.textContent =
            shown.length === plugins.length
              ? `${plugins.length.toLocaleString()} ${noun}`
              : `${shown.length.toLocaleString()} of ${plugins.length.toLocaleString()} ${noun}`;
          list.replaceChildren();
          rendered = 0;
          if (!shown.length) list.append(el("li", "plugin-none", "No plugins match."));
          fill();
          topUp();
        }

        // Load more when the marker after the list comes near the viewport.
        if ("IntersectionObserver" in window) {
          const observer = new IntersectionObserver(
            (seen) => {
              // Gone with a page change (instant navigation): let it go.
              if (!more.isConnected) return observer.disconnect();
              if (seen.some((e) => e.isIntersecting)) topUp();
            },
            { rootMargin: "800px 0px" },
          );
          observer.observe(more);
        } else {
          // Old browsers: everything at once.
          more.remove();
        }
        let typing;
        for (const ev of ["input", "search"]) {
          search.addEventListener(ev, () => {
            clearTimeout(typing);
            typing = setTimeout(apply, 120);
          });
        }
        filters.hidden = false;
        apply();
        if (!("IntersectionObserver" in window)) while (rendered < shown.length) fill();
      },
      // Only a failed download offers Retry; a bug in rendering is reported, not retried.
      (e) => {
        const msg = el("p", null, `The plugin list couldn't be loaded (${e.message}). `);
        const retry = el("button", "md-button", "Retry");
        retry.type = "button";
        retry.addEventListener("click", () => {
          // Start over: the fetch was forgotten when it failed, and nothing was set up yet.
          msg.remove();
          delete catalog.dataset.ready;
          setUp();
        });
        msg.append(retry);
        catalog.before(msg);
      },
    );
  }

  if (typeof document$ !== "undefined" && document$.subscribe) document$.subscribe(setUp);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setUp);
  else setUp();
})();
