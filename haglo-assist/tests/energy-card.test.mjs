// Tests für die MANGFALL-Energiefluss-Karte (custom:mangfall-energy-card) im echten Browser.
// Prüft für typische Lagen: welche Linien laufen, in welche Richtung, in welcher Farbe, wie schnell –
// und ob Satz, Status, Beschriftungen und Ringe dazu passen.
// Start: node --test haglo-assist/tests/
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright");     // global installiert

const SRC = readFileSync(new URL("../app/cards/mangfall-cards.js", import.meta.url), "utf8");
const NODES = { "180,58": "solar", "56,148": "grid", "304,148": "home", "180,240": "batt" };
const COLORS = { "#f0a400": "solar", "#8590a3": "grid", "#7c5cff": "batt" };
let browser, page;

before(async () => {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ timezoneId: "Europe/Berlin", locale: "de-DE" });
  page = await ctx.newPage();
  await page.setContent("<!doctype html><html><body></body></html>");
  await page.addScriptTag({ content: SRC });
});
after(() => browser?.close());

const sensor = (state, unit = "W") => ({ state: String(state), attributes: { unit_of_measurement: unit }, last_updated: String(Math.random()) });

/** Karte mit Leistungen (W) darstellen und auslesen. */
async function render({ grid, solar, battery, soc, home, units = {}, ...cfg }) {
  const states = {};
  const conf = { show_today: false, ...cfg };
  if (grid !== undefined) { states["sensor.grid"] = sensor(grid, units.grid); conf.grid = "sensor.grid"; }
  if (solar !== undefined) { states["sensor.pv"] = sensor(solar, units.solar); conf.solar = "sensor.pv"; }
  if (battery !== undefined) { states["sensor.batt"] = sensor(battery, units.battery); conf.battery = "sensor.batt"; }
  if (soc !== undefined) { states["sensor.soc"] = sensor(soc, "%"); conf.battery_soc = "sensor.soc"; }
  if (home !== undefined) { states["sensor.home"] = sensor(home); conf.home = "sensor.home"; }
  for (const k of ["grid_import", "grid_export", "battery_charge", "battery_discharge"])
    if (cfg[k] !== undefined) { states["sensor." + k] = sensor(cfg[k]); conf[k] = "sensor." + k; }
  for (const [id, st] of Object.entries(states)) st.entity_id = id;                     // wie in Home Assistant
  const out = await page.evaluate(({ conf, states }) => {
    document.body.innerHTML = "";
    const el = document.createElement("mangfall-energy-card");
    document.body.appendChild(el);
    el.setConfig(conf);
    el.hass = { states };
    const r = el.shadowRoot, q = (s) => [...r.querySelectorAll(s)];
    const ends = (d) => { const n = d.match(/-?[\d.]+/g).map(Number); return [`${n[0]},${n[1]}`, `${n.at(-2)},${n.at(-1)}`]; };
    return {
      runs: q("path.run").map((p) => ({ d: ends(p.getAttribute("d")), color: p.style.stroke, width: parseFloat(p.style.strokeWidth),
        dur: parseFloat(p.style.animationDuration), delay: parseFloat(p.style.animationDelay || "0") })),
      bases: q("path.base").map((p) => ends(p.getAttribute("d"))),
      say: r.querySelector(".say")?.textContent.trim(),
      chip: r.querySelector(".chip")?.textContent.trim(),
      hero: r.querySelector(".hero .num")?.textContent.trim(),
      values: q("text.nv").map((t) => t.textContent), labels: q("text.nl").map((t) => t.textContent),
      rings: q("circle.seg").map((c) => ({ cx: c.getAttribute("cx"), cy: c.getAttribute("cy"), color: c.style.stroke,
        len: parseFloat(c.style.strokeDasharray) })),
      text: r.textContent.replace(/\s+/g, " "),
    };
  }, { conf, states });
  const hex = (c) => { const m = c.match(/\d+/g); return m ? "#" + m.slice(0, 3).map((x) => (+x).toString(16).padStart(2, "0")).join("") : c; };
  out.runs = out.runs.map((x) => ({ ...x, from: NODES[x.d[0]], to: NODES[x.d[1]], color: COLORS[hex(x.color)] }));
  out.flows = out.runs.map((x) => `${x.from}>${x.to}`).sort();
  out.ring = (node) => { const [x, y] = Object.entries(NODES).find(([, n]) => n === node)[0].split(",");
    return out.rings.filter((c) => c.cx === x && c.cy === y).map((c) => [COLORS[hex(c.color)] || hex(c.color), c.len]); };
  return out;
}
const CIRC = 2 * Math.PI * 30;
const near = (a, b, tol = 0.6) => assert.ok(Math.abs(a - b) <= tol, `${a} ≠ ${b}`);

