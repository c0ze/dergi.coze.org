// Search page: keyword search (Pagefind's static index) and meaning search (/api/search, the
// Cloud Run service with article embeddings), merged by reciprocal rank fusion. Each result
// shows title, magazine · issue · date, pages and an excerpt. The query and filters live in
// the URL, so results can be shared.
const pagefind = await import("/pagefind/pagefind.js");
await pagefind.options({ excerptLength: 28 });
pagefind.init();

const form = document.querySelector("form.search");
const input = form.q;
const status = document.querySelector(".search-status");
const list = document.querySelector(".results");
const more = document.querySelector(".more");
const ASSETS = list.dataset.assets || "";
const PAGE = 15;
const FUSE = 40;          // keyword results loaded up front to merge with meaning results
const RRF_K = 60;         // reciprocal rank fusion constant
let merged = [];          // [{data, semantic}] in final order
let rest = [];            // keyword results beyond FUSE, loaded on demand
let shown = 0;
let token = 0;

// Filter menus from the index ("dergi" and "yıl" are set on every article page).
const filters = await pagefind.filters();
const fill = (select, values, numeric) => {
  const keys = Object.keys(values || {}).sort((a, b) => (numeric ? a - b : a.localeCompare(b, "tr")));
  for (const k of keys) select.append(new Option(`${k} (${values[k]})`, k));
};
fill(form.dergi, filters["dergi"]);
fill(form.yil, filters["yıl"], true);

const params = new URLSearchParams(location.search);
input.value = params.get("q") || "";
form.dergi.value = params.get("dergi") || "";
form.yil.value = params.get("yil") || "";

function escape(s) {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function card(item) {
  const d = item.data;
  const m = d.meta;
  const li = document.createElement("li");
  const cover = m.cover;  // keyword covers are full asset URLs; meaning covers get ASSETS in fuse()
  li.innerHTML = `
    ${cover ? `<img src="${escape(cover)}" alt="" width="64" height="90" loading="lazy">` : ""}
    <div>
      <a class="title" href="${escape(d.url)}">${escape(m.title || d.url)}</a>
      <p class="result-source">${escape(m.source || "")}${m.pages ? ` · ${escape(m.pages)}` : ""}${m.author ? ` · ${escape(m.author)}` : ""}</p>
      <p class="excerpt">${item.semantic ? escape(d.excerpt) : d.excerpt}</p>
      ${item.semantic ? `<p class="why">Anlamca yakın: bu sözcükleri içermese de konusu benziyor.</p>` : ""}
    </div>`;
  return li;
}

// One retry: a dropped fragment request shouldn't empty the list.
const data = (r) => r.data().catch(() => r.data());

async function meaning(q, chosen) {
  const url = new URLSearchParams({ q, limit: "25" });
  if (chosen["dergi"]) url.set("dergi", chosen["dergi"]);
  if (chosen["yıl"]) url.set("yil", chosen["yıl"]);
  try {
    const r = await fetch(`/api/search?${url}`, { signal: AbortSignal.timeout(8000) });
    return r.ok ? (await r.json()).results : [];
  } catch {
    return [];  // keyword results still show if the meaning service is down or cold
  }
}

function fuse(keyword, semantic) {
  const byUrl = new Map();
  keyword.forEach((d, i) => byUrl.set(d.url, { data: d, score: 1 / (RRF_K + i), semantic: false }));
  semantic.forEach((s, i) => {
    const hit = byUrl.get(s.url);
    if (hit) { hit.score += 1 / (RRF_K + i); return; }
    byUrl.set(s.url, { score: 1 / (RRF_K + i), semantic: true,
      data: { url: s.url, excerpt: s.snippet, meta: { title: s.title, source: s.source, pages: s.pages, author: s.author,
                                                       cover: s.cover && ASSETS + s.cover } } });
  });
  return [...byUrl.values()].sort((a, b) => b.score - a.score);
}

async function showMore() {
  const mine = token;
  if (shown >= merged.length && rest.length) {
    const batch = await Promise.all(rest.splice(0, PAGE).map(data));
    if (mine !== token) return;  // a newer query replaced the results meanwhile
    merged.push(...batch.map((d) => ({ data: d, semantic: false })));
  }
  merged.slice(shown, shown + PAGE).forEach((item) => list.append(card(item)));
  shown = Math.min(merged.length, shown + PAGE);
  more.hidden = shown >= merged.length && !rest.length;
}

async function run() {
  const q = input.value.trim();
  const chosen = {};
  if (form.dergi.value) chosen["dergi"] = form.dergi.value;
  if (form.yil.value) chosen["yıl"] = form.yil.value;
  const url = new URLSearchParams();
  if (q) url.set("q", q);
  if (form.dergi.value) url.set("dergi", form.dergi.value);
  if (form.yil.value) url.set("yil", form.yil.value);
  history.replaceState(null, "", `?${url}`);

  const mine = ++token;
  if (!q && !Object.keys(chosen).length) { list.replaceChildren(); more.hidden = true; status.textContent = ""; return; }
  const search = await pagefind.debouncedSearch(q || null, { filters: chosen }, 250);
  if (search === null || mine !== token) return;  // a newer query superseded this one
  status.textContent = "Aranıyor…";
  const [keyword, semantic] = await Promise.all([
    Promise.all(search.results.slice(0, FUSE).map(data)),
    q.length >= 3 ? meaning(q, chosen) : [],
  ]);
  if (mine !== token) return;
  rest = search.results.slice(FUSE);
  merged = fuse(keyword, semantic);
  shown = 0;
  list.replaceChildren();
  const total = merged.length + rest.length;
  const extra = merged.filter((m) => m.semantic).length;
  status.textContent = total
    ? `${total} yazı bulundu${extra ? ` (${extra} tanesi anlamca yakın)` : ""}`
    : "Sonuç yok. Daha kısa ya da farklı sözcüklerle deneyin.";
  await showMore();
}

form.addEventListener("submit", (e) => { e.preventDefault(); run(); });
input.addEventListener("input", run);
form.dergi.addEventListener("change", run);
form.yil.addEventListener("change", run);
more.addEventListener("click", showMore);
if (input.value || form.dergi.value || form.yil.value) run();
input.focus();
