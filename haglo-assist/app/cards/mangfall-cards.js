/*
 * MANGFALL Cards – Kartenbibliothek für Home Assistant
 * Karten: Zisterne (LEVEL), Energiefluss (WATT), Raumklima, Batterien, Fenster & Türen, Mähroboter
 * (c) Mangfall · MIT-Lizenz
 */
const MC_VERSION = "1.2.0";

const fmt = (n, d = 0) =>
  Number(n).toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });

/* Markenzeichen: MANGFALL-Icon, nachgezeichnet aus der Logo-Vorlage. */
const MARK = `<svg class="m" viewBox="-2 -2 204 129" aria-hidden="true"><g fill="currentColor" stroke="currentColor" stroke-width="4" stroke-linejoin="round">
  <path d="M0 0H31L100 51L169 0H200V41H162L100 83L38 41H0Z"/>
  <path d="M0 55H40L50 76V82H0Z"/><path d="M200 55H160L150 76V82H200Z"/>
  <rect x="0" y="97" width="50" height="27"/><rect x="150" y="97" width="50" height="27"/></g></svg>`;

const ago = (iso) => {
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (m < 1) return "gerade eben";
  if (m < 60) return `seit ${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `seit ${h} h`;
  return `seit ${Math.floor(h / 24)} Tg.`;
};

const toWatt = (st) => {
  if (!st) return null;
  const v = parseFloat(st.state);
  if (isNaN(v)) return null;
  const u = (st.attributes.unit_of_measurement || "W").toLowerCase();
  return u === "kw" ? v * 1000 : u === "mw" ? v * 1e6 : v;
};
const fmtW = (w) => (Math.abs(w) >= 1000 ? `${fmt(w / 1000, 1)} kW` : `${fmt(w)} W`);

/* ================= Basis für alle Karten ================= */
class MangfallBase extends HTMLElement {
  static product = "";          // erscheint neben dem M, z. B. LEVEL oder WATT
  setConfig(config) {
    this._config = { show_branding: true, ...this.constructor.defaults, ...config };
    this._check?.(this._config);
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._lastKey = null;
  }
  set hass(hass) {
    this._hass = hass;
    const key = this._watch().map((id) => {
      const s = hass.states[id];
      return s ? s.state + s.last_updated : "-";
    }).join("|");
    if (key !== this._lastKey) { this._lastKey = key; this._render(); }
    this._onHass?.();
  }
  _watch() { return []; }
  getCardSize() { return 4; }
  getGridOptions() { return { columns: 12, rows: "auto", min_columns: 6 }; }

  _moreInfo(entityId) {
    const ev = new Event("hass-more-info", { bubbles: true, composed: true });
    ev.detail = { entityId };
    this.dispatchEvent(ev);
  }
  _shell(stateKey, body, extraCss = "") {
    const p = this.constructor.product;
    const brand = this._config.show_branding
      ? `<a class="brand" href="https://mangfall.eu" target="_blank" rel="noopener" title="MANGFALL${p ? " " + p : ""}">${MARK}${p ? `<span class="prod">${p}</span>` : ""}</a>`
      : "";
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}${extraCss}</style>
      <ha-card class="st-${stateKey}${this._config.show_branding ? " branded" : ""}">${body}${brand}</ha-card>`;
    this.shadowRoot.querySelectorAll("[data-entity]").forEach((el) =>
      el.addEventListener("click", () => this._moreInfo(el.dataset.entity)));
  }
  _head(title, chip) {
    return `<div class="head"><div class="title">${title}</div>
      ${chip ? `<div class="chip"><ha-icon icon="${chip.icon}"></ha-icon>${chip.label}</div>` : ""}</div>`;
  }
  _tiles(tiles) {
    return `<div class="tiles">${tiles.map((t) => `
      <div class="tile"${t.entity ? ` data-entity="${t.entity}"` : ""}>
        <div class="tl"><ha-icon icon="${t.icon}"></ha-icon>${t.label}</div>
        <div class="tv">${t.value}<span>${t.unit || ""}</span></div>
        <div class="ts">${t.sub || ""}</div></div>`).join("")}</div>`;
  }
  _missing(title, msg) { this._shell("crit", `${this._head(title)}<div class="muted pad">${msg}</div>`); }
}

const BASE_CSS = `
  ha-card { display: block; box-sizing: border-box; height: 100%; padding: 16px; overflow: hidden; position: relative; }
  ha-card.branded { padding-bottom: 32px; }
  .st-ok { --mc-state: var(--success-color, #0ca30c); }
  .st-info { --mc-state: var(--info-color, #2a78d6); }
  .st-low { --mc-state: var(--warning-color, #eda100); }
  .st-crit { --mc-state: var(--error-color, #e34948); }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
  .title { font-size: 15px; font-weight: 500; color: var(--secondary-text-color); letter-spacing: .02em; }
  .chip { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 500; padding: 3px 8px 3px 6px;
    border-radius: 999px; color: var(--primary-text-color); background: color-mix(in srgb, var(--mc-state) 16%, transparent); }
  .chip ha-icon { --mdc-icon-size: 15px; color: var(--mc-state); }
  .big { display: flex; align-items: baseline; gap: 4px; color: var(--primary-text-color); }
  .num { font-size: 40px; font-weight: 600; line-height: 1; letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
  .u { font-size: 18px; color: var(--secondary-text-color); }
  .muted { font-size: 13px; color: var(--secondary-text-color); margin-top: 4px; }
  .pad { padding: 12px 0; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(92px, 1fr)); gap: 8px; margin-top: 14px; }
  .tile { padding: 8px 10px; border-radius: 12px; background: var(--secondary-background-color, rgba(127,127,127,.08)); }
  .tile[data-entity] { cursor: pointer; }
  .tl { display: flex; align-items: center; gap: 4px; font-size: 11.5px; color: var(--secondary-text-color); }
  .tl ha-icon { --mdc-icon-size: 14px; }
  .tv { font-size: 20px; font-weight: 600; color: var(--primary-text-color); margin-top: 2px; font-variant-numeric: tabular-nums; }
  .tv span { font-size: 12px; font-weight: 400; color: var(--secondary-text-color); margin-left: 3px; }
  .ts { font-size: 11px; color: var(--secondary-text-color); opacity: .8; }
  .rows { margin-top: 10px; display: flex; flex-direction: column; }
  .row { display: flex; align-items: center; gap: 10px; padding: 8px 2px; border-top: 1px solid var(--divider-color, rgba(127,127,127,.2)); cursor: pointer; }
  .row:first-child { border-top: none; }
  .row .ri { --mdc-icon-size: 20px; color: var(--secondary-text-color); }
  .row .rn { flex: 1; min-width: 0; font-size: 14px; color: var(--primary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .row .rs { font-size: 12px; color: var(--secondary-text-color); }
  .row .rv { font-size: 15px; font-weight: 600; color: var(--primary-text-color); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .dot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 8px; }
  .d-ok { background: var(--success-color, #0ca30c); } .d-low { background: var(--warning-color, #eda100); } .d-crit { background: var(--error-color, #e34948); }
  .brand { position: absolute; right: 16px; bottom: 11px; display: inline-flex; align-items: baseline; gap: 6px;
    text-decoration: none; color: var(--primary-text-color); opacity: .42; transition: opacity .2s; }
  .brand:hover { opacity: 1; }
  .brand .m { height: 7.3px; width: 11.6px; display: block; }
  .prod { font-size: 10.3px; font-weight: 600; letter-spacing: .24em; line-height: 1; margin-right: -.24em; }
  @media (prefers-reduced-motion: reduce) { * { animation: none !important; } }
`;