// ------------------------------------------------------------------ Richtungen und Anzeige
test("Mittags: Sonne deckt Haus, lädt Speicher, speist den Rest ein", async () => {
  const r = await render({ grid: -1000, solar: 5000, battery: -1500, soc: 60 });       // Haus 2500 W
  assert.deepEqual(r.flows, ["solar>batt", "solar>grid", "solar>home"]);
  for (const x of r.runs) assert.equal(x.color, "solar", "Sonnenstrom gelb");
  assert.equal(r.chip, "Speist ein");
  assert.equal(r.hero, "100");
  assert.equal(r.say, "Die Sonne deckt das ganze Haus, lädt den Speicher und speist 1,0 kW ein.");
  assert.ok(r.labels.includes("Einspeisung"));
  assert.ok(r.labels.includes("lädt · 1,5 kW"));
  assert.ok(r.values.includes("2,5 kW") && r.values.includes("5,0 kW") && r.values.includes("60 %"));
  assert.deepEqual(r.ring("home").map((s) => s[0]), ["solar"]);
  near(r.ring("home")[0][1], CIRC);
});

test("Nachts: Speicher versorgt das Haus", async () => {
  const r = await render({ grid: 0, solar: 0, battery: 800, soc: 40 });
  assert.deepEqual(r.flows, ["batt>home"]);
  assert.equal(r.runs[0].color, "batt");
  assert.equal(r.chip, "Autark");
  assert.equal(r.say, "Der Speicher versorgt das Haus.");
  assert.ok(r.labels.includes("entlädt · 800 W"));
});

test("Nur Netz: Haus bezieht Strom aus dem Netz", async () => {
  const r = await render({ grid: 1200, solar: 0, battery: 0 });
  assert.deepEqual(r.flows, ["grid>home"]);
  assert.equal(r.runs[0].color, "grid");
  assert.equal(r.chip, "Bezieht Netzstrom");
  assert.equal(r.hero, "0");
  assert.equal(r.say, "Das Haus bezieht 1,2 kW aus dem Netz.");
  assert.ok(r.labels.includes("Netz"));
});

test("Sonne reicht nicht: Rest aus dem Netz, Ring ums Haus anteilig", async () => {
  const r = await render({ grid: 500, solar: 1000 });                                   // Haus 1500 W
  assert.deepEqual(r.flows, ["grid>home", "solar>home"]);
  assert.equal(r.chip, "Teilweise Netzstrom");
  assert.equal(r.hero, "67");
  assert.equal(r.say, "Die Sonne deckt 67 % vom Haus, 500 W kommen aus dem Netz.");
  const ring = r.ring("home");
  assert.deepEqual(ring.map((s) => s[0]), ["solar", "grid"]);
  near(ring[0][1], CIRC * 2 / 3); near(ring[1][1], CIRC / 3);
});

test("Fast alles von der Sonne: nie „100 %“, solange noch Netzstrom fließt", async () => {
  const r = await render({ grid: 60, solar: 9940 });
  assert.equal(r.say, "Die Sonne deckt 99 % vom Haus, 60 W kommen aus dem Netz.");
});

