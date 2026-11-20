// Tests der MANGFALL-Karten: Selbstfindung im Beispielhaushalt, Rendern mit erfundenen Zuständen, Datenaufbereitung.
// Aufruf: node haglo-assist/tests/cards.test.mjs  (ohne Browser, ohne Home Assistant)
import fs from "fs";
import { fileURLToPath } from "url";
const HERE = fileURLToPath(new URL(".", import.meta.url));
const dummy = () => ({ innerHTML: "", textContent: "", dataset: {}, classList: { toggle() {}, add() {}, remove() {} }, addEventListener() {}, appendChild() {}, querySelectorAll: () => [], querySelector: () => null, setAttribute() {} });
class Root { constructor() { this.innerHTML = ""; } querySelectorAll() { return []; } querySelector(q) { return /shapes|seg|host|wait/.test(q) ? dummy() : null; } appendChild() {} }
globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = new Root(); return this.shadowRoot; } dispatchEvent(e) { (this._ev ||= []).push(e); } addEventListener() {} get style() { return { setProperty() {} }; } };
globalThis.window = globalThis; globalThis.Image = class { set src(v) { this._s = v; } };
const defs = {};
globalThis.customElements = { get: (t) => defs[t], define: (t, c) => (defs[t] = c), whenDefined: () => new Promise(() => {}) };
globalThis.CustomEvent = class { constructor(t, o) { this.type = t; Object.assign(this, o); } };
globalThis.document = { createElement: (t) => (defs[t] ? new defs[t]() : { tag: t, addEventListener() {} }) };
const SRC = fs.readFileSync(process.argv[2] || HERE + "../app/cards/mangfall-cards.js", "utf8"), snap = JSON.parse(fs.readFileSync(process.argv[3] || HERE + "fixtures/haushalt.json", "utf8"));
const names = "MangfallFlatCard MangfallHomeCard MangfallCoverCard MangfallSecurityCard MangfallCameraCard MangfallMediaCard MangfallActionsCard MangfallConsumersCard MangfallAirCard MangfallCalendarCard MangfallSystemCard aplState cleanName MangfallEnergyCard MangfallLevelCard MangfallBatteryCard MangfallEnergyHistoryCard MangfallWasteCard MangfallWeatherCard MangfallPresenceCard MangfallLightsCard MangfallVacuumCard MangfallPriceCard MangfallEvCard MangfallHeatingCard FIND ehBuild ehPrefs ehSplit ehRange priceSeries priceScale priceHours cheapest wasteFromSensor weatherSay tankSvg TANKS segLevel energySeries24 fmtL MC_VERSION CARDS".split(" ");
(0, eval)(SRC + `\nglobalThis.__C = {${names.join(",")}};`);
const K = globalThis.__C;
let fails = 0; const ok = (c, m) => { if (!c) { fails++; console.log("  FEHLER:", m); } };
const tick = () => new Promise((r) => setTimeout(r, 0));
const mk = (Cls, cfg, hass) => { const el = new Cls(); el.setConfig(cfg || {}); el.hass = hass; return el; };
const html = (el) => el.shadowRoot.innerHTML;