/* ================= 1 · Zisterne / Tank (LEVEL) ================= */
const MEDIA = { water: ["#5aa7f0", "#1c5cab"], oil: ["#f2c14e", "#b8860b"] };

class MangfallLevelCard extends MangfallBase {
  static product = "LEVEL";
  static defaults = { name: "Zisterne", capacity: 5000, max_height: null, low: 25, critical: 10, days: 7, water_price: null, medium: "water" };
  static getStubConfig(hass) {
    const s = Object.values(hass.states).find((e) => e.entity_id.startsWith("sensor.") &&
      /zisterne|cistern|tank|level|fuell|füll/i.test(e.entity_id + (e.attributes.friendly_name || "")));
    return { entity: s ? s.entity_id : "", capacity: 5000 };
  }
  static getConfigForm() {
    return {
      schema: [
        { name: "entity", required: true, selector: { entity: { domain: "sensor" } } },
        { name: "name", selector: { text: {} } },
        { type: "grid", name: "", schema: [
          { name: "capacity", selector: { number: { min: 100, max: 100000, step: 100, unit_of_measurement: "L", mode: "box" } } },
          { name: "max_height", selector: { number: { min: 0.1, max: 10, step: 0.01, unit_of_measurement: "m", mode: "box" } } },
          { name: "low", selector: { number: { min: 0, max: 100, unit_of_measurement: "%", mode: "box" } } },
          { name: "critical", selector: { number: { min: 0, max: 100, unit_of_measurement: "%", mode: "box" } } },
          { name: "days", selector: { number: { min: 2, max: 30, unit_of_measurement: "Tage", mode: "box" } } },
          { name: "water_price", selector: { number: { min: 0, max: 20, step: 0.01, unit_of_measurement: "€/m³", mode: "box" } } },
        ] },
        { name: "medium", selector: { select: { mode: "dropdown", options: [{ value: "water", label: "Wasser" }, { value: "oil", label: "Heizöl" }] } } },
        { name: "show_branding", selector: { boolean: {} } },
      ],
      computeLabel: (s) => ({ entity: "Füllstandssensor", name: "Name", capacity: "Fassungsvermögen", max_height: "Max. Füllhöhe (nur bei m/cm)",
        low: "Warnung ab", critical: "Alarm ab", days: "Verlauf", water_price: "Wasserpreis (optional)", medium: "Inhalt",
        show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
    };
  }
  _check(c) { if (!c.entity) throw new Error("Bitte einen Füllstandssensor angeben (entity)."); this._history = null; this._histFetched = 0; }
  _watch() { return [this._config.entity]; }
  getCardSize() { return 5; }
  _onHass() { if (Date.now() - this._histFetched > 6e5) this._fetchHistory(); }

  _toLiters(raw, unit) {
    const c = this._config, v = parseFloat(raw);
    if (isNaN(v)) return null;
    const u = (unit || "").trim().toLowerCase();
    if (u === "l" || u === "liter") return v;
    if (u === "m³" || u === "m3") return v * 1000;
    if ((u === "m" || u === "cm" || u === "mm") && c.max_height) {
      const m = u === "cm" ? v / 100 : u === "mm" ? v / 1000 : v;
      return Math.max(0, Math.min(1, m / c.max_height)) * c.capacity;
    }
    return (v / 100) * c.capacity;
  }
  async _fetchHistory() {
    if (!this._hass || this._fetching) return;
    this._fetching = true; this._histFetched = Date.now();
    try {
      const end = new Date(), start = new Date(end - this._config.days * 864e5);
      const res = await this._hass.callWS({ type: "history/history_during_period", start_time: start.toISOString(), end_time: end.toISOString(),
        entity_ids: [this._config.entity], minimal_response: true, no_attributes: true });
      const unit = this._hass.states[this._config.entity]?.attributes.unit_of_measurement;
      this._history = (res[this._config.entity] || []).map((r) => ({
        t: r.lu ? r.lu * 1000 : new Date(r.last_updated || r.last_changed).getTime(), l: this._toLiters(r.s ?? r.state, unit),
      })).filter((p) => p.l !== null && !isNaN(p.t));
    } catch (e) { this._history = []; }
    this._fetching = false; this._render();
  }
  _stats() {
    const h = this._history;
    if (!h || h.length < 2) return null;
    const noise = this._config.capacity * 0.003, dayAgo = Date.now() - 864e5;
    let used = 0, inflow24 = 0;
    for (let i = 1; i < h.length; i++) {
      const d = h[i].l - h[i - 1].l;
      if (Math.abs(d) < noise) continue;
      if (d < 0) used -= d;
      if (d > 0 && h[i].t >= dayAgo) inflow24 += d;
    }
    return { perDay: used / Math.max(1, (h[h.length - 1].t - h[0].t) / 864e5), inflow24 };
  }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, st = this._hass.states[c.entity];
    if (!st || ["unavailable", "unknown"].includes(st.state)) return this._missing(c.name, st ? "Sensor nicht erreichbar" : `„${c.entity}“ nicht gefunden`);
    const liters = Math.max(0, Math.min(c.capacity, this._toLiters(st.state, st.attributes.unit_of_measurement)));
    const pct = (liters / c.capacity) * 100, s = this._stats(), oil = c.medium === "oil";

    let state = { key: "ok", label: "Gut gefüllt", icon: "mdi:check-circle" };
    if (pct >= 98) state = { key: "info", label: "Voll – Überlauf möglich", icon: "mdi:waves-arrow-up" };
    else if (pct <= c.critical) state = { key: "crit", label: "Fast leer", icon: "mdi:alert-octagon" };
    else if (pct <= c.low) state = { key: "low", label: "Niedrig", icon: "mdi:alert" };
    let daysLeft = "–", daysSub = "zu wenig Verlauf", dNum = null;
    if (s) {
      if (s.perDay < c.capacity * 0.002) { daysLeft = "> 90"; daysSub = "kaum Verbrauch"; }
      else { dNum = liters / s.perDay; daysLeft = dNum > 90 ? "> 90" : fmt(dNum); daysSub = "Prognose"; }
    }
    if (state.key === "ok" && dNum !== null && dNum < 7) state = { key: "low", label: "Wird knapp", icon: "mdi:alert" };
    const saved = c.water_price && s ? (s.perDay * 30 / 1000) * c.water_price : null;
    const [w1, w2] = MEDIA[c.medium] || MEDIA.water;

    this._shell(state.key, `
      <div class="wrap">
        <div class="tankbox" data-entity="${c.entity}">${this._tank(pct)}</div>
        <div class="info">
          ${this._head(c.name, state)}
          <div class="big" style="margin-top:6px"><span class="num">${fmt(liters)}</span><span class="u">L</span></div>
          <div class="muted">von ${fmt(c.capacity)} L · ${fmt(pct)} %</div>
          ${saved !== null ? `<div class="saved"><ha-icon icon="mdi:piggy-bank-outline"></ha-icon><b>${fmt(saved, 2)} €</b> gespart in 30 Tagen</div>` : ""}
        </div>
      </div>
      ${this._tiles([
        { label: "Reicht noch", value: daysLeft, unit: "Tage", sub: daysSub, icon: "mdi:calendar-clock" },
        { label: "Verbrauch", value: s ? fmt(s.perDay) : "–", unit: "L/Tag", sub: `Ø ${c.days} Tage`, icon: "mdi:water-minus" },
        { label: oil ? "Nachgetankt" : "Regen-Zulauf", value: s ? (s.inflow24 > 0 ? "+" + fmt(s.inflow24) : "0") : "–", unit: "L", sub: "letzte 24 h", icon: oil ? "mdi:gas-station" : "mdi:weather-pouring" },
      ])}
      ${this._spark()}`, `
      ha-card { --mlc-w1: var(--mlc-water-top, ${w1}); --mlc-w2: var(--mlc-water-bottom, ${w2}); }
      .wrap { display: flex; gap: 16px; align-items: center; }
      .tankbox { flex: 0 0 104px; cursor: pointer; }
      .tank { width: 104px; height: 130px; display: block; }
      .info { flex: 1; min-width: 0; }
      .shell { fill: var(--secondary-background-color, rgba(127,127,127,.08)); stroke: var(--divider-color, rgba(127,127,127,.35)); stroke-width: 2; }
      .lid { fill: var(--divider-color, rgba(127,127,127,.4)); }
      .w1 { stop-color: var(--mlc-w1); } .w2 { stop-color: var(--mlc-w2); }
      .wave { fill: url(#mlc-water); }
      .wave.back { opacity: .45; animation: mc-drift 7s linear infinite; }
      .wave.front { opacity: .95; animation: mc-drift 4.5s linear infinite reverse; }
      @keyframes mc-drift { from { transform: translateX(0); } to { transform: translateX(60px); } }
      .bub { fill: rgba(255,255,255,.55); animation: mc-rise 5s ease-in infinite; }
      .b2 { animation-delay: 1.7s; } .b3 { animation-delay: 3.1s; }
      @keyframes mc-rise { 0% { transform: translateY(0); opacity: 0; } 15% { opacity: .8; } 100% { transform: translateY(-70px); opacity: 0; } }
      .tick { stroke: var(--secondary-text-color); stroke-opacity: .45; }
      .tickl { font-size: 7px; fill: var(--secondary-text-color); opacity: .7; }
      .saved { display: inline-flex; align-items: center; gap: 5px; margin-top: 8px; font-size: 12.5px; color: var(--primary-text-color);
        padding: 4px 10px 4px 7px; border-radius: 999px; background: color-mix(in srgb, var(--success-color, #0ca30c) 14%, transparent); }
      .saved ha-icon { --mdc-icon-size: 15px; color: var(--success-color, #0ca30c); }
      .saved b { font-weight: 600; font-variant-numeric: tabular-nums; }
      .spark { margin-top: 12px; } .spark > svg { width: 100%; height: 56px; display: block; }
      .area { fill: var(--mlc-w1); opacity: .16; }
      .line { fill: none; stroke: var(--mlc-w2); stroke-width: 2; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
      .sparkl { display: flex; justify-content: space-between; font-size: 11px; color: var(--secondary-text-color); margin-top: 2px; }
      .empty-spark { font-size: 12px; color: var(--secondary-text-color); padding: 18px 0; text-align: center; }`);
  }
  _tank(pct) {
    const top = 18, bottom = 142, y = bottom - ((bottom - top) * pct) / 100;
    const wave = (amp, len) => { let d = `M -120 ${y}`; for (let x = -120; x <= 240; x += len) d += ` q ${len / 4} ${-amp} ${len / 2} 0 t ${len / 2} 0`; return d + " V 160 H -120 Z"; };
    const ticks = [25, 50, 75].map((p) => { const ty = bottom - ((bottom - top) * p) / 100;
      return `<line x1="14" x2="22" y1="${ty}" y2="${ty}" class="tick"/><text x="26" y="${ty + 3}" class="tickl">${p}</text>`; }).join("");
    const shape = "M10 34 Q10 14 30 14 H90 Q110 14 110 34 V132 Q110 144 98 144 H22 Q10 144 10 132 Z";
    return `<svg viewBox="0 0 120 150" class="tank" role="img" aria-label="Füllstand ${fmt(pct)} Prozent">
      <defs><clipPath id="mlc-clip"><path d="${shape}"/></clipPath>
        <linearGradient id="mlc-water" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="w1"/><stop offset="1" class="w2"/></linearGradient></defs>
      <path d="${shape}" class="shell"/>
      <g clip-path="url(#mlc-clip)"><path d="${wave(3, 60)}" class="wave back"/><path d="${wave(4, 80)}" class="wave front"/>
        <circle cx="40" cy="${bottom - 8}" r="2.2" class="bub"/><circle cx="72" cy="${bottom - 4}" r="1.6" class="bub b2"/><circle cx="88" cy="${bottom - 10}" r="2.6" class="bub b3"/></g>
      ${ticks}<rect x="48" y="6" width="24" height="9" rx="3" class="lid"/></svg>`;
  }
  _spark() {
    const h = this._history;
    if (!h || h.length < 2) return `<div class="empty-spark">Verlauf wird geladen …</div>`;
    const W = 300, H = 56, cap = this._config.capacity, t0 = h[0].t, t1 = h[h.length - 1].t;
    let lo = Math.min(...h.map((p) => p.l)), hi = Math.max(...h.map((p) => p.l));
    const pad = Math.max((hi - lo) * 0.15, cap * 0.02); lo = Math.max(0, lo - pad); hi = Math.min(cap, hi + pad);
    const X = (t) => ((t - t0) / Math.max(1, t1 - t0)) * W, Y = (l) => H - 4 - ((l - lo) / Math.max(1, hi - lo)) * (H - 8);
    let d = `M ${X(h[0].t).toFixed(1)} ${Y(h[0].l).toFixed(1)}`;
    for (let i = 1; i < h.length; i++) d += ` H ${X(h[i].t).toFixed(1)} V ${Y(h[i].l).toFixed(1)}`;
    d += ` H ${W}`;
    return `<div class="spark"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path d="${d} V ${H} H 0 Z" class="area"/><path d="${d}" class="line"/></svg>
      <div class="sparkl"><span>vor ${this._config.days} Tagen</span><span>${fmt(lo)}–${fmt(hi)} L</span><span>jetzt</span></div></div>`;
  }
}

/* ================= 2 · Energiefluss (WATT) ================= */
const EC = { solar: "#f0a400", grid: "#8590a3", batt: "#7c5cff", home: "#2a78d6" };
const EG = { W: 360, H: 290, R: 30, solar: [180, 58], grid: [56, 148], home: [304, 148], batt: [180, 240] };
class MangfallEnergyCard extends MangfallBase {
  static product = "WATT";
  static defaults = { name: "Energie", grid: "", solar: "", battery: "", battery_soc: "", home: "", invert_grid: false, invert_battery: false,
    solar_peak: 0, price: 0.35, feed_in: 0.079, show_today: true };
  static getStubConfig() { return { grid: "" }; }
  static getConfigForm() {
    return {
      schema: [
        { name: "grid", required: true, selector: { entity: { domain: "sensor" } } },
        { name: "solar", selector: { entity: { domain: "sensor" } } },
        { name: "battery", selector: { entity: { domain: "sensor" } } },
        { name: "battery_soc", selector: { entity: { domain: "sensor" } } },
        { name: "home", selector: { entity: { domain: "sensor" } } },
        { name: "name", selector: { text: {} } },
        { type: "grid", name: "", schema: [
          { name: "price", selector: { number: { min: 0, max: 2, step: 0.01, mode: "box", unit_of_measurement: "€/kWh" } } },
          { name: "feed_in", selector: { number: { min: 0, max: 1, step: 0.001, mode: "box", unit_of_measurement: "€/kWh" } } },
          { name: "solar_peak", selector: { number: { min: 0, max: 100000, step: 100, mode: "box", unit_of_measurement: "W" } } },
          { name: "show_today", selector: { boolean: {} } } ] },
        { type: "grid", name: "", schema: [{ name: "invert_grid", selector: { boolean: {} } }, { name: "invert_battery", selector: { boolean: {} } }] },
        { name: "show_branding", selector: { boolean: {} } },
      ],
      computeLabel: (s) => ({ grid: "Netz-Leistung (+ Bezug, − Einspeisung)", solar: "PV-Leistung", battery: "Speicher-Leistung (+ Entladen, − Laden)",
        battery_soc: "Speicher-Ladestand", home: "Hausverbrauch (optional, sonst berechnet)", name: "Name", price: "Strompreis brutto",
        feed_in: "Einspeisevergütung", solar_peak: "PV-Spitzenleistung (für Solar-Ring)", show_today: "Tageswerte anzeigen",
        invert_grid: "Netz-Vorzeichen umdrehen", invert_battery: "Speicher-Vorzeichen umdrehen", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
    };
  }
  _check(c) { if (!c.grid) throw new Error("Bitte mindestens den Netz-Leistungssensor angeben (grid)."); }
  _watch() { const c = this._config; return [c.grid, c.solar, c.battery, c.battery_soc, c.home].filter(Boolean); }
  getCardSize() { return 7; }

  /* Tageswerte: Leistungsverlauf seit Mitternacht integrieren, alle 5 min neu */
  _onHass() {
    if (!this._config.show_today || this._busy || !this._hass.callWS) return;
    const t = this._todayAt;
    if (t && Date.now() - t < 300000 && new Date(t).getDate() === new Date().getDate()) return;
    this._fetchToday();
  }
  async _fetchToday() {
    this._busy = true;
    const c = this._config, start = new Date(); start.setHours(0, 0, 0, 0);
    try {
      const ids = [c.grid, c.solar, c.battery].filter(Boolean);
      const res = await this._hass.callWS({ type: "history/history_during_period", start_time: start.toISOString(), end_time: new Date().toISOString(),
        entity_ids: ids, minimal_response: true, no_attributes: true, significant_changes_only: false });
      const integ = (id, sign) => {
        const arr = res[id] || [], u = (this._hass.states[id]?.attributes.unit_of_measurement || "W").toLowerCase();
        const k = (u === "kw" ? 1000 : 1) * sign; let pos = 0, neg = 0;
        for (let i = 0; i < arr.length; i++) {
          const v = parseFloat(arr[i].s) * k; if (isNaN(v)) continue;
          const t0 = Math.max(arr[i].lu * 1000, start.getTime()), t1 = i + 1 < arr.length ? arr[i + 1].lu * 1000 : Date.now();
          const kwh = (v * Math.max(0, t1 - t0)) / 3.6e9; if (kwh > 0) pos += kwh; else neg -= kwh;
        }
        return [pos, neg];
      };
      const [imp, exp] = integ(c.grid, c.invert_grid ? -1 : 1);
      const pv = c.solar ? integ(c.solar, 1)[0] : 0;
      const [dis, chg] = c.battery ? integ(c.battery, c.invert_battery ? -1 : 1) : [0, 0];
      const home = Math.max(0, imp - exp + pv + dis - chg);
      this._today = { imp, exp, pv, home, aut: home > 0.05 ? Math.max(0, Math.min(100, (1 - imp / home) * 100)) : null,
        saved: Math.max(0, pv - exp) * c.price + exp * c.feed_in };
    } catch (e) { /* kein Verlauf verfügbar – Tageswerte bleiben leer */ }
    this._todayAt = Date.now(); this._busy = false; this._lastKey = null; this._render();
  }

  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, S = this._hass.states;
    let g = toWatt(S[c.grid]);
    if (g === null) return this._missing(c.name, `Netzsensor „${c.grid}“ nicht verfügbar`);
    if (c.invert_grid) g = -g;
    const hasS = !!c.solar, hasB = !!c.battery;
    const s = hasS ? Math.max(0, toWatt(S[c.solar]) ?? 0) : 0;
    let b = hasB ? toWatt(S[c.battery]) ?? 0 : 0;
    if (c.invert_battery) b = -b;
    const socRaw = c.battery_soc ? parseFloat(S[c.battery_soc]?.state) : NaN, soc = isNaN(socRaw) ? null : socRaw;
    const h = Math.max(0, c.home ? toWatt(S[c.home]) ?? 0 : g + s + b);

    // Wer liefert an wen: Sonne zuerst ans Haus, dann in den Speicher, Rest ins Netz
    const chg = Math.max(0, -b), dis = Math.max(0, b), exp = Math.max(0, -g);
    const s2h = Math.min(s, h); let rs = s - s2h;
    const s2b = Math.min(rs, chg); rs -= s2b;
    const s2g = Math.min(rs, exp);
    const b2h = Math.min(dis, h - s2h);
    const g2h = Math.max(0, h - s2h - b2h), g2b = Math.max(0, chg - s2b);
    const own = h > 0 ? Math.max(0, Math.min(100, ((s2h + b2h) / h) * 100)) : 100;
    const T = 15;

    let state;
    if (s2g > T || exp > T) state = { key: "ok", label: "Speist ein", icon: "mdi:transmission-tower-export" };
    else if (g2h + g2b <= T) state = { key: "ok", label: "Autark", icon: "mdi:check-circle" };
    else if (own >= 50) state = { key: "info", label: "Teilweise Netzstrom", icon: "mdi:transmission-tower-import" };
    else state = { key: "low", label: "Bezieht Netzstrom", icon: "mdi:transmission-tower-import" };

    // Ein Satz, der sagt, was gerade passiert
    let say;
    if (s2h + s2b + s2g > T) {
      const p = [];
      if (s2h > T) p.push(s2h >= h - T ? "deckt das ganze Haus" : `deckt ${fmt((s2h / h) * 100)} % vom Haus`);
      if (s2b > T) p.push("lädt den Speicher");
      if (s2g > T) p.push(`speist ${fmtW(s2g)} ein`);
      const last = p.pop(); say = "Die Sonne " + (p.length ? p.join(", ") + " und " : "") + last;
      if (g2h > T) say += `, ${fmtW(g2h)} kommen aus dem Netz`;
    } else if (b2h > T) say = "Der Speicher versorgt das Haus" + (g2h > T ? `, ${fmtW(g2h)} kommen aus dem Netz` : "");
    else if (g2b > T) say = "Der Speicher lädt aus dem Netz";
    else say = h > T ? `Das Haus bezieht ${fmtW(g2h)} aus dem Netz` : "Gerade fließt kaum Strom";

    // Grafik
    const P = EG, O = [180, 148];
    const curve = (a, z) => {
      const [x1, y1] = P[a], [x2, y2] = P[z];
      if (x1 === x2 || y1 === y2) return `M${x1} ${y1} L${x2} ${y2}`;
      const mx = (x1 + x2) / 2, my = (y1 + y2) / 2, dx = mx - O[0], dy = my - O[1], l = Math.hypot(dx, dy) || 1;
      return `M${x1} ${y1} Q${(mx + (dx / l) * 18).toFixed(1)} ${(my + (dy / l) * 18).toFixed(1)} ${x2} ${y2}`;
    };
    const routes = [
      ["solar", "home", s2h, "solar", hasS], ["solar", "batt", s2b, "solar", hasS && hasB], ["solar", "grid", s2g, "solar", hasS],
      ["batt", "home", b2h, "batt", hasB], ["grid", "home", g2h, "grid", true], ["grid", "batt", g2b, "grid", hasB],
    ].filter((r) => r[4]);
    const lines = routes.map(([a, z, w, col]) => {
      const d = curve(a, z);
      if (w <= T) return `<path d="${d}" class="base"/>`;
      const sw = (3 + Math.min(3, w / 1500)).toFixed(1), dur = Math.max(0.28, Math.min(1.4, 1.7 - Math.log10(w) * 0.35)).toFixed(2);
      return `<path d="${d}" class="lane" style="stroke:${EC[col]}"/><path d="${d}" class="run" style="stroke:${EC[col]};stroke-width:${sw};animation-duration:${dur}s"/>`;
    }).join("");

    const CIRC = 2 * Math.PI * P.R;
    const ring = (key, segs) => {
      const [x, y] = P[key]; let acc = 0;
      return `<circle cx="${x}" cy="${y}" r="${P.R}" class="disc"/><circle cx="${x}" cy="${y}" r="${P.R}" class="track"/>` +
        segs.filter((sg) => sg[0] > 0.004).map(([f, col]) => {
          const len = Math.min(1, f) * CIRC, el = `<circle cx="${x}" cy="${y}" r="${P.R}" class="seg" style="stroke:${col};stroke-dasharray:${len.toFixed(1)} ${CIRC.toFixed(1)};stroke-dashoffset:${(-acc).toFixed(1)}" transform="rotate(-90 ${x} ${y})"/>`;
          acc += len; return el;
        }).join("");
    };
    const val = (key, v) => `<text x="${P[key][0]}" y="${P[key][1] + 16}" class="nv">${v}</text>`;
    const lab = (key, t, below = true) => `<text x="${P[key][0]}" y="${below ? P[key][1] + P.R + 15 : P[key][1] - P.R - 8}" class="nl">${t}</text>`;
    const ico = (key, icon, col, entity) => {
      const [x, y] = P[key];
      return `<div class="hit" style="left:${(x / P.W) * 100}%;top:${(y / P.H) * 100}%;color:${col}"${entity ? ` data-entity="${entity}"` : ""}><ha-icon icon="${icon}"></ha-icon></div>`;
    };
    const battIcon = soc !== null ? (soc >= 95 ? "mdi:battery" : `mdi:battery-${Math.max(10, Math.round(soc / 10) * 10)}`) : "mdi:home-battery-outline";
    const gridActive = g2h + g2b + s2g > T;
    const td = this._today;

    const svg = `<svg viewBox="0 0 ${P.W} ${P.H}" aria-hidden="true">${lines}
      ${hasS ? ring("solar", [[c.solar_peak > 0 ? s / c.solar_peak : s > T ? 1 : 0, EC.solar]]) + val("solar", fmtW(s)) + lab("solar", "Solar", false) : ""}
      ${ring("grid", [[gridActive ? 1 : 0, EC.grid]])}${val("grid", fmtW(Math.abs(g)))}${lab("grid", g < -T ? "Einspeisung" : "Netz")}
      ${ring("home", [[h ? s2h / h : 0, EC.solar], [h ? b2h / h : 0, EC.batt], [h ? g2h / h : 0, EC.grid]])}${val("home", fmtW(h))}${lab("home", "Haus")}
      ${hasB ? ring("batt", [[soc !== null ? soc / 100 : 0, EC.batt]]) + val("batt", soc !== null ? `${fmt(soc)} %` : fmtW(Math.abs(b))) +
        lab("batt", b < -T ? `lädt · ${fmtW(chg)}` : b > T ? `entlädt · ${fmtW(dis)}` : "Speicher") : ""}
    </svg>`;

    const tiles = !c.show_today ? "" : `<div class="today"><div class="tt">Heute</div><div class="tg">
      ${hasS ? `<div class="t"><div class="tv">${td ? fmt(td.pv, 1) : "–"}<span>kWh</span></div><div class="tl"><i style="background:${EC.solar}"></i>Solar</div></div>` : ""}
      <div class="t"><div class="tv">${td && td.aut !== null ? fmt(td.aut) : "–"}<span>%</span></div><div class="tl">Autarkie</div></div>
      <div class="t"><div class="tv">${td ? fmt(td.imp, 1) : "–"}<span>kWh</span></div><div class="tl"><i style="background:${EC.grid}"></i>Netzbezug</div></div>
      ${hasS ? `<div class="t"><div class="tv">${td ? fmt(td.saved, 2) : "–"}<span>€</span></div><div class="tl">gespart</div></div>`
        : `<div class="t"><div class="tv">${td ? fmt(td.home, 1) : "–"}<span>kWh</span></div><div class="tl">Verbrauch</div></div>`}
    </div></div>`;

    this._shell(state.key, `
      ${this._head(c.name, state)}
      <div class="hero"><span class="num">${fmt(own)}</span><span class="u">% eigener Strom</span></div>
      <div class="say">${say}.</div>
      <div class="flow">${svg}
        ${hasS ? ico("solar", "mdi:solar-power-variant", EC.solar, c.solar) : ""}
        ${ico("grid", g < -T ? "mdi:transmission-tower-export" : "mdi:transmission-tower", EC.grid, c.grid)}
        ${ico("home", "mdi:home-lightning-bolt-outline", EC.home, c.home || null)}
        ${hasB ? ico("batt", battIcon, EC.batt, c.battery_soc || c.battery) : ""}
      </div>${tiles}`, `
      .hero { display: flex; align-items: baseline; gap: 6px; margin-top: 6px; color: var(--primary-text-color); }
      .say { font-size: 13px; line-height: 1.35; color: var(--secondary-text-color); margin-top: 4px; min-height: 2.7em; }
      .flow { position: relative; aspect-ratio: ${P.W} / ${P.H}; max-width: 400px; margin: 4px auto 0; }
      .flow > svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
      .base { fill: none; stroke: var(--divider-color, rgba(127,127,127,.25)); stroke-width: 1.5; }
      .lane { fill: none; stroke-width: 2; opacity: .28; }
      .run { fill: none; stroke-linecap: round; stroke-dasharray: 0 14; animation: mcrun linear infinite; }
      @keyframes mcrun { to { stroke-dashoffset: -14; } }
      .disc { fill: var(--ha-card-background, var(--card-background-color, #fff)); }
      .track { fill: none; stroke: var(--divider-color, rgba(127,127,127,.22)); stroke-width: 3; }
      .seg { fill: none; stroke-width: 3; }
      .nv { font-size: 12.5px; font-weight: 600; fill: var(--primary-text-color); text-anchor: middle; font-variant-numeric: tabular-nums; }
      .nl { font-size: 11px; fill: var(--secondary-text-color); text-anchor: middle; }
      .hit { position: absolute; width: ${((2 * P.R) / P.W) * 100}%; aspect-ratio: 1; transform: translate(-50%, -50%); border-radius: 50%;
        display: grid; place-items: center; cursor: pointer; }
      .hit:not([data-entity]) { cursor: default; }
      .hit ha-icon { --mdc-icon-size: 21px; width: 21px; height: 21px; transform: translateY(-9px); }
      .today { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--divider-color, rgba(127,127,127,.2)); }
      .tt { font-size: 11px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--secondary-text-color); }
      .tg { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin-top: 6px; }
      .t .tv { font-size: 18px; font-weight: 600; color: var(--primary-text-color); font-variant-numeric: tabular-nums; white-space: nowrap; }
      .t .tv span { font-size: 11px; font-weight: 400; color: var(--secondary-text-color); margin-left: 2px; }
      .t .tl { display: flex; align-items: center; gap: 4px; font-size: 11px; color: var(--secondary-text-color); }
      .t .tl i { width: 7px; height: 7px; border-radius: 50%; }`);
  }
}

/* ================= 3 · Raumklima ================= */
class MangfallClimateCard extends MangfallBase {
  static defaults = { name: "Raumklima", rooms: [], humid_high: 65, humid_low: 35 };
  static getStubConfig(hass) {
    const t = Object.values(hass.states).filter((e) => e.attributes.device_class === "temperature" && e.entity_id.startsWith("sensor.")).slice(0, 3);
    return { rooms: t.map((e) => ({ name: e.attributes.friendly_name || e.entity_id, temperature: e.entity_id })) };
  }
  _check(c) { if (!Array.isArray(c.rooms) || !c.rooms.length) throw new Error("Bitte Räume angeben (rooms: - name, temperature, humidity)."); }
  _watch() { return this._config.rooms.flatMap((r) => [r.temperature, r.humidity]).filter(Boolean); }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, S = this._hass.states;
    let wet = 0, dry = 0;
    const rows = c.rooms.map((r) => {
      const t = parseFloat(S[r.temperature]?.state), h = r.humidity ? parseFloat(S[r.humidity]?.state) : NaN;
      let cls = "d-ok", note = "angenehm";
      if (!isNaN(h) && h >= c.humid_high) { cls = "d-crit"; note = "zu feucht – lüften"; wet++; }
      else if (!isNaN(h) && h <= c.humid_low) { cls = "d-low"; note = "trocken"; dry++; }
      else if (!isNaN(t) && (t < 18 || t > 26)) { cls = "d-low"; note = t < 18 ? "kühl" : "warm"; }
      return `<div class="row" data-entity="${r.temperature}"><span class="dot ${cls}"></span>
        <div class="rn">${r.name}<div class="rs">${note}</div></div>
        ${!isNaN(h) ? `<div class="rh"><ha-icon icon="mdi:water-percent"></ha-icon>${fmt(h)} %</div>` : ""}
        <div class="rv">${isNaN(t) ? "–" : fmt(t, 1) + " °C"}</div></div>`;
    }).join("");
    const temps = c.rooms.map((r) => parseFloat(S[r.temperature]?.state)).filter((v) => !isNaN(v));
    const avg = temps.length ? temps.reduce((a, b) => a + b, 0) / temps.length : NaN;
    const state = wet ? { key: "crit", label: `${wet} ${wet === 1 ? "Raum" : "Räume"} zu feucht`, icon: "mdi:water-alert" }
      : dry ? { key: "low", label: "Luft trocken", icon: "mdi:water-off-outline" }
      : { key: "ok", label: "Alles im grünen Bereich", icon: "mdi:check-circle" };
    this._shell(state.key, `
      ${this._head(c.name, state)}
      <div class="big" style="margin-top:6px"><span class="num">${isNaN(avg) ? "–" : fmt(avg, 1)}</span><span class="u">°C im Schnitt</span></div>
      <div class="rows">${rows}</div>`, `
      .rh { display: inline-flex; align-items: center; gap: 2px; font-size: 13px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .rh ha-icon { --mdc-icon-size: 15px; }`);
  }
}

/* ================= 4 · Batterien ================= */
class MangfallBatteryCard extends MangfallBase {
  static defaults = { name: "Batterien", low: 25, critical: 10, show: 6, exclude: [] };
  static getStubConfig() { return {}; }
  static getConfigForm() {
    return { schema: [
      { name: "name", selector: { text: {} } },
      { type: "grid", name: "", schema: [
        { name: "low", selector: { number: { min: 1, max: 100, unit_of_measurement: "%", mode: "box" } } },
        { name: "show", selector: { number: { min: 1, max: 30, mode: "box" } } }] },
      { name: "exclude", selector: { entity: { multiple: true } } },
      { name: "show_branding", selector: { boolean: {} } }],
      computeLabel: (s) => ({ name: "Name", low: "Schwach ab", show: "Anzahl anzeigen", exclude: "Ausblenden", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]) };
  }
  _list() {
    return Object.values(this._hass.states).filter((e) => e.attributes.device_class === "battery" && e.entity_id.startsWith("sensor.")
      && !this._config.exclude.includes(e.entity_id) && !isNaN(parseFloat(e.state)))
      .map((e) => ({ id: e.entity_id, name: (e.attributes.friendly_name || e.entity_id).replace(/\s*(Batterie|Battery|Akku)\s*$/i, ""), v: parseFloat(e.state) }))
      .sort((a, b) => a.v - b.v);
  }
  _watch() { return this._hass ? this._list().map((b) => b.id) : []; }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, all = this._list();
    const weak = all.filter((b) => b.v <= c.low).length, crit = all.filter((b) => b.v <= c.critical).length;
    const state = crit ? { key: "crit", label: `${crit} fast leer`, icon: "mdi:battery-alert-variant-outline" }
      : weak ? { key: "low", label: `${weak} schwach`, icon: "mdi:battery-low" }
      : { key: "ok", label: "Alle voll genug", icon: "mdi:check-circle" };
    const rows = all.slice(0, c.show).map((b) => {
      const cls = b.v <= c.critical ? "crit" : b.v <= c.low ? "low" : "ok";
      return `<div class="row" data-entity="${b.id}"><div class="rn">${b.name}</div>
        <div class="bar"><div class="bf b-${cls}" style="width:${Math.max(3, Math.min(100, b.v))}%"></div></div>
        <div class="rv">${fmt(b.v)} %</div></div>`;
    }).join("");
    this._shell(state.key, `
      ${this._head(c.name, state)}
      <div class="big" style="margin-top:6px"><span class="num">${all.length}</span><span class="u">Geräte überwacht</span></div>
      <div class="rows">${rows || `<div class="muted pad">Keine Batteriesensoren gefunden.</div>`}</div>
      ${all.length > c.show ? `<div class="muted more">+ ${all.length - c.show} weitere über ${fmt(all[c.show - 1].v)} %</div>` : ""}`, `
      .row .rn { flex: 0 1 45%; }
      .bar { flex: 1; height: 6px; border-radius: 3px; background: var(--secondary-background-color, rgba(127,127,127,.12)); overflow: hidden; }
      .bf { height: 100%; border-radius: 3px; }
      .b-ok { background: var(--success-color, #0ca30c); } .b-low { background: var(--warning-color, #eda100); } .b-crit { background: var(--error-color, #e34948); }
      .row .rv { width: 44px; text-align: right; font-size: 13px; }
      .more { font-size: 12px; }`);
  }
}