test("Sonne und Speicher versorgen gemeinsam das Haus", async () => {
  const r = await render({ grid: 0, solar: 500, battery: 1500, soc: 70 });             // Haus 2000 W
  assert.deepEqual(r.flows, ["batt>home", "solar>home"]);
  assert.equal(r.say, "Die Sonne deckt 25 % vom Haus, der Speicher liefert 1,5 kW.");
});

test("Sonne lädt den Speicher, das Haus hängt am Netz (Netz und Haus gleich groß)", async () => {
  const r = await render({ grid: 243, solar: 172, battery: -172, soc: 5 });              // Haus 243 W
  assert.deepEqual(r.flows, ["grid>home", "solar>batt"]);
  assert.equal(r.say, "Die Sonne lädt den Speicher, 243 W kommen aus dem Netz.");
  assert.equal(r.hero, "0");
  assert.equal(r.chip, "Bezieht Netzstrom");
  assert.deepEqual(r.ring("home").map((s) => s[0]), ["grid"]);
});

test("Speicher lädt stärker, als die Sonne liefert: Rest aus dem Netz", async () => {
  const r = await render({ grid: 1300, solar: 500, battery: -1500 });                    // Haus 300 W
  assert.deepEqual(r.flows, ["grid>batt", "grid>home", "solar>batt"]);
});

test("Speicher lädt aus dem Netz", async () => {
  const r = await render({ grid: 2300, solar: 0, battery: -2000 });                    // Haus 300 W
  assert.deepEqual(r.flows, ["grid>batt", "grid>home"]);
  for (const x of r.runs) assert.equal(x.color, "grid");
  assert.equal(r.say, "Haus und Speicher beziehen 2,3 kW aus dem Netz.");
});

test("Speicher speist ins Netz ein (z. B. dynamischer Tarif)", async () => {
  const r = await render({ grid: -2000, solar: 0, battery: 3000 });                    // Haus 1000 W
  assert.deepEqual(r.flows, ["batt>grid", "batt>home"]);
  for (const x of r.runs) assert.equal(x.color, "batt");
  assert.equal(r.chip, "Speist ein");
  assert.equal(r.say, "Der Speicher versorgt das Haus und speist 2,0 kW ein.");
  assert.ok(r.ring("grid").length === 1, "Netz-Ring zeigt Fluss");
});

test("Umgedrehte Vorzeichen (invert_grid, invert_battery) ergeben dieselbe Anzeige", async () => {
  const a = await render({ grid: -1000, solar: 5000, battery: -1500 });
  const b = await render({ grid: 1000, solar: 5000, battery: 1500, invert_grid: true, invert_battery: true });
  assert.deepEqual(b.flows, a.flows);
  assert.equal(b.say, a.say);
  assert.deepEqual(b.labels, a.labels);
});

test("Getrennte Sensoren für Bezug/Einspeisung und Laden/Entladen ergeben dieselbe Anzeige", async () => {
  const a = await render({ grid: -1000, solar: 5000, battery: -1500 });
  const b = await render({ solar: 5000, grid_import: 0, grid_export: 1000, battery_charge: 1500, battery_discharge: 0 });
  assert.deepEqual(b.flows, a.flows);
  assert.equal(b.say, a.say);
  const c = await render({ solar: 0, grid_import: 0, grid_export: 2000, battery_charge: 0, battery_discharge: 3000 });
  assert.deepEqual(c.flows, ["batt>grid", "batt>home"]);
});

test("Sensoren in kW werden richtig umgerechnet", async () => {
  const r = await render({ grid: 1.2, solar: 0.8, units: { grid: "kW", solar: "kW" } });
  assert.deepEqual(r.flows, ["grid>home", "solar>home"]);
  assert.ok(r.values.includes("2,0 kW"));
});

test("Eigener Haus-Sensor hinkt hinterher: kein Netzbezug, während eingespeist wird", async () => {
  const r = await render({ grid: -300, solar: 1500, home: 2000 });
  assert.ok(!r.flows.includes("grid>home"), r.flows.join());
  assert.ok(r.flows.includes("solar>home"));
});

