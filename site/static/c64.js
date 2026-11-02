// /c64/?p=/commodore-pde/01/prg/saat.prg&ad=Saat&s=RUN: one type-in program in EmulatorJS's
// VICE x64 core, or x128 with m=c128 (/emu/, see scripts/emulator.sh). Only .prg files the site
// itself publishes.
// m=simon: the program needs the Simons' BASIC cartridge, which the site does not publish. The
// reader picks their own .crt; it stays in this browser (IndexedDB) and is never uploaded. VICE
// then gets a command line (an .m3u playlist's #COMMAND:) that attaches it and autostarts the program.
(function () {
  const q = new URLSearchParams(location.search);
  const path = q.get("p") || "";
  const lede = document.querySelector(".c64-lede");
  const m = path.match(/^\/([a-z0-9-]+)\/(\d+)\/prg\/([A-Za-z0-9._-]+\.prg)$/);
  if (!m) {
    lede.textContent = "Çalıştırılacak program bulunamadı. Bir sayfadaki programlar listesinden açın.";
    return;
  }
  const name = (q.get("ad") || m[3]).slice(0, 80);
  const start = (q.get("s") || "").slice(0, 20);
  const machine = q.get("m");
  document.querySelector(".c64-title").textContent = name;
  document.title = name + (machine === "c128" ? " · C128" : " · C64") + " · dergi";
  lede.textContent = "";
  const back = document.createElement("a");
  back.href = `/${m[1]}/${m[2]}/#programlar`;
  back.textContent = "Sayıya dön";
  lede.append(back);
  if (start) {
    lede.append(" · çalıştırmak için ");
    const code = document.createElement("code");
    code.textContent = start;
    lede.append(code);
  }

  async function run(cart) {
    window.EJS_player = "#c64";
    window.EJS_core = machine === "c128" ? "vice_x128" : "vice_x64";
    window.EJS_gameUrl = path;
    window.EJS_gameName = name;
    if (cart) {
      // Both as Blobs: EmulatorJS writes a file it fetches itself as an empty one.
      const prg = await fetch(path).then(r => r.blob());
      window.EJS_externalFiles = { "/simons.crt": new Blob([cart]), ["/" + m[3]]: prg };
      // A playlist whose #COMMAND: line VICE runs instead (this core build ignores .cmd files).
      window.EJS_gameUrl = new TextEncoder().encode(`#COMMAND:x64 -cartcrt /simons.crt -autostartprgmode 1 /${m[3]}\n/${m[3]}\n`);
      window.EJS_gameName = m[3].replace(/\.prg$/, ".m3u"); // the core knows a playlist by its name
    }
    window.EJS_pathtodata = "/emu/";
    window.EJS_language = "tr-TR";
    window.EJS_startOnLoaded = true;
    window.EJS_DEBUG_XX = true; // the unminified sources in /emu/src (the npm package has no build)
    window.EJS_color = "#6c5eb5";
    // The keyboard goes to the C64 as a keyboard, not mapped onto a joypad.
    window.EJS_defaultOptions = { keyboardInput: "enabled" };
    const s = document.createElement("script");
    s.src = "/emu/loader.js";
    document.body.append(s);
  }

  if (machine !== "simon") {
    run(null);
    return;
  }

  // The reader's cartridge, kept in this browser only. Without IndexedDB (a private window) it
  // lasts until the page is closed.
  function store(mode, act) {
    return new Promise((resolve, reject) => {
      const open = indexedDB.open("dergi-c64", 1);
      open.onupgradeneeded = () => open.result.createObjectStore("cart");
      open.onerror = () => reject(open.error);
      open.onsuccess = () => {
        const tx = open.result.transaction("cart", mode);
        const req = act(tx.objectStore("cart"));
        tx.oncomplete = () => resolve(req.result);
        tx.onerror = () => reject(tx.error);
      };
    });
  }
  const load = () => store("readonly", s => s.get("simons")).catch(() => null);
  const save = bytes => store("readwrite", s => s.put(bytes, "simons")).catch(() => null);
  const forget = () => store("readwrite", s => s.delete("simons")).catch(() => null);

  // A .crt file: "C64 CARTRIDGE   ", the hardware type at $16 (4 is Simons' BASIC), the name at $20.
  function check(bytes) {
    const text = (a, b) => String.fromCharCode(...bytes.subarray(a, b)).replace(/\0.*$/, "");
    if (bytes.length < 64 || text(0, 16) !== "C64 CARTRIDGE   ") return "Bu bir .crt kartuş dosyası değil.";
    const type = bytes[0x16] << 8 | bytes[0x17];
    if (type !== 4 && !/simon/i.test(text(0x20, 0x40))) return "Bu kartuş Simons' BASIC değil gibi görünüyor.";
    return null;
  }

  const box = document.createElement("div");
  box.className = "c64-cart";
  document.querySelector(".c64-frame").before(box);

  function ask(error) {
    box.innerHTML = "";
    const p = document.createElement("p");
    p.textContent = "Bu program Simons' BASIC kartuşuyla çalışıyor. Kartuşu site yayımlayamıyor; sizde bir kopyası varsa (.crt) seçin. Dosya yalnızca bu tarayıcıda saklanır, siteye yüklenmez.";
    const label = document.createElement("label");
    label.textContent = "Simons' BASIC kartuşu (.crt): ";
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".crt";
    label.append(input);
    box.append(p, label);
    if (error) {
      const e = document.createElement("p");
      e.className = "error";
      e.setAttribute("role", "alert");
      e.textContent = error;
      box.append(e);
    }
    input.addEventListener("change", async () => {
      const file = input.files[0];
      if (!file) return;
      const bytes = new Uint8Array(await file.arrayBuffer());
      const bad = check(bytes);
      if (bad) return ask(bad);
      await save(bytes);
      go(bytes);
    });
  }

  function go(bytes) {
    box.innerHTML = "";
    const p = document.createElement("p");
    p.textContent = "Simons' BASIC kartuşunuz bu tarayıcıda kayıtlı. ";
    const drop = document.createElement("button");
    drop.type = "button";
    drop.className = "button secondary";
    drop.textContent = "Kartuşu bu tarayıcıdan sil";
    drop.addEventListener("click", async () => {
      await forget();
      location.reload();
    });
    p.append(drop);
    box.append(p);
    run(bytes);
  }

  load().then(bytes => (bytes ? go(bytes) : ask(null)));
})();