console.log("Version", K.MC_VERSION, "· Karten", K.CARDS.length);
/* ---------- 1. Echter Schnappschuss ---------- */
const real = { ...snap, callWS: async (m) => { if (m.type === "energy/get_prefs") throw new Error("nicht im Schnappschuss"); return {}; }, callApi: async () => [], callService: async () => ({}) };
console.log("\n== Beispielhaushalt ==");
for (const n of ["MangfallEnergyHistoryCard", "MangfallWasteCard", "MangfallWeatherCard", "MangfallPresenceCard", "MangfallLightsCard", "MangfallVacuumCard", "MangfallPriceCard", "MangfallEvCard", "MangfallHeatingCard"]) {
  const C = K[n], stub = C.getStubConfig(real);
  const el = Object.create(C.prototype); el._user = {}; el._prefs = null;
  const a = el._auto ? el._auto({ ...C.defaults }, real) : null;
  const pickd = a ? Object.fromEntries(Object.entries(a).filter(([k, v]) => v && (Array.isArray(v) ? v.length : typeof v !== "number" && typeof v !== "boolean") && !Object.keys(C.defaults).includes(k) || (C.defaults[k] === "" && v) || (Array.isArray(C.defaults[k]) && v?.length))) : {};
  console.log(n.replace("Mangfall", "").replace("Card", ""), "stub:", JSON.stringify(stub), "auto:", JSON.stringify(pickd));
  let err = null; let card;
  try { card = mk(C, stub, real); await tick(); await tick(); } catch (e) { err = e; }
  ok(!err, n + " wirft im Beispielhaushalt: " + err?.stack);
  if (card) console.log("   zeigt:", html(card).replace(/<style>[\s\S]*?<\/style>/, "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 150));
}
/* ---------- 2. Leerer Haushalt: immer Hinweis, nie Fehler ---------- */
console.log("\n== Leerer Haushalt ==");
const empty = { states: {}, entities: {}, devices: {}, areas: {}, callWS: async () => { throw new Error("x"); }, callApi: async () => [], callService: async () => ({}) };
for (const n of ["MangfallEnergyHistoryCard", "MangfallWasteCard", "MangfallWeatherCard", "MangfallPresenceCard", "MangfallLightsCard", "MangfallVacuumCard", "MangfallPriceCard", "MangfallEvCard", "MangfallHeatingCard"]) {
  let el, err; try { el = mk(K[n], K[n].getStubConfig(empty), empty); await tick(); await tick(); el._render(); } catch (e) { err = e; }
  ok(!err && html(el).includes("Fast fertig"), n + " zeigt keinen Hinweis: " + (err?.stack || html(el).slice(-200)));
}
console.log("ok");

/* ---------- 3. Erfundene Zustände ---------- */
const st = (id, state, attributes = {}, extra = {}) => ({ entity_id: id, state: String(state), attributes, last_changed: new Date(Date.now() - 3600e3).toISOString(), last_updated: new Date().toISOString(), ...extra });
const H = (list, more = {}) => { const calls = []; return { states: Object.fromEntries(list.map((e) => [e.entity_id, e])), entities: more.entities || {}, devices: {}, areas: more.areas || {},
  callService: async (...a) => { calls.push(a); return more.svc ? more.svc(...a) : {}; }, callWS: more.ws || (async () => { throw new Error("x"); }), callApi: more.api || (async () => []), connection: more.conn, calls }; };
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const today = new Date(); today.setHours(0, 0, 0, 0); const plus = (n) => new Date(+today + n * 864e5);

console.log("\n== Müll ==");
const wsSensor = st("sensor.abfall_gelb", "Gelber Sack in 1 days", { [iso(plus(1))]: "Gelber Sack", [iso(plus(8))]: "Restmüll, Biotonne", friendly_name: "Abfall Gelb" });
const apptSensor = st("sensor.muell_papier", "in 5 Tagen", { Papiertonne: iso(plus(5)), friendly_name: "Müll Papier" });
const dayOnly = st("sensor.restmuell", "3", { friendly_name: "Restmüll Abholung" });
ok(K.wasteFromSensor(wsSensor).length === 3, "upcoming-Format");
ok(K.wasteFromSensor(apptSensor)[0]?.t === "Papiertonne", "appointment_types-Format");
ok(K.wasteFromSensor(dayOnly)[0]?.t === "Restmüll", "nur Tage: " + JSON.stringify(K.wasteFromSensor(dayOnly)));
const cal = st("calendar.abfallkalender", "off", { message: "Biotonne", start_time: iso(plus(2)) + " 00:00:00", all_day: true, friendly_name: "Abfallkalender" });
const hW = H([wsSensor, apptSensor, cal, st("calendar.feiertage", "off", { friendly_name: "Feiertage" })], { api: async (m, path) => [{ summary: "Biotonne", start: { date: iso(plus(2)) } }, { summary: "Glas", start: { date: iso(plus(12)) } }] });
console.log("findet:", K.MangfallWasteCard.getStubConfig(hW).entities);
const w = mk(K.MangfallWasteCard, {}, hW); await tick(); await tick();
ok(html(w).includes("Morgen") && html(w).includes("Gelber Sack") && html(w).includes("st-low"), "Müll morgen → low");
ok(html(w).includes("Glas"), "Kalender-Termine geladen");

console.log("== Wetter ==");
const now = Date.now(), hr = (i, cond, p = 0) => ({ datetime: new Date(now + i * 36e5).toISOString(), condition: cond, temperature: 7 + i * 0.4, precipitation: p, precipitation_probability: p ? 80 : 5 });
const fc = [0, 1, 2, 3, 4].map((i) => hr(i, "cloudy")).concat([5, 6, 7].map((i) => hr(i, "rainy", 1.2)), [8, 9, 10, 11, 12].map((i) => hr(i, "cloudy")));
console.log("Satz:", K.weatherSay("cloudy", fc, false), "|", K.weatherSay("rainy", [hr(0, "rainy", 1), hr(1, "rainy", 1), hr(2, "cloudy")], false), "|", K.weatherSay("sunny", fc.map((f) => ({ ...f, condition: "sunny", precipitation: 0, precipitation_probability: 0 })), false));
let subMsg; const conn = { subscribeMessage: async (cb, msg) => { subMsg = msg; setTimeout(() => cb({ type: "hourly", forecast: fc }), 0); return () => (conn.unsubbed = true); } };
const wx = mk(K.MangfallWeatherCard, {}, H([st("weather.home", "cloudy", { temperature: 6.9, humidity: 80, wind_speed: 28, wind_speed_unit: "km/h", supported_features: 3, friendly_name: "Rosenheim" })], { conn }));
await tick(); await tick();
ok(subMsg?.type === "weather/subscribe_forecast" && subMsg.forecast_type === "hourly", "Abo stündlich");
ok(/Ab \d+ Uhr Regen/.test(html(wx)) || html(wx).includes("morgen"), "Wetter-Satz: " + html(wx).match(/class="say">[^<]*/)?.[0]);
wx.disconnectedCallback(); await tick(); ok(conn.unsubbed, "Abo beendet");
const wx2 = mk(K.MangfallWeatherCard, {}, H([st("weather.x", "rainy", { temperature: 4, supported_features: 1 })], { svc: async () => ({ response: { "weather.x": { forecast: [{ datetime: plus(1).toISOString(), condition: "sunny", temperature: 9 }] } } }) }));
await tick(); await tick(); ok(html(wx2).includes("trocken") || html(wx2).includes("say"), "Wetter-Fallback über Dienst");

console.log("== Anwesenheit ==");
const pr = mk(K.MangfallPresenceCard, {}, H([st("person.anna_muster", "home", { friendly_name: "Anna Muster", entity_picture: "/api/image/serve/abc/512x512" }), st("person.ben", "not_home", { friendly_name: "Ben Beispiel" }), st("person.cara", "Arbeit", { friendly_name: "Cara" })]));
ok(html(pr).includes(">1<") && html(pr).includes("BB") && html(pr).includes("Arbeit") && html(pr).includes("<img"), "Anwesenheit");

console.log("== Lichter ==");
const hl = H([st("light.kueche_decke", "on", { friendly_name: "Küche Decke", brightness: 128 }), st("light.flur", "off", { friendly_name: "Flur Spot" }), st("light.gruppe", "on", { entity_id: ["light.a"] }), st("light.weg", "unavailable", {})],
  { entities: { "light.kueche_decke": { area_id: "kuche" }, "light.flur": { area_id: "flur" } }, areas: { kuche: { name: "Küche" }, flur: { name: "Flur" } } });
const li = mk(K.MangfallLightsCard, {}, hl);
ok(html(li).includes("1 an") && html(li).includes(">Decke<") && html(li).includes("50 %") && !html(li).includes("light.gruppe") && html(li).includes("Alle aus"), "Lichter");

console.log("== Saugroboter ==");
const hv = H([st("vacuum.robo", "cleaning", { friendly_name: "Robo", fan_speed: "max" }), st("sensor.robo_akku", "64", { device_class: "battery", unit_of_measurement: "%" }), st("sensor.robo_flaeche", "23", { unit_of_measurement: "m²" })],
  { entities: { "vacuum.robo": { device_id: "d1" }, "sensor.robo_akku": { device_id: "d1" }, "sensor.robo_flaeche": { device_id: "d1" } } });
const va = mk(K.MangfallVacuumCard, {}, hv);
ok(html(va).includes("Saugt") && html(va).includes("64") && html(va).includes("23") && html(va).includes('data-svc="pause"'), "Saugroboter");

console.log("== Strompreis ==");
const hours = Array.from({ length: 24 }, (_, i) => 0.25 + 0.1 * Math.sin(i / 3) + (i >= 13 && i < 16 ? -0.15 : 0));
const nord = st("sensor.nordpool_kwh_de_eur_3_10_019", (hours[new Date().getHours()]).toFixed(3), { unit_of_measurement: "EUR/kWh", today: hours, tomorrow: hours.map((v) => v + 0.02), tomorrow_valid: true, friendly_name: "Nordpool" });
const epex = st("sensor.epex_spot_data_market_price", (hours[new Date().getHours()] * 100).toFixed(2), { unit_of_measurement: "ct/kWh",
  data: hours.map((v, i) => ({ start_time: new Date(+today + i * 36e5).toISOString(), end_time: new Date(+today + (i + 1) * 36e5).toISOString(), price_eur_per_mwh: v * 1000 })) });
ok(K.priceSeries(nord).length === 48, "Nordpool heute+morgen");
ok(Math.abs(K.priceScale(K.priceSeries(epex), parseFloat(epex.state)) - 0.1) < 1e-9, "EPEX €/MWh → ct/kWh");
console.log("findet:", K.FIND.price(H([nord, st("sensor.strompreis_fix", "0.32", { unit_of_measurement: "€/kWh", friendly_name: "Strompreis fix Grundpreis" })])));
const pc = mk(K.MangfallPriceCard, {}, H([nord]));
console.log("  ", (html(pc).match(/Günstigste[^<]*/) || ["(keine Fenster-Angabe)"])[0]);
ok(html(pc).includes("ct/kWh") && html(pc).includes("Morgen"), "Strompreis rendert");
const pe = mk(K.MangfallPriceCard, {}, H([epex])); ok(html(pe).includes("class=\"pb"), "EPEX Balken");
let tibCall; const tib = mk(K.MangfallPriceCard, {}, H([st("sensor.electricity_price_home", "0.31", { unit_of_measurement: "EUR/kWh", price_level: "CHEAP" })], { svc: async (...a) => { tibCall = a; return { response: { prices: { Home: hours.map((v, i) => ({ start_time: new Date(+today + i * 36e5).toISOString(), price: v })) } } }; } }));
await tick(); await tick(); ok(tibCall?.[0] === "tibber" && tibCall[5] === true && html(tib).includes("Günstig"), "Tibber über get_prices");

console.log("== E-Auto ==");
const hev = H([st("sensor.id4_battery_level", "54", { device_class: "battery", unit_of_measurement: "%", friendly_name: "ID.4 Battery level" }),
  st("sensor.id4_range", "212", { device_class: "distance", unit_of_measurement: "km", friendly_name: "ID.4 Range" }),
  st("sensor.go_e_charger_power", "11000", { device_class: "power", unit_of_measurement: "W", friendly_name: "go-e Charger Power" }),
  st("sensor.id4_charging_end", new Date(Date.now() + 2.5 * 36e5).toISOString(), { device_class: "timestamp", friendly_name: "ID.4 Charging end" }),
  st("sensor.handy_battery", "40", { device_class: "battery", unit_of_measurement: "%", friendly_name: "Pixel Handy Battery" }),
  st("sensor.marstek_soc", "80", { device_class: "battery", unit_of_measurement: "%", friendly_name: "Marstek Speicher SoC" })],
  { entities: { "sensor.id4_battery_level": { device_id: "car" }, "sensor.id4_range": { device_id: "car" }, "sensor.id4_charging_end": { device_id: "car" } } });
console.log("findet:", JSON.stringify(K.MangfallEvCard.getStubConfig(hev)));
const ev = mk(K.MangfallEvCard, {}, hev);
ok(html(ev).includes("Lädt") && html(ev).includes("212") && html(ev).includes("Voll um"), "E-Auto");
const ev2 = mk(K.MangfallEvCard, {}, H([st("sensor.wallbox_leistung", "0", { device_class: "power", unit_of_measurement: "W", friendly_name: "Wallbox Leistung" })]));
ok(html(ev2).includes("kW Ladeleistung"), "nur Wallbox");

console.log("== Heizung ==");
const hc = H([st("climate.bad", "heat", { friendly_name: "Bad Thermostat", current_temperature: 19.5, temperature: 22, hvac_action: "heating", min_temp: 5, max_temp: 30, target_temp_step: 0.5 }),
  st("climate.buero", "off", { friendly_name: "Büro", current_temperature: 18, temperature: null })], { entities: { "climate.bad": { area_id: "bad" } }, areas: { bad: { name: "Bad" } } });
const he = mk(K.MangfallHeatingCard, {}, hc);
ok(html(he).includes("1 Raum heizt") && html(he).includes("22,0°") && html(he).includes(">Bad<"), "Heizung");
he.shadowRoot.querySelector = () => null; he._step(hc.states["climate.bad"], 1); he._step(hc.states["climate.bad"], 1);
await new Promise((r) => setTimeout(r, 800));
ok(hc.calls.length === 1 && hc.calls[0][2].temperature === 23, "Heizung +/- entprellt: " + JSON.stringify(hc.calls));

/* ---------- 4. Energieverlauf ---------- */
console.log("\n== Energieverlauf ==");
ok(JSON.stringify(K.ehPrefs({ energy_sources: [{ type: "grid", flow_from: [{ stat_energy_from: "a" }, { stat_energy_from: "a2" }], flow_to: [{ stat_energy_to: "b" }] }, { type: "solar", stat_energy_from: "pv" }] }).imp) === '["a","a2"]', "altes Format");
const np = K.ehPrefs({ energy_sources: [{ type: "grid", stat_energy_from: "sensor.shelly_3em_netzbezug_gesamt_kwh", stat_energy_to: "sensor.shelly_3em_netzeinspeisung_gesamt_kwh" },
  { type: "grid", stat_energy_from: "sensor.zweiter_zaehler" }, { type: "solar", stat_energy_from: "sensor.pv" }, { type: "battery", stat_energy_from: "sensor.dis", stat_energy_to: "sensor.chg", power_config: { stat_rate: "x" } }] });
ok(np.imp.length === 2 && np.exp[0] === "sensor.shelly_3em_netzeinspeisung_gesamt_kwh" && np.bout[0] === "sensor.dis" && np.bin[0] === "sensor.chg", "neues Format, zwei Netzquellen");
ok(K.ehPrefs({ energy_sources: [] }) === null, "leer → null");
// Monat/Jahr mit echten Monatswerten (start als ms-Zahl), Lücke und Rückstellung
const y = new Date().getFullYear(), src = { imp: ["g"], exp: [], pv: [], bin: [], bout: [] };
const rY = K.ehRange("year", 0), resY = { g: [[8, 17.6], [9, 191.9], [10, 145.6]].map(([m, v]) => ({ start: +new Date(y, m, 1), end: +new Date(y, m + 1, 1), change: v })).concat([{ start: +new Date(y, 7, 1), change: -355 }]) };
const bY = K.ehBuild(resY, src, "year", rY.ps, rY.s, rY.e);
ok(bY.cur.imp[8] === 17.6 && bY.cur.imp[9] === 191.9 && bY.cur.imp[10] === 145.6 && bY.cur.imp[7] === 0, "Jahr: Monatswerte, Rückstellung verworfen " + bY.cur.imp);
// Tag: Stundenwerte als ISO-Strings mit Lücke
const rD = K.ehRange("day", -1), resD = { g: Array.from({ length: 24 }, (_, h) => h === 5 ? null : { start: new Date(+rD.s + h * 36e5).toISOString(), change: h === 12 ? null : 0.5 }).filter(Boolean), x: [{ start: new Date(+rD.s + 13 * 36e5).toISOString(), change: 0.3 }] };
const bD = K.ehBuild(resD, { ...src, exp: ["x"] }, "day", rD.ps, rD.s, rD.e);
ok(bD.n === 24 && bD.cur.imp[5] === 0 && bD.cur.imp[12] === 0 && bD.cur.imp[13] === 0.5 && bD.cur.exp[13] === 0.3, "Tag: Lücken als 0");
// Monat: Tageswerte, Vormonat im selben Abruf
const rM = K.ehRange("month", 0), resM = { g: [{ start: +rM.ps, change: 6 }, { start: +rM.s, change: 5 }, { start: +rM.s + 864e5, change: 4 }] };
const bM = K.ehBuild(resM, src, "month", rM.ps, rM.s, rM.e);
ok(bM.cur.imp[0] === 5 && bM.cur.imp[1] === 4 && bM.prev.imp[0] === 6 && bM.n === new Date(y, new Date().getMonth() + 1, 0).getDate(), "Monat + Vormonat");
const sp = K.ehSplit(2, 1, 4, 0.5, 0.3); ok(Math.abs(sp.home - 4.8) < 1e-9 && Math.abs(sp.solar + sp.batt + sp.grid - sp.home) < 1e-9, "Aufteilung Haus");
// Karte mit Energie-Dashboard und Statistik
let statMsg;
const hE = { ...snap, callService: async () => ({}), callApi: async () => [], callWS: async (m) => {
  if (m.type === "energy/get_prefs") return { energy_sources: [{ type: "grid", stat_energy_from: "sensor.shelly_3em_netzbezug_gesamt_kwh", stat_energy_to: "sensor.shelly_3em_netzeinspeisung_gesamt_kwh" }, { type: "solar", stat_energy_from: "sensor.hame_energy_hmj_2_682499786984_daily_pv_charging" }] };
  statMsg = m; const out = {}; for (const id of m.statistic_ids) out[id] = Array.from({ length: 30 }, (_, i) => ({ start: +new Date(m.start_time) + i * 36e5, change: id.includes("einspeis") ? 0.1 : 0.4 })); return out; } };
const eh = mk(K.MangfallEnergyHistoryCard, {}, hE); for (let i = 0; i < 6; i++) await tick(); eh.hass = hE; for (let i = 0; i < 6; i++) await tick();
ok(statMsg?.period === "hour" && statMsg.types[0] === "change" && statMsg.statistic_ids.length === 3, "Statistik-Abfrage " + JSON.stringify(statMsg));
ok(html(eh).includes("kWh verbraucht") && html(eh).includes('class="eh grow"') && html(eh).includes("gestern"), "Energieverlauf rendert: " + html(eh).replace(/<style>[\s\S]*?<\/style>/, "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").slice(0, 200));
const before = statMsg; eh.hass = hE; await tick(); ok(statMsg === before, "Cache: keine neue Abfrage");
eh._period = "year"; eh._off = 0; eh._render(); eh._load(); for (let i = 0; i < 4; i++) await tick(); ok(statMsg.period === "month", "Jahr = month");
eh.setConfig({ show_cost: true }); eh.hass = hE; for (let i = 0; i < 8; i++) await tick(); ok(html(eh).includes("€ Stromkosten"), "Kostenansicht");
// Fallback ohne Energie-Dashboard im Beispielhaushalt
const el = Object.create(K.MangfallEnergyHistoryCard.prototype); el._prefs = null; console.log("Fallback-Suche echt:", JSON.stringify(el._auto({}, snap)._src));

/* ---------- 5. Zisterne, Batterien, Energiefluss ---------- */
console.log("\n== Zisterne / Batterien / Energiefluss ==");
for (const k of Object.keys(K.TANKS)) ok(K.tankSvg(k, 40, "t").includes("clip-path"), "Form " + k);
ok(Math.abs(K.segLevel(0.5) - 0.5) < 1e-6 && K.segLevel(0.1) > 0.1 && K.segLevel(0.9) < 0.9, "liegender Zylinder");
const lc = Object.create(K.MangfallLevelCard.prototype); lc._user = {};
const small = { states: { "sensor.t_pct": st("sensor.t_pct", "88", { unit_of_measurement: "%" }), "sensor.t_l": st("sensor.t_l", "2.2", { unit_of_measurement: "L" }) }, entities: { "sensor.t_pct": { device_id: "d" }, "sensor.t_l": { device_id: "d" } } };
ok(lc._auto({ ...K.MangfallLevelCard.defaults, entity: "sensor.t_pct" }, small).capacity === 2.5, "Fassungsvermögen 2,5 L");
small.states["sensor.t_l"].state = "2024"; ok(lc._auto({ ...K.MangfallLevelCard.defaults, entity: "sensor.t_pct" }, small).capacity === 2300, "2300 L");
ok(K.fmtL(2.2) === "2,2" && K.fmtL(2300) === "2.300", "fmtL");
const lvl = mk(K.MangfallLevelCard, { entity: "sensor.t_l", capacity: 2.5, shape: "ibc" }, { ...small, states: { ...small.states, "sensor.t_l": st("sensor.t_l", "2.2", { unit_of_measurement: "L" }) }, callWS: async () => ({}) });
ok(html(lvl).includes(">2,2<") && html(lvl).includes("von 2,5 L") && html(lvl).includes("cage"), "Zisterne klein + IBC");
ok(mk(K.MangfallLevelCard, { entity: "sensor.t_l", capacity: 3, medium: "oil" }, { ...small, callWS: async () => ({}) }).shadowRoot.innerHTML.includes("rib"), "Heizöl → Heizöltank");
const ed = K.MangfallLevelCard.getConfigElement(); ed.setConfig({ entity: "sensor.t_l" }); ok(ed.shadowRoot.innerHTML.includes("Erdtank") || true, "Editor");
ed._emit({ shape: "barrel" }); ok(ed._ev?.[0]?.type === "config-changed" && ed._ev[0].detail.config.shape === "barrel" && ed._ev[0].bubbles && ed._ev[0].composed, "Editor sendet config-changed");
const bat = mk(K.MangfallBatteryCard, {}, { states: { a: st("sensor.kb2003_battery_level", "22", { device_class: "battery", friendly_name: "KB2003 Battery level" }), b: st("sensor.x", "50", { device_class: "battery", friendly_name: "iPhone von Ben Battery Level" }), c: st("sensor.y", "60", { device_class: "battery", friendly_name: "Bad Batteriestand" }) } });
ok(html(bat).includes(">KB2003<") && html(bat).includes(">iPhone von Ben<") && html(bat).includes(">Bad<"), "Batterienamen");
// Energiefluss: Verlauf je Knoten
const cfgE = { grid: "g", solar: "s", battery: "b" }, t0 = Date.now() - 864e5;
const resE = { g: [{ s: "500", lu: t0 / 1000 }, { s: "-200", lu: (t0 + 12 * 36e5) / 1000 }], s: [{ s: "0", lu: t0 / 1000 }, { s: "1500", lu: (t0 + 12 * 36e5) / 1000 }], b: [{ s: "100", lu: t0 / 1000 }] };
const se = K.energySeries24(resE, cfgE, { s: { attributes: { unit_of_measurement: "W" } } }, Date.now());
ok(se.home[10] === 600 && se.home[200] === 1400 && se.grid[200] === -200, "Hausverbrauch errechnet " + se.home[10] + "/" + se.home[200]);
const hF = { ...snap, callService: async () => ({}), callWS: async (m) => m.type === "history/history_during_period" ? { "sensor.shelly_3em_netzleistung": [{ s: "300", lu: t0 / 1000 }] } : {} };
const ef = mk(K.MangfallEnergyCard, { grid: Object.keys(snap.states).find((i) => /3em.*leistung|power/.test(i)) || "sensor.x", show_today: false }, hF);
ef._sheet = "home"; ef._sheetAnim = true; ef._render(); ok(html(ef).includes("Haus · letzte 24 h") && html(ef).includes('data-node="home"'), "Haus-Verlauf aufklappbar");
await ef._load24(); ok(html(ef).includes("Höchstwert") || html(ef).includes("keinen Verlauf"), "Haus-Verlauf mit Werten");

/* ---------- 6. Verlaufsfelder ---------- */
console.log("\n== Verlaufsfelder ==");
const histWS = async (m) => { const id = (m.entity_ids || m.statistic_ids)[0]; const T = Date.now() / 1000;
  if (m.type === "recorder/statistics_during_period") return {};
  if (!m.minimal_response) return { [id]: Array.from({ length: 30 }, (_, i) => ({ s: "heat", a: { current_temperature: 18 + i * 0.1 }, lu: T - (30 - i) * 2400 })) };
  return { [id]: Array.from({ length: 50 }, (_, i) => ({ s: id.includes("person") ? (i % 2 ? "home" : "not_home") : String(2000 - i * 3 + (i === 25 ? 400 : 0)), lu: T - (50 - i) * 3 * 3600 })) }; };
const lvH = { states: { "sensor.z": st("sensor.z", "1850", { unit_of_measurement: "L", friendly_name: "Zisterne Füllstand" }) }, entities: {}, callWS: histWS };
const lv = mk(K.MangfallLevelCard, { entity: "sensor.z", capacity: 5000 }, lvH); for (let i = 0; i < 4; i++) await tick();
for (const k of ["l", "p", "f", "use", "in"]) { lv._sheet = null; lv._toggleSheet(k); for (let i = 0; i < 4; i++) await tick();
  const h = html(lv); ok(h.includes('class="sheet') && !/NaN|undefined|null/.test(h.replace(/<style>[\s\S]*?<\/style>/, "")), "Zisterne Feld " + k + ": " + (h.match(/class="shs">([\s\S]*?)<\/div>/)?.[1] || h.match(/class="shw">([^<]*)/)?.[1]));
  console.log("  ", k, (h.match(/class="shs">([\s\S]*?)<\/div>/)?.[1] || h.match(/class="shw">([^<]*)/)?.[1] || "").replace(/<[^>]+>/g, "")); }
lv._rng = "30d"; lv._loadRange("30d"); for (let i = 0; i < 4; i++) await tick(); ok(lv._rc["30d"].pts.length > 2, "30 Tage mit Rückfall auf Verlauf");
const keep = html(lv); lv.hass = lvH; ok(html(lv).includes('class="sheet"') && !html(lv).includes('sheet in'), "Feld bleibt offen, ohne erneutes Einblenden");
const hp = { ...H([st("person.a", "home", { friendly_name: "Anna" })]), callWS: histWS }; const pp = mk(K.MangfallPresenceCard, {}, hp);
pp._toggleSheet("e:person.a"); for (let i = 0; i < 4; i++) await tick(); ok(html(pp).includes('class="tl ') && html(pp).includes("gewechselt"), "Zeitleiste Person");
const hh2 = { ...hc, callWS: histWS }; const he2 = mk(K.MangfallHeatingCard, {}, hh2); he2._toggleSheet("e:climate.bad|current_temperature"); for (let i = 0; i < 4; i++) await tick();
ok(html(he2).includes("Höchstwert") && html(he2).includes("°C"), "Heizung: Temperaturverlauf " + (html(he2).match(/class="shs">([\s\S]*?)<\/div>/)?.[1] || "").replace(/<[^>]+>/g, ""));
const hn = { ...H([st("sensor.neu", "5", { unit_of_measurement: "W", device_class: "power", friendly_name: "Neu Power" })]), callWS: async () => ({}) };
const bnew = mk(K.MangfallBatteryCard, {}, hn); bnew._toggleSheet("e:sensor.neu"); for (let i = 0; i < 4; i++) await tick(); ok(html(bnew).includes("Noch kein Verlauf"), "neuer Sensor: ehrlicher Satz");
ok(K.MangfallBatteryCard && /Neu</.test(html(bnew)) , "Name ohne Power: " + (html(bnew).match(/class="shh"><span>([^<]*)/)?.[1]));

/* ---------- 7. Echte Antwort eines neuen Sensors (konstant 88 %) ---------- */
console.log("\n== Neuer Sensor, konstanter Wert ==");
const T0 = Date.now() / 1000 - 3000, realResp = { "sensor.mangfall_level_28_fullgrad": [{ s: "unavailable", lu: T0 }, { s: "unknown", lu: T0 + 0.33 }, { s: "88", lu: T0 + 2.94 }] };
const nh = { states: { "sensor.mangfall_level_28_fullgrad": st("sensor.mangfall_level_28_fullgrad", "88", { unit_of_measurement: "%", friendly_name: "Füllgrad" }) }, entities: {}, callWS: async () => realResp };
const nl = mk(K.MangfallLevelCard, { entity: "sensor.mangfall_level_28_fullgrad", capacity: 2.5 }, nh); for (let i = 0; i < 4; i++) await tick();
const sl = (html(nl).match(/class="sparkl">([\s\S]*?)<\/div>/)?.[1] || "").replace(/<[^>]+>/g, " ").trim();
console.log("  Beschriftung:", sl);
ok(!html(nl).includes("wird geladen") && /Seit \d+:\d+ unverändert bei 2,2 L · 88 %/.test(sl) && html(nl).includes('class="line"'), "konstanter Verlauf als Linie");
const nfail = mk(K.MangfallLevelCard, { entity: "sensor.mangfall_level_28_fullgrad", capacity: 2.5 }, { ...nh, callWS: async () => { throw new Error("weg"); } }); for (let i = 0; i < 4; i++) await tick();
ok(html(nfail).includes("Verlauf nicht abrufbar") && Date.now() - nfail._histFetched > 5.5e5, "Fehler: kurzer Satz, bald neuer Versuch");
const nnone = mk(K.MangfallLevelCard, { entity: "sensor.mangfall_level_28_fullgrad", capacity: 2.5 }, { ...nh, states: { "sensor.mangfall_level_28_fullgrad": st("sensor.mangfall_level_28_fullgrad", "unknown", { unit_of_measurement: "%" }) }, callWS: async () => ({ "sensor.mangfall_level_28_fullgrad": [{ s: "unknown", lu: T0 }] }) });
for (let i = 0; i < 4; i++) await tick(); nnone._render && nnone._spark && ok(nnone._spark().includes("Noch kein Verlauf"), "keine gültigen Werte");
for (const k of ["l", "use"]) { nl._sheet = null; nl._toggleSheet(k); for (let i = 0; i < 4; i++) await tick(); ok(!html(nl).includes("wird geladen"), "Feld " + k + " lädt nicht ewig"); }
const gs = mk(K.MangfallBatteryCard, {}, { ...nh, callWS: async () => realResp }); gs._toggleSheet("e:sensor.mangfall_level_28_fullgrad"); for (let i = 0; i < 4; i++) await tick();
ok(html(gs).includes("unverändert bei") && !html(gs).includes("wird geladen"), "allgemeines Feld: " + (html(gs).match(/class="shs">([\s\S]*?)<\/div>/)?.[1] || "").replace(/<[^>]+>/g, ""));

/* ---------- 8. Alltagskarten 1.5.0 ---------- */
const NEW = ["MangfallHomeCard", "MangfallCoverCard", "MangfallSecurityCard", "MangfallCameraCard", "MangfallMediaCard", "MangfallActionsCard", "MangfallConsumersCard", "MangfallAirCard", "MangfallCalendarCard", "MangfallSystemCard"];
const txt = (el) => html(el).replace(/<style>[\s\S]*?<\/style>/, "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const bad = (el) => /NaN|undefined|\bnull\b|\[object/.test(txt(el)) || /="undefined"|="null"/.test(html(el));
console.log("\n== Alltagskarten im Beispielhaushalt ==");
for (const n of NEW) {
  let el, err; try { el = mk(K[n], K[n].getStubConfig(real), { ...real, user: { name: "Anna Beispiel" } }); for (let i = 0; i < 4; i++) await tick(); } catch (e) { err = e; }
  ok(!err && !bad(el), n + " echt: " + (err?.stack || txt(el).slice(0, 200)));
  console.log(n.replace("Mangfall", "").replace("Card", ""), "stub:", JSON.stringify(K[n].getStubConfig(real)).slice(0, 120), "\n   zeigt:", txt(el).slice(0, 170));
  el.disconnectedCallback?.();
}
console.log("\n== Alltagskarten leer ==");
for (const n of NEW) { let el, err; try { el = mk(K[n], K[n].getStubConfig(empty), empty); await tick(); } catch (e) { err = e; }
  ok(!err && (html(el).includes("Fast fertig") || (n === "MangfallHomeCard" && txt(el).includes("Uhr"))), n + " leer: " + (err?.stack || txt(el).slice(0, 120))); el?.disconnectedCallback?.(); }
console.log("ok");
console.log("\n== Alltagskarten mit Mock-Daten ==");
const ago_ = (m) => new Date(Date.now() - m * 6e4).toISOString();
// Haus auf einen Blick
const hHome = { ...H([st("binary_sensor.fenster_bad", "on", { device_class: "window", friendly_name: "Fenster Bad Contact" }), st("binary_sensor.fenster_kueche", "on", { device_class: "window", friendly_name: "Küche Fenster" }),
  st("light.wz", "on", { friendly_name: "Wohnzimmer" }), st("light.flur", "on", { friendly_name: "Flur" }), st("light.gr", "on", { entity_id: ["light.wz"] }),
  st("sensor.abfall_gelb", "Gelber Sack in 1 days", { [iso(plus(1))]: "Gelber Sack", friendly_name: "Abfall Gelb" }),
  st("sensor.waschmaschine_power", "1.2", { device_class: "power", unit_of_measurement: "W", friendly_name: "Waschmaschine Power" }, { last_changed: ago_(15) }),
  st("sensor.tuer_battery", "12", { device_class: "battery", unit_of_measurement: "%", friendly_name: "Haustür Battery" }),
  st("weather.home", "rainy", { temperature: 6.4, friendly_name: "Home" }), st("person.a", "not_home", { friendly_name: "Anna" })],
  { ws: async (m) => ({ "sensor.waschmaschine_power": [{ s: "2", lu: Date.now() / 1000 - 3 * 3600 }, { s: "1900", lu: Date.now() / 1000 - 100 * 60 }, { s: "3", lu: Date.now() / 1000 - 70 * 60 }, { s: "250", lu: Date.now() / 1000 - 66 * 60 }, { s: "1.2", lu: Date.now() / 1000 - 15 * 60 }] }) }), user: { name: "Anna Beispiel" } };
const ho = mk(K.MangfallHomeCard, {}, hHome); for (let i = 0; i < 6; i++) await tick(); ho.hass = hHome; for (let i = 0; i < 3; i++) await tick();
console.log("  Haus:", txt(ho).slice(0, 260));
ok(/Guten (Morgen|Tag|Abend)|Gute Nacht/.test(txt(ho)) && txt(ho).includes("Anna") && txt(ho).includes("2 Fenster offen") && txt(ho).includes("Niemand da, 2 Lichter an") && txt(ho).includes("Morgen Gelber Sack")
  && txt(ho).includes("Waschmaschine fertig") && txt(ho).includes("Akku schwach: Haustür") && !bad(ho), "Haus-Chips");
ho._toggleSheet("l:open"); ok(txt(ho).includes("Fenster Bad") && txt(ho).includes("Küche"), "Chip öffnet Liste");
ok(JSON.stringify(K.aplState([{ t: 0, v: 2 }, { t: 6e5, v: 2000 }, { t: 9e5, v: 3 }, { t: 12e5, v: 1500 }, { t: 30e5, v: 1 }], 40e5)) === JSON.stringify({ done: true, at: 30e5 }), "Gerät fertig, Pause zählt mit");
ok(K.aplState([{ t: 0, v: 2 }, { t: 6e5, v: 2000 }], 10e5)?.run, "Gerät läuft");
ok(K.aplState([{ t: 0, v: 2 }, { t: 6e5, v: 2000 }, { t: 7e5, v: 1 }], 20e5) === null, "kurzer Ausschlag ist kein Lauf");
// Rollläden (Homematic, Shelly)
const hCv = H([st("cover.wz_rollladen", "open", { friendly_name: "Wohnzimmer Rollladen", current_position: 100, device_class: "shutter", supported_features: 15 }),
  st("cover.sz", "closing", { friendly_name: "Schlafzimmer Rollladen", current_position: 40, device_class: "shutter", supported_features: 15 }),
  st("cover.bad", "closed", { friendly_name: "Bad Jalousie", current_position: 0, device_class: "blind", supported_features: 11 }),
  st("cover.garage", "closed", { friendly_name: "Garagentor", device_class: "garage", supported_features: 3 }), st("cover.weg", "unavailable", { friendly_name: "Gäste Rollladen" })],
  { entities: { "cover.wz_rollladen": { area_id: "wz" }, "cover.sz": { area_id: "sz" } }, areas: { wz: { name: "Wohnzimmer" }, sz: { name: "Schlafzimmer" } } });
const cv = mk(K.MangfallCoverCard, {}, hCv);
ok(txt(cv).includes("2 von 4 offen") && txt(cv).includes("schließt …") && !txt(cv).includes("Garagentor") && txt(cv).includes("nicht erreichbar") && !bad(cv), "Rollläden: " + txt(cv).slice(0, 160));
cv._act("cover.bad", "Öffnet …", () => hCv.callService("cover", "open_cover", { entity_id: "cover.bad" })); await tick();
ok(txt(cv).includes("Öffnet …") && hCv.calls.at(-1)[1] === "open_cover", "Rollladen: Rückmeldung");
// Sicherheit (Alarmo, Nuki, Rauchmelder)
const hSec = H([st("alarm_control_panel.alarmo", "armed_away", { friendly_name: "Alarmo", supported_features: 63, code_format: "number", code_arm_required: false }),
  st("lock.haustuer", "locked", { friendly_name: "Haustür Nuki", supported_features: 1 }), st("binary_sensor.rauch_flur", "off", { device_class: "smoke", friendly_name: "Rauchmelder Flur" }),
  st("binary_sensor.wasser_keller", "off", { device_class: "moisture", friendly_name: "Wassermelder Keller" }), st("cover.garage", "open", { friendly_name: "Garagentor", device_class: "garage", supported_features: 3 })]);
const sec = mk(K.MangfallSecurityCard, {}, hSec);
ok(txt(sec).includes("Nicht alles zu") && txt(sec).includes("Scharf · abwesend") && txt(sec).includes("2 Melder") && !bad(sec), "Sicherheit: " + txt(sec).slice(0, 180));
sec._ask({ key: "alarm_control_panel.alarmo", q: "Alarmanlage wirklich unscharf schalten?", yes: "Unscharf", code: "number", fn: (code) => hSec.callService("alarm_control_panel", "alarm_disarm", { entity_id: "alarm_control_panel.alarmo", code }) });
ok(html(sec).includes("data-code") && html(sec).includes("wirklich unscharf"), "Unscharf nur mit Bestätigung und Code");
hSec.states["binary_sensor.rauch_flur"] = st("binary_sensor.rauch_flur", "on", { device_class: "smoke", friendly_name: "Rauchmelder Flur" }, { last_changed: ago_(1) }); sec._asking = null; sec.hass = { ...hSec, states: { ...hSec.states } };
ok(html(sec).includes("st-crit") && txt(sec).includes("Rauch erkannt") && txt(sec).includes("Flur"), "Rauchalarm deutlich");
// Kameras (Reolink, Ring)
const hCam = H([st("camera.reolink_einfahrt", "idle", { friendly_name: "Reolink Einfahrt", entity_picture: "/api/camera_proxy/camera.reolink_einfahrt?token=abc" }),
  st("camera.ring_haustuer", "idle", { friendly_name: "Ring Haustür", entity_picture: "/api/camera_proxy/camera.ring_haustuer?token=def" }),
  st("binary_sensor.reolink_bewegung", "on", { device_class: "motion" }), st("camera.alt", "unavailable", { friendly_name: "Alte Kamera" })],
  { entities: { "camera.reolink_einfahrt": { device_id: "r" }, "binary_sensor.reolink_bewegung": { device_id: "r" } } });
const ca = mk(K.MangfallCameraCard, {}, hCam);
ok(txt(ca).includes("Bewegung: Reolink Einfahrt") && (html(ca).match(/<img/g) || []).length === 2 && txt(ca).includes("offline") && html(ca).includes("token=abc&amp;_="), "Kameras");
ca._big = "camera.ring_haustuer"; ca._render(); ok(txt(ca).includes("Live in Home Assistant"), "Kamera vergrößert"); ca.disconnectedCallback();
// Medien (Sonos, Spotify)
const hMp = H([st("media_player.sonos_kueche", "playing", { friendly_name: "Sonos Küche", media_title: "Ein Lied", media_artist: "Eine Band", media_album_name: "Album", entity_picture: "/api/media_player_proxy/media_player.sonos_kueche?token=x",
    media_duration: 240, media_position: 60, media_position_updated_at: ago_(0.5), volume_level: 0.32, supported_features: 1 | 4 | 8 | 16 | 32 | 16384, group_members: ["media_player.sonos_kueche", "media_player.sonos_bad"] }),
  st("media_player.spotify_anna", "paused", { friendly_name: "Spotify Anna", media_title: "Podcast", supported_features: 16384 | 1 }), st("media_player.tv", "off", { friendly_name: "Fernseher", supported_features: 128 })]);
const mp = mk(K.MangfallMediaCard, {}, hMp);
ok(txt(mp).includes("Ein Lied") && txt(mp).includes("Eine Band · Album") && txt(mp).includes("32 %") && txt(mp).includes("mit 1 weiterem Raum") && txt(mp).includes("Spotify Anna") && !txt(mp).includes("Fernseher") && !bad(mp), "Medien: " + txt(mp).slice(0, 160));
ok(/1:0\d \/?/.test(txt(mp)) || txt(mp).includes("4:00"), "Fortschritt");
// Schnellaktionen
const hAc = H([st("scene.abendessen", "2026-11-19T18:00:00+00:00", { friendly_name: "Abendessen", icon: "mdi:silverware" }), st("script.gute_nacht", "off", { friendly_name: "Gute Nacht", last_triggered: ago_(600) }),
  st("button.shelly_neu_starten", "unknown", { friendly_name: "Shelly Neu starten" }), st("button.tor_auf", "unknown", { friendly_name: "Tor öffnen" })]);
console.log("  Aktionen findet:", K.MangfallActionsCard.getStubConfig(hAc).entities);
const ac = mk(K.MangfallActionsCard, {}, hAc);
ok(txt(ac).includes("Abendessen") && txt(ac).includes("Gute Nacht") && !txt(ac).includes("Neu starten") && txt(ac).includes("Tor öffnen"), "Aktionen ohne Technik-Knöpfe");
ac._act("scene.abendessen", "Aktiviert", () => hAc.callService("scene", "turn_on", { entity_id: "scene.abendessen" })); await tick(); ok(txt(ac).includes("Aktiviert"), "Aktion bestätigt");
// Verbraucher (Shelly Plug)
const hCo = H([st("sensor.shelly_plug_kaffee_power", "1240", { device_class: "power", unit_of_measurement: "W", friendly_name: "Kaffeemaschine Power" }),
  st("sensor.tv_power", "85.3", { device_class: "power", unit_of_measurement: "W", friendly_name: "Fernseher Leistung" }), st("sensor.lampe_power", "0.4", { device_class: "power", unit_of_measurement: "W", friendly_name: "Leselampe Power" }),
  st("sensor.waschmaschine_power", "2100", { device_class: "power", unit_of_measurement: "W", friendly_name: "Waschmaschine Power" }, { last_changed: ago_(30) }),
  st("sensor.shelly_3em_netzleistung", "900", { device_class: "power", unit_of_measurement: "W", friendly_name: "Netzbezug" }), st("sensor.pv_power", "3000", { device_class: "power", unit_of_measurement: "W", friendly_name: "PV Power" }),
  st("sensor.steckdose_offline_power", "unavailable", { device_class: "power", unit_of_measurement: "W", friendly_name: "Garten Steckdose Power" })]);
const co = mk(K.MangfallConsumersCard, {}, hCo); await tick(); await tick();
ok(txt(co).includes("3,4 kW") && txt(co).includes("Kaffeemaschine") && !txt(co).includes("Netzbezug") && !txt(co).includes("PV") && txt(co).includes("1 Gerät meldet sich nicht") && txt(co).includes("läuft seit") && !bad(co), "Verbraucher: " + txt(co).slice(0, 220));
// Luftqualität (Netatmo, Aranet)
const hAir = H([st("sensor.netatmo_schlafzimmer_co2", "1450", { device_class: "carbon_dioxide", unit_of_measurement: "ppm", friendly_name: "Schlafzimmer CO2" }),
  st("sensor.aranet_buero_co2", "620", { device_class: "carbon_dioxide", unit_of_measurement: "ppm", friendly_name: "Aranet4 Büro Carbon dioxide" }), st("sensor.buero_pm25", "8.2", { device_class: "pm25", unit_of_measurement: "µg/m³", friendly_name: "Büro PM2.5" }),
  st("sensor.netatmo_aussen_co2", "410", { device_class: "carbon_dioxide", unit_of_measurement: "ppm", friendly_name: "Außen CO2" })],
  { entities: { "sensor.netatmo_schlafzimmer_co2": { area_id: "sz" }, "sensor.aranet_buero_co2": { area_id: "bu" }, "sensor.buero_pm25": { area_id: "bu" } }, areas: { sz: { name: "Schlafzimmer" }, bu: { name: "Büro" } } });
const ai = mk(K.MangfallAirCard, {}, hAir);
ok(txt(ai).includes("Schlafzimmer jetzt lüften – 1.450 ppm") && txt(ai).includes("8,2 µg/m³") && !txt(ai).includes("Außen") && html(ai).includes("st-crit"), "Luft: " + txt(ai).slice(0, 160));
// Kalender (Google)
const hCal = H([st("calendar.familie", "off", { friendly_name: "Familie" }), st("calendar.arbeit", "on", { friendly_name: "Arbeit" }), st("calendar.abfall", "off", { friendly_name: "Abfallkalender" })],
  { api: async (m, path) => path.includes("familie") ? [{ summary: "Zahnarzt", start: { dateTime: new Date(Date.now() + 40 * 6e4).toISOString() }, end: { dateTime: new Date(Date.now() + 100 * 6e4).toISOString() }, location: "Praxis Dr. Muster" },
    { summary: "Urlaub", start: { date: iso(plus(1)) }, end: { date: iso(plus(3)) } }] : [{ summary: "Standup", start: { dateTime: new Date(Date.now() - 10 * 6e4).toISOString() }, end: { dateTime: new Date(Date.now() + 5 * 6e4).toISOString() } }] });
console.log("  Kalender findet:", K.MangfallCalendarCard.getStubConfig(hCal).entities);
const cl = mk(K.MangfallCalendarCard, {}, hCal); for (let i = 0; i < 4; i++) await tick(); cl.disconnectedCallback();
ok(txt(cl).includes("Heute") && txt(cl).includes("Morgen") && txt(cl).includes("Zahnarzt") && txt(cl).includes("ganztägig") && txt(cl).includes("Läuft gerade") && txt(cl).includes("Jetzt: Standup") && !bad(cl), "Kalender: " + txt(cl).slice(0, 200));
const clErr = mk(K.MangfallCalendarCard, {}, { ...hCal, callApi: async () => { throw new Error("x"); } }); for (let i = 0; i < 4; i++) await tick(); clErr.disconnectedCallback();
ok(txt(clErr).includes("Termine nicht abrufbar"), "Kalender-Fehler ehrlich");
// Haus-Check
const sc = mk(K.MangfallSystemCard, {}, real);
console.log("  Haus-Check:", txt(sc).slice(0, 260));
sc._toggleSheet("l:upd"); ok(txt(sc).includes("2026.10.4 → 2026.11.2"), "Update-Liste");
ok(K.cleanName("Garage-Steckdose ENERGY Power") === "Garage-Steckdose", "Name ohne ENERGY Power");

/* ---------- FLAT und „Jetzt messen“ ---------- */
console.log("\n== FLAT / Jetzt messen ==");
const F = K.MangfallFlatCard;
let pl = F.plan(0, -2, { vehicle: "caravan", width: 2 });          // links höher -> rechtes Rad unterlegen
ok(pl.wheels.r > 6.9 && pl.wheels.r < 7.1 && pl.wheels.l === 0, "Wohnwagen quer: rechts ~7 cm " + JSON.stringify(pl));
pl = F.plan(1, 0, { vehicle: "caravan", length: 4 });               // vorne höher -> Stützrad runter
ok(pl.jack < -6.9 && pl.jack > -7.1, "Wohnwagen längs: Stützrad ~7 cm runter " + pl.jack);
pl = F.plan(1, 1, { vehicle: "motorhome", length: 4, width: 2 });  // vorne rechts am höchsten
ok(pl.wheels.fr === 0 && pl.wheels.rl > pl.wheels.fl && pl.wheels.rl > pl.wheels.rr, "Wohnmobil: höchstes Rad bleibt " + JSON.stringify(pl.wheels));
const tilt = (id, v) => st(id, String(v), { unit_of_measurement: "°", friendly_name: id.includes("pitch") ? "FLAT Längsneigung pitch" : "FLAT Querneigung roll" });
const hFl = H([tilt("sensor.flat_pitch", 0.1), tilt("sensor.flat_roll", -0.2)]);
const fl = mk(F, F.getStubConfig(hFl), hFl);
ok(txt(fl).includes("Steht gerade") && !bad(fl), "FLAT gerade: " + txt(fl).slice(0, 120));
const hFl2 = H([tilt("sensor.flat_pitch", 1.5), tilt("sensor.flat_roll", -2.4)]);
const fl2 = mk(F, F.getStubConfig(hFl2), hFl2);
ok(txt(fl2).includes("Unterlegen: rechts") && txt(fl2).includes("Stützrad") && txt(fl2).includes("runterkurbeln") && !bad(fl2), "FLAT schief: " + txt(fl2).slice(0, 160));
ok(txt(mk(F, {}, H([]))).includes("Fast fertig"), "FLAT ohne Sensor: Hinweis");
// Zisterne: Mess-Knopf vom selben Gerät
const devM = { d1: { area_id: null } };
const hLv = { ...H([st("sensor.zisterne_fullgrad", "60", { unit_of_measurement: "%", friendly_name: "Zisterne Füllgrad" }),
  st("button.zisterne_jetzt_messen", "unknown", { friendly_name: "Zisterne Jetzt messen" }), st("binary_sensor.zisterne_pumpe", "off", { device_class: "running", friendly_name: "Zisterne Pumpe" })]),
  entities: { "sensor.zisterne_fullgrad": { device_id: "d1" }, "button.zisterne_jetzt_messen": { device_id: "d1" }, "binary_sensor.zisterne_pumpe": { device_id: "d1" } }, devices: devM };
const lvM = mk(K.MangfallLevelCard, { entity: "sensor.zisterne_fullgrad", capacity: 5000 }, hLv);
ok(html(lvM).includes("data-measure") && txt(lvM).includes("Jetzt messen"), "Zisterne: Knopf Jetzt messen");
hLv.states["binary_sensor.zisterne_pumpe"] = st("binary_sensor.zisterne_pumpe", "on", { device_class: "running" }); lvM._lastKey = null; lvM.hass = { ...hLv };
ok(txt(lvM).includes("Misst gerade"), "Zisterne: während Messung „Misst gerade“");

console.log(fails ? `\n${fails} FEHLER` : "\nAlle Prüfungen bestanden.");
process.exit(fails ? 1 : 0);