test("Kleine Werte (unter 15 W) und negativer PV-Wert nachts laufen nicht", async () => {
  const r = await render({ grid: 10, solar: -5, battery: 0 });
  assert.deepEqual(r.flows, []);
  assert.equal(r.say, "Gerade fließt kaum Strom.");
  assert.ok(r.bases.length >= 4);
});

test("Netzsensor nicht verfügbar: klare Meldung", async () => {
  const r = await render({ grid: "unavailable", solar: 100 });
  assert.match(r.text, /sensor.grid meldet sich gerade nicht/);
});

test("Knapp unter 1 kW wird als 1,0 kW angezeigt, nicht als 1.000 W", async () => {
  const r = await render({ grid: 999.7 });
  assert.ok(r.values.includes("1,0 kW"), r.values.join());
});

// ------------------------------------------------------------------ Geschwindigkeit
test("Mehr Leistung: schnellere und dickere Linien, gleich schnell bei gleicher Leistung", async () => {
  const durs = [], widths = [];
  for (const w of [20, 100, 500, 1000, 3000, 6000, 12000]) {
    const r = await render({ grid: w });
    assert.equal(r.runs.length, 1);
    durs.push(r.runs[0].dur); widths.push(r.runs[0].width);
  }
  for (let i = 1; i < durs.length; i++) {
    assert.ok(durs[i] < durs[i - 1], `Dauer fällt nicht: ${durs}`);
    assert.ok(widths[i] >= widths[i - 1], `Breite: ${widths}`);
  }
  for (const d of durs) assert.ok(d >= 0.28 && d <= 1.4, `Dauer ${d}`);
  const r = await render({ grid: -2000, solar: 4000 });                                  // 2 kW Haus, 2 kW Netz
  const [x, y] = r.runs;
  assert.equal(x.dur, y.dur);
});

test("Fluss läuft nach einer Aktualisierung ohne Sprung weiter (Phase hängt an der Uhrzeit)", async () => {
  const r = await render({ grid: 800 });
  const { dur, delay } = r.runs[0];
  assert.ok(delay <= 0 && delay > -dur, `delay ${delay}`);
  const phase = await page.evaluate(() => Date.now() / 1000);
  near(((phase + delay) % dur + dur) % dur, 0, 0.15);
});

test("Richtung der Animation: Strichmuster läuft vom Start- zum Zielknoten", async () => {
  await render({ grid: 800 });
  const kf = await page.evaluate(() => {
    const css = document.querySelector("mangfall-energy-card").shadowRoot.querySelector("style").textContent;
    return css.match(/@keyframes mcrun\s*{[^}]*}/)[0];
  });
  assert.match(kf, /stroke-dashoffset:\s*-14/);                // negativer Versatz = Muster wandert zum Pfadende
});

// ------------------------------------------------------------------ Tageswerte
test("Tageswerte: Solar, Autarkie, Netzbezug, gespart aus dem Verlauf seit Mitternacht", async () => {
  await page.clock.setFixedTime(new Date("2026-11-20T12:00:00+01:00"));
  const t = (h) => new Date(`2026-11-20T${h}:00:00+01:00`).getTime() / 1000;
  const hist = {
    "sensor.grid": [{ s: "1000", lu: t("10") }, { s: "-500", lu: t("11") }],           // 1 kWh Bezug, 0,5 kWh Einspeisung
    "sensor.pv": [{ s: "2000", lu: t("10") }],                                            // 4 kWh
  };
  const text = await page.evaluate(async ({ hist }) => {
    document.body.innerHTML = "";
    const el = document.createElement("mangfall-energy-card");
    document.body.appendChild(el);
    el.setConfig({ grid: "sensor.grid", solar: "sensor.pv", show_today: true });
    const st = (id, s) => ({ entity_id: id, state: s, attributes: { unit_of_measurement: "W" }, last_updated: "x" });
    el.hass = { states: { "sensor.grid": st("sensor.grid", "-500"), "sensor.pv": st("sensor.pv", "2000") }, callWS: async () => hist };
    await new Promise((r) => setTimeout(r, 50));
    return el.shadowRoot.querySelector(".today").textContent.replace(/\s+/g, " ");
  }, { hist });
  assert.match(text, /4,0kWhSolar/);
  assert.match(text, /78%/);                                    // 1 − 1/4,5
  assert.match(text, /1,0kWhNetzbezug/);
  assert.match(text, /1,26€gespart/);                          // 3,5 kWh × 0,35 + 0,5 kWh × 0,079
});