/* ================= 5 · Fenster & Türen ================= */
const OPEN_CLASSES = ["door", "window", "garage_door", "opening"];
class MangfallOpeningsCard extends MangfallBase {
  static defaults = { name: "Fenster & Türen", entities: null, exclude: [] };
  static getStubConfig() { return {}; }
  _list() {
    const ids = this._config.entities || Object.values(this._hass.states)
      .filter((e) => e.entity_id.startsWith("binary_sensor.") && OPEN_CLASSES.includes(e.attributes.device_class)).map((e) => e.entity_id);
    return ids.filter((id) => !this._config.exclude.includes(id) && this._hass.states[id]).map((id) => this._hass.states[id]);
  }
  _watch() { return this._hass ? this._list().map((e) => e.entity_id) : []; }
  _render() {
    if (!this._config || !this._hass) return;
    const all = this._list(), open = all.filter((e) => e.state === "on").sort((a, b) => new Date(a.last_changed) - new Date(b.last_changed));
    const longOpen = open.filter((e) => Date.now() - new Date(e.last_changed) > 30 * 60000).length;
    const state = !open.length ? { key: "ok", label: "Alles zu", icon: "mdi:check-circle" }
      : longOpen ? { key: "crit", label: `${longOpen} länger als 30 min offen`, icon: "mdi:alert" }
      : { key: "low", label: `${open.length} offen`, icon: "mdi:door-open" };
    const icon = (e) => ({ door: "mdi:door-open", window: "mdi:window-open-variant", garage_door: "mdi:garage-open" }[e.attributes.device_class] || "mdi:square-rounded-outline");
    const rows = open.map((e) => `<div class="row" data-entity="${e.entity_id}"><ha-icon class="ri" icon="${icon(e)}"></ha-icon>
      <div class="rn">${e.attributes.friendly_name || e.entity_id}</div><div class="rs">${ago(e.last_changed)}</div></div>`).join("");
    this._shell(state.key, `
      ${this._head(this._config.name, state)}
      <div class="big" style="margin-top:6px"><span class="num">${open.length}</span><span class="u">von ${all.length} offen</span></div>
      ${open.length ? `<div class="rows">${rows}</div>` : `<div class="muted">Alle Fenster und Türen sind geschlossen.</div>`}`);
  }
}

