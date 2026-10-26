// Page reader.
// - ← and → move between pages (unless the reader is typing somewhere).
// - On wide screens, a two-page mode shows facing pages like the printed magazine:
//   the cover alone, then 2–3, 4–5, ... It is on by default there; the choice is remembered.
(() => {
  const reader = document.querySelector(".reader");
  if (!reader) return;
  const page = +reader.dataset.page;
  const pages = +reader.dataset.pages;
  const fill = (tpl, n) => tpl.replace("{n}", n).replace("{nnn}", String(n).padStart(3, "0"));
  const pageUrl = (n) => fill(reader.dataset.pageUrl, n);
  const toggle = reader.querySelector(".spread-toggle");
  const wide = matchMedia("(min-width: 75rem)");

  const stored = () => { try { return localStorage.getItem("dergi.spread"); } catch { return null; } };
  const store = (v) => { try { localStorage.setItem("dergi.spread", v); } catch { /* private mode */ } };

  // Facing pages: even on the left, odd on the right; the cover stands alone.
  const left = page === 1 ? 1 : page - (page % 2);
  const spread = left === 1 ? [1] : [left, left + 1].filter((n) => n <= pages);
  const prevTarget = left === 1 ? null : Math.max(1, left - 2);
  const nextTarget = left === 1 ? (pages >= 2 ? 2 : null) : (left + 2 <= pages ? left + 2 : null);

  const figure = reader.querySelector("figure.page");
  const single = figure.innerHTML;
  const pos = reader.querySelector(".pos");
  const singlePos = pos.textContent;
  let target = { prev: page > 1 ? page - 1 : null, next: page < pages ? page + 1 : null };

  function setNav(prev, next) {
    target = { prev, next };
    for (const [rel, n] of Object.entries(target)) {
      const old = reader.querySelector(`[data-nav="${rel}"]`);
      const el = document.createElement(n ? "a" : "span");
      el.className = old.className;
      el.dataset.nav = rel;
      el.textContent = old.textContent;
      if (n) el.href = pageUrl(n); else el.setAttribute("aria-disabled", "true");
      old.replaceWith(el);
    }
  }

  // Page text and article list for both pages of a spread, in page order.
  const side = reader.querySelector(".page-side");
  const facing = document.getElementById("facing");
  const texts = side.querySelector(".page-texts");
  const ownText = texts && texts.innerHTML;
  const list = side.querySelector(".page-articles");
  const ownList = list && list.innerHTML;

  function renderSideText(on) {
    if (!facing) return;
    const extra = facing.content.cloneNode(true);
    const facingText = extra.querySelector(".page-text");
    const facingItems = [...extra.querySelectorAll("li")];
    if (texts) {
      texts.innerHTML = ownText;
      if (on) {
        const before = +facingText.dataset.page < page;
        texts.insertBefore(facingText, before ? texts.firstChild : null);
      }
    }
    if (list) {
      list.innerHTML = ownList;
      if (on) {
        const have = new Set([...list.querySelectorAll("a")].map((a) => a.getAttribute("href")));
        facingItems.filter((li) => !have.has(li.querySelector("a").getAttribute("href"))).forEach((li) => list.append(li));
      }
      list.closest(".articles-panel").hidden = !list.children.length;
      list.closest(".articles-panel").querySelector("h2").textContent = on ? "Bu sayfalardaki yazılar" : "Bu sayfadaki yazılar";
    }
    side.querySelector(".text-panel summary")?.replaceChildren(on ? "Sayfaların metni" : "Sayfanın metni");
  }

  function render(on) {
    reader.classList.toggle("spread", on);
    renderSideText(on);
    toggle.setAttribute("aria-pressed", String(on));
    if (on) {
      figure.innerHTML = spread.map((n) => {
        const src = fill(reader.dataset.imageUrl, n);
        return `<a href="${src}" title="Tam boyutta aç"><img src="${src}" alt="${fill(reader.dataset.alt, n)}" width="1131" height="1600"></a>`;
      }).join("");
      pos.textContent = `Sayfa ${spread.join("–")} / ${pages}`;
      setNav(prevTarget, nextTarget);
    } else {
      figure.innerHTML = single;
      pos.textContent = singlePos;
      setNav(page > 1 ? page - 1 : null, page < pages ? page + 1 : null);
    }
  }

  function update() {
    toggle.hidden = !wide.matches;
    render(wide.matches && stored() !== "off");
  }
  toggle.addEventListener("click", () => {
    store(toggle.getAttribute("aria-pressed") === "true" ? "off" : "on");
    update();
  });
  wide.addEventListener("change", update);
  update();

  document.addEventListener("keydown", (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (e.target.closest("input, textarea, select, button, summary, [contenteditable]")) return;
    const n = { ArrowLeft: target.prev, ArrowRight: target.next }[e.key];
    if (n) location.href = pageUrl(n);
  });
})();
