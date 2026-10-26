// Görünüm menu: reading theme and text size, remembered per browser.
(() => {
  const root = document.documentElement;
  const form = document.querySelector(".prefs form");
  if (!form) return;
  const get = (k, d) => { try { return localStorage.getItem(k) || d; } catch { return d; } };
  const set = (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } };
  form.theme.value = get("dergi.theme", "auto");
  form.size.value = get("dergi.size", "m");
  form.addEventListener("change", (e) => {
    if (e.target.name === "theme") {
      set("dergi.theme", e.target.value);
      if (e.target.value === "auto") delete root.dataset.theme; else root.dataset.theme = e.target.value;
    } else if (e.target.name === "size") {
      set("dergi.size", e.target.value);
      if (e.target.value === "m") delete root.dataset.size; else root.dataset.size = e.target.value;
    }
  });
  // Close the menu on Escape or a click elsewhere.
  const menu = form.closest("details");
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && menu.open) { menu.open = false; menu.querySelector("summary").focus(); } });
  document.addEventListener("click", (e) => { if (menu.open && !menu.contains(e.target)) menu.open = false; });
})();