/* ================= 6 · Mähroboter ================= */
const MOWER = {
  mowing: { label: "Mäht", key: "ok", icon: "mdi:robot-mower" },
  docked: { label: "In der Station", key: "info", icon: "mdi:home-import-outline" },
  paused: { label: "Pausiert", key: "low", icon: "mdi:pause-circle" },
  returning: { label: "Fährt zur Station", key: "info", icon: "mdi:home-export-outline" },
  error: { label: "Fehler", key: "crit", icon: "mdi:alert-octagon" },
};
class MangfallMowerCard extends MangfallBase {
  static defaults = { name: "Mähroboter", entity: "", battery: "" };
  static getStubConfig(hass) { const m = Object.keys(hass.states).find((id) => id.startsWith("lawn_mower.")); return { entity: m || "" }; }
  static getConfigForm() {
    return { schema: [
      { name: "entity", required: true, selector: { entity: { domain: "lawn_mower" } } },
      { name: "battery", selector: { entity: { domain: "sensor" } } },
      { name: "name", selector: { text: {} } },
      { name: "show_branding", selector: { boolean: {} } }],
      computeLabel: (s) => ({ entity: "Mähroboter", battery: "Akku-Sensor (optional)", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]) };
  }
  _check(c) { if (!c.entity) throw new Error("Bitte den Mähroboter angeben (entity: lawn_mower.…)."); }
  _watch() { return [this._config.entity, this._config.battery].filter(Boolean); }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, st = this._hass.states[c.entity];
    if (!st) return this._missing(c.name, `„${c.entity}“ nicht gefunden`);
    const m = MOWER[st.state] || { label: st.state, key: "info", icon: "mdi:robot-mower-outline" };
    const bat = c.battery ? parseFloat(this._hass.states[c.battery]?.state) : parseFloat(st.attributes.battery_level);
    const mowing = st.state === "mowing";
    this._shell(m.key, `
      ${this._head(c.name, m)}
      <div class="lawn ${mowing ? "on" : ""}" data-entity="${c.entity}">
        <div class="stripes"></div><ha-icon class="bot" icon="mdi:robot-mower"></ha-icon>
      </div>
      ${this._tiles([
        { label: "Akku", value: isNaN(bat) ? "–" : fmt(bat), unit: "%", sub: isNaN(bat) ? "" : bat < 20 ? "lädt bald" : "", icon: "mdi:battery-high", entity: c.battery || null },
        { label: "Status", value: m.label.split(" ")[0], unit: "", sub: ago(st.last_changed), icon: m.icon },
      ])}
      <div class="btns">
        <button data-svc="start_mowing"><ha-icon icon="mdi:play"></ha-icon>Mähen</button>
        <button data-svc="pause"><ha-icon icon="mdi:pause"></ha-icon>Pause</button>
        <button data-svc="dock"><ha-icon icon="mdi:home-import-outline"></ha-icon>Station</button>
      </div>`, `
      .lawn { position: relative; height: 70px; margin-top: 12px; border-radius: 12px; overflow: hidden; cursor: pointer;
        background: color-mix(in srgb, #3f9d3f 22%, var(--secondary-background-color, #eee)); }
      .stripes { position: absolute; inset: 0; background: repeating-linear-gradient(90deg, transparent 0 22px, rgba(255,255,255,.12) 22px 44px); }
      .bot { position: absolute; top: 50%; left: 12%; transform: translate(-50%, -50%); --mdc-icon-size: 34px; color: var(--primary-text-color); }
      .lawn.on .bot { animation: mc-mow 9s ease-in-out infinite alternate; }
      .lawn.on .stripes { animation: mc-grass 9s linear infinite; }
      @keyframes mc-mow { from { left: 12%; } to { left: 88%; } }
      @keyframes mc-grass { from { background-position: 0 0; } to { background-position: 88px 0; } }
      .btns { display: flex; gap: 8px; margin-top: 10px; }
      .btns button { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 4px; padding: 8px 0; border: none; border-radius: 10px; cursor: pointer;
        font: inherit; font-size: 13px; font-weight: 500; color: var(--primary-text-color); background: var(--secondary-background-color, rgba(127,127,127,.1)); }
      .btns button:hover { background: color-mix(in srgb, var(--primary-color, #2a78d6) 15%, var(--secondary-background-color, #eee)); }
      .btns ha-icon { --mdc-icon-size: 17px; }`);
    this.shadowRoot.querySelectorAll("[data-svc]").forEach((b) => b.addEventListener("click", () =>
      this._hass.callService("lawn_mower", b.dataset.svc, { entity_id: c.entity })));
  }
}