// ------------------------------------------------------------------ Zufallslagen: Bilanz und Widerspruchsfreiheit
test("300 Zufallslagen: Bilanz stimmt, nie gleichzeitig Bezug und Einspeisung, Farben passen zur Quelle", async () => {
  const res = await page.evaluate(() => {
    let seed = 7; const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    const node = { "180,58": "solar", "56,148": "grid", "304,148": "home", "180,240": "batt" };
    const col = { "rgb(240, 164, 0)": "solar", "rgb(133, 144, 163)": "grid", "rgb(124, 92, 255)": "batt" };
    const bad = [];
    for (let i = 0; i < 300; i++) {
      const s = Math.round(rnd() * 8000), h = Math.round(rnd() * 5000);
      const b = Math.round((rnd() - 0.5) * 6000), g = h - s - b;          // Netz gleicht aus
      const st = (v) => ({ state: String(v), attributes: { unit_of_measurement: "W" }, last_updated: String(i) });
      const states = { g: st(g), s: st(s), b: st(b) }; for (const k in states) states[k].entity_id = k;
      document.body.innerHTML = "";
      const el = document.createElement("mangfall-energy-card"); document.body.appendChild(el);
      el.setConfig({ grid: "g", solar: "s", battery: "b", show_today: false });
      el.hass = { states };
      const flows = [...el.shadowRoot.querySelectorAll("path.run")].map((p) => {
        const n = p.getAttribute("d").match(/-?[\d.]+/g).map(Number);
        return { from: node[`${n[0]},${n[1]}`], to: node[`${n.at(-2)},${n.at(-1)}`], color: col[p.style.stroke] };
      });
      const has = (f, t) => flows.some((x) => x.from === f && x.to === t);
      const tag = `s=${s} h=${h} b=${b} g=${g}: ${flows.map((x) => x.from + ">" + x.to).join(",")}`;
      if (flows.some((x) => x.color !== x.from)) bad.push("Farbe " + tag);
      if (flows.some((x) => x.from === "grid") && flows.some((x) => x.to === "grid")) bad.push("Netz beidseitig " + tag);
      if (flows.some((x) => x.from === "batt") && flows.some((x) => x.to === "batt")) bad.push("Speicher beidseitig " + tag);
      if (g > 15 && flows.some((x) => x.to === "grid")) bad.push("Einspeisung bei Bezug " + tag);
      if (g < -15 && flows.some((x) => x.from === "grid")) bad.push("Bezug bei Einspeisung " + tag);
      if (b > 15 && has("solar", "batt")) bad.push("Laden beim Entladen " + tag);
      if (b < -15 && flows.some((x) => x.from === "batt")) bad.push("Entladen beim Laden " + tag);
      if (s <= 15 && flows.some((x) => x.from === "solar")) bad.push("Sonne ohne Sonne " + tag);
      if (h > 30 && !flows.some((x) => x.to === "home")) bad.push("Haus ohne Zufluss " + tag);
      if (g < -30 && !flows.some((x) => x.to === "grid")) bad.push("Einspeisung unsichtbar " + tag);
      if (b < -30 && !flows.some((x) => x.to === "batt")) bad.push("Laden unsichtbar " + tag);
      if (b > 30 && !flows.some((x) => x.from === "batt")) bad.push("Entladen unsichtbar " + tag);
    }
    return bad;
  });
  assert.deepEqual(res.slice(0, 5), []);
});