/* ================= Registrierung ================= */
const CARDS = [
  ["mangfall-level-card", MangfallLevelCard, "MANGFALL · Zisterne & Tank", "Animierter Tank mit Reichweite, Verbrauch, Regen-Zulauf und Ersparnis."],
  ["mangfall-energy-card", MangfallEnergyCard, "MANGFALL · Energiefluss", "PV, Netz, Speicher und Haus mit Live-Fluss und Eigenversorgung."],
  ["mangfall-climate-card", MangfallClimateCard, "MANGFALL · Raumklima", "Temperatur und Luftfeuchte je Raum mit Lüftungs-Hinweis."],
  ["mangfall-battery-card", MangfallBatteryCard, "MANGFALL · Batterien", "Findet alle Batteriesensoren automatisch, schwächste zuerst."],
  ["mangfall-openings-card", MangfallOpeningsCard, "MANGFALL · Fenster & Türen", "Zeigt offene Fenster und Türen mit Dauer, findet Sensoren automatisch."],
  ["mangfall-mower-card", MangfallMowerCard, "MANGFALL · Mähroboter", "Status, Akku und Steuerung für jeden Mähroboter in Home Assistant."],
];
window.customCards = window.customCards || [];
for (const [tag, cls, name, description] of CARDS) {
  if (!customElements.get(tag)) customElements.define(tag, cls);
  window.customCards.push({ type: tag, name, description, preview: true, documentationURL: "https://mangfall.eu" });
}
console.info(`%c MANGFALL CARDS %c ${MC_VERSION} `, "background:#184f95;color:#fff;font-weight:700", "background:#e8eefa;color:#184f95");
