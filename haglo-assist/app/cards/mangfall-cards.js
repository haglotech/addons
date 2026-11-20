/*
 * MANGFALL Cards – Kartenbibliothek für Home Assistant
 * Karten: Zisterne (LEVEL), Energiefluss und Energieverlauf (WATT), Raumklima, Batterien, Fenster & Türen, Mähroboter,
 *         Müllabfuhr, Wetter, Anwesenheit, Lichter, Saugroboter, Strompreis, E-Auto, Heizung, Wasserwaage (FLAT),
 *         Haus auf einen Blick, Rollläden, Sicherheit, Kameras, Musik, Schnellaktionen, Verbraucher, Luftqualität, Termine, Haus-Check
 * (c) Mangfall · MIT-Lizenz
 */
const MC_VERSION = "1.5.0";

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
const fmtL = (l) => fmt(l, l > 0 && l < 10 ? 1 : 0);   // kleine Behälter: 2,2 L statt 2 L
const fmtW = (w) => (Math.abs(w) >= 1000 ? `${fmt(w / 1000, 1)} kW` : `${fmt(w)} W`);

/* Zahlen mit Einheit: unter 10 mit Nachkommastelle, Tausenderpunkt, Watt ab 1000 als kW */
const fmtNum = (v, unit = "") => {
  if (v === null || v === undefined || !isFinite(v)) return "–";
  const u = (unit || "").trim();
  if (/^w$/i.test(u)) return fmtW(v);
  return `${fmt(v, Math.abs(v) < 10 && v % 1 !== 0 ? 1 : 0)}${u ? (u === "%" || u.startsWith("°") ? (u === "%" ? " %" : u) : " " + u) : ""}`;
};
/* Gerätenamen ohne technische Anhängsel */
const CLEAN_RE = /(?:^|[\s_-]+)(battery\s*level|battery|batterie(stand)?|akku(stand)?|ladestand|temperature|temperatur|humidity|luftfeuchtigkeit|luftfeuchte|power|leistung|energy|contact|kontakt|opening|sensor)\s*$/i;
const cleanName = (s) => String(s || "").replace(CLEAN_RE, "").replace(CLEAN_RE, "").trim() || String(s || "");
const STATE_DE = { on: "an", off: "aus", home: "zu Hause", not_home: "unterwegs", open: "offen", closed: "zu", unavailable: "offline", unknown: "unbekannt",
  cleaning: "saugt", docked: "in der Station", mowing: "mäht", paused: "pausiert", returning: "fährt zurück", idle: "bereit", error: "Fehler",
  heat: "Heizen", cool: "Kühlen", auto: "Automatik", heat_cool: "Automatik", locked: "verriegelt", unlocked: "offen", playing: "spielt" };
const stateDe = (s) => STATE_DE[s] || WX?.[s]?.[0] || s || "–";
const ATTR_DE = { temperature: "Temperatur", current_temperature: "Temperatur", humidity: "Luftfeuchte", wind_speed: "Wind", apparent_temperature: "Gefühlt",
  cloud_coverage: "Wolken", battery_level: "Akku" };
/* ================= Einrichten ohne Fachwissen =================
 * Jede Karte sucht sich ihre Sensoren selbst: einfach hinzufügen, und sie zeigt Werte. Was der Kunde im Editor
 * auswählt, hat immer Vorrang; im Editor stehen nur Sensoren der passenden Art zur Auswahl. */
const PHONE_RE = /iphone|ipad|phone|handy|smartphone|pixel|galaxy|watch|tablet|macbook|laptop|notebook|kindle|airpods/;
const lcName = (e) => (e.entity_id + " " + (e.attributes.friendly_name || "")).toLowerCase();
const isLive = (e) => !!e && !["unavailable", "unknown"].includes(e.state);
const isPower = (e) => e.entity_id.startsWith("sensor.") &&
  (e.attributes.device_class === "power" || /^[km]?w$/i.test(e.attributes.unit_of_measurement || ""));
const unitOf = (e) => (e?.attributes.unit_of_measurement || "").trim().toLowerCase();
/* Bester Treffer nach Punkten; unter `min` Punkten lieber gar keiner als ein falscher */
function pick(hass, filter, score, min = 3, not = []) {
  let best = "", top = min - 0.001;
  for (const e of Object.values(hass?.states || {})) {
    if (not.includes(e.entity_id) || !filter(e)) continue;
    const s = score(lcName(e), e) + (isLive(e) ? 0.5 : 0);
    if (s > top) { top = s; best = e.entity_id; }
  }
  return best;
}
const has = (re, n) => (re.test(n) ? 1 : 0);
const NOT_POWER = /inverter|wechselrichter|transmitted|ct_|automatic|size|limit|setting|einstellung|ziel|target|_max|_min|\bmax\b|\bmin\b|threshold|schwelle/;
const NOT_GRID = /solar|\bpv\b|_pv|pv_|batter|speicher|akku|wechselrichter|inverter|steckdose|plug|socket|kaffee|tasmota|heizung|w(ä|ae)rmepumpe|wallbox|auto\b/;
const FIND = {
  grid: (h) => pick(h, isPower, (n, e) => 3 * has(/netz|grid|3em|em3|smart.?meter|stromz(ä|ae)hler|hausanschluss|power.?meter|z(ä|ae)hler/, n)
    + 2 * has(/netzleistung|grid.?power|net.?power|total.?power|leistung.?gesamt|gesamtleistung/, n)
    - 2 * has(/phase|_l[123]\b|_[abc]_|einspeis|export|feed|bezug|import|consum/, n)
    - 6 * has(NOT_GRID, n) - 6 * has(NOT_POWER, n) + (parseFloat(e.state) < 0 ? 1 : 0)),
  grid_import: (h) => pick(h, isPower, (n) => 2 * has(/netz|grid|3em|em3|z(ä|ae)hler|meter/, n) + 2 * has(/bezug|import|consum/, n)
    - 2 * has(/phase|_l[123]\b|_[abc]_|gesamt_kwh|total_kwh/, n) - 6 * has(NOT_GRID, n), 4),
  grid_export: (h) => pick(h, isPower, (n) => 2 * has(/netz|grid|3em|em3|z(ä|ae)hler|meter/, n) + 2 * has(/einspeis|export|feed/, n)
    - 2 * has(/phase|_l[123]\b|_[abc]_/, n) - 6 * has(NOT_GRID, n), 4),
  solar: (h) => pick(h, isPower, (n) => 3 * has(/\bpv\b|_pv|pv_|solar|photovolt|wechselrichter|inverter|mppt|erzeugung|production|input.?power/, n)
    - 4 * has(/netz|grid|einspeis|export|forecast|prognose|tomorrow|morgen/, n)),
  battery: (h) => pick(h, isPower, (n) => 3 * has(/batter|speicher|akku/, n) - 4 * has(/input|output|lad|charg|entlad|discharg|solar|pv|daily|heute/, n) - 6 * has(NOT_POWER, n)),
  battery_charge: (h) => pick(h, isPower, (n) => 2 * has(/batter|speicher|akku/, n) + 2 * has(/input|ladeleistung|charg/, n) - 4 * has(/discharg|entlad|output|daily|heute/, n) - 6 * has(NOT_POWER, n) + has(/total|gesamt|summe/, n) - has(/_[1-4]_|\b[1-4]\b/, n), 4),
  battery_discharge: (h) => pick(h, isPower, (n) => 2 * has(/batter|speicher|akku/, n) + 2 * has(/output|entlad|discharg/, n) - 4 * has(/daily|heute/, n) - 6 * has(NOT_POWER, n) + has(/total|gesamt|summe/, n) - has(/_[1-4]_|\b[1-4]\b/, n), 4),
  battery_soc: (h) => pick(h, (e) => e.entity_id.startsWith("sensor.") && e.attributes.device_class === "battery" && unitOf(e) === "%",
    (n) => 3 * has(/speicher|batteriespeicher|home.?battery|hausbatterie|marstek|zendure|anker|solix|ecoflow|byd|sonnen|growatt|huawei|victron|fronius|\bsoc\b|_soc|state.?of.?charge|ladestand/, n)
    + has(/\bsoc\b|_soc|ladestand/, n) - 9 * has(PHONE_RE, n)),
  home: (h) => pick(h, isPower, (n) => 3 * has(/hausverbrauch|house|home.?(power|consumption|load)|verbrauch.?haus|gesamtverbrauch|load.?power/, n)),
  level: (h) => pick(h, (e) => e.entity_id.startsWith("sensor.") && e.attributes.device_class !== "battery" && !PHONE_RE.test(lcName(e)),
    (n, e) => 3 * has(/zisterne|cistern|regenwasser|regentonne|tank|heiz(ö|oe)l|(ö|oe)ltank/, n)
      + (["l", "liter", "m³", "m3"].includes(unitOf(e)) || e.attributes.device_class === "volume_storage" ? 4 : unitOf(e) === "%" ? 2 : 0)
      + has(/f(ü|ue|u)llstand|f(ü|ue|u)llgrad|inhalt|fill|level|pegel|wasserstand/, n) + 2 * has(/mangfall/, n)
      - 9 * has(/batter|akku|signal|rssi|temperatur|druck|chip|wlan|wifi|durchfluss|flow|(ä|a)nderung|f(ü|u)llzeit|fehler/, n)),
};
/* Zu einer Entität gehörige Geräte- und Raum-Angaben (Home Assistant liefert die Register mit, ältere Versionen nicht) */
const regOf = (hass, id) => hass?.entities?.[id] || {};
const areaOf = (hass, id) => { const r = regOf(hass, id); return r.area_id || hass?.devices?.[r.device_id]?.area_id || null; };
const sameDevice = (hass, id, filter) => {
  const d = regOf(hass, id).device_id;
  return d ? Object.values(hass.states).find((e) => e.entity_id !== id && regOf(hass, e.entity_id).device_id === d && filter(e)) : null;
};
const sensorSel = (device_class, extra = {}) => ({ entity: { filter: device_class ? { domain: "sensor", device_class } : { domain: "sensor" }, ...extra } });

/* ================= Basis für alle Karten ================= */
class MangfallBase extends HTMLElement {
  static product = "";          // erscheint neben dem M, z. B. LEVEL oder WATT
  // Nie ein roter Fehlerkasten: fehlt etwas, sucht die Karte selbst und erklärt sonst in einem Satz, was zu tun ist.
  setConfig(config) {
    this._user = { show_branding: true, ...this.constructor.defaults, ...config };
    this._config = this._user;
    this._check?.(this._config);
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._lastKey = null; this._autoAt = 0;
  }
  set hass(hass) {
    this._hass = hass;
    if (this._auto && (!this._autoAt || Date.now() - this._autoAt > 60000)) {   // Sensoren selbst suchen, einmal pro Minute
      this._autoAt = Date.now();
      const next = this._auto({ ...this._user }, hass);
      if (JSON.stringify(next) !== JSON.stringify(this._config)) { this._config = next; this._lastKey = null; }
    }
    const key = this._watch().map((id) => {
      const s = hass.states[id];
      return s ? s.state + s.last_updated : "-";
    }).join("|");
    if (key !== this._lastKey) { this._lastKey = key; this._render(); }
    this._onHass?.();
    if (this._sheet?.startsWith?.("e:")) this._loadRange(this._rng);
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
      <ha-card class="st-${stateKey}${this._config.show_branding ? " branded" : ""}">${body}${this._sheet && this._hass ? (this._sheet.startsWith("e:") ? this._entSheet() : this._extraSheet?.(this._sheet) || "") : ""}${brand}</ha-card>`;
    // Alles Antippbare: Detailansicht (data-entity) oder eigenes Verlaufsfeld (data-sheet), auch per Tastatur
    this.shadowRoot.querySelectorAll("[data-entity], [data-sheet]").forEach((el) => {
      const go = () => (el.dataset.sheet ? this._toggleSheet(el.dataset.sheet) : el.classList.contains("shd") ? this._moreInfo(el.dataset.entity) : this._toggleSheet("e:" + el.dataset.entity));
      if (!/^(BUTTON|A)$/.test(el.tagName)) { el.setAttribute("role", "button"); el.setAttribute("tabindex", "0"); }
      el.addEventListener("click", (ev) => { ev.stopPropagation(); go(); });
      el.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); } });
    });
    this.shadowRoot.querySelectorAll("[data-more]").forEach((el) => {
      const go = () => this._moreInfo(el.dataset.more);
      el.addEventListener("click", (ev) => { ev.stopPropagation(); go(); });
      el.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); } });
    });
    this.shadowRoot.querySelector(".shc[data-close]")?.addEventListener("click", () => this._toggleSheet(this._sheet));
    this.shadowRoot.querySelectorAll("[data-sheet], [data-entity]:not(.shd)").forEach((el) => el.classList.toggle("sel", !!this._sheet && (el.dataset.sheet === this._sheet || "e:" + el.dataset.entity === this._sheet)));
    this._sheetAnim = false;
    this.shadowRoot.querySelectorAll("[data-rng]").forEach((b) => b.addEventListener("click", () => { this._rng = b.dataset.rng; this._sheetAnim = false; this._render(); this._loadRange?.(b.dataset.rng); }));
  }
  /* Eingebautes Verlaufsfeld für jede Entität (Schlüssel „e:<entity>“ oder „e:<entity>|<attribut>“):
   * Zahlen als feine Linie mit Höchst-, Tiefst- und Mittelwert, Zustände als Zeitleiste. Abfrage höchstens alle 5 min. */
  _toggleSheet(k) {
    this._sheet = this._sheet === k ? null : k; this._sheetAnim = !!this._sheet; this._rng = this._rng || "24h";
    this._lastKey = null; this._render();
    if (this._sheet) this._loadRange(this._rng);
  }
  async _loadRange(r) {
    if (!this._sheet?.startsWith("e:") || !this._hass?.callWS) return;
    const [id, attr] = this._sheet.slice(2).split("|"), key = this._sheet + r, hit = (this._gc ||= {})[key];
    if ((hit && Date.now() - hit.at < 3e5) || this._gLoading === key) return;
    this._gLoading = key;
    const end = new Date(), start = new Date(+end - LV_RANGES[r][1] * 864e5), stNow = this._hass.states[id];
    const numeric = attr ? true : isFinite(parseFloat(stNow?.state)) && !["timestamp", "date", "enum"].includes(stNow?.attributes.device_class);
    let pts = [];
    try {
      if (r === "30d" && numeric && !attr && stNow?.attributes.state_class === "measurement") {
        const res = await this._hass.callWS({ type: "recorder/statistics_during_period", start_time: start.toISOString(), end_time: end.toISOString(), statistic_ids: [id], period: "hour", types: ["mean"] });
        pts = (res?.[id] || []).map((x) => ({ t: toMs(x.start), v: x.mean })).filter((p) => isFinite(p.v));
      }
      if (pts.length < 2) {
        const res = await this._hass.callWS({ type: "history/history_during_period", start_time: start.toISOString(), end_time: end.toISOString(), entity_ids: [id],
          minimal_response: !attr, no_attributes: !attr, significant_changes_only: false });
        pts = (res?.[id] || []).map((x) => {
          const t = x.lu ? x.lu * 1000 : toMs(x.last_updated || x.last_changed), s = attr ? (x.a || x.attributes || {})[attr] : x.s ?? x.state;
          return numeric ? { t, v: parseFloat(s) } : { t, s: String(s) };
        }).filter((p) => isFinite(p.t) && (numeric ? isFinite(p.v) : true));
      }
      this._gc[key] = { at: Date.now(), pts, numeric };
    } catch (e) { this._gc[key] = { at: Date.now() - 3e5 + 3e4, pts: [], numeric, err: true }; }
    this._gLoading = null; this._lastKey = null; this._render();
  }
  _entSheet() {
    const [id, attr] = this._sheet.slice(2).split("|"), st = this._hass.states[id], r = this._rng || "24h", d = this._gc?.[this._sheet + r];
    const now = Date.now(), t0 = now - LV_RANGES[r][1] * 864e5, W = 300, H = 60;
    const title = esc(cleanName(fname(st)) || id) + (attr ? ` · ${ATTR_DE[attr] || attr}` : "");
    const unit = attr ? (attr.includes("temp") ? (st?.attributes.temperature_unit || "°C") : attr === "wind_speed" ? st?.attributes.wind_speed_unit || "km/h" : ["humidity", "cloud_coverage", "battery_level"].includes(attr) ? "%" : "")
      : st?.attributes.unit_of_measurement || "";
    const seg = `<div class="seg sm">${Object.entries(LV_RANGES).map(([k, [l]]) => `<button data-rng="${k}" class="${k === r ? "on" : ""}">${l}</button>`).join("")}</div>`;
    let body;
    if (!d) body = `<div class="shw">Verlauf wird geladen …</div>`;
    else if (d.err) body = `<div class="shw">Verlauf nicht abrufbar.</div>`;
    else if (d.numeric) {
      const cur = attr ? parseFloat(st?.attributes[attr]) : parseFloat(st?.state);
      const pts = [...d.pts, ...(isFinite(cur) ? [{ t: now, v: cur }] : [])].filter((p) => p.t >= t0 - 864e5);
      if (pts.length < 2) body = `<div class="shw">${st && !isLive(st) ? "Das Gerät meldet sich gerade nicht." : "Noch kein Verlauf – der Sensor ist neu oder hat sich nicht verändert."}</div>`;
      else {
        const vs = pts.map((p) => p.v), hi = Math.max(...vs), lo = Math.min(...vs), avg = vs.reduce((a, b) => a + b, 0) / vs.length;
        const iMax = vs.indexOf(hi), pad = Math.max((hi - lo) * 0.12, Math.abs(hi) * 0.01, 0.1), a = lo - pad, b = hi + pad;
        const X = (t) => ((Math.max(0, t - t0) / (now - t0)) * W).toFixed(1), Y = (v) => (H - 3 - ((v - a) / (b - a)) * (H - 6)).toFixed(1);
        let p = `M ${X(pts[0].t)} ${Y(vs[0])}`;
        for (let i = 1; i < pts.length; i++) p += ` H ${X(pts[i].t)} V ${Y(vs[i])}`;
        const f = (v) => fmtNum(v, unit), flat = hi - lo < Math.max(Math.abs(hi) * 0.001, 1e-9);
        body = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path d="${p} H ${W} V ${H} H ${X(pts[0].t)} Z" class="sha"/><path d="${p} H ${W}" class="shl"/></svg>
          <div class="shx"><span>vor ${LV_RANGES[r][2]}</span><span>jetzt</span></div>
          <div class="shs">${flat ? `Seit ${hm(pts[0].t)} unverändert bei <b>${f(hi)}</b>` : `Höchstwert <b>${f(hi)}</b> ${r === "24h" ? "um " + hm(pts[iMax].t) : "am " + new Date(pts[iMax].t).toLocaleDateString("de-DE", { day: "numeric", month: "short" })} · Tiefstwert <b>${f(lo)}</b> · Ø <b>${f(avg)}</b>`}</div>`;
      }
    } else {
      const pts = d.pts.length ? d.pts : st ? [{ t: toMs(st.last_changed), s: st.state }] : [];
      if (!pts.length) body = `<div class="shw">Noch kein Verlauf vorhanden.</div>`;
      else {
        const segs = pts.map((p, i) => ({ s: p.s, a: Math.max(t0, p.t), z: i + 1 < pts.length ? pts[i + 1].t : now })).filter((x) => x.z > x.a);
        const cls = (s) => (["on", "home", "open", "cleaning", "mowing", "heat", "heating", "playing"].includes(s) ? "on" : ["unavailable", "unknown"].includes(s) ? "na" : "off");
        const rects = segs.map((x) => `<rect x="${X0(x.a)}" y="18" width="${Math.max(0.6, X0(x.z) - X0(x.a)).toFixed(1)}" height="22" class="tl ${cls(x.s)}"><title>${esc(stateDe(x.s))} · ${hm(x.a)}</title></rect>`).join("");
        function X0(t) { return +(((t - t0) / (now - t0)) * W).toFixed(1); }
        const ch = segs.length - 1;
        body = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${rects}</svg>
          <div class="shx"><span>vor ${LV_RANGES[r][2]}</span><span>jetzt</span></div>
          <div class="shs">Jetzt <b>${esc(stateDe(st?.state))}</b> ${st ? ago(st.last_changed) : ""}${ch > 0 ? ` · ${ch}-mal gewechselt` : ""}</div>`;
      }
    }
    return `<div class="sheet${this._sheetAnim ? " in" : ""}"><div class="shh"><span>${title}</span>
      <span><button class="shd" data-entity="${id}">Details</button><button class="shc" data-close aria-label="Schließen"><ha-icon icon="mdi:close"></ha-icon></button></span></div>${seg}${body}</div>`;
  }
  /* Aktion mit sichtbarer Rückmeldung („Schließt …“), verschwindet nach 5 s oder wenn der Zustand sich ändert */
  _act(key, text, run) {
    (this._fb ||= {})[key] = { text, at: Date.now() };
    this._lastKey = null; this._render();
    Promise.resolve().then(run).catch(() => { this._fb[key] = { text: "Hat nicht geklappt", at: Date.now() }; this._lastKey = null; this._render(); });
    clearTimeout(this._fbT); this._fbT = setTimeout(() => { this._lastKey = null; this._render(); }, 5100);
  }
  _fbText(key, ms = 5000) { const f = this._fb?.[key]; return f && Date.now() - f.at < ms ? f.text : ""; }
  _frame(title, body, entity = "") {
    return `<div class="sheet${this._sheetAnim ? " in" : ""}"><div class="shh"><span>${esc(title)}</span>
      <span>${entity ? `<button class="shd" data-entity="${entity}">Details</button>` : ""}<button class="shc" data-close aria-label="Schließen"><ha-icon icon="mdi:close"></ha-icon></button></span></div>${body}</div>`;
  }
  _head(title, chip) {
    return `<div class="head"><div class="title">${title}</div>
      ${chip ? `<div class="chip"><ha-icon icon="${chip.icon}"></ha-icon>${chip.label}</div>` : ""}</div>`;
  }
  _tiles(tiles) {
    return `<div class="tiles">${tiles.map((t) => `
      <div class="tile"${t.sheet ? ` data-sheet="${t.sheet}"` : t.entity ? ` data-entity="${t.entity}"` : ""}>
        <div class="tl"><ha-icon icon="${t.icon}"></ha-icon>${t.label}</div>
        <div class="tv">${t.value}<span>${t.unit || ""}</span></div>
        <div class="ts">${t.sub || ""}</div></div>`).join("")}</div>`;
  }
  _missing(title, msg) { this._shell("crit", `${this._head(title)}<div class="muted pad">${msg}</div>`); }
  _setup(title, what) {
    this._shell("info", `${this._head(title)}<div class="setup"><ha-icon icon="mdi:gesture-tap"></ha-icon>
      <div><b>Fast fertig.</b> ${what}<br>Dashboard bearbeiten, auf diese Karte tippen und den Sensor aus der Liste wählen.</div></div>`);
  }
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
  .setup { display: flex; gap: 10px; align-items: flex-start; margin-top: 12px; padding: 12px; border-radius: 12px; font-size: 13.5px; line-height: 1.45;
    color: var(--primary-text-color); background: color-mix(in srgb, var(--info-color, #2a78d6) 10%, transparent); }
  .setup ha-icon { --mdc-icon-size: 22px; color: var(--info-color, #2a78d6); flex: 0 0 auto; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(92px, 1fr)); gap: 8px; margin-top: 14px; }
  .tile { padding: 8px 10px; border-radius: 12px; background: var(--secondary-background-color, rgba(127,127,127,.08)); }
  [data-entity], [data-sheet] { cursor: pointer; }
  .tile[data-entity]:hover, .tile[data-sheet]:hover, .tile.sel { background: color-mix(in srgb, var(--primary-text-color) 7%, var(--secondary-background-color, transparent)); }
  [data-entity]:focus-visible, [data-sheet]:focus-visible { outline: 2px solid var(--primary-color, #2a78d6); outline-offset: 2px; border-radius: 8px; }
  .seg.sm { margin: 8px 0 2px; } .seg.sm button { font-size: 11.5px; padding: 3px 0; }
  .sheet { margin-top: 8px; padding: 10px 12px; border-radius: 12px; background: var(--secondary-background-color, rgba(127,127,127,.08)); }
  .sheet.in { animation: mc-sheet .28s ease-out; }
  @keyframes mc-sheet { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: none; } }
  .shh { display: flex; align-items: center; justify-content: space-between; font-size: 12.5px; font-weight: 500; color: var(--primary-text-color); }
  .shh > span { display: inline-flex; align-items: center; gap: 6px; }
  .shh i { width: 8px; height: 8px; border-radius: 50%; }
  .shd, .shc { border: none; background: none; cursor: pointer; font: inherit; font-size: 12px; color: var(--secondary-text-color); padding: 2px 6px; border-radius: 8px; }
  .shd:hover, .shc:hover { color: var(--primary-text-color); }
  .shc ha-icon { --mdc-icon-size: 16px; }
  .sheet svg { width: 100%; height: 60px; display: block; margin-top: 6px; }
  .sha { opacity: .16; } .shl { fill: none; stroke-width: 1.5; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
  .sh0 { stroke: var(--divider-color, rgba(127,127,127,.35)); stroke-dasharray: 2 3; vector-effect: non-scaling-stroke; }
  .shx { display: flex; justify-content: space-between; font-size: 10.5px; color: var(--secondary-text-color); margin-top: 2px; }
  .shs { font-size: 12.5px; color: var(--secondary-text-color); margin-top: 6px; } .shs b { font-weight: 600; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .shw { font-size: 12.5px; color: var(--secondary-text-color); padding: 18px 0; text-align: center; }
  .sheet .seg { display: flex; gap: 2px; padding: 2px; border-radius: 10px; background: color-mix(in srgb, var(--primary-text-color) 6%, transparent); }
  .sheet .seg button { flex: 1; border: none; background: none; font: inherit; border-radius: 8px; color: var(--secondary-text-color); cursor: pointer; }
  .sheet .seg button.on { background: var(--ha-card-background, var(--card-background-color, #fff)); color: var(--primary-text-color); }
  .sha { fill: var(--primary-text-color); } .shl { stroke: var(--primary-text-color); }
  .shf { fill: none; stroke: var(--mc-state); stroke-width: 1.5; stroke-dasharray: 4 3; vector-effect: non-scaling-stroke; }
  .she { fill: var(--mc-state); }
  .shbars { width: 100%; height: 72px; display: block; margin-top: 6px; }
  .shb { fill: var(--primary-text-color); opacity: .55; } .shb.dim { opacity: .2; } .shbars .stub { fill: var(--divider-color, rgba(127,127,127,.35)); }
  .sha2 { stroke: var(--mc-state); stroke-dasharray: 3 3; }
  .tl.on { fill: var(--mc-state); } .tl.off { fill: var(--secondary-text-color); opacity: .25; } .tl.na { fill: var(--secondary-text-color); opacity: .08; }
  .sel:not(.tile):not(.lp) { background: color-mix(in srgb, var(--primary-text-color) 6%, transparent); border-radius: 8px; }
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
const LV_RANGES = { "24h": ["24 h", 1, "24 h"], "7d": ["7 Tage", 7, "7 Tagen"], "30d": ["30 Tage", 30, "30 Tagen"] };
const MEDIA = { water: ["#5aa7f0", "#1c5cab"], oil: ["#f2c14e", "#b8860b"] };

/* Behälterformen: je eine eigene Zeichnung im selben Stil, Füllung nach Prozent.
 * clip = Innenraum, top/bottom = Wasserhöhe bei 100 % und 0 %, back/front = Zeichnung hinter und über dem Wasser. */
const segLevel = (f) => {   // liegender Zylinder: Füllhöhe (0..1 vom Durchmesser) aus dem Füllgrad (0..1 vom Volumen)
  let lo = 0, hi = 1;
  for (let i = 0; i < 30; i++) {
    const h = (lo + hi) / 2, x = 1 - 2 * h, a = (Math.acos(x) - x * Math.sqrt(1 - x * x)) / Math.PI;
    if (a < f) lo = h; else hi = h;
  }
  return (lo + hi) / 2;
};
const TANKS = {
  cistern: { label: "Zisterne", top: 18, bottom: 142, tx: 14,
    clip: "M10 34 Q10 14 30 14 H90 Q110 14 110 34 V132 Q110 144 98 144 H22 Q10 144 10 132 Z",
    front: `<rect x="48" y="6" width="24" height="9" rx="3" class="lid"/>` },
  underground: { label: "Erdtank", top: 46, bottom: 134, tx: 22, round: true,
    clip: "M60 46 A44 44 0 1 1 59.99 46 Z",
    back: `<line x1="0" x2="120" y1="34" y2="34" class="soil"/><rect x="46" y="20" width="28" height="30" rx="3" class="shell"/>`,
    front: `<rect x="42" y="14" width="36" height="7" rx="3" class="lid"/><circle cx="12" cy="44" r="1.2" class="grain"/><circle cx="104" cy="48" r="1.4" class="grain"/><circle cx="20" cy="136" r="1.2" class="grain"/><circle cx="102" cy="132" r="1" class="grain"/>` },
  rect: { label: "Kellertank", top: 22, bottom: 140, tx: 16,
    clip: "M14 18 H106 Q110 18 110 22 V140 Q110 144 106 144 H14 Q10 144 10 140 V22 Q10 18 14 18 Z",
    front: `<rect x="6" y="12" width="108" height="7" rx="2" class="lid"/><rect x="74" y="8" width="22" height="5" rx="1.5" class="lid"/>` },
  barrel: { label: "Regentonne", top: 22, bottom: 140, tx: 24,
    clip: "M24 16 Q12 80 24 144 H96 Q108 80 96 16 Z",
    front: `<path d="M19.5 52 Q60 58 100.5 52" class="ring"/><path d="M19.5 108 Q60 114 100.5 108" class="ring"/><rect x="18" y="9" width="84" height="8" rx="4" class="lid"/>` },
  ibc: { label: "IBC", top: 30, bottom: 128, tx: 20,
    clip: "M18 26 H102 Q106 26 106 30 V126 Q106 130 102 130 H18 Q14 130 14 126 V30 Q14 26 18 26 Z",
    front: `<g class="cage"><rect x="10" y="22" width="100" height="112" rx="3"/><path d="M30 22V134M50 22V134M70 22V134M90 22V134M10 59H110M10 96H110"/></g>
      <rect x="50" y="14" width="20" height="9" rx="2" class="lid"/><rect x="6" y="134" width="108" height="6" rx="1.5" class="lid"/>
      <rect x="12" y="140" width="14" height="6" class="lid"/><rect x="53" y="140" width="14" height="6" class="lid"/><rect x="94" y="140" width="14" height="6" class="lid"/>
      <rect x="54" y="122" width="12" height="9" rx="2" class="lid"/>` },
  oil: { label: "Heizöltank", top: 22, bottom: 138, tx: 22,
    clip: "M16 26 Q16 16 26 16 H94 Q104 16 104 26 V134 Q104 144 94 144 H26 Q16 144 16 134 Z",
    front: `<path d="M16 50 H104 M16 80 H104 M16 110 H104" class="rib"/><path d="M40 16 V144 M80 16 V144" class="rib v"/>
      <rect x="28" y="8" width="16" height="9" rx="2" class="lid"/><rect x="76" y="10" width="10" height="7" rx="2" class="lid"/>` },
};
const shapeOf = (c) => (TANKS[c.shape] ? c.shape : c.medium === "oil" ? "oil" : "cistern");
function tankSvg(shape, pct, uid = "m", still = false) {
  const T = TANKS[shape] || TANKS.cistern, { top, bottom } = T;
  const yAt = (p) => (T.round ? bottom - (bottom - top) * segLevel(Math.max(0, Math.min(1, p / 100))) : bottom - ((bottom - top) * p) / 100);
  const y = yAt(pct), amp = T.round ? 0.8 : 1;
  const wave = (a, len) => { let d = `M -120 ${y}`; for (let x = -120; x <= 240; x += len) d += ` q ${len / 4} ${-a * amp} ${len / 2} 0 t ${len / 2} 0`; return d + " V 160 H -120 Z"; };
  const ticks = [25, 50, 75].map((p) => { const ty = yAt(p);
    return `<line x1="${T.tx}" x2="${T.tx + 8}" y1="${ty}" y2="${ty}" class="tick"/><text x="${T.tx + 12}" y="${ty + 3}" class="tickl">${p}</text>`; }).join("");
  return `<svg viewBox="0 0 120 150" class="tank${still ? " still" : ""}" role="img" aria-label="Füllstand ${fmt(pct)} Prozent">
    <defs><clipPath id="mlc-clip-${uid}"><path d="${T.clip}"/></clipPath>
      <linearGradient id="mlc-water-${uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="w1"/><stop offset="1" class="w2"/></linearGradient></defs>
    ${T.back || ""}<path d="${T.clip}" class="shell"/>
    <g clip-path="url(#mlc-clip-${uid})" fill="url(#mlc-water-${uid})"><path d="${wave(3, 60)}" class="wave back"/><path d="${wave(4, 80)}" class="wave front"/>
      <circle cx="40" cy="${bottom - 8}" r="2.2" class="bub"/><circle cx="72" cy="${bottom - 4}" r="1.6" class="bub b2"/><circle cx="88" cy="${bottom - 10}" r="2.6" class="bub b3"/></g>
    ${ticks}${T.front || ""}</svg>`;
}
const TANK_CSS = `
  .shell { fill: var(--secondary-background-color, rgba(127,127,127,.08)); stroke: var(--divider-color, rgba(127,127,127,.35)); stroke-width: 2; }
  .lid { fill: var(--divider-color, rgba(127,127,127,.4)); }
  .w1 { stop-color: var(--mlc-w1); } .w2 { stop-color: var(--mlc-w2); }
  .wave.back { opacity: .45; animation: mc-drift 7s linear infinite; }
  .wave.front { opacity: .95; animation: mc-drift 4.5s linear infinite reverse; }
  @keyframes mc-drift { from { transform: translateX(0); } to { transform: translateX(60px); } }
  .bub { fill: rgba(255,255,255,.55); animation: mc-rise 5s ease-in infinite; }
  .b2 { animation-delay: 1.7s; } .b3 { animation-delay: 3.1s; }
  @keyframes mc-rise { 0% { transform: translateY(0); opacity: 0; } 15% { opacity: .8; } 100% { transform: translateY(-70px); opacity: 0; } }
  .still .wave, .still .bub { animation: none; } .still .bub { opacity: 0; }
  .tick { stroke: var(--secondary-text-color); stroke-opacity: .45; }
  .tickl { font-size: 7px; fill: var(--secondary-text-color); opacity: .7; }
  .ring { fill: none; stroke: var(--divider-color, rgba(127,127,127,.5)); stroke-width: 3; }
  .rib { fill: none; stroke: var(--primary-text-color); stroke-opacity: .12; stroke-width: 3; } .rib.v { stroke-opacity: .07; }
  .cage { fill: none; stroke: var(--secondary-text-color); stroke-opacity: .55; stroke-width: 1.6; }
  .soil { stroke: var(--secondary-text-color); stroke-opacity: .35; stroke-dasharray: 3 3; }
  .grain { fill: var(--secondary-text-color); opacity: .35; }`;

/* Eigener Editor der Zisternenkarte: Formen als antippbare Bilder, Inhalt als Schalter, der Rest wie gewohnt */
class MangfallLevelEditor extends HTMLElement {
  setConfig(config) { this._config = { ...config }; this._draw(); }
  set hass(h) { this._hass = h; if (this._form) this._form.hass = h; }
  _emit(patch) {
    this._config = { ...this._config, ...patch };
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
    this._draw();
  }
  _draw() {
    if (!this._config) return;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.innerHTML = `<style>${TANK_CSS}
        :host { display: block; --mlc-w1: #5aa7f0; --mlc-w2: #1c5cab; }
        .lbl { font-size: 13px; font-weight: 500; color: var(--secondary-text-color); margin: 4px 0 8px; }
        .shapes { display: grid; grid-template-columns: repeat(auto-fill, minmax(78px, 1fr)); gap: 8px; margin-bottom: 16px; }
        .sh { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 8px 4px 6px; border-radius: 12px; cursor: pointer; font: inherit;
          border: 2px solid transparent; background: var(--secondary-background-color, rgba(127,127,127,.08)); color: var(--primary-text-color); transition: border-color .15s; }
        .sh.on { border-color: var(--primary-color, #2a78d6); }
        .sh svg { width: 48px; height: 60px; } .sh span { font-size: 11.5px; }
        .seg { display: flex; gap: 2px; padding: 2px; margin-bottom: 16px; border-radius: 10px; background: var(--secondary-background-color, rgba(127,127,127,.1)); }
        .seg button { flex: 1; border: none; background: none; font: inherit; font-size: 13px; font-weight: 500; padding: 7px 0; border-radius: 8px; cursor: pointer; color: var(--secondary-text-color); }
        .seg button.on { background: var(--card-background-color, #fff); color: var(--primary-text-color); box-shadow: 0 1px 2px rgba(0,0,0,.14); }
        .wait { font-size: 13px; color: var(--secondary-text-color); padding: 8px 0; }</style>
        <div class="lbl">Form</div><div class="shapes"></div><div class="lbl">Inhalt</div><div class="seg"></div><div class="host"><div class="wait">Einstellungen werden geladen …</div></div>`;
      this._mountForm();
    }
    const R = this.shadowRoot, cur = shapeOf(this._config), oil = this._config.medium === "oil";
    this.style.setProperty("--mlc-w1", (oil ? MEDIA.oil : MEDIA.water)[0]); this.style.setProperty("--mlc-w2", (oil ? MEDIA.oil : MEDIA.water)[1]);
    R.querySelector(".shapes").innerHTML = Object.entries(TANKS).map(([k, t]) =>
      `<button class="sh${k === cur ? " on" : ""}" data-shape="${k}" aria-pressed="${k === cur}">${tankSvg(k, 62, "e" + k, true)}<span>${t.label}</span></button>`).join("");
    R.querySelector(".seg").innerHTML = `<button data-med="water" class="${oil ? "" : "on"}">Wasser</button><button data-med="oil" class="${oil ? "on" : ""}">Heizöl</button>`;
    R.querySelectorAll("[data-shape]").forEach((b) => b.addEventListener("click", () => this._emit({ shape: b.dataset.shape })));
    R.querySelectorAll("[data-med]").forEach((b) => b.addEventListener("click", () => this._emit({ medium: b.dataset.med })));
    if (this._form) this._form.data = this._config;
  }
  _mountForm() {
    const make = () => {
      if (this._form) return;
      const f = MangfallLevelCard.getConfigForm(), form = document.createElement("ha-form");
      form.schema = f.schema.filter((s) => s.name !== "medium"); form.computeLabel = f.computeLabel; form.computeHelper = f.computeHelper;
      form.hass = this._hass; form.data = this._config;
      form.addEventListener("value-changed", (ev) => { ev.stopPropagation(); this._emit({ ...ev.detail.value }); });
      const host = this.shadowRoot.querySelector(".host"); host.innerHTML = ""; host.appendChild(form); this._form = form;
    };
    if (customElements.get("ha-form")) return make();
    customElements.whenDefined("ha-form").then(make);
    setTimeout(() => { if (!this._form) { const w = this.shadowRoot.querySelector(".wait"); if (w) w.textContent = "Die weiteren Einstellungen erscheinen, sobald Home Assistant den Editor geladen hat. Alternativ im Code-Editor eintragen."; } }, 4000);
  }
}

class MangfallLevelCard extends MangfallBase {
  static product = "LEVEL";
  static defaults = { name: "Zisterne", entity: "", capacity: null, max_height: null, low: 25, critical: 10, days: 7, water_price: null, medium: "water", shape: "" };
  static getConfigElement() { return document.createElement("mangfall-level-card-editor"); }
  static getStubConfig(hass) { return { entity: FIND.level(hass) }; }
  static getConfigForm() {
    return {
      schema: [
        { name: "entity", selector: sensorSel() },
        { name: "capacity", selector: { number: { min: 0.5, max: 100000, step: "any", unit_of_measurement: "L", mode: "box" } } },
        { name: "medium", selector: { select: { mode: "dropdown", options: [{ value: "water", label: "Wasser" }, { value: "oil", label: "Heizöl" }] } } },
        { name: "shape", selector: { select: { mode: "dropdown", options: Object.entries(TANKS).map(([value, t]) => ({ value, label: t.label })) } } },
        { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [
          { name: "name", selector: { text: {} } },
          { name: "max_height", selector: { number: { min: 0.1, max: 10, step: 0.01, unit_of_measurement: "m", mode: "box" } } },
          { type: "grid", name: "", schema: [
            { name: "low", selector: { number: { min: 0, max: 100, unit_of_measurement: "%", mode: "box" } } },
            { name: "critical", selector: { number: { min: 0, max: 100, unit_of_measurement: "%", mode: "box" } } },
            { name: "days", selector: { number: { min: 2, max: 30, unit_of_measurement: "Tage", mode: "box" } } },
            { name: "water_price", selector: { number: { min: 0, max: 20, step: 0.01, unit_of_measurement: "€/m³", mode: "box" } } } ] },
          { name: "show_branding", selector: { boolean: {} } } ] },
      ],
      computeLabel: (s) => ({ entity: "Füllstand", name: "Name", capacity: "Fassungsvermögen", max_height: "Max. Füllhöhe",
        low: "Warnung ab", critical: "Alarm ab", days: "Verlauf", water_price: "Wasserpreis", medium: "Inhalt", shape: "Form",
        show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ entity: "Der Sensor deiner Zisterne oder deines Tanks – in Litern, Prozent oder als Höhe.",
        capacity: "Wie viele Liter passen hinein? Steht auf dem Typenschild oder in der Rechnung.",
        max_height: "Nur nötig, wenn der Sensor eine Höhe misst (m oder cm): die Höhe, wenn der Tank voll ist." }[s.name]),
    };
  }
  _check() { this._history = null; this._histFetched = 0; }
  _auto(c, hass) {
    if (!c.entity) c.entity = FIND.level(hass);
    // Fassungsvermögen nicht eingetragen: aus Litern und Prozent desselben Geräts ausrechnen, sonst 5000 L annehmen
    if (!c.capacity) {
      const st = hass.states[c.entity], u = unitOf(st);
      const other = st && sameDevice(hass, c.entity, (e) => isLive(e) && (u === "%" ? ["l", "m³", "m3"].includes(unitOf(e)) : unitOf(e) === "%"));
      if (other && isLive(st)) {
        const lit = (x) => parseFloat(x.state) * (["m³", "m3"].includes(unitOf(x)) ? 1000 : 1);
        const [l, p] = u === "%" ? [lit(other), parseFloat(st.state)] : [lit(st), parseFloat(other.state)];
        if (p > 1 && l > 0) c.capacity = Number(((l / p) * 100).toPrecision(2));   // 2 signifikante Stellen: 2,5 / 2300 / 12000
      }
      c.capacity = c.capacity || 5000; c._capGuess = !this._user.capacity && !other;
    }
    // „Jetzt messen“ und Pumpe vom selben Gerät (MANGFALL LEVEL und andere Sensoren mit Mess-Knopf)
    if (c.entity) {
      c._measure = c.measure_button || sameDevice(hass, c.entity, (e) => e.entity_id.startsWith("button.") && /jetzt.?messen|messen|measure|refresh|aktualisier/.test(lcName(e)))?.entity_id || "";
      c._pump = sameDevice(hass, c.entity, (e) => e.entity_id.startsWith("binary_sensor.") && (e.attributes.device_class === "running" || /pumpe|pump/.test(lcName(e))))?.entity_id || "";
    }
    return c;
  }
  _watch() { return [this._config.entity, this._config._pump].filter(Boolean); }
  _measureBtn() {
    const c = this._config; if (!c._measure) return "";
    const st = this._hass.states[c.entity], pump = c._pump && this._hass.states[c._pump]?.state === "on";
    const waiting = this._measAt && Date.now() - this._measAt < 60000 && !(st && toMs(st.last_updated) > this._measAt + 3000);
    const busy = pump || waiting, na = !isLive(this._hass.states[c._measure]) && this._hass.states[c._measure]?.state === "unavailable";
    return `<div class="mrow"><button class="mbtn${busy ? " busy" : ""}" data-measure ${busy || na ? "disabled" : ""}>
      <ha-icon icon="${busy ? "mdi:loading" : "mdi:gauge"}"></ha-icon>${na ? "Sensor offline" : busy ? "Misst gerade …" : "Jetzt messen"}</button>
      <span class="mwhen">${st && isLive(st) ? "gemessen " + ago(st.last_updated).replace(/^seit /, "vor ") : ""}</span></div>`;
  }
  getCardSize() { return 5; }
  _onHass() {
    if (this._config.entity && Date.now() - this._histFetched > 6e5) this._fetchHistory();
    if (this._sheet) this._loadRange(this._rng);
  }

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
      this._histErr = false;
    } catch (e) { this._history = this._history || []; this._histErr = true; this._histFetched = Date.now() - 6e5 + 3e4; }   // Fehler: in 30 s erneut
    this._fetching = false; this._lastKey = null; this._render();
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
    if (!c.entity) return this._setup(c.name, "Ich habe keinen Füllstandssensor gefunden.");
    if (!st || ["unavailable", "unknown"].includes(st.state)) return this._missing(c.name, st ? `${st.attributes.friendly_name || c.entity} meldet sich gerade nicht.` : `„${c.entity}“ gibt es nicht mehr – bitte neu wählen.`);
    if (["m", "cm", "mm"].includes(unitOf(st)) && !c.max_height) return this._setup(c.name, "Dein Sensor misst die Höhe. Trag noch ein, wie hoch das Wasser steht, wenn der Tank voll ist („Max. Füllhöhe“).");
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
        <div class="tankbox" data-sheet="l" aria-label="Verlauf der Liter">${tankSvg(shapeOf(c), pct)}</div>
        <div class="info">
          ${this._head(c.name, state)}
          <div class="big tap" style="margin-top:6px" data-sheet="l"><span class="num">${fmtL(liters)}</span><span class="u">L</span></div>
          <div class="muted tap" data-sheet="p">von ${fmtL(c.capacity)} L${c._capGuess ? " (angenommen – unter Bearbeiten eintragen)" : ""} · ${fmt(pct)} %</div>
          ${saved !== null ? `<div class="saved"><ha-icon icon="mdi:piggy-bank-outline"></ha-icon><b>${fmt(saved, 2)} €</b> gespart in 30 Tagen</div>` : ""}
        </div>
      </div>
      ${this._tiles([
        { label: "Reicht noch", value: daysLeft, unit: "Tage", sub: daysSub, icon: "mdi:calendar-clock", sheet: "f" },
        { label: "Verbrauch", value: s ? fmtL(s.perDay) : "–", unit: "L/Tag", sub: `Ø ${c.days} Tage`, icon: "mdi:water-minus", sheet: "use" },
        { label: oil ? "Nachgetankt" : "Regen-Zulauf", value: s ? (s.inflow24 > 0 ? "+" + fmtL(s.inflow24) : "0") : "–", unit: "L", sub: "letzte 24 h", icon: oil ? "mdi:gas-station" : "mdi:weather-pouring", sheet: "in" },
      ])}
      ${this._sheet ? this._sheetHtml(liters, pct, s) : this._spark()}
      ${this._measureBtn()}`, `
      .mrow { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
      .mbtn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 14px 8px 11px; border: none; border-radius: 10px; cursor: pointer;
        font: inherit; font-size: 13px; font-weight: 500; color: var(--primary-text-color); background: var(--secondary-background-color, rgba(127,127,127,.1)); transition: background .15s; }
      .mbtn:hover:not([disabled]) { background: color-mix(in srgb, var(--primary-color, #2a78d6) 15%, var(--secondary-background-color, #eee)); }
      .mbtn:focus-visible { outline: 2px solid var(--primary-color, #2a78d6); outline-offset: 2px; }
      .mbtn[disabled] { cursor: default; opacity: .75; }
      .mbtn ha-icon { --mdc-icon-size: 17px; color: var(--mlc-w2); }
      .mbtn.busy ha-icon { animation: mc-spin 1s linear infinite; }
      @keyframes mc-spin { to { transform: rotate(360deg); } }
      .mwhen { font-size: 12px; color: var(--secondary-text-color); }
      @media (prefers-reduced-motion: reduce) { .mbtn.busy ha-icon { animation: none; } }

      ha-card { --mlc-w1: var(--mlc-water-top, ${w1}); --mlc-w2: var(--mlc-water-bottom, ${w2}); }
      .wrap { display: flex; gap: 16px; align-items: center; }
      .tankbox { flex: 0 0 104px; cursor: pointer; }
      .tank { width: 104px; height: 130px; display: block; }
      .info { flex: 1; min-width: 0; }
      ${TANK_CSS}
      .saved { display: inline-flex; align-items: center; gap: 5px; margin-top: 8px; font-size: 12.5px; color: var(--primary-text-color);
        padding: 4px 10px 4px 7px; border-radius: 999px; background: color-mix(in srgb, var(--success-color, #0ca30c) 14%, transparent); }
      .saved ha-icon { --mdc-icon-size: 15px; color: var(--success-color, #0ca30c); }
      .saved b { font-weight: 600; font-variant-numeric: tabular-nums; }
      .spark { margin-top: 12px; } .spark > svg { width: 100%; height: 56px; display: block; }
      .area { fill: var(--mlc-w1); opacity: .16; }
      .line { fill: none; stroke: var(--mlc-w2); stroke-width: 2; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
      .sparkl { display: flex; justify-content: space-between; font-size: 11px; color: var(--secondary-text-color); margin-top: 2px; }
      .empty-spark { font-size: 12px; color: var(--secondary-text-color); padding: 18px 0; text-align: center; }
      .tap { border-radius: 8px; transition: background .15s; } .tap:hover, .tankbox:hover { background: color-mix(in srgb, var(--primary-text-color) 5%, transparent); }
      .tankbox { border-radius: 12px; transition: background .15s; }
      .spark { cursor: pointer; }`);
    this._sheetAnim = false;
    this.shadowRoot.querySelectorAll(".tile[data-sheet]").forEach((t) => t.classList.toggle("sel", t.dataset.sheet === this._sheet));
    this.shadowRoot.querySelector(".spark")?.addEventListener("click", () => this._toggleSheet("l"));
    this.shadowRoot.querySelector("[data-measure]")?.addEventListener("click", () => {
      this._measAt = Date.now(); this._hass.callService("button", "press", { entity_id: this._config._measure });
      this._lastKey = null; this._render(); clearTimeout(this._measT); this._measT = setTimeout(() => { this._lastKey = null; this._render(); }, 61000);
    });
  }
  /* Verlaufsfeld: jede Zahl der Karte ist antippbar und zeigt ihren Verlauf direkt in der Karte */
  _toggleSheet(k) {
    this._sheet = this._sheet === k ? null : k; this._sheetAnim = !!this._sheet;
    if (!this._rng) this._rng = this._config.days >= 30 ? "30d" : "7d";
    this._render();
    if (this._sheet) this._loadRange(this._rng);
  }
  async _loadRange(r) {
    this._rc = this._rc || {};
    const hit = this._rc[r], c = this._config;
    if ((hit && Date.now() - hit.at < 6e5) || this._rLoading === r || !this._hass?.callWS) return;
    this._rLoading = r;
    const days = LV_RANGES[r][1], end = new Date(), start = new Date(+end - days * 864e5), unit = this._hass.states[c.entity]?.attributes.unit_of_measurement;
    let pts = [];
    try {
      if (r === "30d") {   // 30 Tage: Langzeitstatistik (Stundenmittel), sonst der normale Verlauf
        const res = await this._hass.callWS({ type: "recorder/statistics_during_period", start_time: start.toISOString(), end_time: end.toISOString(),
          statistic_ids: [c.entity], period: "hour", types: ["mean"] });
        pts = (res?.[c.entity] || []).map((x) => ({ t: toMs(x.start), l: this._toLiters(x.mean, unit) })).filter((p) => p.l !== null && isFinite(p.t));
      }
      if (pts.length < 2) {
        const res = await this._hass.callWS({ type: "history/history_during_period", start_time: start.toISOString(), end_time: end.toISOString(),
          entity_ids: [c.entity], minimal_response: true, no_attributes: true });
        pts = (res?.[c.entity] || []).map((x) => ({ t: x.lu ? x.lu * 1000 : toMs(x.last_updated || x.last_changed), l: this._toLiters(x.s ?? x.state, unit) }))
          .filter((p) => p.l !== null && isFinite(p.t));
      }
      this._rc[r] = { at: Date.now(), pts };
    } catch (e) { this._rc[r] = { at: Date.now() - 6e5 + 3e4, pts: this._rc[r]?.pts || [], err: true }; }
    this._rLoading = null; this._lastKey = null; this._render();
  }
  _sheetHtml(liters, pct, s) {
    const k = this._sheet;
    if (!k) return "";
    const c = this._config, oil = c.medium === "oil", r = this._rng, d = this._rc?.[r], now = Date.now(), days = LV_RANGES[r][1];
    const titles = { l: "Füllstand", p: "Füllstand in Prozent", f: "Reicht noch", use: "Verbrauch", in: oil ? "Nachgetankt" : "Regen-Zulauf" };
    const seg = `<div class="seg sm">${Object.entries(LV_RANGES).map(([key, [l]]) => `<button data-rng="${key}" class="${key === r ? "on" : ""}">${l}</button>`).join("")}</div>`;
    let body = "";
    const pts = d ? [...d.pts, { t: now, l: liters }].filter((p) => p.t >= now - days * 864e5) : null;
    if (!d) body = `<div class="shw">Verlauf wird geladen …</div>`;
    else if (d.err && !d.pts.length) body = `<div class="shw">Verlauf nicht abrufbar.</div>`;
    else if (pts.length < 2) body = `<div class="shw">Noch kein Verlauf – erscheint, sobald der Sensor Werte liefert.</div>`;
    else if (k === "use" || k === "in") body = this._bars(pts, k, r, days);
    else {
      const W = 300, H = 72, t0 = now - days * 864e5, P = k === "p", val = (l) => (P ? (l / c.capacity) * 100 : l);
      let t1 = now, extra = "", note = "";
      if (k === "f") {
        if (!s || s.perDay < c.capacity * 0.002) note = "Kaum Verbrauch – so reicht es über 90 Tage.";
        else {
          const empty = now + (liters / s.perDay) * 864e5;
          t1 = Math.min(empty, now + 90 * 864e5);
          note = empty > now + 90 * 864e5 ? "Bei gleichem Verbrauch reicht es über 90 Tage."
            : `Bei gleichem Verbrauch leer etwa am <b>${new Date(empty).toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "short" })}</b> (in ${fmt((empty - now) / 864e5)} Tagen).`;
        }
      }
      const vs = pts.map((p) => val(p.l)), hiV = Math.max(...vs), loV = Math.min(...vs);
      let lo = k === "f" ? 0 : loV, hi = hiV;
      const pad = Math.max((hi - lo) * 0.15, (P ? 100 : c.capacity) * 0.02); if (k !== "f") lo = Math.max(0, lo - pad); hi = Math.min(P ? 100 : c.capacity, hi + pad);
      const X = (t) => (((t - t0) / Math.max(1, t1 - t0)) * W).toFixed(1), Y = (v) => (H - 4 - ((v - lo) / Math.max(1e-9, hi - lo)) * (H - 10)).toFixed(1);
      let p = `M ${X(pts[0].t)} ${Y(vs[0])}`;
      for (let i = 1; i < pts.length; i++) p += ` H ${X(pts[i].t)} V ${Y(vs[i])}`;
      if (k === "f" && t1 > now) {
        const endL = Math.max(0, liters - s.perDay * ((t1 - now) / 864e5));
        extra = `<path d="M ${X(now)} ${Y(liters)} L ${X(t1)} ${Y(endL)}" class="shf"/>${endL <= 0.5 ? `<circle cx="${X(t1)}" cy="${Y(0)}" r="3" class="she"/>` : ""}
          <line x1="${X(now)}" x2="${X(now)}" y1="4" y2="${H}" class="sh0"/>`;
      }
      const u = P ? "%" : "L", f = (v) => (P ? fmt(v) : fmtL(v));
      body = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path d="${p} V ${H} H ${X(pts[0].t)} Z" class="sha"/><path d="${p}" class="shl"/>${extra}</svg>
        <div class="shx"><span>vor ${LV_RANGES[r][2]}</span><span>${k === "f" && t1 > now ? "jetzt" : ""}</span><span>${k === "f" && t1 > now ? (t1 - now > 2 * 864e5 ? new Date(t1).toLocaleDateString("de-DE", { day: "numeric", month: "short" }) : hm(t1)) : "jetzt"}</span></div>
        <div class="shs">${note || (hiV - loV < (P ? 0.3 : c.capacity * 0.003) ? `Seit ${hm(pts[0].t)} unverändert bei <b>${f(hiV)} ${u}</b>` : `Höchststand <b>${f(hiV)} ${u}</b> · Tiefststand <b>${f(loV)} ${u}</b>`)}</div>`;
    }
    return `<div class="sheet${this._sheetAnim ? " in" : ""}"><div class="shh"><span>${titles[k]}</span>
      <span><button class="shd" data-entity="${c.entity}">Details</button><button class="shc" data-close aria-label="Schließen"><ha-icon icon="mdi:close"></ha-icon></button></span></div>${seg}${body}</div>`;
  }
  /* Säulen je Tag (bei 24 h je Stunde): Verbrauch mit Durchschnittslinie, Zulauf mit hervorgehobenen letzten 24 h */
  _bars(pts, k, r, days) {
    const c = this._config, now = Date.now(), hourly = r === "24h", n = hourly ? 24 : days, noise = c.capacity * 0.003;
    const starts = Array.from({ length: n }, (_, i) => {
      if (hourly) { const h = new Date(now); h.setMinutes(0, 0, 0); return +h - (n - 1 - i) * 36e5; }
      const d = dayStart(); return +new Date(d.getFullYear(), d.getMonth(), d.getDate() - (n - 1 - i));
    });
    const v = new Array(n).fill(0);
    for (let i = 1; i < pts.length; i++) {
      const dl = pts[i].l - pts[i - 1].l;
      if (Math.abs(dl) < noise || (k === "use" ? dl > 0 : dl < 0)) continue;
      let b = -1; for (let j = n - 1; j >= 0; j--) if (pts[i].t >= starts[j]) { b = j; break; }
      if (b >= 0) v[b] += Math.abs(dl);
    }
    const W = 300, H = 72, slot = W / n, bw = slot * 0.62, max = Math.max(1, ...v), avg = v.reduce((a, b) => a + b, 0) / n;
    const lab = (i) => (hourly ? hh(starts[i]) : new Date(starts[i]).toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "short" }));
    const bars = v.map((x, i) => {
      const h = (x / max) * (H - 8), y = H - h, recent = starts[i] + (hourly ? 36e5 : 864e5) > now - 864e5;
      return h < 0.5 ? `<circle cx="${(i * slot + slot / 2).toFixed(1)}" cy="${H - 1}" r="1.2" class="stub"/>`
        : `<rect x="${(i * slot + (slot - bw) / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="${Math.min(3, bw / 2).toFixed(1)}"
          class="shb${k === "in" && !recent ? " dim" : ""}"><title>${lab(i)}: ${fmtL(x)} L</title></rect>`;
    }).join("");
    const avgY = (H - (avg / max) * (H - 8)).toFixed(1), top = v.indexOf(Math.max(...v)), last24 = v.filter((_, i) => starts[i] + (hourly ? 36e5 : 864e5) > now - 864e5).reduce((a, b) => a + b, 0);
    const per = hourly ? "pro Stunde" : "pro Tag";
    const say = k === "use" ? `Ø <b>${fmtL(avg)} L</b> ${per}${max > 1 && v[top] > 0 ? ` · am meisten ${hourly ? "um" : "am"} ${lab(top)}: <b>${fmtL(v[top])} L</b>` : ""}`
      : `Letzte 24 h: <b>+${fmtL(last24)} L</b> · im ganzen Zeitraum <b>+${fmtL(v.reduce((a, b) => a + b, 0))} L</b>`;
    return `<svg viewBox="0 0 ${W} ${H}" aria-hidden="true" class="shbars">${bars}${k === "use" && avg > 0 ? `<line x1="0" x2="${W}" y1="${avgY}" y2="${avgY}" class="sha2"/>` : ""}</svg>
      <div class="shx"><span>${lab(0)}</span><span>${lab(Math.floor(n / 2))}</span><span>${hourly ? "jetzt" : "heute"}</span></div><div class="shs">${say}</div>`;
  }
  _spark() {
    const h0 = this._history, c = this._config, now = Date.now();
    if (h0 == null || (this._fetching && !h0.length)) return `<div class="empty-spark">Verlauf wird geladen …</div>`;
    if (this._histErr && !h0.length) return `<div class="empty-spark">Verlauf nicht abrufbar.</div>`;
    const st = this._hass.states[c.entity], cur = isLive(st) ? this._toLiters(st.state, st.attributes.unit_of_measurement) : null;
    const h = cur === null ? [...h0] : [...h0, { t: now, l: Math.max(0, Math.min(c.capacity, cur)) }];   // letzter Stand bis jetzt
    if (!h.length) return `<div class="empty-spark">Noch kein Verlauf – erscheint, sobald der Sensor Werte liefert.</div>`;
    const W = 300, H = 56, cap = c.capacity, t0 = h[0].t, t1 = Math.max(now, t0 + 1);
    let lo = Math.min(...h.map((p) => p.l)), hi = Math.max(...h.map((p) => p.l));
    const same = hi - lo < cap * 0.003, short = h0.length <= 1 || now - t0 < 36e5;
    const pad = Math.max((hi - lo) * 0.15, cap * 0.02); lo = Math.max(0, lo - pad); hi = Math.min(cap, hi + pad);
    const X = (t) => ((t - t0) / (t1 - t0)) * W, Y = (l) => H - 4 - ((l - lo) / Math.max(1e-9, hi - lo)) * (H - 8);
    let d = `M 0 ${Y(h[0].l).toFixed(1)}`;
    for (let i = 1; i < h.length; i++) d += ` H ${X(h[i].t).toFixed(1)} V ${Y(h[i].l).toFixed(1)}`;
    d += ` H ${W}`;
    const since = (t) => (dayStart(new Date(t)) < dayStart() ? new Date(t).toLocaleDateString("de-DE", { day: "numeric", month: "short" }) + ", " : "") + hm(t);
    const lbl = same && short
      ? `<span>Seit ${since(t0)} unverändert bei ${fmtL(h[h.length - 1].l)} L · ${fmt((h[h.length - 1].l / cap) * 100)} %</span>`
      : `<span>${now - t0 < c.days * 864e5 - 36e5 ? "seit " + since(t0) : `vor ${c.days} Tagen`}</span><span>${fmtL(lo)}–${fmtL(hi)} L</span><span>jetzt</span>`;
    return `<div class="spark"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path d="${d} V ${H} H 0 Z" class="area"/><path d="${d}" class="line"/></svg>
      <div class="sparkl">${lbl}</div></div>`;
  }
}

/* ================= 2 · Energiefluss (WATT) ================= */
const EC = { solar: "#f0a400", grid: "#8590a3", batt: "#7c5cff", home: "#2a78d6" };
const EG = { W: 360, H: 290, R: 30, solar: [180, 58], grid: [56, 148], home: [304, 148], batt: [180, 240] };
/* Leistungsverlauf der letzten 24 h je Knoten, im 5-min-Raster. Gleiche Formeln wie die Tageswerte der Energiekarte. */
function energySeries24(res, c, states, end = Date.now(), N = 288) {
  const t0 = end - 864e5, times = Array.from({ length: N + 1 }, (_, i) => t0 + (i * 864e5) / N);
  const at = (id) => {
    if (!id || !res?.[id]) return null;
    const k = (states[id]?.attributes.unit_of_measurement || "W").toLowerCase() === "kw" ? 1000 : 1;
    const arr = res[id].map((r) => ({ t: (r.lu ?? r.lc ?? 0) * 1000 || toMs(r.last_updated || r.last_changed), v: parseFloat(r.s ?? r.state) * k }));
    let j = -1;
    return times.map((t) => { while (j + 1 < arr.length && arr[j + 1].t <= t) j++; return j >= 0 && isFinite(arr[j].v) ? arr[j].v : null; });
  };
  const signed = (one, plus, minus, inv) => {
    if (one) { const a = at(one); return a && a.map((v) => (v === null ? null : inv ? -v : v)); }
    const p = at(plus), m = at(minus);
    if (!p && !m) return null;
    return times.map((_, i) => (p?.[i] == null && m?.[i] == null ? null : Math.abs(p?.[i] ?? 0) - Math.abs(m?.[i] ?? 0)));
  };
  const grid = signed(c.grid, c.grid_import, c.grid_export, c.invert_grid);
  const solar = c.solar ? at(c.solar)?.map((v) => (v === null ? null : Math.max(0, v))) : null;
  const batt = signed(c.battery, c.battery_discharge, c.battery_charge, c.invert_battery);
  const home = c.home ? at(c.home) : grid && times.map((_, i) => (grid[i] === null ? null : Math.max(0, grid[i] + (solar?.[i] ?? 0) + (batt?.[i] ?? 0))));
  return { times, grid, solar, batt, home };
}

class MangfallEnergyCard extends MangfallBase {
  static product = "WATT";
  static defaults = { name: "Energie", grid: "", grid_import: "", grid_export: "", solar: "", battery: "", battery_charge: "", battery_discharge: "",
    battery_soc: "", home: "", invert_grid: false, invert_battery: false,
    solar_peak: 0, price: 0.35, feed_in: 0.079, show_today: true };
  static getStubConfig(hass) {
    const c = MangfallEnergyCard.prototype._auto({}, hass), out = {};
    for (const k of ["grid", "grid_import", "grid_export", "solar", "battery", "battery_charge", "battery_discharge", "battery_soc"]) if (c[k]) out[k] = c[k];
    return out;
  }
  static getConfigForm() {
    const pw = sensorSel("power");
    return {
      schema: [
        { name: "grid", selector: pw },
        { name: "solar", selector: pw },
        { name: "battery_soc", selector: sensorSel("battery") },
        { name: "battery", selector: pw },
        { type: "expandable", name: "", title: "Zähler oder Speicher melden zwei getrennte Werte?", flatten: true, schema: [
          { name: "grid_import", selector: pw }, { name: "grid_export", selector: pw },
          { name: "battery_charge", selector: pw }, { name: "battery_discharge", selector: pw } ] },
        { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [
          { name: "name", selector: { text: {} } },
          { name: "home", selector: pw },
          { type: "grid", name: "", schema: [
            { name: "price", selector: { number: { min: 0, max: 2, step: 0.01, mode: "box", unit_of_measurement: "€/kWh" } } },
            { name: "feed_in", selector: { number: { min: 0, max: 1, step: 0.001, mode: "box", unit_of_measurement: "€/kWh" } } },
            { name: "solar_peak", selector: { number: { min: 0, max: 100000, step: 100, mode: "box", unit_of_measurement: "W" } } },
            { name: "show_today", selector: { boolean: {} } } ] },
          { type: "grid", name: "", schema: [{ name: "invert_grid", selector: { boolean: {} } }, { name: "invert_battery", selector: { boolean: {} } }] },
          { name: "show_branding", selector: { boolean: {} } } ] },
      ],
      computeLabel: (s) => ({ grid: "Stromzähler", solar: "Solaranlage", battery: "Speicher-Leistung",
        battery_soc: "Speicher-Ladestand", grid_import: "Netzbezug", grid_export: "Einspeisung", battery_charge: "Speicher lädt mit",
        battery_discharge: "Speicher entlädt mit", home: "Hausverbrauch", name: "Name", price: "Strompreis brutto",
        feed_in: "Einspeisevergütung", solar_peak: "PV-Spitzenleistung", show_today: "Tageswerte anzeigen",
        invert_grid: "Netz-Vorzeichen umdrehen", invert_battery: "Speicher-Vorzeichen umdrehen", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ grid: "Die Leistung am Hausanschluss, z. B. vom Shelly 3EM oder Smart Meter.",
        solar: "Was die Solaranlage gerade erzeugt (Watt).", battery_soc: "Nur mit Batteriespeicher: der Ladestand in Prozent.",
        battery: "Nur mit Batteriespeicher: Leistung mit Vorzeichen. Meldet dein Speicher Laden und Entladen getrennt, nimm die Felder darunter.",
        home: "Leer lassen – die Karte rechnet den Verbrauch selbst aus.",
        invert_grid: "Zeigt die Karte Einspeisung, obwohl du Strom beziehst? Dann einschalten." }[s.name]),
    };
  }
  _auto(c, hass) {
    if (!c.grid && !c.grid_import && !c.grid_export) {
      c.grid = FIND.grid(hass);
      if (!c.grid) { c.grid_import = FIND.grid_import(hass); c.grid_export = FIND.grid_export(hass); }
    }
    if (!c.solar) c.solar = FIND.solar(hass);
    if (!c.battery_soc) c.battery_soc = FIND.battery_soc(hass);
    if (!c.battery && !c.battery_charge && !c.battery_discharge && c.battery_soc) {   // Speicherleistung nur, wenn es einen Speicher gibt
      c.battery = FIND.battery(hass);
      if (!c.battery) { c.battery_charge = FIND.battery_charge(hass); c.battery_discharge = FIND.battery_discharge(hass); }
    }
    return c;
  }
  /* Netz und Speicher als ein Wert mit Vorzeichen, egal ob ein Sensor oder zwei getrennte */
  _signed(one, plus, minus, inv) {
    const S = this._hass.states;
    if (one) { const v = toWatt(S[one]); return v === null ? null : inv ? -v : v; }
    const p = plus ? toWatt(S[plus]) : null, m = minus ? toWatt(S[minus]) : null;
    return p === null && m === null ? null : Math.abs(p ?? 0) - Math.abs(m ?? 0);
  }
  _watch() { const c = this._config; return [c.grid, c.grid_import, c.grid_export, c.solar, c.battery, c.battery_charge, c.battery_discharge, c.battery_soc, c.home].filter(Boolean); }
  getCardSize() { return 7; }

  /* Tageswerte: Leistungsverlauf seit Mitternacht integrieren, alle 5 min neu */
  _onHass() {
    if (this._sheet) this._load24();
    if (!this._config.show_today || this._busy || !this._hass.callWS) return;
    const t = this._todayAt;
    if (t && Date.now() - t < 300000 && new Date(t).getDate() === new Date().getDate()) return;
    this._fetchToday();
  }
  async _fetchToday() {
    this._busy = true;
    const c = this._config, start = new Date(); start.setHours(0, 0, 0, 0);
    try {
      const ids = [c.grid, c.grid_import, c.grid_export, c.solar, c.battery, c.battery_charge, c.battery_discharge].filter(Boolean);
      if (!ids.length) throw new Error("keine Sensoren");
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
      const pair = (one, plus, minus, inv) => one ? integ(one, inv ? -1 : 1)
        : [plus ? integ(plus, 1)[0] + integ(plus, 1)[1] : 0, minus ? integ(minus, 1)[0] + integ(minus, 1)[1] : 0];
      const [imp, exp] = pair(c.grid, c.grid_import, c.grid_export, c.invert_grid);
      const pv = c.solar ? integ(c.solar, 1)[0] : 0;
      const [dis, chg] = pair(c.battery, c.battery_discharge, c.battery_charge, c.invert_battery);
      const home = Math.max(0, imp - exp + pv + dis - chg);
      this._today = { imp, exp, pv, home, aut: home > 0.05 ? Math.max(0, Math.min(100, (1 - imp / home) * 100)) : null,
        saved: Math.max(0, pv - exp) * c.price + exp * c.feed_in };
      this._todayErr = false; this._todayAt = Date.now();
    } catch (e) { this._todayErr = true; this._todayAt = Date.now() - 300000 + 30000; }   // in 30 s erneut versuchen
    this._busy = false; this._lastKey = null; this._render();
  }

  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, S = this._hass.states;
    const gridId = c.grid || c.grid_import || c.grid_export;
    if (!gridId) return this._setup(c.name, "Ich habe deinen Stromzähler nicht gefunden.");
    const g = this._signed(c.grid, c.grid_import, c.grid_export, c.invert_grid);
    if (g === null) return this._missing(c.name, `${S[gridId]?.attributes.friendly_name || gridId} meldet sich gerade nicht.`);
    const hasS = !!c.solar, hasB = !!(c.battery || c.battery_charge || c.battery_discharge);
    const s = hasS ? Math.max(0, toWatt(S[c.solar]) ?? 0) : 0;
    const b = hasB ? this._signed(c.battery, c.battery_discharge, c.battery_charge, c.invert_battery) ?? 0 : 0;
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
      return `<div class="hit${this._sheet === key ? " sel" : ""}" style="left:${(x / P.W) * 100}%;top:${(y / P.H) * 100}%;color:${col}" data-node="${key}" role="button" tabindex="0" aria-label="Verlauf"><ha-icon icon="${icon}"></ha-icon></div>`;
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

    const tiles = !c.show_today ? "" : `<div class="today"><div class="tt">Heute${this._todayErr && !td ? " · Verlauf nicht abrufbar" : ""}</div><div class="tg">
      ${hasS ? `<div class="t" data-node="solar" role="button" tabindex="0"><div class="tv">${td ? fmt(td.pv, 1) : "–"}<span>kWh</span></div><div class="tl"><i style="background:${EC.solar}"></i>Solar</div></div>` : ""}
      <div class="t" data-node="home" role="button" tabindex="0"><div class="tv">${td && td.aut !== null ? fmt(td.aut) : "–"}<span>%</span></div><div class="tl">Autarkie</div></div>
      <div class="t" data-node="grid" role="button" tabindex="0"><div class="tv">${td ? fmt(td.imp, 1) : "–"}<span>kWh</span></div><div class="tl"><i style="background:${EC.grid}"></i>Netzbezug</div></div>
      ${hasS ? `<div class="t" data-node="solar" role="button" tabindex="0"><div class="tv">${td ? fmt(td.saved, 2) : "–"}<span>€</span></div><div class="tl">gespart</div></div>`
        : `<div class="t" data-node="home" role="button" tabindex="0"><div class="tv">${td ? fmt(td.home, 1) : "–"}<span>kWh</span></div><div class="tl">Verbrauch</div></div>`}
    </div></div>`;

    this._shell(state.key, `
      ${this._head(c.name, state)}
      <div class="hero" data-node="home" role="button" tabindex="0"><span class="num">${fmt(own)}</span><span class="u">% eigener Strom</span></div>
      <div class="say">${say}.</div>
      <div class="flow">${svg}
        ${hasS ? ico("solar", "mdi:solar-power-variant", EC.solar, c.solar) : ""}
        ${ico("grid", g < -T ? "mdi:transmission-tower-export" : "mdi:transmission-tower", EC.grid, gridId)}
        ${ico("home", "mdi:home-lightning-bolt-outline", EC.home, c.home || null)}
        ${hasB ? ico("batt", battIcon, EC.batt, c.battery_soc || c.battery || c.battery_discharge) : ""}
      </div>${this._sheetHtml()}${tiles}`, `
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
      .hit.sel { background: color-mix(in srgb, currentColor 12%, transparent); }
      .hit ha-icon { --mdc-icon-size: 21px; width: 21px; height: 21px; transform: translateY(-9px); }
      .today { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--divider-color, rgba(127,127,127,.2)); }
      .tt { font-size: 11px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--secondary-text-color); }
      .tg { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin-top: 6px; }
      .t .tv { font-size: 18px; font-weight: 600; color: var(--primary-text-color); font-variant-numeric: tabular-nums; white-space: nowrap; }
      .t .tv span { font-size: 11px; font-weight: 400; color: var(--secondary-text-color); margin-left: 2px; }
      .t .tl { display: flex; align-items: center; gap: 4px; font-size: 11px; color: var(--secondary-text-color); }
      .t .tl i { width: 7px; height: 7px; border-radius: 50%; }
      .t[data-node], .hero[data-node] { cursor: pointer; border-radius: 8px; transition: background .15s; }
      .t[data-node]:hover, .hero[data-node]:hover { background: color-mix(in srgb, var(--primary-text-color) 5%, transparent); }
      [data-node]:focus-visible { outline: 2px solid var(--primary-color, #2a78d6); outline-offset: 2px; }`);
    this._sheetAnim = false;
    const R = this.shadowRoot, open = (k) => { this._sheet = this._sheet === k ? null : k; this._sheetAnim = !!this._sheet; this._render(); if (this._sheet) this._load24(); };
    R.querySelectorAll("[data-node]").forEach((el) => {
      const k = el.dataset.node, ent = this._nodeEntity(k);
      let timer = null, long = false;
      el.addEventListener("pointerdown", () => { long = false; timer = setTimeout(() => { long = true; if (ent) this._moreInfo(ent); }, 550); });
      ["pointerup", "pointerleave", "pointercancel"].forEach((t) => el.addEventListener(t, () => clearTimeout(timer)));
      el.addEventListener("contextmenu", (ev) => ev.preventDefault());
      el.addEventListener("click", () => { if (!long) open(k); });
      el.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); open(k); } });
    });
    R.querySelector(".shc")?.addEventListener("click", () => open(this._sheet));
  }
  /* Verlaufsfeld je Knoten: Tipp auf Solar, Netz, Haus oder Speicher zeigt die letzten 24 h direkt in der Karte */
  _nodeEntity(k) {
    const c = this._config;
    return { solar: c.solar, grid: c.grid || c.grid_import || c.grid_export, home: c.home, batt: c.battery_soc || c.battery || c.battery_discharge }[k] || "";
  }
  async _load24() {
    if (this._busy24 || !this._hass.callWS || (this._h24 && Date.now() - this._h24.at < 3e5)) return;
    const c = this._config, ids = [c.grid, c.grid_import, c.grid_export, c.solar, c.battery, c.battery_charge, c.battery_discharge, c.home].filter(Boolean);
    if (!ids.length) return;
    this._busy24 = true;
    const end = Date.now();
    try {
      const res = await this._hass.callWS({ type: "history/history_during_period", start_time: new Date(end - 864e5).toISOString(), end_time: new Date(end).toISOString(),
        entity_ids: ids, minimal_response: true, no_attributes: true, significant_changes_only: false });
      this._h24 = { at: Date.now(), s: energySeries24(res, c, this._hass.states, end) };
    } catch (e) { this._h24 = { at: Date.now() - 3e5 + 3e4, s: this._h24?.s || null, err: true }; }
    this._busy24 = false; this._lastKey = null; this._render();
  }
  _sheetHtml() {
    const k = this._sheet;
    if (!k) return "";
    const names = { home: "Haus", grid: "Netz", solar: "Solar", batt: "Speicher" }, col = { home: EC.home, grid: EC.grid, solar: EC.solar, batt: EC.batt }[k];
    const ent = this._nodeEntity(k), d = this._h24?.s;
    let body;
    if (!this._h24) body = `<div class="shw">Verlauf wird geladen …</div>`;
    else if (this._h24.err && !d) body = `<div class="shw">Verlauf nicht abrufbar.</div>`;
    else if (!d || !d[k] || !d[k].some((v) => v !== null)) body = `<div class="shw">Noch kein Verlauf – erscheint, sobald der Sensor Werte liefert.</div>`;
    else {
      const v = d[k], W = 300, H = 60, ok = v.map((x, i) => [x, i]).filter((p) => p[0] !== null);
      const max = ok.reduce((a, b) => (b[0] > a[0] ? b : a)), min = ok.reduce((a, b) => (b[0] < a[0] ? b : a));
      const avg = ok.reduce((s, p) => s + p[0], 0) / ok.length, lo = Math.min(0, min[0]), hi = Math.max(1, max[0]);
      const X = (i) => ((i / (v.length - 1)) * W).toFixed(1), Y = (x) => (H - 2 - ((x - lo) / (hi - lo)) * (H - 6)).toFixed(1), y0 = Y(0);
      let line = "", area = "", run = [];
      const flush = () => { if (run.length > 1) { const p = run.map(([x, i], j) => `${j ? "L" : "M"}${X(i)} ${Y(x)}`).join(" ");
        line += p + " "; area += `${p} V${y0} H${X(run[0][1])} Z `; } run = []; };
      v.forEach((x, i) => (x === null ? flush() : run.push([x, i]))); flush();
      const tOf = (i) => d.times[i];
      const more = (k === "grid" || k === "batt") && min[0] < -15 ? ` · ${k === "grid" ? "Einspeisung" : "Laden"} bis ${fmtW(-min[0])}` : "";
      body = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path d="${area}" class="sha" style="fill:${col}"/>
          ${lo < 0 ? `<line x1="0" x2="${W}" y1="${y0}" y2="${y0}" class="sh0"/>` : ""}<path d="${line}" class="shl" style="stroke:${col}"/></svg>
        <div class="shx"><span>vor 24 h</span><span>vor 12 h</span><span>jetzt</span></div>
        <div class="shs">Höchstwert <b>${fmtW(max[0])}</b> um ${hm(tOf(max[1]))} · Ø <b>${fmtW(avg)}</b>${more}</div>`;
    }
    return `<div class="sheet${this._sheetAnim ? " in" : ""}"><div class="shh"><span><i style="background:${col}"></i>${names[k]} · letzte 24 h</span>
      <span>${ent ? `<button class="shd" data-entity="${ent}">Details</button>` : ""}<button class="shc" aria-label="Schließen"><ha-icon icon="mdi:close"></ha-icon></button></span></div>${body}</div>`;
  }
}

/* ================= 3 · Raumklima ================= */
class MangfallClimateCard extends MangfallBase {
  static defaults = { name: "Raumklima", rooms: [], sensors: [], humid_high: 65, humid_low: 35 };
  static getStubConfig() { return {}; }      // leer = alle Räume automatisch
  static getConfigForm() {
    return { schema: [
      { name: "sensors", selector: { entity: { multiple: true, filter: { domain: "sensor", device_class: "temperature" } } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [
        { name: "name", selector: { text: {} } },
        { type: "grid", name: "", schema: [
          { name: "humid_high", selector: { number: { min: 40, max: 90, unit_of_measurement: "%", mode: "box" } } },
          { name: "humid_low", selector: { number: { min: 10, max: 60, unit_of_measurement: "%", mode: "box" } } }] },
        { name: "show_branding", selector: { boolean: {} } }] }],
      computeLabel: (s) => ({ sensors: "Thermometer", name: "Name", humid_high: "Zu feucht ab", humid_low: "Zu trocken ab", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ sensors: "Leer lassen: Die Karte zeigt jeden Raum mit Thermometer von selbst. Die Luftfeuchte findet sie dazu." }[s.name]) };
  }
  // Räume selbst bilden: je Raum ein Thermometer, die Luftfeuchte vom selben Gerät oder aus demselben Raum
  _auto(c, hass) {
    if (c.rooms?.length) return c;
    const S = Object.values(hass.states), areas = hass.areas || {};
    const NOT_ROOM = /chip|cpu|prozessor|processor|core|gpu|disk|nvme|board|esp|batter|speicher|akku|inverter|wechselrichter|wasser|water|boiler|vorlauf|r(ü|ue)cklauf|au(ß|ss)en|outdoor|outside|wetter|weather|forecast|k(ü|ue)hlschrank|gefrier|fridge|freezer|pool|aquarium|taupunkt|dew/;
    let temps = c.sensors?.length ? c.sensors.map((id) => hass.states[id]).filter(Boolean)
      : S.filter((e) => e.entity_id.startsWith("sensor.") && e.attributes.device_class === "temperature" && isLive(e) && !NOT_ROOM.test(lcName(e)));
    const withArea = temps.filter((e) => areaOf(hass, e.entity_id));
    if (!c.sensors?.length && withArea.length) temps = withArea;          // mit Raumzuordnung nur die echten Räume
    const hums = S.filter((e) => e.entity_id.startsWith("sensor.") && e.attributes.device_class === "humidity" && isLive(e));
    const seen = new Set();
    c.rooms = [];
    for (const t of temps) {
      const a = areaOf(hass, t.entity_id), key = a || t.entity_id;
      if (seen.has(key)) continue; seen.add(key);
      const hum = sameDevice(hass, t.entity_id, (e) => e.attributes.device_class === "humidity") || (a && hums.find((e) => areaOf(hass, e.entity_id) === a));
      c.rooms.push({ name: (a && areas[a]?.name) || cleanName(t.attributes.friendly_name || t.entity_id),
        temperature: t.entity_id, humidity: hum ? hum.entity_id : undefined });
      if (c.rooms.length >= 8) break;
    }
    return c;
  }
  _watch() { return this._config.rooms.flatMap((r) => [r.temperature, r.humidity]).filter(Boolean); }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, S = this._hass.states;
    if (!c.rooms.length) return this._setup(c.name, "Ich habe noch kein Thermometer in einem Raum gefunden.");
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
        <div class="rv">${isNaN(t) ? `<span class="rs">${isLive(S[r.temperature]) ? "kein Wert" : "offline"}</span>` : fmt(t, 1) + " °C"}</div></div>`;
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
      { name: "exclude", selector: { entity: { multiple: true, filter: { domain: "sensor", device_class: "battery" } } } },
      { name: "show_branding", selector: { boolean: {} } }],
      computeLabel: (s) => ({ name: "Name", low: "Schwach ab", show: "Anzahl anzeigen", exclude: "Ausblenden", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ exclude: "Die Karte findet alle Batterien von selbst. Hier wählst du nur, was sie nicht zeigen soll." }[s.name]) };
  }
  _list() {
    return Object.values(this._hass.states).filter((e) => e.attributes.device_class === "battery" && e.entity_id.startsWith("sensor.")
      && !this._config.exclude.includes(e.entity_id) && !isNaN(parseFloat(e.state)))
      .map((e) => ({ id: e.entity_id, name: cleanName(e.attributes.friendly_name || e.entity_id), v: parseFloat(e.state) }))
      .sort((a, b) => a.v - b.v);
  }
  _watch() { return this._hass ? this._list().map((b) => b.id) : []; }
  _extraSheet(k) {
    if (k !== "l:all") return "";
    const c = this._config, all = this._list();
    return this._frame(`Alle ${all.length} Akkus`, `<div class="rows">${all.map((b) => `<div class="row" data-entity="${b.id}"><div class="rn">${esc(b.name)}</div>
      <div class="rv">${fmt(b.v)} %</div></div>`).join("") || `<div class="muted pad">Keine Batteriesensoren gefunden.</div>`}</div>`);
  }
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
      <div class="big tapn" style="margin-top:6px" data-sheet="l:all"><span class="num">${all.length}</span><span class="u">Geräte überwacht</span></div>
      <div class="rows">${rows || `<div class="muted pad">Keine Batteriesensoren gefunden.</div>`}</div>
      ${all.length > c.show ? `<div class="muted more tapn" data-sheet="l:all">+ ${all.length - c.show} weitere über ${fmt(all[c.show - 1].v)} % – alle zeigen</div>` : ""}`, `
      .tapn { cursor: pointer; border-radius: 8px; } .tapn:hover { background: color-mix(in srgb, var(--primary-text-color) 5%, transparent); }
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
  static getConfigForm() {
    const sel = { entity: { multiple: true, filter: OPEN_CLASSES.map((device_class) => ({ domain: "binary_sensor", device_class })) } };
    return { schema: [{ name: "exclude", selector: sel }, { name: "name", selector: { text: {} } }, { name: "show_branding", selector: { boolean: {} } }],
      computeLabel: (s) => ({ exclude: "Ausblenden", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ exclude: "Die Karte findet alle Fenster- und Türkontakte von selbst. Hier wählst du nur, was sie nicht zeigen soll." }[s.name]) };
  }
  _list() {
    const ids = this._config.entities?.length ? this._config.entities : Object.values(this._hass.states)
      .filter((e) => e.entity_id.startsWith("binary_sensor.") && OPEN_CLASSES.includes(e.attributes.device_class)).map((e) => e.entity_id);
    return ids.filter((id) => !this._config.exclude.includes(id) && this._hass.states[id]).map((id) => this._hass.states[id]);
  }
  _watch() { return this._hass ? this._list().map((e) => e.entity_id) : []; }
  _extraSheet(k) {
    if (k !== "l:all") return "";
    const all = this._list().sort((a, b) => (b.state === "on") - (a.state === "on"));
    return this._frame(`Alle ${all.length} Fenster und Türen`, `<div class="rows">${all.map((e) => `<div class="row" data-entity="${e.entity_id}">
      <div class="rn">${esc(cleanName(fname(e)))}</div><div class="rs">${e.state === "on" ? "offen" : e.state === "off" ? "zu" : "offline"} · ${ago(e.last_changed)}</div></div>`).join("")}</div>`);
  }
  _render() {
    if (!this._config || !this._hass) return;
    const all = this._list(), open = all.filter((e) => e.state === "on").sort((a, b) => new Date(a.last_changed) - new Date(b.last_changed));
    const longOpen = open.filter((e) => Date.now() - new Date(e.last_changed) > 30 * 60000).length;
    const state = !open.length ? { key: "ok", label: "Alles zu", icon: "mdi:check-circle" }
      : longOpen ? { key: "crit", label: `${longOpen} länger als 30 min offen`, icon: "mdi:alert" }
      : { key: "low", label: `${open.length} offen`, icon: "mdi:door-open" };
    const icon = (e) => ({ door: "mdi:door-open", window: "mdi:window-open-variant", garage_door: "mdi:garage-open" }[e.attributes.device_class] || "mdi:square-rounded-outline");
    const rows = open.map((e) => `<div class="row" data-entity="${e.entity_id}"><ha-icon class="ri" icon="${icon(e)}"></ha-icon>
      <div class="rn">${esc(cleanName(fname(e)))}</div><div class="rs">${ago(e.last_changed)}</div></div>`).join("");
    this._shell(state.key, `
      ${this._head(this._config.name, state)}
      <div class="big tapn" style="margin-top:6px" data-sheet="l:all"><span class="num">${open.length}</span><span class="u">von ${all.length} offen</span></div>
      ${open.length ? `<div class="rows">${rows}</div>` : `<div class="muted">Alle Fenster und Türen sind geschlossen.</div>`}`,
      `.tapn { cursor: pointer; border-radius: 8px; } .tapn:hover { background: color-mix(in srgb, var(--primary-text-color) 5%, transparent); }`);
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
      { name: "entity", selector: { entity: { domain: "lawn_mower" } } },
      { name: "battery", selector: sensorSel("battery") },
      { name: "name", selector: { text: {} } },
      { name: "show_branding", selector: { boolean: {} } }],
      computeLabel: (s) => ({ entity: "Mähroboter", battery: "Akku", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ battery: "Leer lassen – die Karte nimmt den Akku des Mähroboters von selbst." }[s.name]) };
  }
  _auto(c, hass) {
    if (!c.entity) c.entity = Object.keys(hass.states).find((id) => id.startsWith("lawn_mower.")) || "";
    if (c.entity && !c.battery) c.battery = sameDevice(hass, c.entity, (e) => e.attributes.device_class === "battery")?.entity_id || "";
    return c;
  }
  _watch() { return [this._config.entity, this._config.battery].filter(Boolean); }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, st = this._hass.states[c.entity];
    if (!c.entity) return this._setup(c.name, "Ich habe keinen Mähroboter in deinem Home Assistant gefunden.");
    if (!st) return this._missing(c.name, `„${c.entity}“ gibt es nicht mehr – bitte neu wählen.`);
    const m = MOWER[st.state] || (isLive(st) ? { label: stateDe(st.state), key: "info", icon: "mdi:robot-mower-outline" } : { label: "Nicht erreichbar", key: "crit", icon: "mdi:wifi-off" });
    const bat = c.battery ? parseFloat(this._hass.states[c.battery]?.state) : parseFloat(st.attributes.battery_level);
    const mowing = st.state === "mowing";
    this._shell(m.key, `
      ${this._head(c.name, m)}
      <div class="lawn ${mowing ? "on" : ""}" data-entity="${c.entity}">
        <div class="stripes"></div><ha-icon class="bot" icon="mdi:robot-mower"></ha-icon>
      </div>
      ${this._tiles([
        { label: "Akku", value: isNaN(bat) ? "–" : fmt(bat), unit: "%", sub: isNaN(bat) ? "kein Akkuwert" : bat < 20 ? "lädt bald" : "", icon: "mdi:battery-high", entity: c.battery || null, sheet: c.battery ? "" : `e:${c.entity}|battery_level` },
        { label: "Status", value: m.label.split(" ")[0], unit: "", sub: ago(st.last_changed), icon: m.icon, entity: c.entity },
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

/* ================= Gemeinsame Helfer der neuen Karten ================= */
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
const fname = (e) => e?.attributes.friendly_name || e?.entity_id || "";
const toMs = (v) => (typeof v === "number" ? (v < 1e11 ? v * 1000 : v) : new Date(v).getTime());
const hh = (d) => `${new Date(d).getHours()} Uhr`;
const hm = (d) => new Date(d).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
const dayStart = (d = new Date()) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
const WD = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
const isHidden = (hass, id) => !!regOf(hass, id).hidden;
const BRANDING = { name: "show_branding", selector: { boolean: {} } };
const SEG_CSS = `
  .seg { display: flex; gap: 2px; padding: 2px; margin-top: 10px; border-radius: 10px; background: var(--secondary-background-color, rgba(127,127,127,.1)); }
  .seg button { flex: 1; border: none; background: none; font: inherit; font-size: 12.5px; font-weight: 500; padding: 5px 0; border-radius: 8px;
    color: var(--secondary-text-color); cursor: pointer; transition: background .2s, color .2s; }
  .seg button.on { background: var(--ha-card-background, var(--card-background-color, #fff)); color: var(--primary-text-color); box-shadow: 0 1px 2px rgba(0,0,0,.14); }`;
const BTN_CSS = `
  .btns { display: flex; gap: 8px; margin-top: 10px; }
  .btns button { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 4px; padding: 8px 0; border: none; border-radius: 10px; cursor: pointer;
    font: inherit; font-size: 13px; font-weight: 500; color: var(--primary-text-color); background: var(--secondary-background-color, rgba(127,127,127,.1)); transition: background .2s; }
  .btns button:hover { background: color-mix(in srgb, var(--primary-color, #2a78d6) 15%, var(--secondary-background-color, #eee)); }
  .btns button.on { background: color-mix(in srgb, var(--mc-state) 18%, var(--secondary-background-color, #eee)); }
  .btns ha-icon { --mdc-icon-size: 17px; }`;

/* Zusätzliche Suchregeln */
const isEnergyTotal = (e) => e.entity_id.startsWith("sensor.") && (e.attributes.device_class === "energy" || ["kwh", "wh", "mwh"].includes(unitOf(e)))
  && ["total", "total_increasing"].includes(e.attributes.state_class);
const NOT_ENERGY = /forecast|prognose|solcast|energy_production_|energy_current_hour|energy_next_hour|tomorrow|morgen|remaining|cost|kosten|compensation|verg(ü|ue)tung|yesterday|gestern/;
const GRID_WORD = /netz|grid|3em|em3|z(ä|ae)hler|meter|hausanschluss/;
const CAR = /\bauto\b|_auto\b|\bcar\b|_car\b|\bev\b|_ev_|fahrzeug|vehicle|tesla|model_?[3sxy]\b|volkswagen|\bvw\b|\bid[._ ]?[3-7]\b|id_buzz|enyaq|\bzoe\b|twizy|megane|ioniq|kona|e.?golf|leaf|polestar|\bbmw\b|cupra|skoda|hyundai|\bkia\b|niro|\bev[69]\b|e.?tron|mercedes|\beq[abcesv]\b|porsche|taycan|evcc|wallbox|charger|loadpoint|ladepunkt/;
const HOME_BATT = /speicher|marstek|zendure|hame|solix|ecoflow|sonnen|growatt|huawei|victron|fronius|hausbatterie|home.?battery|powerwall|e3dc|senec|kostal/;
const WALLBOX = /wallbox|charger|ladestation|ladepunkt|loadpoint|charg(e|ing).?power|ladeleistung|go.?e\b|goe_|easee|openwb|evcc|keba|zaptec|wallbe|pulsar|cfos|alfen|webasto|mennekes|heidelberg|juice/;
const PRICE_UNIT = /(€|eur|ct|cent)\s*\/\s*(kwh|mwh)/i;
const WASTE_RE = /abfall|m(ü|ue)ll|tonne|waste|abfuhr|entsorg|wertstoff|recycl|biotonne|altpapier|gelbe|restm/;

Object.assign(FIND, {
  e_import: (h) => pick(h, isEnergyTotal, (n) => 2 * has(GRID_WORD, n) + 2 * has(/bezug|import|consum|from.?grid|bought|purchased|verbrauch/, n)
    - 6 * has(/einspeis|export|feed|return|to.?grid|r(ü|ue)ckspeis|sold|lieferung/, n) - 2 * has(/phase|_l[123]\b|_[abc]_/, n) - 6 * has(NOT_GRID, n) - 9 * has(NOT_ENERGY, n), 4),
  e_export: (h) => pick(h, isEnergyTotal, (n) => 2 * has(GRID_WORD, n) + 2 * has(/einspeis|export|feed|return|to.?grid|r(ü|ue)ckspeis|sold/, n)
    - 4 * has(/bezug|import|consum|bought|purchased/, n) - 2 * has(/phase|_l[123]\b|_[abc]_/, n) - 6 * has(NOT_GRID, n) - 9 * has(NOT_ENERGY, n), 4),
  e_solar: (h) => pick(h, isEnergyTotal, (n) => 3 * has(/\bpv\b|_pv|pv_|solar|photovolt|wechselrichter|inverter|erzeugung|production|yield|ertrag/, n)
    - 4 * has(/netz|grid|einspeis|export/, n) - 9 * has(NOT_ENERGY, n)),
  e_bin: (h) => pick(h, isEnergyTotal, (n) => 2 * has(/batter|speicher|akku/, n) + 2 * has(/charg|ladung|geladen|input|to.?batt/, n)
    - 6 * has(/discharg|entlad|output|from.?batt/, n) - 3 * has(/\bpv\b|_pv|pv_|solar/, n) - 9 * has(NOT_ENERGY, n), 4),
  e_bout: (h) => pick(h, isEnergyTotal, (n) => 2 * has(/batter|speicher|akku/, n) + 2 * has(/discharg|entlad|output|from.?batt/, n)
    - 3 * has(/\bpv\b|_pv|pv_|solar|reverse/, n) - 9 * has(NOT_ENERGY, n), 4),
  weather: (h) => pick(h, (e) => e.entity_id.startsWith("weather."), (n, e) => 2 + ((e.attributes.supported_features || 0) & 2 ? 1 : 0)
    + (/deutscher wetterdienst|dwd/i.test(e.attributes.attribution || "") ? 1 : 0) - (/_\d+$/.test(e.entity_id) ? 1 : 0), 2),
  vacuum: (h) => pick(h, (e) => e.entity_id.startsWith("vacuum."), () => 3),
  price: (h) => pick(h, (e) => e.entity_id.startsWith("sensor.") && PRICE_UNIT.test(e.attributes.unit_of_measurement || ""),
    (n, e) => 3 * has(/preis|price|tarif|spot|b(ö|oe)rse|tibber|nordpool|epex|awattar|entso|energyzero|dynami/, n) + (priceSeries(e).length ? 3 : 0)
      + (e.attributes.price_level ? 2 : 0) - 6 * has(/einspeis|feed|export|grund|fixed|monat|month|cost|kosten|average|durchschnitt|_min|_max|\bmin\b|\bmax\b|lowest|highest|tomorrow|morgen|accumulated/, n)),
  ev_soc: (h) => pick(h, (e) => e.entity_id.startsWith("sensor.") && unitOf(e) === "%" && (e.attributes.device_class === "battery" || /soc|ladestand|state.?of.?charge|battery.?level/.test(lcName(e))),
    (n, e) => 3 * has(CAR, n) + has(/soc|ladestand|state.?of.?charge|battery.?level/, n)
      + (sameDevice(h, e.entity_id, (x) => x.attributes.device_class === "distance" || /range|reichweite/.test(lcName(x))) ? 3 : 0)
      - 9 * has(PHONE_RE, n) - 9 * has(HOME_BATT, n) - 4 * has(/target|ziel|limit|\bmin\b|\bmax\b/, n), 4),
  ev_range: (h) => pick(h, (e) => e.entity_id.startsWith("sensor.") && (e.attributes.device_class === "distance" || ["km", "mi"].includes(unitOf(e))),
    (n) => 3 * has(/range|reichweite/, n) + 2 * has(CAR, n) - 9 * has(PHONE_RE, n) - 6 * has(/odometer|kilometerstand|mileage|travel|trip|fahrt/, n)),
  ev_power: (h) => pick(h, isPower, (n) => 3 * has(WALLBOX, n) + has(CAR, n) - 9 * has(HOME_BATT, n)
    - 6 * has(/batter|akku|\bpv\b|_pv|pv_|solar|inverter|wechselrichter/, n) - 6 * has(NOT_POWER, n)),
});

/* ================= 7 · Energieverlauf (WATT) ================= */
const EH_PERIODS = { day: "Tag", week: "Woche", month: "Monat", year: "Jahr" };
const EH_STAT = { day: "hour", week: "day", month: "day", year: "month" };
const EH_KEYS = ["imp", "exp", "pv", "bin", "bout"];
function ehRange(P, off, now = new Date()) {
  const y = now.getFullYear(), m = now.getMonth(), d = now.getDate();
  if (P === "day") return { s: new Date(y, m, d + off), e: new Date(y, m, d + off + 1), ps: new Date(y, m, d + off - 1) };
  if (P === "week") { const w = d - ((now.getDay() + 6) % 7) + 7 * off; return { s: new Date(y, m, w), e: new Date(y, m, w + 7), ps: new Date(y, m, w - 7) }; }
  if (P === "month") return { s: new Date(y, m + off, 1), e: new Date(y, m + off + 1, 1), ps: new Date(y, m + off - 1, 1) };
  return { s: new Date(y + off, 0, 1), e: new Date(y + off + 1, 0, 1), ps: new Date(y + off - 1, 0, 1) };
}
const ehCount = (P, s) => (P === "day" ? 24 : P === "week" ? 7 : P === "month" ? new Date(s.getFullYear(), s.getMonth() + 1, 0).getDate() : 12);
function ehIdx(P, t, from) {
  const d = new Date(t), f = new Date(from);
  if (P === "day") return d.getHours();
  if (P === "year") return (d.getFullYear() - f.getFullYear()) * 12 + d.getMonth() - f.getMonth();
  return Math.round((dayStart(d) - dayStart(f)) / 864e5);   // Tage, sommerzeitfest
}
/* Statistik-Antwort in Säulen je Reihe aufteilen. Lücken bleiben 0, negative Änderungen (Zähler zurückgesetzt) werden verworfen. */
function ehBuild(res, src, P, ps, s, e) {
  const n = ehCount(P, s), pn = ehCount(P, ps);
  let any = false;
  const fill = (ids, from, to, cnt) => {
    const a = new Array(cnt).fill(0);
    for (const id of ids || []) for (const r of res?.[id] || []) {
      const t = toMs(r.start), v = parseFloat(r.change);
      if (!(t >= from && t < to) || !isFinite(v) || v <= 0) continue;
      const i = ehIdx(P, t, from);
      if (i >= 0 && i < cnt) { a[i] += v; any = true; }
    }
    return a;
  };
  const cur = {}, prev = {};
  for (const k of EH_KEYS) { cur[k] = fill(src[k], +s, +e, n); prev[k] = fill(src[k], +ps, +s, pn); }
  return { n, pn, cur, prev, empty: !any };
}
/* Wohin ging der Strom: Haus = Netz + PV + Speicher raus − Einspeisung − Speicher rein */
const ehSplit = (g, x, p, bi, bo) => {
  const home = Math.max(0, g + p + bo - x - bi), solar = Math.min(home, Math.max(0, p - x - bi)), batt = Math.min(bo, home - solar);
  return { home, solar, batt, grid: Math.max(0, home - solar - batt), exp: x, imp: g };
};
/* Energie-Dashboard-Einstellungen lesen: altes Format (flow_from/flow_to) und neues (stat_energy_from/to direkt) */
function ehPrefs(prefs) {
  const o = { imp: [], exp: [], pv: [], bin: [], bout: [] };
  for (const s of prefs?.energy_sources || []) {
    if (s.type === "grid") {
      if (Array.isArray(s.flow_from)) s.flow_from.forEach((f) => o.imp.push(f.stat_energy_from));
      if (Array.isArray(s.flow_to)) s.flow_to.forEach((f) => o.exp.push(f.stat_energy_to));
      if (s.stat_energy_from) o.imp.push(s.stat_energy_from);
      if (s.stat_energy_to) o.exp.push(s.stat_energy_to);
    } else if (s.type === "solar") o.pv.push(s.stat_energy_from);
    else if (s.type === "battery") { o.bout.push(s.stat_energy_from); o.bin.push(s.stat_energy_to); }
  }
  for (const k of EH_KEYS) o[k] = [...new Set(o[k].filter(Boolean))];
  return o.imp.length || o.pv.length ? o : null;
}

class MangfallEnergyHistoryCard extends MangfallBase {
  static product = "WATT";
  static defaults = { name: "Energieverlauf", period: "day", grid_import: "", grid_export: "", solar: "", battery_charge: "", battery_discharge: "",
    show_cost: false, price: 0.35, feed_in: 0.079 };
  // Leer lassen: die Karte nimmt zuerst die Zähler aus dem Energie-Dashboard, erst dann eigene Suche
  static getStubConfig() { return { period: "day" }; }
  static getConfigForm() {
    const en = sensorSel("energy");
    return {
      schema: [
        { name: "period", selector: { select: { mode: "dropdown", options: Object.entries(EH_PERIODS).map(([value, label]) => ({ value, label })) } } },
        { type: "expandable", name: "", title: "Zähler selbst wählen", flatten: true, schema: [
          { name: "grid_import", selector: en }, { name: "grid_export", selector: en }, { name: "solar", selector: en },
          { name: "battery_charge", selector: en }, { name: "battery_discharge", selector: en }] },
        { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [
          { name: "name", selector: { text: {} } },
          { name: "show_cost", selector: { boolean: {} } },
          { type: "grid", name: "", schema: [
            { name: "price", selector: { number: { min: 0, max: 2, step: 0.01, mode: "box", unit_of_measurement: "€/kWh" } } },
            { name: "feed_in", selector: { number: { min: 0, max: 1, step: 0.001, mode: "box", unit_of_measurement: "€/kWh" } } }] },
          BRANDING] },
      ],
      computeLabel: (s) => ({ period: "Startansicht", grid_import: "Netzbezug", grid_export: "Einspeisung", solar: "Solarertrag", battery_charge: "In den Speicher",
        battery_discharge: "Aus dem Speicher", name: "Name", show_cost: "Kosten in € statt kWh", price: "Strompreis brutto", feed_in: "Einspeisevergütung",
        show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ grid_import: "Leer lassen – die Karte nimmt die Zähler aus deinem Energie-Dashboard.",
        grid_export: "Zählerstand in kWh für den Strom, den du ins Netz schickst.", show_cost: "Zeigt statt Kilowattstunden, was der Strom gekostet hat." }[s.name]),
    };
  }
  _check() { this._cache = {}; this._anim = null; }
  _auto(c, hass) {
    const P = this._prefs;
    const take = (field, k, f) => (c[field] ? [c[field]] : P ? P[k] : [FIND[f](hass)].filter(Boolean));
    c._src = { imp: take("grid_import", "imp", "e_import"), exp: take("grid_export", "exp", "e_export"), pv: take("solar", "pv", "e_solar"),
      bin: take("battery_charge", "bin", "e_bin"), bout: take("battery_discharge", "bout", "e_bout") };
    return c;
  }
  getCardSize() { return 6; }
  _onHass() {
    if (!this._hass.callWS) return;
    if (!this._prefsAsked) {
      this._prefsAsked = true;
      this._hass.callWS({ type: "energy/get_prefs" }).then((p) => ehPrefs(p), () => null).then((p) => {
        this._prefs = p; this._prefsDone = true; this._cache = {};
        this._config = this._auto({ ...this._user }, this._hass); this._render(); this._load();
      });
      return;
    }
    if (this._prefsDone) this._load();
  }
  async _load() {
    const P = this._p(), off = this._off || 0, key = P + off, hit = this._cache[key];
    if (hit && (hit.data?.error ? Date.now() - hit.at < 3e4 : off < 0 || Date.now() - hit.at < 6e5)) return;
    if (this._loading === key || !this._hass?.callWS) return;
    const src = this._config._src || {}, ids = [...new Set(EH_KEYS.flatMap((k) => src[k] || []))];
    if (!ids.length) return;
    this._loading = key;
    const { s, e, ps } = ehRange(P, off);
    let data;
    try {
      const res = await this._hass.callWS({ type: "recorder/statistics_during_period", start_time: ps.toISOString(), end_time: e.toISOString(),
        statistic_ids: ids, period: EH_STAT[P], types: ["change"], units: { energy: "kWh" } });
      data = ehBuild(res, src, P, ps, s, e);
    } catch (err) { data = { error: true }; }
    this._cache[key] = { at: Date.now(), data }; this._loading = null;
    if (key === this._p() + (this._off || 0)) this._render();
  }
  _p() { return this._period || (EH_PERIODS[this._config.period] ? this._config.period : "day"); }
  _title(P, off, s) {
    if (P === "day") return off === 0 ? "Heute" : off === -1 ? "Gestern" : s.toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "short" });
    if (P === "week") {
      if (off === 0) return "Diese Woche"; if (off === -1) return "Letzte Woche";
      const e = new Date(s.getFullYear(), s.getMonth(), s.getDate() + 6);
      return `${s.getDate()}.${s.getMonth() === e.getMonth() ? "" : ` ${MONTHS[s.getMonth()].slice(0, 3)}.`}–${e.getDate()}. ${MONTHS[e.getMonth()].slice(0, 3)}.`;
    }
    return P === "month" ? `${MONTHS[s.getMonth()]} ${s.getFullYear()}` : `${s.getFullYear()}`;
  }
  _prevName(P, off, ps) {
    if (P === "day") return off === 0 ? "gestern" : "am Vortag";
    if (P === "week") return off === 0 ? "letzte Woche" : "in der Woche davor";
    return P === "month" ? `im ${MONTHS[ps.getMonth()]}` : `${ps.getFullYear()}`;
  }
  _barLabel(P, i, s) {
    if (P === "day") return `${i}–${i + 1} Uhr`;
    if (P === "year") return `${MONTHS[i]} ${s.getFullYear()}`;
    return new Date(s.getFullYear(), s.getMonth(), s.getDate() + i).toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "short" });
  }
  _axis(P, i, n) {
    if (P === "day") return i % 6 === 0 ? String(i) : "";
    if (P === "week") return WD[(i + 1) % 7];
    if (P === "month") return [0, 7, 14, 21, 28].includes(i) ? String(i + 1) : "";
    return MONTHS[i][0];
  }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, src = c._src || {}, P = this._p(), off = this._off || 0;
    if (!src.imp?.length && !src.pv?.length) {
      if (this._hass.callWS && !this._prefsDone) return this._shell("info", `${this._head(c.name)}<div class="muted pad">Suche deine Zähler …</div>`);
      return this._setup(c.name, "Ich habe keinen Stromzähler mit Zählerstand in kWh gefunden.");
    }
    const { s, ps } = ehRange(P, off), d = this._cache[P + off]?.data;
    const cost = !!c.show_cost, unit = cost ? "€" : "kWh";
    const hasPv = !!src.pv?.length, hasBat = !!(src.bin?.length || src.bout?.length), hasExp = !!src.exp?.length;
    const now = new Date(), curIdx = off < 0 ? Infinity : P === "day" ? now.getHours() : P === "week" ? (now.getDay() + 6) % 7 : P === "month" ? now.getDate() - 1 : now.getMonth();
    const seg = `<div class="seg">${Object.entries(EH_PERIODS).map(([k, l]) => `<button data-p="${k}" class="${k === P ? "on" : ""}">${l}</button>`).join("")}</div>`;
    const nav = `<div class="nav"><button class="nb" data-nav="-1" aria-label="Zurück"><ha-icon icon="mdi:chevron-left"></ha-icon></button>
      <span class="nt">${this._title(P, off, s)}</span>
      <button class="nb" data-nav="1" aria-label="Vor"${off >= 0 ? " disabled" : ""}><ha-icon icon="mdi:chevron-right"></ha-icon></button></div>`;
    const dot = (col, op = 1) => `<i style="background:${col};opacity:${op}"></i>`;
    const legend = [[EC.grid, "Netz"], hasPv && [EC.solar, "Solar"], hasBat && [EC.batt, "Speicher"], hasExp && [EC.solar, "Einspeisung", 0.4]]
      .filter(Boolean).map(([col, l, op]) => `<span>${dot(col, op)}${l}</span>`).join("");

    let body, state = { key: "info", label: EH_PERIODS[P], icon: "mdi:chart-bar" }, hero = "–", say = "&nbsp;";
    if (!d) body = `<div class="ehwait">Verlauf wird geladen …</div>`;
    else if (d.error || d.empty) body = `<div class="ehwait">${d.error ? "Die Langzeitstatistik ist gerade nicht erreichbar." : "Für diesen Zeitraum gibt es noch keine Daten."}</div>`;
    if (d && !d.error) {
      const f = cost ? { g: c.price, x: c.feed_in } : { g: 1, x: 1 };
      const rows = Array.from({ length: d.n }, (_, i) => ehSplit(d.cur.imp[i], d.cur.exp[i], d.cur.pv[i], d.cur.bin[i], d.cur.bout[i]));
      const total = (set, cnt, upTo) => {
        let home = 0, imp = 0, exp = 0, own = 0;
        for (let i = 0; i < Math.min(cnt, upTo + 1); i++) {
          const r = ehSplit(set.imp[i], set.exp[i], set.pv[i], set.bin[i], set.bout[i]);
          home += r.home; imp += r.imp; exp += r.exp; own += r.solar + r.batt;
        }
        return { home, imp, exp, own, val: cost ? imp * c.price - exp * c.feed_in : home };
      };
      const cur = total(d.cur, d.n, Infinity), prev = total(d.prev, d.pn, off < 0 ? Infinity : curIdx);
      hero = fmt(cur.val, cost || cur.val < 100 ? (cost ? 2 : 1) : 0);
      if (!d.empty) {
        const pct = prev.val > 0.05 ? ((cur.val - prev.val) / prev.val) * 100 : null, pn = this._prevName(P, off, ps), same = off < 0 ? "" : " bis zu dieser Zeit";
        say = pct === null ? `Noch kein Vergleich mit ${pn} möglich.` : Math.abs(pct) < 3 ? `Etwa so viel wie ${pn}${same}.`
          : `${fmt(Math.abs(pct))} % ${pct < 0 ? "weniger" : "mehr"} als ${pn}${same}.`;
        if (hasPv || hasBat) {
          const aut = cur.home > 0.01 ? Math.round((cur.own / cur.home) * 100) : 0;
          state = { key: aut >= 50 ? "ok" : "info", label: `${aut} % selbst erzeugt`, icon: "mdi:solar-power-variant" };
        } else if (pct !== null) state = { key: pct <= 0 ? "ok" : "info", label: pct <= 0 ? "Sparsamer" : "Mehr als sonst", icon: pct <= 0 ? "mdi:trending-down" : "mdi:trending-up" };
        // Säulen: gestapelt nach oben, Einspeisung fein nach unten – gleicher Maßstab für beides
        const W = 320, top = 14, bot = 116, slot = W / d.n, bw = Math.max(2, slot * (d.n > 12 ? 0.62 : 0.56)), r0 = Math.min(3.5, bw / 2);
        const segs = rows.map((r) => [[r.grid * f.g, EC.grid], [r.solar * f.g, EC.solar], [r.batt * f.g, EC.batt]]);
        const maxP = Math.max(0.0001, ...segs.map((sg) => sg.reduce((a, b) => a + b[0], 0))), maxN = hasExp ? Math.max(0, ...rows.map((r) => r.exp * f.x)) : 0;
        const k = (bot - top) / (maxP + maxN), base = top + maxP * k;
        const path = (x, y, w, h, r, up) => {
          r = Math.min(r, h, w / 2);
          return up ? `M${x} ${y + h}V${y + r}Q${x} ${y} ${x + r} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h}Z`
            : `M${x} ${y}V${y + h - r}Q${x} ${y + h} ${x + r} ${y + h}H${x + w - r}Q${x + w} ${y + h} ${x + w} ${y + h - r}V${y}Z`;
        };
        const grow = this._anim !== P + off; this._anim = P + off;
        const bars = segs.map((sg, i) => {
          const x = +(i * slot + (slot - bw) / 2).toFixed(2), delay = grow ? ` style="animation-delay:${Math.round(i * (360 / d.n))}ms"` : "";
          if (i > curIdx) return `<circle cx="${(x + bw / 2).toFixed(1)}" cy="${base}" r="1.3" class="stub"/>`;
          const vis = sg.filter((q) => q[0] * k >= 0.4), out = [];
          let y = base;
          vis.forEach(([v, col], j) => { const h = v * k; y -= h; out.push(`<path d="${path(x, +y.toFixed(2), +bw.toFixed(2), +h.toFixed(2), j === vis.length - 1 ? r0 : 0, true)}" fill="${col}" class="up"${delay}/>`); });
          const ex = rows[i].exp * f.x * k;
          if (ex >= 0.4) out.push(`<path d="${path(x, base + 0.8, +bw.toFixed(2), +ex.toFixed(2), r0, false)}" fill="${EC.solar}" class="dn"${delay}/>`);
          return `<g class="b" data-i="${i}">${out.join("") || `<circle cx="${(x + bw / 2).toFixed(1)}" cy="${base}" r="1.3" class="stub"/>`}</g>`;
        }).join("");
        const hits = rows.map((_, i) => i > curIdx ? "" : `<rect class="hit" data-i="${i}" x="${(i * slot).toFixed(2)}" y="0" width="${slot.toFixed(2)}" height="${bot + 4}"/>`).join("");
        const labels = rows.map((_, i) => { const t = this._axis(P, i, d.n); return t ? `<text x="${(i * slot + slot / 2).toFixed(1)}" y="${bot + 16}" class="ax" text-anchor="middle">${t}</text>` : ""; }).join("");
        body = `<svg class="eh${grow ? " grow" : ""}" viewBox="0 0 ${W} ${bot + 20}" role="img" aria-label="Verlauf ${this._title(P, off, s)}">
          <line x1="0" x2="${W}" y1="${top}" y2="${top}" class="grid"/><text x="${W}" y="${top - 4}" class="ax" text-anchor="end">${fmt(maxP, maxP < 10 ? 1 : 0)} ${unit}</text>
          ${bars}<line x1="0" x2="${W}" y1="${base}" y2="${base}" class="base"/>${labels}${hits}</svg>`;
        this._detail = (i) => {
          const r = rows[i], u = cost ? " €" : " kWh", dd = (v, fx) => fmt(v * fx, cost || v * fx < 10 ? (cost ? 2 : 1) : 0);
          return `<b>${this._barLabel(P, i, s)}</b>` + `<span>${dot(EC.grid)}${dd(r.grid, f.g)}</span>`
            + (hasPv ? `<span>${dot(EC.solar)}${dd(r.solar, f.g)}</span>` : "") + (hasBat ? `<span>${dot(EC.batt)}${dd(r.batt, f.g)}</span>` : "")
            + (hasExp ? `<span>${dot(EC.solar, 0.4)}−${dd(r.exp, f.x)}</span>` : "") + `<span class="eu">${u.trim()}</span>`;
        };
      }
    }
    this._shell(state.key, `
      ${this._head(c.name, state)}${seg}
      <div class="big" style="margin-top:12px" data-entity="${src.imp?.[0] || src.pv?.[0]}"><span class="num">${hero}</span><span class="u">${cost ? "€ Stromkosten" : "kWh verbraucht"}</span></div>
      <div class="muted">${say}</div>${nav}
      <div class="ehinfo">${legend}</div>${body}`, SEG_CSS + `
      .big[data-entity] { cursor: pointer; }
      .nav { display: flex; align-items: center; justify-content: space-between; margin-top: 10px; }
      .nt { font-size: 13px; font-weight: 500; color: var(--primary-text-color); }
      .nb { display: grid; place-items: center; width: 30px; height: 30px; border: none; border-radius: 50%; background: none; cursor: pointer; color: var(--secondary-text-color); }
      .nb:hover:not([disabled]) { background: var(--secondary-background-color, rgba(127,127,127,.1)); }
      .nb[disabled] { opacity: .25; cursor: default; }
      .ehinfo { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; min-height: 18px; margin: 6px 0 4px; font-size: 11.5px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .ehinfo b { font-weight: 600; color: var(--primary-text-color); }
      .ehinfo span { display: inline-flex; align-items: center; gap: 4px; }
      .ehinfo i { width: 7px; height: 7px; border-radius: 2px; display: inline-block; }
      .ehinfo .eu { margin-left: -6px; }
      .eh { width: 100%; display: block; overflow: visible; }
      .eh .grid { stroke: var(--divider-color, rgba(127,127,127,.2)); stroke-dasharray: 2 3; }
      .eh .base { stroke: var(--divider-color, rgba(127,127,127,.35)); }
      .eh .ax { font-size: 9px; fill: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .eh .stub { fill: var(--divider-color, rgba(127,127,127,.35)); }
      .eh .dn { opacity: .4; }
      .eh .b { transition: opacity .15s; }
      .eh.sel .b:not(.on) { opacity: .35; }
      .eh .hit { fill: transparent; cursor: pointer; }
      .eh.grow .up, .eh.grow .dn { transform-box: fill-box; animation: eh-grow .55s cubic-bezier(.2,.8,.2,1) both; }
      .eh.grow .up { transform-origin: 50% 100%; } .eh.grow .dn { transform-origin: 50% 0; }
      @keyframes eh-grow { from { transform: scaleY(0); } to { transform: scaleY(1); } }
      .ehwait { font-size: 12.5px; color: var(--secondary-text-color); text-align: center; padding: 48px 0; }`);
    const R = this.shadowRoot;
    R.querySelectorAll("[data-p]").forEach((b) => b.addEventListener("click", () => { this._period = b.dataset.p; this._off = 0; this._render(); this._load(); }));
    R.querySelectorAll("[data-nav]").forEach((b) => b.addEventListener("click", () => {
      const n = (this._off || 0) + +b.dataset.nav; if (n > 0 || n < -60) return; this._off = n; this._render(); this._load();
    }));
    const svg = R.querySelector("svg.eh"), info = R.querySelector(".ehinfo");
    if (svg && this._detail) {
      const sel = (i) => {
        this._sel = i; svg.classList.toggle("sel", i !== null);
        svg.querySelectorAll(".b").forEach((g) => g.classList.toggle("on", +g.dataset.i === i));
        info.innerHTML = i === null ? legend : this._detail(i);
      };
      svg.querySelectorAll(".hit").forEach((h) => {
        h.addEventListener("mouseenter", () => sel(+h.dataset.i));
        h.addEventListener("click", () => sel(this._sel === +h.dataset.i ? null : +h.dataset.i));
      });
      svg.addEventListener("mouseleave", () => sel(null));
      this._sel = null;
    }
  }
}

/* ================= 8 · Müllabfuhr ================= */
const BINS = [
  [/gelb|wertstoff|verpackung|plastik|leichtverpack|\blvp\b|recycl/, "#e3b800"],
  [/bio|kompost|organic|gr(ü|ue)nschnitt|gr(ü|ue)ngut|garten/, "#8a5a2b"],
  [/papier|pappe|karton|paper|blau/, "#2a6fd6"],
  [/glas/, "#2f9e6a"],
  [/sperr|elektro|schadstoff|problem|gift|metall|schrott/, "#9c5cc8"],
  [/rest|haus|schwarz|grau|general|residual/, "#6f747c"],
];
const binColor = (t) => (BINS.find(([re]) => re.test(t.toLowerCase())) || [0, "#8590a3"])[1];
const parseDay = (s) => {
  if (typeof s !== "string") return null;
  let m = /(\d{4})-(\d{2})-(\d{2})/.exec(s);
  if (m) return new Date(+m[1], m[2] - 1, +m[3]);
  m = /(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})\b/.exec(s);
  if (m) return new Date(m[3].length === 2 ? 2000 + +m[3] : +m[3], m[2] - 1, +m[1]);
  m = /(\d{1,2})\/(\d{1,2})\/(\d{4})/.exec(s);
  return m ? new Date(+m[3], m[1] - 1, +m[2]) : null;
};
/* Termine aus einem Sensor von „Waste Collection Schedule“ lesen: alle Formate der Integration */
function wasteFromSensor(e) {
  const out = [], a = e.attributes, today = dayStart();
  const SKIP = /^(friendly_name|icon|attribution|unit_of_measurement|device_class|last_update|daysTo|types|upcoming|entity_picture|state_class)$/i;
  for (const [k, v] of Object.entries(a)) {
    if (SKIP.test(k)) continue;
    const dk = parseDay(k);
    if (dk && typeof v === "string" && v.trim()) { v.split(/\s*,\s*/).forEach((t) => out.push({ d: dk, t })); continue; }
    const dv = parseDay(v);
    if (dv && !dk) out.push({ d: dv, t: k });
  }
  if (Array.isArray(a.upcoming)) for (const u of a.upcoming) {
    const d = parseDay(String(u?.date ?? "")); if (d) (u.types || []).forEach((t) => out.push({ d, t }));
  }
  if (!out.length && isLive(e)) {   // nur der Zustand, z. B. „Gelber Sack in 2 days“, „morgen“ oder „2“
    const st = String(e.state).trim(), m = /^(.*?)\s*(?:in\s+)?(\d+)\s*(?:days?|tagen?|tg\.?)$/i.exec(st);
    let n = m ? +m[2] : /^(heute|today)$/i.test(st) ? 0 : /^(morgen|tomorrow)$/i.test(st) ? 1 : /^\d+$/.test(st) ? +st : a.daysTo ?? null;
    if (n !== null && isFinite(n)) {
      const t = (m && m[1].trim()) || fname(e).replace(/\s*(abfuhr|abholung|termin|collection|pickup)\s*$/i, "");
      out.push({ d: new Date(today.getFullYear(), today.getMonth(), today.getDate() + n), t });
    }
  }
  return out;
}
const isWasteSensor = (hass, e) => regOf(hass, e.entity_id).platform === "waste_collection_schedule"
  || (WASTE_RE.test(lcName(e)) && (wasteFromSensor(e).length > 0));

class MangfallWasteCard extends MangfallBase {
  static defaults = { name: "Müllabfuhr", entities: [], show: 4 };
  static getStubConfig(hass) { return { entities: MangfallWasteCard._find(hass) }; }
  static _find(hass) {
    const S = Object.values(hass?.states || {});
    const sensors = S.filter((e) => e.entity_id.startsWith("sensor.") && isWasteSensor(hass, e)).map((e) => e.entity_id);
    const cals = S.filter((e) => e.entity_id.startsWith("calendar.") && (regOf(hass, e.entity_id).platform === "waste_collection_schedule"
      || WASTE_RE.test(lcName(e)))).map((e) => e.entity_id);
    return [...sensors, ...cals].slice(0, 12);
  }
  static getConfigForm() {
    return { schema: [
      { name: "entities", selector: { entity: { multiple: true, filter: [{ domain: "calendar" }, { domain: "sensor", integration: "waste_collection_schedule" }] } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [
        { name: "name", selector: { text: {} } },
        { name: "show", selector: { number: { min: 1, max: 10, mode: "box" } } }, BRANDING] }],
      computeLabel: (s) => ({ entities: "Abfuhrkalender", name: "Name", show: "Termine anzeigen", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ entities: "Leer lassen – die Karte findet Waste Collection Schedule und Müll-Kalender von selbst." }[s.name]) };
  }
  _check() { this._cal = {}; this._calAt = 0; }
  _auto(c, hass) { if (!c.entities?.length) c.entities = MangfallWasteCard._find(hass); return c; }
  _watch() { return this._config.entities; }
  _onHass() {
    const cals = this._config.entities.filter((id) => id.startsWith("calendar."));
    if (!cals.length || !this._hass.callApi || Date.now() - this._calAt < 6e5) return;
    this._calAt = Date.now();
    const s = dayStart(), e = new Date(+s + 42 * 864e5);
    Promise.all(cals.map((id) => this._hass.callApi("GET", `calendars/${id}?start=${encodeURIComponent(s.toISOString())}&end=${encodeURIComponent(e.toISOString())}`)
      .then((evs) => { this._cal[id] = (evs || []).map((ev) => ({ d: dayStart(new Date(ev.start?.date ? ev.start.date + "T00:00" : ev.start?.dateTime || ev.start)), t: ev.summary || "" })); })
      .catch(() => {}))).then(() => this._render());
  }
  _items() {
    const S = this._hass.states, today = +dayStart(), all = [];
    for (const id of this._config.entities) {
      const e = S[id]; if (!e) continue;
      if (id.startsWith("calendar.")) {
        if (this._cal[id]) all.push(...this._cal[id]);
        else if (e.attributes.message && e.attributes.start_time) all.push({ d: dayStart(new Date(e.attributes.start_time.replace(" ", "T"))), t: e.attributes.message });
      } else all.push(...wasteFromSensor(e));
    }
    const seen = new Set(), days = new Map();
    for (const it of all) {
      const t = String(it.t).trim(), k = +it.d + "|" + t.toLowerCase();
      if (!t || isNaN(+it.d) || +it.d < today || seen.has(k)) continue;
      seen.add(k); if (!days.has(+it.d)) days.set(+it.d, []); days.get(+it.d).push(t);
    }
    return [...days.entries()].sort((a, b) => a[0] - b[0]).map(([d, types]) => ({ d: new Date(d), types, n: Math.round((d - today) / 864e5) }));
  }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config;
    if (!c.entities.length) return this._setup(c.name, "Ich habe keinen Abfuhrkalender gefunden – zum Beispiel aus „Waste Collection Schedule“ oder einem Müll-Kalender.");
    const items = this._items(), next = items[0];
    const rel = (n) => (n === 0 ? "Heute" : n === 1 ? "Morgen" : n === 2 ? "Übermorgen" : `in ${n} Tagen`);
    const pill = (t) => `<span class="bin"><ha-icon icon="${/sack/i.test(t) ? "mdi:sack" : "mdi:delete"}" style="color:${binColor(t)}"></ha-icon>${esc(t)}</span>`;
    const state = !next ? { key: "info", label: "Keine Termine", icon: "mdi:calendar-blank" }
      : next.n === 0 ? { key: "info", label: "Heute Abholung", icon: "mdi:truck-outline" }
      : next.n === 1 ? { key: "low", label: "Heute Abend rausstellen", icon: "mdi:bell-ring-outline" }
      : { key: "ok", label: "Nichts zu tun", icon: "mdi:check-circle" };
    const date = (d) => d.toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "short" });
    const rows = items.slice(1, 1 + c.show).map((it) => `<div class="row" data-entity="${c.entities[0]}">
      <div class="dots">${it.types.map((t) => `<span class="dot" style="background:${binColor(t)}"></span>`).join("")}</div>
      <div class="rn">${it.types.map(esc).join(", ")}<div class="rs">${date(it.d)}</div></div><div class="rs">${rel(it.n)}</div></div>`).join("");
    this._shell(state.key, `
      ${this._head(c.name, state)}
      ${next ? `<div class="big" style="margin-top:6px" data-entity="${c.entities[0]}"><span class="num w">${rel(next.n)}</span>${next.n > 2 ? "" : `<span class="u">${date(next.d)}</span>`}</div>
        <div class="bins">${next.types.map(pill).join("")}</div>
        ${rows ? `<div class="rows">${rows}</div>` : ""}`
      : `<div class="muted pad">In den nächsten Wochen steht keine Abholung im Kalender.</div>`}`, `
      .num.w { font-size: 34px; } .big[data-entity] { cursor: pointer; }
      .bins { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
      .bin { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px 6px 8px; border-radius: 999px; font-size: 14px; font-weight: 500;
        color: var(--primary-text-color); background: var(--secondary-background-color, rgba(127,127,127,.1)); }
      .bin ha-icon { --mdc-icon-size: 20px; }
      .st-low .bin { animation: mc-nudge 2.8s ease-in-out infinite; }
      @keyframes mc-nudge { 0%, 88%, 100% { transform: none; } 92% { transform: translateY(-2px); } 96% { transform: translateY(0); } }
      .dots { display: flex; gap: 3px; width: 30px; } .dots .dot { flex: 0 0 9px; width: 9px; height: 9px; }`);
  }
}

/* ================= 9 · Wetter ================= */
const WX = { "clear-night": ["Klar", "mdi:weather-night"], cloudy: ["Bewölkt", "mdi:weather-cloudy"], exceptional: ["Unwetter", "mdi:alert-circle-outline"],
  fog: ["Nebel", "mdi:weather-fog"], hail: ["Hagel", "mdi:weather-hail"], lightning: ["Gewitter", "mdi:weather-lightning"],
  "lightning-rainy": ["Gewitter", "mdi:weather-lightning-rainy"], partlycloudy: ["Leicht bewölkt", "mdi:weather-partly-cloudy"],
  pouring: ["Starkregen", "mdi:weather-pouring"], rainy: ["Regen", "mdi:weather-rainy"], snowy: ["Schnee", "mdi:weather-snowy"],
  "snowy-rainy": ["Schneeregen", "mdi:weather-snowy-rainy"], sunny: ["Sonnig", "mdi:weather-sunny"], windy: ["Windig", "mdi:weather-windy"],
  "windy-variant": ["Windig", "mdi:weather-windy-variant"] };
const WET = new Set(["rainy", "pouring", "snowy", "snowy-rainy", "lightning-rainy", "hail"]);
const isWet = (f) => WET.has(f.condition) || (f.precipitation_probability ?? 0) >= 60 || (f.precipitation ?? 0) >= 0.5;
const wetWord = (f) => (/snow/.test(f.condition || "") ? "Schnee" : /lightning/.test(f.condition || "") ? "Gewitter" : /hail/.test(f.condition || "") ? "Hagel" : "Regen");
/* Ein Satz aus der Vorhersage: „Ab 15 Uhr Regen“, „Regen bis etwa 14 Uhr“, „Bis heute Abend trocken“ */
function weatherSay(nowCond, fc, daily, now = new Date()) {
  const list = (fc || []).filter((f) => toMs(f.datetime) >= +now - (daily ? 864e5 : 36e5)).slice(0, daily ? 6 : 13);
  if (!list.length) return "";
  const when = (f) => {
    const d = new Date(toMs(f.datetime));
    if (daily) { const n = Math.round((dayStart(d) - dayStart(now)) / 864e5); return n <= 0 ? "heute" : n === 1 ? "morgen" : `am ${d.toLocaleDateString("de-DE", { weekday: "long" })}`; }
    return (dayStart(d) > dayStart(now) ? "morgen " : "") + hh(d);
  };
  const cap = (s) => s[0].toUpperCase() + s.slice(1);
  if (daily) {
    const w = list.find(isWet);
    return w ? cap(`${when(w)} ${wetWord(w)}`) : "Die nächsten Tage bleibt es trocken";
  }
  if (!WET.has(nowCond)) {
    const i = list.findIndex(isWet);
    if (i === 0) return `Gleich ${wetWord(list[0])}`;
    if (i > 0) return cap(`ab ${when(list[i])} ${wetWord(list[i])}`);
    const max = Math.max(...list.map((f) => f.temperature).filter((t) => typeof t === "number"));
    return `Die nächsten 12 Stunden trocken${isFinite(max) ? `, bis ${fmt(max)}°` : ""}`;
  }
  const j = list.findIndex((f) => !isWet(f));
  return j > 0 ? `${wetWord(list[0])} bis etwa ${when(list[j])}` : j === 0 ? "Lässt gleich nach" : `${wetWord(list[0])} hält an`;
}

class MangfallWeatherCard extends MangfallBase {
  static defaults = { name: "", entity: "" };
  static getStubConfig(hass) { return { entity: FIND.weather(hass) }; }
  static getConfigForm() {
    return { schema: [{ name: "entity", selector: { entity: { domain: "weather" } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [{ name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ entity: "Wetter", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ entity: "Leer lassen – die Karte nimmt deinen Wetterdienst von selbst.", name: "Leer lassen zeigt den Ort." }[s.name]) };
  }
  _auto(c, hass) { if (!c.entity) c.entity = FIND.weather(hass); return c; }
  _watch() { return [this._config.entity].filter(Boolean); }
  getCardSize() { return 4; }
  disconnectedCallback() { this._unsub(); }
  _unsub() {
    if (this._subP) { const p = this._subP; this._subP = null; p.then((u) => typeof u === "function" && u()).catch(() => {}); }
    this._subKey = null;
  }
  _onHass() {
    const id = this._config.entity, st = id && this._hass.states[id];
    if (!st) return;
    const f = st.attributes.supported_features || 0, type = f & 2 ? "hourly" : f & 4 ? "twice_daily" : "daily", want = id + type;
    if (this._subKey === want) { if (this._noSub && Date.now() - (this._fcAt || 0) > 6e5) this._fetchFc(type); return; }
    this._unsub(); this._subKey = want; this._fc = null; this._fcType = type; this._noSub = false; this._subAt = Date.now();
    const conn = this._hass.connection;
    if (conn?.subscribeMessage) {
      this._subP = conn.subscribeMessage((ev) => { this._fc = ev.forecast || []; this._fcAt = Date.now(); this._render(); },
        { type: "weather/subscribe_forecast", forecast_type: type, entity_id: id }).catch(() => { this._noSub = true; this._fetchFc(type); return null; });
    } else { this._noSub = true; this._fetchFc(type); }
  }
  async _fetchFc(type) {
    this._fcAt = Date.now();
    try {
      const r = await this._hass.callService("weather", "get_forecasts", { type }, { entity_id: this._config.entity }, false, true);
      this._fc = r?.response?.[this._config.entity]?.forecast || [];
    } catch (e) { this._fc = this._fc || []; }
    this._render();
  }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, st = this._hass.states[c.entity];
    if (!c.entity) return this._setup(c.name || "Wetter", "Ich habe keinen Wetterdienst gefunden. In Home Assistant unter Geräte & Dienste zum Beispiel „Deutscher Wetterdienst“ oder „Met.no“ hinzufügen.");
    if (!isLive(st)) return this._missing(c.name || "Wetter", st ? `${esc(fname(st))} meldet sich gerade nicht.` : `„${c.entity}“ gibt es nicht mehr – bitte neu wählen.`);
    const a = st.attributes, [label, icon] = WX[st.state] || [st.state, "mdi:weather-cloudy"], daily = this._fcType !== "hourly";
    const fc = (this._fc || []).filter((f) => toMs(f.datetime) >= Date.now() - (daily ? 864e5 : 36e5)).slice(0, daily ? 6 : 13);
    const say = this._fc ? weatherSay(st.state, this._fc, daily) : "";
    const storm = ["lightning", "lightning-rainy", "hail", "exceptional"];
    const state = st.state === "exceptional" ? { key: "crit", label, icon } : storm.includes(st.state) || fc.some((f) => storm.includes(f.condition)) ? { key: "low", label, icon }
      : WET.has(st.state) || fc.some(isWet) ? { key: "info", label, icon } : { key: "ok", label, icon };
    const tu = a.temperature_unit || "°C", ws = a.wind_speed_unit || "km/h";
    const rain = fc.reduce((s, f) => s + (f.precipitation || 0), 0), prob = Math.max(0, ...fc.map((f) => f.precipitation_probability || 0));
    // Feine Linie: Temperatur der nächsten Stunden, darunter Regenwahrscheinlichkeit als zarte Säulen
    let line = "";
    const pts = fc.filter((f) => typeof f.temperature === "number");
    if (pts.length > 1) {
      const W = 300, H = 46, ts = pts.map((f) => f.temperature), lo = Math.min(...ts), hi = Math.max(...ts), span = Math.max(1, hi - lo);
      const X = (i) => (i / (pts.length - 1)) * W, Y = (t) => 6 + (1 - (t - lo) / span) * (H - 14);
      const d = pts.map((f, i) => `${i ? "L" : "M"}${X(i).toFixed(1)} ${Y(f.temperature).toFixed(1)}`).join(" ");
      const bars = pts.map((f, i) => { const p = (f.precipitation_probability ?? (isWet(f) ? 70 : 0)) / 100;
        return p > 0.05 ? `<rect x="${(X(i) - 3).toFixed(1)}" y="${(H - p * 16).toFixed(1)}" width="6" height="${(p * 16).toFixed(1)}" rx="2" class="pr"/>` : ""; }).join("");
      const lab = (i) => { const f = pts[i], dt = new Date(toMs(f.datetime));
        return `<span>${i === 0 && !daily ? "jetzt" : daily ? WD[dt.getDay()] : hh(dt)}<b>${fmt(f.temperature)}°</b></span>`; };
      const idx = daily ? pts.map((_, i) => i) : [0, Math.round((pts.length - 1) / 2), pts.length - 1];
      line = `<div class="wl"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${bars}
        <path d="${d} V ${H} H 0 Z" class="ar"/><path d="${d}" class="ln"/></svg><div class="wll">${idx.map(lab).join("")}</div></div>`;
    } else if (!this._fc) line = `<div class="wl muted">${Date.now() - (this._subAt || Date.now()) > 15000 ? "Keine Vorhersage verfügbar." : "Vorhersage wird geladen …"}</div>`;
    this._shell(state.key, `
      ${this._head(esc(c.name || fname(st)), state)}
      <div class="now" data-sheet="e:${c.entity}|temperature"><ha-icon class="wi" icon="${icon}"></ha-icon>
        <div class="big"><span class="num">${typeof a.temperature === "number" ? fmt(a.temperature) : "–"}</span><span class="u">${tu}</span></div></div>
      ${say ? `<div class="say">${esc(say)}.</div>` : ""}${line}
      ${this._tiles([
        a.apparent_temperature != null ? { label: "Gefühlt", value: fmt(a.apparent_temperature), unit: "°", icon: "mdi:thermometer", sheet: `e:${c.entity}|apparent_temperature` }
          : a.humidity != null ? { label: "Feuchte", value: fmt(a.humidity), unit: "%", icon: "mdi:water-percent", sheet: `e:${c.entity}|humidity` }
          : { label: "Wolken", value: a.cloud_coverage != null ? fmt(a.cloud_coverage) : "–", unit: "%", icon: "mdi:cloud-outline", sheet: `e:${c.entity}|cloud_coverage` },
        { label: "Wind", value: a.wind_speed != null ? fmt(a.wind_speed) : "–", unit: ws, sub: a.wind_gust_speed ? `Böen ${fmt(a.wind_gust_speed)}` : "", icon: "mdi:weather-windy", sheet: `e:${c.entity}|wind_speed` },
        rain > 0 || !prob ? { label: "Regen", value: fmt(rain, rain && rain < 10 ? 1 : 0), unit: a.precipitation_unit || "mm", sub: daily ? "nächste Tage" : "nächste 12 h", icon: "mdi:weather-rainy", entity: c.entity }
          : { label: "Regen", value: fmt(prob), unit: "%", sub: "Wahrscheinlichkeit", icon: "mdi:weather-rainy", entity: c.entity },
      ])}`, `
      .now { display: flex; align-items: center; gap: 12px; margin-top: 6px; cursor: pointer; }
      .wi { --mdc-icon-size: 44px; color: var(--mc-state); }
      .say { font-size: 14px; color: var(--primary-text-color); margin-top: 8px; }
      .wl { margin-top: 10px; } .wl svg { width: 100%; height: 46px; display: block; overflow: visible; }
      .ln { fill: none; stroke: var(--primary-text-color); stroke-opacity: .7; stroke-width: 1.5; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
      .ar { fill: var(--primary-text-color); opacity: .05; }
      .pr { fill: var(--info-color, #2a78d6); opacity: .35; }
      .wll { display: flex; justify-content: space-between; font-size: 11px; color: var(--secondary-text-color); margin-top: 3px; }
      .wll span { display: flex; flex-direction: column; align-items: center; gap: 1px; }
      .wll span:first-child { align-items: flex-start; } .wll span:last-child { align-items: flex-end; }
      .wll b { font-weight: 600; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }`);
  }
}

/* ================= 10 · Anwesenheit ================= */
class MangfallPresenceCard extends MangfallBase {
  static defaults = { name: "Zu Hause", entities: [] };
  static getStubConfig(hass) { return { entities: Object.keys(hass.states).filter((id) => id.startsWith("person.")).slice(0, 8) }; }
  static getConfigForm() {
    return { schema: [{ name: "entities", selector: { entity: { multiple: true, filter: { domain: "person" } } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [{ name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ entities: "Personen", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ entities: "Leer lassen – die Karte zeigt alle Personen aus deinem Home Assistant." }[s.name]) };
  }
  _auto(c, hass) { if (!c.entities?.length) c.entities = Object.keys(hass.states).filter((id) => id.startsWith("person.")); return c; }
  _watch() { return this._config.entities; }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, S = this._hass.states;
    const ppl = c.entities.map((id) => S[id]).filter(Boolean);
    if (!ppl.length) return this._setup(c.name, "Ich habe noch keine Personen gefunden. Lege sie unter Einstellungen › Personen an und verknüpfe ein Handy.");
    const where = (p) => (p.state === "home" ? "zu Hause" : p.state === "not_home" ? "unterwegs" : p.state === "unknown" ? "Standort unbekannt" : p.state === "unavailable" ? "keine Verbindung" : esc(p.state));
    const sorted = [...ppl].sort((a, b) => (b.state === "home") - (a.state === "home") || fname(a).localeCompare(fname(b), "de"));
    const home = ppl.filter((p) => p.state === "home").length;
    const state = home === ppl.length ? { key: "ok", label: ppl.length > 1 ? "Alle zu Hause" : "Zu Hause", icon: "mdi:home-account" }
      : home ? { key: "ok", label: "Jemand zu Hause", icon: "mdi:home-account" } : { key: "info", label: "Alle unterwegs", icon: "mdi:home-export-outline" };
    const av = (p) => {
      const pic = p.attributes.entity_picture, url = pic && (this._hass.hassUrl ? this._hass.hassUrl(pic) : pic);
      const ini = fname(p).split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
      return `<div class="av${p.state === "home" ? " in" : ""}">${url ? `<img src="${esc(url)}" alt="" loading="lazy">` : `<span>${esc(ini)}</span>`}</div>`;
    };
    const rows = sorted.map((p) => `<div class="row" data-entity="${p.entity_id}">${av(p)}
      <div class="rn">${esc(fname(p).split(/\s+/)[0])}<div class="rs">${where(p)}</div></div><div class="rs">${["unknown", "unavailable"].includes(p.state) ? "" : ago(p.last_changed)}</div></div>`).join("");
    this._shell(state.key, `
      ${this._head(c.name, state)}
      <div class="big" style="margin-top:6px"><span class="num">${home}</span><span class="u">von ${ppl.length} zu Hause</span></div>
      <div class="rows">${rows}</div>`, `
      .av { position: relative; flex: 0 0 36px; width: 36px; height: 36px; border-radius: 50%; display: grid; place-items: center; overflow: hidden;
        background: var(--secondary-background-color, rgba(127,127,127,.12)); color: var(--secondary-text-color); font-size: 13px; font-weight: 600;
        filter: grayscale(1); opacity: .6; transition: opacity .3s, filter .3s; }
      .av img { width: 100%; height: 100%; object-fit: cover; }
      .av.in { filter: none; opacity: 1; box-shadow: 0 0 0 2px var(--ha-card-background, var(--card-background-color, #fff)), 0 0 0 4px var(--success-color, #0ca30c); }
      .row:first-child { padding-top: 4px; }`);
  }
}

/* ================= 11 · Lichter ================= */
class MangfallLightsCard extends MangfallBase {
  static defaults = { name: "Lichter", exclude: [] };
  static getStubConfig() { return {}; }   // leer = alle Lampen, nach Räumen sortiert
  static getConfigForm() {
    return { schema: [{ name: "exclude", selector: { entity: { multiple: true, filter: { domain: "light" } } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [{ name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ exclude: "Ausblenden", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ exclude: "Die Karte findet alle Lampen von selbst und sortiert sie nach Räumen. Hier wählst du nur, was sie nicht zeigen soll." }[s.name]) };
  }
  _list() {
    const H = this._hass;
    return Object.values(H.states).filter((e) => e.entity_id.startsWith("light.") && !Array.isArray(e.attributes.entity_id)
      && !this._config.exclude.includes(e.entity_id) && !isHidden(H, e.entity_id));
  }
  _watch() { return this._hass ? this._list().map((e) => e.entity_id) : []; }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, H = this._hass, all = this._list();
    if (!all.length) return this._setup(c.name, "Ich habe keine Lampen in deinem Home Assistant gefunden.");
    const on = all.filter((e) => e.state === "on"), rooms = new Map();
    for (const e of all) {
      const a = areaOf(H, e.entity_id), n = (a && H.areas?.[a]?.name) || "Ohne Raum";
      if (!rooms.has(n)) rooms.set(n, []); rooms.get(n).push(e);
    }
    const order = [...rooms.keys()].sort((a, b) => (a === "Ohne Raum") - (b === "Ohne Raum") || a.localeCompare(b, "de"));
    const state = on.length ? { key: "info", label: `${on.length} an`, icon: "mdi:lightbulb-on" } : { key: "ok", label: "Alle aus", icon: "mdi:lightbulb-off-outline" };
    const pill = (e, room) => {
      const live = isLive(e), lit = e.state === "on", a = e.attributes;
      const col = lit && a.rgb_color ? `rgb(${a.rgb_color.join(",")})` : "";
      const pct = lit && a.brightness != null ? Math.round((a.brightness / 255) * 100) : null;
      const nm = fname(e).replace(new RegExp(`^${room.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s+`, "i"), "") || fname(e);
      return `<button class="lp${lit ? " on" : ""}" data-l="${e.entity_id}"${live ? "" : " disabled title=\"nicht erreichbar\""}${col ? ` style="--lc:${col}"` : ""}>
        <ha-icon icon="${lit ? "mdi:lightbulb-on" : "mdi:lightbulb-outline"}"></ha-icon><span class="ln">${esc(nm)}</span>${pct !== null ? `<span class="lb">${pct} %</span>` : live ? "" : `<span class="lb">offline</span>`}</button>`;
    };
    const groups = order.map((r) => { const ls = rooms.get(r), n = ls.filter((e) => e.state === "on").length;
      return `<div class="rm"><div class="rh"><span>${esc(r)}</span>${n ? `<span>${n} an</span>` : ""}</div><div class="lps">${ls.map((e) => pill(e, r)).join("")}</div></div>`; }).join("");
    this._shell(state.key, `
      ${this._head(c.name, state)}
      <div class="top"><div class="big"><span class="num">${on.length}</span><span class="u">von ${all.length} an</span></div>
        ${on.length ? `<button class="off"><ha-icon icon="mdi:power"></ha-icon>Alle aus</button>` : ""}</div>
      ${groups}`, `
      .top { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 6px; }
      .off { display: inline-flex; align-items: center; gap: 4px; border: none; border-radius: 999px; padding: 6px 12px; cursor: pointer; font: inherit; font-size: 13px; font-weight: 500;
        color: var(--primary-text-color); background: var(--secondary-background-color, rgba(127,127,127,.12)); }
      .off ha-icon { --mdc-icon-size: 16px; }
      .rm { margin-top: 12px; }
      .rh { display: flex; justify-content: space-between; font-size: 11.5px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--secondary-text-color); margin-bottom: 6px; }
      .rh span:last-child { font-weight: 500; letter-spacing: 0; text-transform: none; }
      .lps { display: grid; grid-template-columns: repeat(auto-fill, minmax(132px, 1fr)); gap: 6px; }
      .lp { --lc: #f5b400; display: flex; align-items: center; gap: 6px; min-width: 0; padding: 9px 10px; border: none; border-radius: 12px; cursor: pointer; font: inherit; text-align: left;
        color: var(--primary-text-color); background: var(--secondary-background-color, rgba(127,127,127,.08)); transition: background .25s; }
      .lp ha-icon { --mdc-icon-size: 19px; flex: 0 0 auto; color: var(--secondary-text-color); transition: color .25s, filter .25s; }
      .lp.on { background: color-mix(in srgb, var(--lc) 16%, var(--secondary-background-color, transparent)); }
      .lp.on ha-icon { color: var(--lc); filter: drop-shadow(0 0 4px color-mix(in srgb, var(--lc) 60%, transparent)); }
      .lp[disabled] { opacity: .45; cursor: default; }
      .lp:hover:not([disabled]) { background: color-mix(in srgb, var(--primary-text-color) 7%, var(--secondary-background-color, transparent)); }
      .lp.on:hover { background: color-mix(in srgb, var(--lc) 22%, var(--secondary-background-color, transparent)); }
      .lp:focus-visible, .pm:focus-visible, .btns button:focus-visible, .seg button:focus-visible, .nb:focus-visible, .off:focus-visible { outline: 2px solid var(--primary-color, #2a78d6); outline-offset: 2px; }
      .ln { flex: 1; min-width: 0; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .lb { font-size: 11px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }`);
    const R = this.shadowRoot;
    R.querySelectorAll("[data-l]").forEach((b) => {
      b.addEventListener("click", () => { b.classList.toggle("on"); H.callService("light", "toggle", { entity_id: b.dataset.l }); });
      b.addEventListener("contextmenu", (ev) => { ev.preventDefault(); this._moreInfo(b.dataset.l); });
    });
    R.querySelector(".off")?.addEventListener("click", () => {
      R.querySelectorAll(".lp.on").forEach((b) => b.classList.remove("on"));
      H.callService("light", "turn_off", { entity_id: on.map((e) => e.entity_id) });
    });
  }
}

/* ================= 12 · Saugroboter ================= */
const VAC = {
  cleaning: { label: "Saugt", key: "ok", icon: "mdi:robot-vacuum" },
  docked: { label: "In der Station", key: "info", icon: "mdi:home-import-outline" },
  idle: { label: "Bereit", key: "info", icon: "mdi:robot-vacuum" },
  paused: { label: "Pausiert", key: "low", icon: "mdi:pause-circle" },
  returning: { label: "Fährt zur Station", key: "info", icon: "mdi:home-export-outline" },
  error: { label: "Fehler", key: "crit", icon: "mdi:alert-octagon" },
};
class MangfallVacuumCard extends MangfallBase {
  static defaults = { name: "Saugroboter", entity: "", battery: "" };
  static getStubConfig(hass) { return { entity: FIND.vacuum(hass) }; }
  static getConfigForm() {
    return { schema: [{ name: "entity", selector: { entity: { domain: "vacuum" } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [
        { name: "battery", selector: sensorSel("battery") }, { name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ entity: "Saugroboter", battery: "Akku", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ entity: "Leer lassen – die Karte nimmt deinen Saugroboter von selbst.", battery: "Leer lassen – die Karte nimmt den Akku des Roboters von selbst." }[s.name]) };
  }
  _auto(c, hass) {
    if (!c.entity) c.entity = FIND.vacuum(hass);
    if (c.entity && !c.battery) c.battery = sameDevice(hass, c.entity, (e) => e.attributes.device_class === "battery" && e.entity_id.startsWith("sensor."))?.entity_id || "";
    if (c.entity) {
      c._area = sameDevice(hass, c.entity, (e) => e.entity_id.startsWith("sensor.") && /m²|m2/.test(e.attributes.unit_of_measurement || "") && !/total|gesamt/.test(lcName(e)))?.entity_id || "";
      c._time = sameDevice(hass, c.entity, (e) => e.entity_id.startsWith("sensor.") && e.attributes.device_class === "duration" && /clean|reinig|saug/.test(lcName(e)) && !/total|gesamt|filter|brush|b(ü|ue)rste|sensor/.test(lcName(e)))?.entity_id || "";
    }
    return c;
  }
  _watch() { const c = this._config; return [c.entity, c.battery, c._area, c._time].filter(Boolean); }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, S = this._hass.states, st = S[c.entity];
    if (!c.entity) return this._setup(c.name, "Ich habe keinen Saugroboter in deinem Home Assistant gefunden.");
    if (!st) return this._missing(c.name, `„${c.entity}“ gibt es nicht mehr – bitte neu wählen.`);
    const m = VAC[st.state] || { label: st.state === "unavailable" ? "Nicht erreichbar" : st.state, key: st.state === "unavailable" ? "crit" : "info", icon: "mdi:robot-vacuum-alert" };
    const bat = c.battery ? parseFloat(S[c.battery]?.state) : parseFloat(st.attributes.battery_level);
    const run = st.state === "cleaning", area = c._area && parseFloat(S[c._area]?.state), tm = c._time && S[c._time];
    const tiles = [
      { label: "Akku", value: isNaN(bat) ? "–" : fmt(bat), unit: "%", sub: isNaN(bat) ? "kein Akkuwert" : bat < 20 ? "lädt bald" : st.state === "docked" && bat < 100 ? "lädt" : "", icon: "mdi:battery-high", entity: c.battery || null, sheet: c.battery ? "" : `e:${c.entity}|battery_level` },
      { label: "Status", value: m.label.split(" ")[0], unit: "", sub: ago(st.last_changed), icon: m.icon, entity: c.entity },
    ];
    if (area > 0) tiles.push({ label: "Fläche", value: fmt(area), unit: "m²", sub: "zuletzt", icon: "mdi:texture-box", entity: c._area });
    else if (st.attributes.fan_speed) tiles.push({ label: "Saugstärke", value: esc(st.attributes.fan_speed), unit: "", icon: "mdi:fan", entity: c.entity });
    if (tm && isLive(tm) && tiles.length < 4) tiles.push({ label: "Dauer", value: fmt(parseFloat(tm.state)), unit: tm.attributes.unit_of_measurement || "min", sub: "zuletzt", icon: "mdi:timer-outline", entity: c._time });
    this._shell(m.key, `
      ${this._head(c.name, m)}
      <div class="floor ${run ? "on" : ""}" data-entity="${c.entity}"><div class="trail"></div><ha-icon class="bot" icon="mdi:robot-vacuum"></ha-icon>
        <ha-icon class="dock" icon="mdi:home-outline"></ha-icon></div>
      ${this._tiles(tiles)}
      <div class="btns">
        <button data-svc="${run ? "pause" : "start"}" class="${run ? "on" : ""}"><ha-icon icon="${run ? "mdi:pause" : "mdi:play"}"></ha-icon>${run ? "Pause" : "Saugen"}</button>
        <button data-svc="return_to_base"><ha-icon icon="mdi:home-import-outline"></ha-icon>Station</button>
        <button data-svc="locate"><ha-icon icon="mdi:map-marker-radius-outline"></ha-icon>Finden</button>
      </div>`, BTN_CSS + `
      .floor { position: relative; height: 70px; margin-top: 12px; border-radius: 12px; overflow: hidden; cursor: pointer;
        background: repeating-linear-gradient(90deg, transparent 0 38px, rgba(127,127,127,.07) 38px 39px), color-mix(in srgb, #b98a5a 16%, var(--secondary-background-color, #eee)); }
      .bot { position: absolute; left: 14%; top: 50%; transform: translate(-50%, -50%); --mdc-icon-size: 30px; color: var(--primary-text-color); z-index: 1; }
      .dock { position: absolute; left: 14px; bottom: 8px; --mdc-icon-size: 16px; color: var(--secondary-text-color); opacity: .6; }
      .trail { position: absolute; inset: 0; opacity: 0; }
      .floor.on .bot { animation: mc-vac 12s ease-in-out infinite; }
      .floor.on .trail { opacity: 1; background: linear-gradient(90deg, rgba(255,255,255,.18), transparent); animation: mc-fade 12s linear infinite; }
      @keyframes mc-vac { 0% { left: 14%; top: 30%; } 25% { left: 86%; top: 30%; } 50% { left: 86%; top: 70%; } 75% { left: 14%; top: 70%; } 100% { left: 14%; top: 30%; } }
      @keyframes mc-fade { 0%, 100% { opacity: .2; } 50% { opacity: .7; } }`);
    this.shadowRoot.querySelectorAll("[data-svc]").forEach((b) => b.addEventListener("click", () =>
      this._hass.callService("vacuum", b.dataset.svc, { entity_id: c.entity })));
  }
}

/* ================= 13 · Dynamischer Strompreis ================= */
const PT = ["start", "start_time", "startsAt", "starts_at", "time", "from", "datetime", "begin", "start_datetime", "date"];
const PV = ["value", "price", "total", "price_per_kwh", "price_ct_per_kwh", "price_eur_per_mwh", "marketprice", "buy_price_per_kwh", "energy"];
/* Preise aus den Attributen lesen: Nordpool (today/tomorrow, raw_*), EPEX Spot (data), ENTSO-E (prices_*), Tibber-Varianten u. a. */
function priceSeries(e, now = new Date()) {
  const a = e?.attributes || {}, map = new Map(), mid = +dayStart(now);
  const objs = (arr) => arr.forEach((o) => {
    if (!o || typeof o !== "object") return;
    const tk = PT.find((k) => o[k] != null), vk = PV.find((k) => o[k] != null && isFinite(parseFloat(o[k])))
      || Object.keys(o).find((k) => /price|value|total/i.test(k) && isFinite(parseFloat(o[k])));
    if (!tk || !vk) return;
    const t = toMs(o[tk]); if (isFinite(t)) map.set(t, parseFloat(o[vk]));
  });
  const nums = (arr, day) => { const slot = arr.length >= 90 ? 9e5 : 36e5; arr.forEach((v, i) => { const x = parseFloat(v); if (isFinite(x)) map.set(mid + day * 864e5 + i * slot, x); }); };
  for (const k of ["raw_today", "raw_tomorrow", "prices_today", "prices_tomorrow", "today", "tomorrow", "data", "prices", "forecast", "rates"]) {
    const arr = a[k];
    if (!Array.isArray(arr) || !arr.length) continue;
    if (arr.some((x) => x && typeof x === "object")) objs(arr); else nums(arr, /tomorrow/.test(k) ? 1 : 0);
  }
  return [...map.entries()].sort((x, y) => x[0] - y[0]).map(([t, v]) => ({ t, v }));
}
/* Attributwerte in die Einheit des Sensors bringen (manche liefern €/MWh, andere ct/kWh) */
function priceScale(series, nowVal, now = Date.now()) {
  if (!series.length || !isFinite(nowVal) || nowVal === 0) return 1;
  let cur = series[0]; for (const p of series) if (p.t <= now) cur = p;
  if (!cur.v) return 1;
  const r = nowVal / cur.v;
  const best = [1, 100, 0.01, 0.1, 10, 1000, 0.001].reduce((b, f) => (Math.abs(Math.log(r / f)) < Math.abs(Math.log(r / b)) ? f : b), 1);
  return Math.abs(Math.log(r / best)) < Math.log(1.6) ? best : 1;
}
/* Stundenmittel aus 15-min- oder Stundenwerten */
function priceHours(series) {
  const h = new Map();
  for (const p of series) { const k = Math.floor(p.t / 36e5) * 36e5; const o = h.get(k) || [0, 0]; o[0] += p.v; o[1]++; h.set(k, o); }
  return [...h.entries()].sort((a, b) => a[0] - b[0]).map(([t, [s, n]]) => ({ t, v: s / n }));
}
function cheapest(hours, len, from) {
  const fut = hours.filter((p) => p.t + 36e5 > from);
  let best = null;
  for (let i = 0; i + len <= fut.length; i++) {
    if (fut[i + len - 1].t - fut[i].t !== (len - 1) * 36e5) continue;   // nur zusammenhängende Stunden
    const avg = fut.slice(i, i + len).reduce((s, p) => s + p.v, 0) / len;
    if (!best || avg < best.avg) best = { t: fut[i].t, avg };
  }
  return best;
}
const TIBBER_LEVEL = { VERY_CHEAP: ["ok", "Sehr günstig"], CHEAP: ["ok", "Günstig"], NORMAL: ["info", "Normal"], EXPENSIVE: ["low", "Teuer"], VERY_EXPENSIVE: ["crit", "Sehr teuer"] };

class MangfallPriceCard extends MangfallBase {
  static product = "WATT";
  static defaults = { name: "Strompreis", entity: "", hours: 3 };
  static getStubConfig(hass) { return { entity: FIND.price(hass) }; }
  static getConfigForm() {
    return { schema: [{ name: "entity", selector: sensorSel(null) },
      { name: "hours", selector: { number: { min: 1, max: 8, mode: "box", unit_of_measurement: "h" } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [{ name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ entity: "Preis-Sensor", hours: "Günstigstes Zeitfenster", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ entity: "Der aktuelle Strompreis, z. B. von Tibber, Nordpool, EPEX Spot oder Awattar. Leer lassen – die Karte sucht ihn selbst.",
        hours: "Wie lange braucht dein Gerät? Die Karte sucht den günstigsten Block dieser Länge, z. B. für Waschmaschine oder Auto." }[s.name]) };
  }
  _check() { this._extra = null; this._exAt = 0; this._day = 0; }
  _auto(c, hass) { if (!c.entity) c.entity = FIND.price(hass); return c; }
  _watch() { return [this._config.entity].filter(Boolean); }
  getCardSize() { return 5; }
  _onHass() {
    const st = this._hass.states[this._config.entity];
    if (!st || priceSeries(st).length || Date.now() - this._exAt < 6e5) return;
    if (regOf(this._hass, st.entity_id).platform === "tibber" || st.attributes.price_level) this._fetchTibber();
  }
  async _fetchTibber() {
    this._exAt = Date.now();
    try {
      const s = dayStart(), e = new Date(+s + 2 * 864e5);
      const r = await this._hass.callService("tibber", "get_prices", { start: s.toISOString(), end: e.toISOString() }, undefined, false, true);
      const arr = Object.values(r?.response?.prices || {})[0] || [];
      this._extra = arr.map((p) => ({ t: toMs(p.start_time), v: parseFloat(p.price) })).filter((p) => isFinite(p.t) && isFinite(p.v));
    } catch (e) { this._extra = []; }
    this._render();
  }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, st = this._hass.states[c.entity];
    if (!c.entity) return this._setup(c.name, "Ich habe keinen Sensor mit dynamischem Strompreis gefunden – zum Beispiel von Tibber, Nordpool, EPEX Spot oder Awattar.");
    if (!isLive(st)) return this._missing(c.name, st ? `${esc(fname(st))} meldet sich gerade nicht.` : `„${c.entity}“ gibt es nicht mehr – bitte neu wählen.`);
    const u = st.attributes.unit_of_measurement || "", toCt = /ct|cent/i.test(u) ? 1 : /mwh/i.test(u) ? 0.1 : 100;
    const nowV = parseFloat(st.state) * toCt, raw = priceSeries(st).length ? priceSeries(st) : this._extra || [];
    const sc = priceScale(raw, parseFloat(st.state)) * toCt, hours = priceHours(raw.map((p) => ({ t: p.t, v: p.v * sc })));
    const t0 = +dayStart(), now = Date.now(), hasTom = hours.some((p) => p.t >= t0 + 864e5 && p.t < t0 + 2 * 864e5);
    const day = hasTom ? this._day : 0, d0 = t0 + day * 864e5, today = hours.filter((p) => p.t >= d0 && p.t < d0 + 864e5);
    const ref = hours.filter((p) => p.t >= t0 && p.t < t0 + 864e5), vals = (ref.length ? ref : [{ v: nowV }]).map((p) => p.v).sort((a, b) => a - b);
    const q = (f) => vals[Math.min(vals.length - 1, Math.floor(f * vals.length))];
    const lvl = TIBBER_LEVEL[st.attributes.price_level];
    const state = lvl ? { key: lvl[0], label: lvl[1], icon: "mdi:cash" }
      : nowV <= q(0.33) ? { key: "ok", label: "Günstig jetzt", icon: "mdi:trending-down" }
      : nowV >= q(0.67) ? { key: "low", label: "Teuer jetzt", icon: "mdi:trending-up" } : { key: "info", label: "Normal", icon: "mdi:minus" };
    const win = cheapest(hours, c.hours, now);
    let say = "";
    if (win) {
      const a = new Date(win.t), b = new Date(win.t + c.hours * 36e5), tom = dayStart(a) > dayStart();
      say = `Günstigste ${c.hours === 1 ? "Stunde" : `${c.hours} Stunden`}: ${tom ? "morgen " : ""}${win.t <= now ? "jetzt" : a.getHours()}–${b.getHours()} Uhr · Ø ${fmt(win.avg, 1)} ct`;
    }
    let chart = "";
    if (today.length >= 2) {
      const W = 300, H = 64, n = 24, slot = W / n, bw = slot * 0.64, lo = Math.min(0, ...today.map((p) => p.v)), hi = Math.max(...today.map((p) => p.v), 0.01);
      const Y = (v) => H - ((v - lo) / (hi - lo)) * (H - 4), y0 = Y(0);
      const inWin = (t) => win && t >= win.t && t < win.t + c.hours * 36e5;
      const bars = today.map((p) => {
        const i = Math.round((p.t - d0) / 36e5); if (i < 0 || i >= n) return "";
        const y = Math.min(Y(p.v), y0), h = Math.max(1.5, Math.abs(Y(p.v) - y0)), cls = p.v <= q(0.33) ? "lo" : p.v >= q(0.67) ? "hi" : "mid";
        return `<rect x="${(i * slot + (slot - bw) / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="${Math.min(2.5, bw / 2).toFixed(1)}"
          class="pb ${cls}${p.t + 36e5 <= now ? " past" : ""}${p.t <= now && now < p.t + 36e5 ? " cur" : ""}${inWin(p.t) ? " win" : ""}"><title>${hh(p.t)}: ${fmt(p.v, 1)} ct</title></rect>`;
      }).join("");
      const band = win && win.t >= d0 && win.t < d0 + 864e5 ? `<rect x="${(((win.t - d0) / 36e5) * slot).toFixed(1)}" y="0" width="${(c.hours * slot).toFixed(1)}" height="${H}" rx="6" class="band"/>` : "";
      chart = `<div class="pc"><svg viewBox="0 0 ${W} ${H}" aria-hidden="true">${band}${bars}</svg>
        <div class="pl"><span>0</span><span>6</span><span>12</span><span>18</span><span>24 Uhr</span></div></div>`;
    }
    const lowP = ref.length ? ref.reduce((a, b) => (b.v < a.v ? b : a)) : null, hiP = ref.length ? ref.reduce((a, b) => (b.v > a.v ? b : a)) : null;
    const avg = ref.length ? ref.reduce((s, p) => s + p.v, 0) / ref.length : NaN;
    this._shell(state.key, `
      ${this._head(c.name, state)}
      <div class="big" style="margin-top:6px" data-entity="${c.entity}"><span class="num">${fmt(nowV, 1)}</span><span class="u">ct/kWh</span></div>
      <div class="muted">${say ? esc(say) : ref.length ? "&nbsp;" : "Für die Tagesbalken braucht die Karte einen Sensor mit Preisen für heute (z. B. Nordpool oder EPEX Spot)."}</div>
      ${hasTom ? `<div class="seg"><button data-d="0" class="${day === 0 ? "on" : ""}">Heute</button><button data-d="1" class="${day === 1 ? "on" : ""}">Morgen</button></div>` : ""}
      ${chart}
      ${ref.length ? this._tiles([
        { label: "Tief", value: fmt(lowP.v, 1), unit: "ct", sub: hh(lowP.t), icon: "mdi:arrow-down", entity: c.entity },
        { label: "Schnitt", value: fmt(avg, 1), unit: "ct", sub: "heute", icon: "mdi:approximately-equal", entity: c.entity },
        { label: "Hoch", value: fmt(hiP.v, 1), unit: "ct", sub: hh(hiP.t), icon: "mdi:arrow-up", entity: c.entity },
      ]) : ""}`, SEG_CSS + `
      .big[data-entity] { cursor: pointer; }
      .pc { margin-top: 12px; } .pc svg { width: 100%; height: auto; display: block; overflow: visible; }
      .pb { transition: opacity .2s; } .pb.lo { fill: var(--success-color, #0ca30c); } .pb.mid { fill: var(--secondary-text-color); opacity: .55; } .pb.hi { fill: var(--warning-color, #eda100); }
      .pb.past { opacity: .25; } .pb.cur { opacity: 1; stroke: var(--primary-text-color); stroke-width: 1.2; }
      .band { fill: var(--success-color, #0ca30c); opacity: .1; }
      .pl { display: flex; justify-content: space-between; font-size: 10.5px; color: var(--secondary-text-color); margin-top: 3px; }`);
    this.shadowRoot.querySelectorAll("[data-d]").forEach((b) => b.addEventListener("click", () => { this._day = +b.dataset.d; this._render(); }));
  }
}

/* ================= 14 · E-Auto & Wallbox ================= */
class MangfallEvCard extends MangfallBase {
  static defaults = { name: "E-Auto", soc: "", range: "", power: "", plug: "", finish: "", target: 100 };
  static getStubConfig(hass) {
    const c = MangfallEvCard.prototype._auto.call({ _user: {} }, { ...MangfallEvCard.defaults }, hass), out = {};
    for (const k of ["soc", "range", "power", "plug", "finish"]) if (c[k]) out[k] = c[k];
    return out;
  }
  static getConfigForm() {
    return { schema: [
      { name: "soc", selector: sensorSel("battery") },
      { name: "power", selector: sensorSel("power") },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [
        { name: "range", selector: sensorSel("distance") },
        { name: "plug", selector: { entity: { filter: { domain: "binary_sensor", device_class: "plug" } } } },
        { name: "finish", selector: { entity: { filter: [{ domain: "sensor", device_class: "timestamp" }, { domain: "sensor", device_class: "duration" }] } } },
        { name: "target", selector: { number: { min: 50, max: 100, step: 5, unit_of_measurement: "%", mode: "box" } } },
        { name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ soc: "Ladestand Auto", power: "Ladeleistung", range: "Reichweite", plug: "Stecker", finish: "Ladeende", target: "Laden bis",
        name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ soc: "Der Akkustand deines Autos in Prozent. Leer lassen – die Karte sucht ihn selbst.",
        power: "Die Leistung deiner Wallbox oder Ladesteckdose in Watt.",
        finish: "Meldet dein Auto, wann es fertig ist? Sonst schätzt die Karte es aus dem Verlauf.", target: "Wird genommen, wenn dein Auto kein Ladeziel meldet." }[s.name]) };
  }
  _check() { this._hist = null; this._histAt = 0; }
  _auto(c, hass) {
    const near = (id, f) => (id ? sameDevice(hass, id, f)?.entity_id : "") || "";
    if (!c.soc) c.soc = FIND.ev_soc(hass);
    if (!c.power) c.power = near(c.soc, (e) => isPower(e) && !/limit|max|min/.test(lcName(e))) || FIND.ev_power(hass);
    const any = c.soc || c.power;
    if (!c.range && any) c.range = near(c.soc, (e) => e.attributes.device_class === "distance" && /range|reichweite/.test(lcName(e))) || (c.soc ? FIND.ev_range(hass) : "");
    if (!c.plug && any) c.plug = near(c.soc, (e) => e.entity_id.startsWith("binary_sensor.") && e.attributes.device_class === "plug")
      || near(c.power, (e) => e.entity_id.startsWith("binary_sensor.") && e.attributes.device_class === "plug");
    const fin = (e) => e.entity_id.startsWith("sensor.") && ["timestamp", "duration"].includes(e.attributes.device_class)
      && /charg|lade/.test(lcName(e)) && /end|ende|finish|fertig|full|voll|complete|remaining|rest|verbleib/.test(lcName(e));
    if (!c.finish && any) c.finish = near(c.soc, fin) || near(c.power, fin);
    const tgt = c.soc && sameDevice(hass, c.soc, (e) => /^(number|sensor)\./.test(e.entity_id) && unitOf(e) === "%" && /target|ziel|limit/.test(lcName(e)) && /soc|charg|lade/.test(lcName(e)));
    c._target = tgt?.entity_id || "";
    return c;
  }
  _watch() { const c = this._config; return [c.soc, c.range, c.power, c.plug, c.finish, c._target].filter(Boolean); }
  _onHass() {   // Verlauf für die Schätzung „voll um“, höchstens alle 10 min
    const c = this._config;
    if (!c.soc || c.finish || !this._hass.callWS || Date.now() - this._histAt < 6e5) return;
    if (!((toWatt(this._hass.states[c.power]) ?? 0) > 100)) return;
    this._histAt = Date.now();
    const end = new Date(), start = new Date(+end - 3 * 36e5);
    this._hass.callWS({ type: "history/history_during_period", start_time: start.toISOString(), end_time: end.toISOString(), entity_ids: [c.soc], minimal_response: true, no_attributes: true })
      .then((res) => { this._hist = (res[c.soc] || []).map((r) => ({ t: r.lu ? r.lu * 1000 : toMs(r.last_changed), v: parseFloat(r.s ?? r.state) })).filter((p) => isFinite(p.v)); this._render(); })
      .catch(() => { this._hist = []; });
  }
  _eta(soc, target) {
    const c = this._config, f = c.finish && this._hass.states[c.finish];
    if (isLive(f)) {
      if (f.attributes.device_class === "timestamp") { const t = toMs(f.state); return t > Date.now() ? { t, est: false } : null; }
      const v = parseFloat(f.state), u = unitOf(f), ms = u === "h" ? 36e5 : u === "s" ? 1e3 : u === "d" ? 864e5 : 6e4;
      return v > 0 ? { t: Date.now() + v * ms, est: false } : null;
    }
    const h = (this._hist || []).filter((p) => p.t >= Date.now() - 90 * 6e4);
    if (h.length < 2 || soc >= target) return null;
    const dv = h[h.length - 1].v - h[0].v, dt = Date.now() - h[0].t;
    if (dv < 2 || dt <= 0) return null;
    const t = Date.now() + ((target - soc) / dv) * dt;
    return t - Date.now() < 864e5 ? { t, est: true } : null;
  }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, S = this._hass.states;
    if (!c.soc && !c.power) return this._setup(c.name, "Ich habe weder dein E-Auto noch eine Wallbox gefunden.");
    const soc = parseFloat(S[c.soc]?.state), w = toWatt(S[c.power]), rng = parseFloat(S[c.range]?.state);
    const plug = c.plug ? S[c.plug]?.state : null, charging = (w ?? 0) > 50;
    const tv = parseFloat(S[c._target]?.state), target = tv > 0 && tv <= 100 ? tv : c.target;
    const eta = charging && !isNaN(soc) ? this._eta(soc, target) : null;
    const state = charging ? { key: "ok", label: "Lädt", icon: "mdi:ev-station" }
      : !isNaN(soc) && soc < 20 ? { key: "low", label: "Akku niedrig", icon: "mdi:battery-low" }
      : plug === "on" ? { key: "info", label: !isNaN(soc) && soc >= target - 1 ? "Voll geladen" : "Angesteckt", icon: "mdi:power-plug" }
      : plug === "off" ? { key: "info", label: "Nicht angesteckt", icon: "mdi:power-plug-off-outline" }
      : { key: "info", label: "Bereit", icon: "mdi:car-electric-outline" };
    const ru = S[c.range]?.attributes.unit_of_measurement || "km";
    const head = !isNaN(soc)
      ? `<div class="big" style="margin-top:6px" data-entity="${c.soc}"><span class="num">${fmt(soc)}</span><span class="u">%${!isNaN(rng) ? ` · ${fmt(rng)} ${esc(ru)}` : ""}</span></div>
        <div class="pack${charging ? " on" : ""}"><div class="fill" style="width:${Math.max(2, Math.min(100, soc))}%"></div>${target < 100 ? `<div class="tg" style="left:${target}%"></div>` : ""}</div>
        ${eta ? `<div class="muted">${target < 100 ? `${fmt(target)} %` : "Voll"} um ${hm(eta.t)}${eta.est ? " (geschätzt)" : ""}</div>` : ""}`
      : `<div class="big" style="margin-top:6px" data-entity="${c.power}"><span class="num">${w === null ? "–" : fmt(w / 1000, 1)}</span><span class="u">kW Ladeleistung</span></div>`;
    const tiles = [];
    if (!isNaN(soc) && c.power) tiles.push({ label: "Leistung", value: w === null ? "–" : fmt(w / 1000, 1), unit: "kW", sub: charging ? "lädt" : "", icon: "mdi:flash", entity: c.power });
    if (!isNaN(rng)) tiles.push({ label: "Reichweite", value: fmt(rng), unit: ru, icon: "mdi:map-marker-distance", entity: c.range });
    if (charging) tiles.push({ label: "Fertig", value: eta ? hm(eta.t) : "–", unit: eta ? "Uhr" : "", sub: eta ? (eta.est ? "geschätzt" : `bis ${fmt(target)} %`) : "noch unbekannt", icon: "mdi:clock-check-outline", entity: c.finish || c.soc || c.power });
    else if (plug !== null) tiles.push({ label: "Stecker", value: plug === "on" ? "Steckt" : "Ab", unit: "", sub: S[c.plug] ? ago(S[c.plug].last_changed) : "", icon: plug === "on" ? "mdi:power-plug" : "mdi:power-plug-off-outline", entity: c.plug });
    this._shell(state.key, `${this._head(c.name, state)}${head}${tiles.length ? this._tiles(tiles) : ""}`, `
      .big[data-entity] { cursor: pointer; }
      .pack { position: relative; height: 12px; margin-top: 10px; border-radius: 6px; overflow: hidden; background: var(--secondary-background-color, rgba(127,127,127,.12)); }
      .fill { height: 100%; border-radius: 6px; background: var(--mc-state); transition: width .6s; }
      .pack.on .fill { background-image: linear-gradient(90deg, transparent 0, rgba(255,255,255,.35) 50%, transparent 100%); background-size: 60px 100%; background-repeat: no-repeat;
        animation: mc-charge 2.2s linear infinite; }
      @keyframes mc-charge { from { background-position: -60px 0; } to { background-position: calc(100% + 60px) 0; } }
      .tg { position: absolute; top: -2px; bottom: -2px; width: 2px; background: var(--primary-text-color); opacity: .5; }`);
  }
}

/* ================= 15 · Heizung ================= */
class MangfallHeatingCard extends MangfallBase {
  static defaults = { name: "Heizung", entities: [] };
  static getStubConfig() { return {}; }   // leer = alle Thermostate
  static getConfigForm() {
    return { schema: [{ name: "entities", selector: { entity: { multiple: true, filter: { domain: "climate" } } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [{ name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ entities: "Thermostate", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ entities: "Leer lassen – die Karte zeigt jedes Thermostat mit seinem Raum." }[s.name]) };
  }
  _check() { this._pend = {}; }
  _list() {
    const H = this._hass, ids = this._config.entities?.length ? this._config.entities
      : Object.keys(H.states).filter((id) => id.startsWith("climate.") && !isHidden(H, id));
    return ids.map((id) => H.states[id]).filter(Boolean).slice(0, 12);
  }
  _watch() { return this._hass ? this._list().map((e) => e.entity_id) : []; }
  _target(e) {
    const p = this._pend[e.entity_id];
    if (p && (Date.now() - p.at > 8000 || e.attributes.temperature === p.v)) delete this._pend[e.entity_id];
    return this._pend[e.entity_id]?.v ?? e.attributes.temperature;
  }
  _step(e, dir) {
    const a = e.attributes, step = a.target_temp_step || (/f/i.test(a.temperature_unit || "") ? 1 : 0.5);
    const v = Math.max(a.min_temp ?? 5, Math.min(a.max_temp ?? 30, Math.round(((this._target(e) ?? 20) + dir * step) / step) * step));
    this._pend[e.entity_id] = { v, at: Date.now() };
    const el = this.shadowRoot.querySelector(`[data-t="${e.entity_id}"]`); if (el) el.textContent = fmt(v, step < 1 ? 1 : 0) + "°";
    clearTimeout(this._tm?.[e.entity_id]); this._tm = this._tm || {};
    this._tm[e.entity_id] = setTimeout(() => this._hass.callService("climate", "set_temperature", { entity_id: e.entity_id, temperature: v }), 700);
  }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, H = this._hass, list = this._list();
    if (!list.length) return this._setup(c.name, "Ich habe kein Thermostat in deinem Home Assistant gefunden.");
    const perArea = {};
    list.forEach((e) => { const a = areaOf(H, e.entity_id); if (a) perArea[a] = (perArea[a] || 0) + 1; });
    const heating = list.filter((e) => e.attributes.hvac_action === "heating" || (e.attributes.hvac_action == null && e.state === "heat" && (e.attributes.current_temperature ?? 99) < (e.attributes.temperature ?? 0)));
    const allOff = list.every((e) => e.state === "off");
    const state = heating.length ? { key: "info", label: `${heating.length} ${heating.length === 1 ? "Raum heizt" : "Räume heizen"}`, icon: "mdi:fire" }
      : allOff ? { key: "info", label: "Alles aus", icon: "mdi:power" } : { key: "ok", label: "Auf Temperatur", icon: "mdi:check-circle" };
    const rows = list.map((e) => {
      const a = e.attributes, ar = areaOf(H, e.entity_id), live = isLive(e);
      const name = ar && perArea[ar] === 1 && H.areas?.[ar]?.name ? H.areas[ar].name : fname(e).replace(/\s*(thermostat|heizung|heizk(ö|oe)rper)\s*$/i, "") || fname(e);
      const hot = heating.includes(e), off = e.state === "off", t = this._target(e), step = a.target_temp_step || 0.5;
      const note = !live ? "nicht erreichbar" : off ? "aus" : hot ? "heizt" : a.hvac_action === "idle" ? "hält die Temperatur" : WX[e.state] ? "" : ({ heat: "Heizen", auto: "Automatik", cool: "Kühlen", heat_cool: "Automatik" }[e.state] || esc(e.state));
      const ctrl = t != null && live && !off
        ? `<button class="pm" data-s="-1" data-id="${e.entity_id}" aria-label="Kälter"><ha-icon icon="mdi:minus"></ha-icon></button>
           <span class="tt" data-t="${e.entity_id}">${fmt(t, step < 1 ? 1 : 0)}°</span>
           <button class="pm" data-s="1" data-id="${e.entity_id}" aria-label="Wärmer"><ha-icon icon="mdi:plus"></ha-icon></button>`
        : a.target_temp_low != null && live && !off ? `<span class="tt">${fmt(a.target_temp_low, 1)}–${fmt(a.target_temp_high, 1)}°</span>` : "";
      return `<div class="row"><span class="dot ${hot ? "heat" : off || !live ? "off" : "d-ok"}"></span>
        <div class="rn" data-sheet="e:${e.entity_id}|current_temperature">${esc(name)}<div class="rs">${note}</div></div>
        <div class="rv cur" data-sheet="e:${e.entity_id}|current_temperature">${a.current_temperature != null ? fmt(a.current_temperature, 1) + "°" : "–"}</div>
        <div class="ctl">${ctrl}</div></div>`;
    }).join("");
    this._shell(state.key, `
      ${this._head(c.name, state)}
      <div class="big" style="margin-top:6px"><span class="num">${heating.length}</span><span class="u">von ${list.length} ${list.length === 1 ? "Raum heizt" : "Räumen heizen"}</span></div>
      <div class="rows">${rows}</div>`, `
      .row { cursor: default; } .rn, .cur { cursor: pointer; }
      .dot.off { background: var(--disabled-text-color, #9e9e9e); opacity: .5; }
      .dot.heat { background: #f2711c; box-shadow: 0 0 0 0 rgba(242,113,28,.5); animation: mc-glow 2.4s ease-out infinite; }
      @keyframes mc-glow { 0% { box-shadow: 0 0 0 0 rgba(242,113,28,.45); } 100% { box-shadow: 0 0 0 7px rgba(242,113,28,0); } }
      .cur { font-weight: 500; color: var(--secondary-text-color); font-size: 14px; }
      .ctl { display: flex; align-items: center; gap: 2px; min-width: 104px; justify-content: flex-end; }
      .pm { display: grid; place-items: center; width: 30px; height: 30px; border: none; border-radius: 50%; cursor: pointer; color: var(--primary-text-color);
        background: var(--secondary-background-color, rgba(127,127,127,.1)); transition: background .15s; }
      .pm:hover { background: color-mix(in srgb, #f2711c 18%, var(--secondary-background-color, #eee)); }
      .pm:active { transform: scale(.94); }
      .pm ha-icon { --mdc-icon-size: 16px; }
      .tt { min-width: 42px; text-align: center; font-size: 15px; font-weight: 600; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }`);
    this.shadowRoot.querySelectorAll(".pm").forEach((b) => b.addEventListener("click", (ev) => {
      ev.stopPropagation(); const e = H.states[b.dataset.id]; if (e) this._step(e, +b.dataset.s);
    }));
  }
}

/* ================= 16 · FLAT – Wasserwaage für Wohnwagen und Wohnmobil ================= */
// Längsneigung: + = vorne höher. Querneigung: + = rechts höher (in Fahrtrichtung). Aus den Winkeln und den Maßen
// des Fahrzeugs rechnet die Karte aus, welches Rad wie viele Zentimeter Keil braucht.
const isTilt = (e) => /^(sensor|input_number)\./.test(e.entity_id) && /^(°|deg|grad)$/i.test((e.attributes.unit_of_measurement || "").trim());
FIND.pitch = (h) => pick(h, isTilt, (n) => 3 * has(/pitch|l(ä|ae)ngs|nick|front|vorn|forward/, n) + 2 * has(/flat|neigung|tilt|level|wasserwaage/, n) - 4 * has(/roll|quer|seit|side/, n));
FIND.roll = (h) => pick(h, isTilt, (n) => 3 * has(/roll|quer|seit|side|lateral/, n) + 2 * has(/flat|neigung|tilt|level|wasserwaage/, n) - 4 * has(/pitch|l(ä|ae)ngs|nick/, n));
const fmtCm = (cm) => (cm < 10 ? fmt(cm, 1) : fmt(cm)) + " cm";
const fmtDeg = (d) => fmt(Math.abs(d), Math.abs(d) < 10 ? 1 : 0) + "°";

class MangfallFlatCard extends MangfallBase {
  static product = "FLAT";
  static defaults = { name: "Wasserwaage", pitch: "", roll: "", battery: "", vehicle: "caravan", length: null, width: null, tolerance: 0.3 };
  static getStubConfig(hass) { return { pitch: FIND.pitch(hass), roll: FIND.roll(hass) }; }
  static getConfigForm() {
    const tilt = { entity: { filter: [{ domain: "sensor" }, { domain: "input_number" }] } };
    return { schema: [
      { name: "vehicle", selector: { select: { mode: "list", options: [{ value: "caravan", label: "Wohnwagen" }, { value: "motorhome", label: "Wohnmobil" }] } } },
      { name: "pitch", selector: tilt }, { name: "roll", selector: tilt },
      { type: "expandable", name: "", title: "Maße und weitere Einstellungen", flatten: true, schema: [
        { type: "grid", name: "", schema: [
          { name: "length", selector: { number: { min: 1, max: 12, step: 0.1, unit_of_measurement: "m", mode: "box" } } },
          { name: "width", selector: { number: { min: 1, max: 3, step: 0.05, unit_of_measurement: "m", mode: "box" } } },
          { name: "tolerance", selector: { number: { min: 0.1, max: 2, step: 0.1, unit_of_measurement: "°", mode: "box" } } }] },
        { name: "battery", selector: sensorSel("battery") }, { name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ vehicle: "Fahrzeug", pitch: "Längsneigung", roll: "Querneigung", length: "Abstand Achse – Stützrad", width: "Spurbreite",
        tolerance: "Gilt als gerade bis", battery: "Akku", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ pitch: "Leer lassen – die Karte nimmt deinen FLAT von selbst.", roll: "Leer lassen – die Karte nimmt deinen FLAT von selbst.",
        length: "Wohnwagen: Achse bis Stützrad. Wohnmobil: Radstand. Leer lassen = übliche Werte.", width: "Abstand der Räder links und rechts. Leer lassen = 1,9 m." }[s.name]) };
  }
  _auto(c, hass) {
    if (!c.pitch) c.pitch = FIND.pitch(hass);
    if (!c.roll) c.roll = FIND.roll(hass);
    const any = c.pitch || c.roll;
    if (any && !c.battery) c.battery = sameDevice(hass, any, (e) => e.attributes.device_class === "battery" && e.entity_id.startsWith("sensor."))?.entity_id || "";
    return c;
  }
  _watch() { const c = this._config; return [c.pitch, c.roll, c.battery].filter(Boolean); }
  getCardSize() { return 6; }
  // Höhe, die jedes Rad (bzw. das Stützrad) braucht, damit das Fahrzeug gerade steht
  static plan(p, r, c) {
    const mh = c.vehicle === "motorhome", L = c.length || (mh ? 3.8 : 4.2), W = c.width || 1.9;
    const tp = Math.tan((p * Math.PI) / 180), tr = Math.tan((r * Math.PI) / 180);
    if (mh) {          // vier Räder: das höchste bleibt, die anderen bekommen Keile
      const z = { fl: (L / 2) * tp - (W / 2) * tr, fr: (L / 2) * tp + (W / 2) * tr, rl: -(L / 2) * tp - (W / 2) * tr, rr: -(L / 2) * tp + (W / 2) * tr };
      const top = Math.max(...Object.values(z));
      return { wheels: Object.fromEntries(Object.entries(z).map(([k, v]) => [k, (top - v) * 100])), jack: null };
    }
    const lift = Math.abs(W * tr) * 100;      // Wohnwagen: tiefes Rad unterlegen, dann Stützrad nachkurbeln
    return { wheels: { l: r > 0 ? lift : 0, r: r < 0 ? lift : 0 }, jack: -L * tp * 100 };
  }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, S = this._hass.states;
    if (!c.pitch && !c.roll) return this._setup(c.name, "Ich habe keinen FLAT-Neigungssensor gefunden.");
    const ps = S[c.pitch], rs = S[c.roll], p = parseFloat(ps?.state), r = parseFloat(rs?.state);
    if ((c.pitch && !isLive(ps)) && (c.roll && !isLive(rs))) return this._missing(c.name, "Dein FLAT meldet sich gerade nicht – ist er in Bluetooth-Reichweite?");
    const P = isFinite(p) ? p : 0, R = isFinite(r) ? r : 0, tilt = Math.max(Math.abs(P), Math.abs(R)), tol = c.tolerance || 0.3;
    const mh = c.vehicle === "motorhome", plan = MangfallFlatCard.plan(P, R, c);
    const need = Object.entries(plan.wheels).filter(([, v]) => v >= 0.5), maxLift = Math.max(0, ...Object.values(plan.wheels));
    const state = tilt <= tol ? { key: "ok", label: "Steht gerade", icon: "mdi:check-circle" }
      : maxLift > 12 ? { key: "crit", label: "Zu schief", icon: "mdi:alert" }
      : tilt <= 1 ? { key: "info", label: "Fast gerade", icon: "mdi:approximately-equal" }
      : { key: "low", label: "Schief", icon: "mdi:angle-acute" };
    const NAME = { l: "links", r: "rechts", fl: "vorne links", fr: "vorne rechts", rl: "hinten links", rr: "hinten rechts" };
    let say;
    if (tilt <= tol) say = "Alles im Lot – Kühlschrank, Dusche und Bett passen.";
    else if (maxLift > 12) say = `Dafür bräuchtest du ${fmtCm(maxLift)} Keil – such dir lieber einen ebeneren Platz.`;
    else {
      const parts = need.sort((a, b) => b[1] - a[1]).map(([k, v]) => `${NAME[k]} ${fmtCm(v)}`);
      say = parts.length ? `Unterlegen: ${parts.join(", ")}` : "";
      if (!mh && Math.abs(plan.jack) >= 0.5) say += `${say ? ", dann " : ""}Stützrad ${fmtCm(Math.abs(plan.jack))} ${plan.jack > 0 ? "hochkurbeln" : "runterkurbeln"}`;
      say = say.charAt(0).toUpperCase() + say.slice(1) + ".";
      if (tilt > 3) say += " Der Absorber-Kühlschrank braucht weniger als 3°.";
    }
    // Draufsicht: Fahrzeug, Räder mit Keil-Angabe, Libelle in der Mitte
    const Wv = 300, Hv = 200, bx = 95, by = 22, bw = 110, bh = mh ? 160 : 140;
    const wheel = (k, x, y) => {
      const v = plan.wheels[k], on = v >= 0.5;
      return `<g class="wh ${on ? "on" : ""}" data-sheet="e:${c.roll || c.pitch}"><rect x="${x - 7}" y="${y - 16}" width="14" height="32" rx="4"/>
        ${on ? `<text x="${x < 150 ? x - 14 : x + 14}" y="${y + 4}" text-anchor="${x < 150 ? "end" : "start"}" class="lift">+${fmtCm(v)}</text>` : ""}</g>`;
    };
    const cy = by + bh / 2, ax = mh ? null : by + bh * 0.62;
    const wheels = mh ? wheel("fl", bx, by + 34) + wheel("fr", bx + bw, by + 34) + wheel("rl", bx, by + bh - 30) + wheel("rr", bx + bw, by + bh - 30)
      : wheel("l", bx, ax) + wheel("r", bx + bw, ax);
    const jackOn = !mh && Math.abs(plan.jack) >= 0.5;
    const front = mh ? `<path d="M${bx} ${by + 26} Q${bx} ${by} ${bx + 26} ${by} H${bx + bw - 26} Q${bx + bw} ${by} ${bx + bw} ${by + 26}" class="cab"/>`
      : `<path d="M${bx + 30} ${by} L150 ${by - 14} L${bx + bw - 30} ${by}" class="bar"/><g class="jack ${jackOn ? "on" : ""}" data-sheet="e:${c.pitch || c.roll}"><circle cx="150" cy="${by - 14}" r="6"/>
        ${jackOn ? `<text x="162" y="${by - 10}" class="lift">${plan.jack > 0 ? "↑" : "↓"} ${fmtCm(Math.abs(plan.jack))}</text>` : ""}</g>`;
    const RNG = 5, bubX = 150 + Math.max(-1, Math.min(1, R / RNG)) * 34, bubY = cy - Math.max(-1, Math.min(1, P / RNG)) * 34;
    const svg = `<svg viewBox="0 0 ${Wv} ${Hv}" class="van" role="img" aria-label="Neigung ${fmtDeg(tilt)}">
      <rect x="${bx}" y="${by}" width="${bw}" height="${bh}" rx="${mh ? 18 : 14}" class="body"/>${front}${wheels}
      <g class="vial" data-sheet="e:${c.pitch || c.roll}"><circle cx="150" cy="${cy}" r="40" class="ring"/><circle cx="150" cy="${cy}" r="${(tol / RNG) * 34 + 9}" class="ring in"/>
        <line x1="110" x2="190" y1="${cy}" y2="${cy}" class="cross"/><line x1="150" x2="150" y1="${cy - 40}" y2="${cy + 40}" class="cross"/>
        <circle cx="0" cy="0" r="9" class="bub" style="transform:translate(${bubX.toFixed(1)}px,${bubY.toFixed(1)}px)"/></g>
      <text x="150" y="${by + bh + 15}" class="dir">${mh ? "Heck" : "Heck"}</text></svg>`;
    const bat = c.battery ? parseFloat(S[c.battery]?.state) : NaN;
    const tiles = [
      { label: "Längs", value: isFinite(p) ? fmtDeg(p) : "–", unit: "", sub: !isFinite(p) ? "kein Wert" : Math.abs(p) <= tol ? "gerade" : p > 0 ? "vorne höher" : "hinten höher", icon: "mdi:arrow-up-down", sheet: c.pitch ? `e:${c.pitch}` : "" },
      { label: "Quer", value: isFinite(r) ? fmtDeg(r) : "–", unit: "", sub: !isFinite(r) ? "kein Wert" : Math.abs(r) <= tol ? "gerade" : r > 0 ? "rechts höher" : "links höher", icon: "mdi:arrow-left-right", sheet: c.roll ? `e:${c.roll}` : "" },
    ];
    if (c.battery) tiles.push({ label: "Akku", value: isFinite(bat) ? fmt(bat) : "–", unit: "%", sub: isFinite(bat) && bat < 20 ? "bald laden" : "", icon: "mdi:battery-high", sheet: `e:${c.battery}` });
    this._shell(state.key, `
      ${this._head(c.name, state)}
      <div class="big" style="margin-top:6px" data-sheet="e:${c.pitch || c.roll}"><span class="num">${fmtDeg(tilt)}</span><span class="u">${tilt <= tol ? "gerade" : "Neigung"}</span></div>
      <div class="say">${say}</div>
      <div class="vwrap">${svg}</div>
      ${this._tiles(tiles)}`, `
      .say { font-size: 13.5px; color: var(--primary-text-color); margin-top: 6px; line-height: 1.45; }
      .big[data-sheet] { cursor: pointer; }
      .vwrap { margin: 8px auto 0; max-width: 340px; }
      .van { width: 100%; display: block; overflow: visible; }
      .body { fill: var(--secondary-background-color, rgba(127,127,127,.08)); stroke: var(--divider-color, rgba(127,127,127,.35)); stroke-width: 1.5; }
      .cab { fill: none; stroke: var(--secondary-text-color); stroke-width: 2; opacity: .5; }
      .bar { fill: none; stroke: var(--secondary-text-color); stroke-width: 2.5; stroke-linejoin: round; opacity: .55; }
      .wh rect { fill: var(--secondary-text-color); opacity: .55; transition: fill .3s, opacity .3s; }
      .wh.on rect, .jack.on circle { fill: var(--mc-state); opacity: 1; }
      .jack circle { fill: var(--secondary-text-color); opacity: .55; }
      .wh, .jack, .vial { cursor: pointer; }
      .lift { font-size: 12px; font-weight: 600; fill: var(--primary-text-color); font-variant-numeric: tabular-nums; }
      .ring { fill: color-mix(in srgb, var(--mc-state) 7%, transparent); stroke: var(--divider-color, rgba(127,127,127,.35)); stroke-width: 1.2; }
      .ring.in { fill: none; stroke: var(--mc-state); stroke-dasharray: 2 3; opacity: .7; }
      .cross { stroke: var(--divider-color, rgba(127,127,127,.3)); stroke-width: 1; }
      .bub { fill: var(--mc-state); opacity: .9; transition: transform .8s cubic-bezier(.3,1.4,.5,1); filter: drop-shadow(0 1px 2px rgba(0,0,0,.25)); }
      .dir { font-size: 10px; letter-spacing: .14em; text-transform: uppercase; fill: var(--secondary-text-color); text-anchor: middle; opacity: .7; }
      @media (prefers-reduced-motion: reduce) { .bub { transition: none; } }`);
  }
}

/* ================= Gemeinsames für die Alltagskarten (1.5.0) ================= */
const wParts = (w) => { const s = fmtW(w), i = s.lastIndexOf(" "); return [s.slice(0, i), s.slice(i + 1)]; };
const listIds = (hass, dom, f = () => true) => Object.values(hass?.states || {}).filter((e) => e.entity_id.startsWith(dom + ".") && !isHidden(hass, e.entity_id) && f(e)).map((e) => e.entity_id);
const areaName = (hass, id) => { const a = areaOf(hass, id); return (a && hass.areas?.[a]?.name) || ""; };
const stripArea = (name, area) => (area ? name.replace(new RegExp(`^${area.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s+`, "i"), "") || name : name);
const nice = (hass, e) => stripArea(cleanName(fname(e)), areaName(hass, e.entity_id));
const byArea = (hass, list) => {
  const m = new Map();
  for (const e of list) { const n = areaName(hass, e.entity_id) || "Ohne Raum"; if (!m.has(n)) m.set(n, []); m.get(n).push(e); }
  return [...m.entries()].sort((a, b) => (a[0] === "Ohne Raum") - (b[0] === "Ohne Raum") || a[0].localeCompare(b[0], "de"));
};
const DAY_LONG = (d) => d.toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long" });
const dayWord = (d) => { const n = Math.round((dayStart(d) - dayStart()) / 864e5); return n === 0 ? "Heute" : n === 1 ? "Morgen" : d.toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "short" }); };
const CHIP_CSS = `
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
  .hc { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px 6px 8px; border: none; border-radius: 999px; cursor: pointer; font: inherit; font-size: 13px;
    font-weight: 500; color: var(--primary-text-color); background: var(--secondary-background-color, rgba(127,127,127,.1)); transition: background .15s; }
  .hc ha-icon { --mdc-icon-size: 17px; color: var(--hc, var(--secondary-text-color)); }
  .hc:hover, .hc.sel { background: color-mix(in srgb, var(--hc, var(--primary-text-color)) 14%, var(--secondary-background-color, transparent)); }
  .hc:focus-visible { outline: 2px solid var(--primary-color, #2a78d6); outline-offset: 2px; }
  .k-ok { --hc: var(--success-color, #0ca30c); } .k-info { --hc: var(--info-color, #2a78d6); } .k-low { --hc: var(--warning-color, #eda100); } .k-crit { --hc: var(--error-color, #e34948); }`;
const ACT_CSS = `
  .ib { display: grid; place-items: center; width: 32px; height: 32px; flex: 0 0 32px; border: none; border-radius: 50%; cursor: pointer; color: var(--primary-text-color);
    background: var(--secondary-background-color, rgba(127,127,127,.1)); transition: background .15s, transform .1s; }
  .ib:hover:not([disabled]) { background: color-mix(in srgb, var(--primary-color, #2a78d6) 16%, var(--secondary-background-color, #eee)); }
  .ib:active { transform: scale(.94); } .ib[disabled] { opacity: .35; cursor: default; }
  .ib ha-icon { --mdc-icon-size: 18px; }
  .pill { display: inline-flex; align-items: center; gap: 4px; border: none; border-radius: 999px; padding: 6px 12px; cursor: pointer; font: inherit; font-size: 13px; font-weight: 500;
    color: var(--primary-text-color); background: var(--secondary-background-color, rgba(127,127,127,.12)); transition: background .15s; }
  .pill:hover:not([disabled]) { background: color-mix(in srgb, var(--primary-color, #2a78d6) 16%, var(--secondary-background-color, #eee)); }
  .pill[disabled] { opacity: .5; cursor: default; } .pill ha-icon { --mdc-icon-size: 16px; }
  .pill.warn { background: color-mix(in srgb, var(--error-color, #e34948) 16%, var(--secondary-background-color, #eee)); }
  .ib:focus-visible, .pill:focus-visible { outline: 2px solid var(--primary-color, #2a78d6); outline-offset: 2px; }
  .fb { font-size: 12px; color: var(--mc-state); font-weight: 500; }
  .ask { margin: 6px 0 4px; padding: 10px 12px; border-radius: 12px; background: color-mix(in srgb, var(--warning-color, #eda100) 12%, var(--secondary-background-color, transparent));
    display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 13.5px; color: var(--primary-text-color); }
  .ask b { flex: 1 1 100%; font-weight: 500; }
  .ask input { flex: 1 1 120px; min-width: 0; font: inherit; padding: 7px 10px; border-radius: 10px; border: 1px solid var(--divider-color, rgba(127,127,127,.4));
    background: var(--card-background-color, #fff); color: var(--primary-text-color); }
  .rh2 { display: flex; justify-content: space-between; font-size: 11.5px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--secondary-text-color); margin: 12px 0 2px; }`;

/* Haushaltsgeräte an der Leistung erkennen: läuft, oder fertig seit … (Pausen unter 10 min zählen zum Lauf) */
const APL_RE = /wasch|washer|washing|trockner|dryer|sp(ü|ue)lmasch|dishwasher|geschirr/;
const aplType = (n) => (/trockner|dryer/.test(n) ? ["Trockner", "mdi:tumble-dryer"] : /sp(ü|ue)l|dish|geschirr/.test(n) ? ["Spülmaschine", "mdi:dishwasher"] : ["Waschmaschine", "mdi:washing-machine"]);
function aplState(pts, now = Date.now(), on = 8) {
  const runs = [];
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i].t, z = i + 1 < pts.length ? pts[i + 1].t : now;
    if (!(pts[i].v > on) || z <= a) continue;
    const l = runs[runs.length - 1];
    if (l && a - l[1] <= 6e5) l[1] = z; else runs.push([a, z]);
  }
  const r = runs[runs.length - 1];
  if (!r) return null;
  if (r[1] >= now - 6e5 && (pts[pts.length - 1].v > on || r[1] - r[0] >= 6e5)) return { run: true, since: r[0], paused: !(pts[pts.length - 1].v > on) };
  if (r[1] - r[0] >= 6e5 && now - r[1] < 3 * 36e5) return { done: true, at: r[1] };
  return null;
}
async function aplFetch(card, ids) {
  if (!ids.length || card._aplBusy || !card._hass?.callWS || (card._aplAt && Date.now() - card._aplAt < 6e5)) return;
  card._aplBusy = true; card._aplAt = Date.now();
  try {
    const end = new Date(), res = await card._hass.callWS({ type: "history/history_during_period", start_time: new Date(+end - 4 * 36e5).toISOString(), end_time: end.toISOString(),
      entity_ids: ids, minimal_response: true, no_attributes: true, significant_changes_only: false });
    const unitK = (id) => ((card._hass.states[id]?.attributes.unit_of_measurement || "W").toLowerCase() === "kw" ? 1000 : 1);
    card._apl = Object.fromEntries(ids.map((id) => [id, (res?.[id] || []).map((r) => ({ t: (r.lu || 0) * 1000, v: parseFloat(r.s) * unitK(id) })).filter((p) => isFinite(p.v) && p.t > 0)]));
  } catch (e) { card._aplAt = Date.now() - 6e5 + 6e4; }
  card._aplBusy = false; card._lastKey = null; card._render();
}
function aplNow(card, id) {
  const st = card._hass.states[id], pts = [...(card._apl?.[id] || [])], v = toWatt(st);
  if (v !== null) { const t = toMs(st.last_changed); if (!pts.length || t > pts[pts.length - 1].t) pts.push({ t, v }); }
  return pts.length ? aplState(pts) : null;
}
/* Gerätestatus von Home Connect, Miele u. a. (Textsensoren) */
const aplText = (e) => { const s = String(e.state).toLowerCase();
  return /finish|fertig|beendet|\bend\b|ende|complete/.test(s) ? { done: true, at: toMs(e.last_changed) } : /run|l(ä|ae)uft|active|in.?betrieb|wash|dry|rinse|sp(ü|ue)l/.test(s) ? { run: true, since: toMs(e.last_changed) } : null; };
const isAplText = (e) => e.entity_id.startsWith("sensor.") && APL_RE.test(lcName(e)) && /operation.?state|betriebszustand|programm.?status|status|zustand/.test(lcName(e)) && isNaN(parseFloat(e.state));
const isConsumer = (e) => isPower(e) && !/netz|grid|3em|em3|smart.?meter|hausanschluss|z(ä|ae)hler|solar|\bpv\b|_pv|pv_|batter|speicher|akku|inverter|wechselrichter|phase|_l[123]\b|einspeis|export|import|bezug|total|gesamt|house|haus.?verbrauch|home|load|limit|max|min|setting|ziel|target|apparent|reactive|schein|blind|forecast|prognose|production|erzeugung|rated|threshold|clip|transmitted|netto|bkw|balkonkraftwerk|heute|today|daily|t(ä|ae)glich/.test(lcName(e)) && !NOT_POWER.test(lcName(e));

/* ================= 17 · Haus auf einen Blick ================= */
class MangfallHomeCard extends MangfallBase {
  static defaults = { name: "", weather: "", show_weather: true };
  static getStubConfig(hass) { return { weather: FIND.weather(hass) }; }
  static getConfigForm() {
    return { schema: [{ name: "weather", selector: { entity: { domain: "weather" } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [{ name: "name", selector: { text: {} } }, { name: "show_weather", selector: { boolean: {} } }, BRANDING] }],
      computeLabel: (s) => ({ weather: "Wetter", name: "Überschrift", show_weather: "Wetter anzeigen", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ weather: "Leer lassen – die Karte nimmt deinen Wetterdienst von selbst.", name: "Leer lassen zeigt „Guten Abend“ mit deinem Namen." }[s.name]) };
  }
  _auto(c, hass) {
    if (!c.weather && c.show_weather) c.weather = FIND.weather(hass);
    c._open = listIds(hass, "binary_sensor", (e) => OPEN_CLASSES.includes(e.attributes.device_class));
    c._lights = listIds(hass, "light", (e) => !Array.isArray(e.attributes.entity_id));
    c._bats = listIds(hass, "sensor", (e) => e.attributes.device_class === "battery" && unitOf(e) === "%" && !PHONE_RE.test(lcName(e)));
    c._waste = MangfallWasteCard._find(hass);
    c._apl = listIds(hass, "sensor", (e) => isPower(e) && APL_RE.test(lcName(e))).slice(0, 4);
    c._aplT = listIds(hass, "sensor", isAplText).slice(0, 4);
    c._people = listIds(hass, "person");
    return c;
  }
  _watch() { const c = this._config; return [c.weather, ...c._open, ...c._lights, ...c._bats, ...c._waste, ...c._apl, ...c._aplT, ...c._people].filter(Boolean); }
  connectedCallback() {
    clearInterval(this._clk);
    this._clk = setInterval(() => { const m = new Date().getMinutes(); if (m !== this._min && this._hass) { this._min = m; this._lastKey = null; this._render(); } }, 5000);
  }
  disconnectedCallback() { clearInterval(this._clk); }
  _onHass() { aplFetch(this, this._config._apl); }
  _chips() {
    const c = this._config, S = this._hass.states, out = [];
    const open = c._open.map((id) => S[id]).filter((e) => e?.state === "on");
    if (open.length) {
      const win = open.every((e) => e.attributes.device_class === "window"), door = open.every((e) => e.attributes.device_class !== "window");
      out.push({ k: "open", key: "low", icon: win ? "mdi:window-open-variant" : "mdi:door-open", ids: open.map((e) => e.entity_id),
        label: open.length === 1 ? `${nice(this._hass, open[0])} offen` : `${open.length} ${win ? "Fenster" : door ? "Türen" : "Fenster und Türen"} offen`, title: "Offen" });
    }
    const lit = c._lights.map((id) => S[id]).filter((e) => e?.state === "on");
    const away = c._people.length && c._people.every((id) => S[id]?.state && S[id].state !== "home" && isLive(S[id]));
    if (lit.length) out.push({ k: "lights", key: away ? "low" : "info", icon: "mdi:lightbulb-on", ids: lit.map((e) => e.entity_id), title: "Lichter an",
      label: away ? `Niemand da, ${lit.length === 1 ? "1 Licht" : lit.length + " Lichter"} an` : `${lit.length === 1 ? "1 Licht" : lit.length + " Lichter"} an` });
    if (c._waste.length) {
      const items = [];
      for (const id of c._waste) {
        const e = S[id]; if (!e) continue;
        if (id.startsWith("calendar.")) { if (e.attributes.message && e.attributes.start_time) items.push({ d: dayStart(new Date(e.attributes.start_time.replace(" ", "T"))), t: e.attributes.message }); }
        else items.push(...wasteFromSensor(e));
      }
      const t0 = +dayStart(), soon = items.filter((x) => +x.d >= t0 && +x.d <= t0 + 864e5).sort((a, b) => a.d - b.d);
      if (soon.length) {
        const n = Math.round((soon[0].d - t0) / 864e5), types = [...new Set(soon.filter((x) => +x.d === +soon[0].d).map((x) => x.t))];
        out.push({ k: "waste", key: n === 1 ? "low" : "info", icon: /sack/i.test(types[0]) ? "mdi:sack" : "mdi:delete", ids: c._waste, title: "Müllabfuhr", label: `${n ? "Morgen" : "Heute"} ${types.join(", ")}` });
      }
    }
    for (const id of [...c._apl, ...c._aplT]) {
      const e = S[id]; if (!e) continue;
      const st = id.startsWith("sensor.") && isAplText(e) ? aplText(e) : aplNow(this, id), [type, icon] = aplType(lcName(e));
      if (st?.done) out.push({ k: "apl:" + id, key: "ok", icon, ids: [id], title: type, label: `${type} fertig` });
      else if (st?.run) out.push({ k: "apl:" + id, key: "info", icon, ids: [id], title: type, label: `${type} läuft` });
    }
    const weak = c._bats.map((id) => S[id]).filter((e) => isLive(e) && parseFloat(e.state) <= 20);
    if (weak.length) out.push({ k: "bat", key: "low", icon: "mdi:battery-low", ids: weak.map((e) => e.entity_id), title: "Akku schwach",
      label: weak.length === 1 ? `Akku schwach: ${nice(this._hass, weak[0])}` : `${weak.length} Akkus schwach` });
    return out;
  }
  _extraSheet(k) {
    if (!k.startsWith("l:")) return "";
    const ch = this._chips().find((x) => "l:" + x.k === k);
    if (!ch) { this._sheet = null; return ""; }
    const S = this._hass.states, rows = ch.ids.map((id) => S[id]).filter(Boolean).map((e) => `<div class="row" data-entity="${e.entity_id}">
      <div class="rn">${esc(nice(this._hass, e))}<div class="rs">${esc(areaName(this._hass, e.entity_id) || stateDe(e.state))}</div></div>
      <div class="rs">${e.attributes.device_class === "battery" ? fmtNum(parseFloat(e.state), "%") : ago(e.last_changed)}</div></div>`).join("");
    return this._frame(ch.title, `<div class="rows">${rows}</div>`);
  }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, now = new Date(), h = now.getHours(), S = this._hass.states;
    const hello = h < 5 ? "Gute Nacht" : h < 11 ? "Guten Morgen" : h < 17 ? "Guten Tag" : h < 22 ? "Guten Abend" : "Gute Nacht";
    const who = (this._hass.user?.name || "").trim().split(/\s+/)[0];
    const chips = this._chips(), w = c.show_weather && S[c.weather];
    const state = chips.some((x) => x.key === "low" || x.key === "crit") ? { key: "low", label: `${chips.length} ${chips.length === 1 ? "Hinweis" : "Hinweise"}`, icon: "mdi:bell-outline" }
      : chips.length ? { key: "info", label: `${chips.length} ${chips.length === 1 ? "Hinweis" : "Hinweise"}`, icon: "mdi:information-outline" }
      : { key: "ok", label: "Alles ruhig", icon: "mdi:check-circle" };
    const wx = w && isLive(w) ? `<span class="wx" data-sheet="e:${c.weather}|temperature"><ha-icon icon="${(WX[w.state] || [])[1] || "mdi:weather-cloudy"}"></ha-icon>${typeof w.attributes.temperature === "number" ? fmt(w.attributes.temperature) + "°" : ""} ${esc((WX[w.state] || [w.state])[0])}</span>` : "";
    this._shell(state.key, `
      ${this._head(esc(c.name || (who ? `${hello}, ${who}` : hello)), state)}
      <div class="big" style="margin-top:6px"><span class="num">${now.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}</span><span class="u">Uhr</span></div>
      <div class="muted dl"><span>${DAY_LONG(now)}</span>${wx}</div>
      ${chips.length ? `<div class="chips">${chips.map((x) => `<button class="hc k-${x.key}" data-sheet="l:${x.k}"><ha-icon icon="${x.icon}"></ha-icon>${esc(x.label)}</button>`).join("")}</div>`
        : `<div class="muted calm">${[c._open.length && "Alles zu", c._lights.length && (c._open.length ? "alles aus" : "Alles aus")].filter(Boolean).join(", ") || "Nichts Besonderes"} – nichts zu tun.</div>`}`, CHIP_CSS + `
      .dl { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: center; }
      .wx { display: inline-flex; align-items: center; gap: 4px; border-radius: 8px; } .wx ha-icon { --mdc-icon-size: 17px; }
      .wx:hover { color: var(--primary-text-color); }
      .calm { margin-top: 12px; }`);
  }
}

/* ================= 18 · Rollläden & Jalousien ================= */
const COVER_ICON = { awning: "mdi:awning-outline", blind: "mdi:blinds", curtain: "mdi:curtains", shade: "mdi:roller-shade", shutter: "mdi:window-shutter", window: "mdi:window-closed-variant", garage: "mdi:garage", gate: "mdi:gate", door: "mdi:door" };
class MangfallCoverCard extends MangfallBase {
  static defaults = { name: "Rollläden", exclude: [], include_garage: false };
  static getStubConfig() { return {}; }   // leer = alle Rollläden, nach Räumen
  static getConfigForm() {
    return { schema: [{ name: "exclude", selector: { entity: { multiple: true, filter: { domain: "cover" } } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [{ name: "name", selector: { text: {} } }, { name: "include_garage", selector: { boolean: {} } }, BRANDING] }],
      computeLabel: (s) => ({ exclude: "Ausblenden", name: "Name", include_garage: "Garagentore und Tore zeigen", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ exclude: "Die Karte findet alle Rollläden, Jalousien und Markisen von selbst. Hier wählst du nur, was sie nicht zeigen soll.",
        include_garage: "Garagentore stehen sonst in der Sicherheitskarte." }[s.name]) };
  }
  _list() {
    const c = this._config, H = this._hass;
    return listIds(H, "cover", (e) => !Array.isArray(e.attributes.entity_id) && !c.exclude.includes(e.entity_id)
      && (c.include_garage || !["garage", "gate", "door"].includes(e.attributes.device_class))).map((id) => H.states[id]);
  }
  _watch() { return this._hass ? this._list().map((e) => e.entity_id) : []; }
  _pos(e) { const p = e.attributes.current_position; return typeof p === "number" ? p : e.state === "open" ? 100 : e.state === "closed" ? 0 : null; }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, H = this._hass, all = this._list();
    if (!all.length) return this._setup(c.name, "Ich habe keine Rollläden oder Jalousien gefunden.");
    const live = all.filter(isLive), open = live.filter((e) => (this._pos(e) ?? 0) > 0 || e.state === "open" || e.state === "opening");
    const moving = live.filter((e) => ["opening", "closing"].includes(e.state));
    const state = moving.length ? { key: "info", label: `${moving.length} ${moving.length === 1 ? "fährt" : "fahren"}`, icon: "mdi:swap-vertical" }
      : open.length ? { key: "info", label: `${open.length} offen`, icon: "mdi:window-shutter-open" } : { key: "ok", label: "Alle zu", icon: "mdi:window-shutter" };
    const row = (e, room) => {
      const f = e.attributes.supported_features ?? 15, p = this._pos(e), id = e.entity_id, ok = isLive(e);
      const txt = this._fbText(id) || (!ok ? "nicht erreichbar" : e.state === "opening" ? "öffnet …" : e.state === "closing" ? "schließt …" : p === null ? stateDe(e.state) : p >= 99 ? "offen" : p <= 0 ? "zu" : `${fmt(p)} % offen`);
      const icon = (COVER_ICON[e.attributes.device_class] || "mdi:window-shutter") + (p > 0 && /shutter|blinds|shade|garage/.test(COVER_ICON[e.attributes.device_class] || "shutter") ? "-open" : "");
      return `<div class="row cv"><ha-icon class="ri" icon="${icon}"></ha-icon>
        <div class="rn" data-sheet="e:${id}${typeof e.attributes.current_position === "number" ? "|current_position" : ""}">${esc(stripArea(cleanName(fname(e)), room))}
          <div class="rs${this._fbText(id) ? " fb" : ""}">${txt}</div>${p !== null && ok ? `<div class="pos"><i style="width:${Math.max(0, Math.min(100, p))}%"></i></div>` : ""}</div>
        <div class="cb">
          ${f & 1 ? `<button class="ib" data-cv="open_cover" data-id="${id}" aria-label="Auf"${!ok || p >= 100 ? " disabled" : ""}><ha-icon icon="mdi:arrow-up"></ha-icon></button>` : ""}
          ${f & 8 ? `<button class="ib" data-cv="stop_cover" data-id="${id}" aria-label="Stopp"${!ok ? " disabled" : ""}><ha-icon icon="mdi:stop"></ha-icon></button>` : ""}
          ${f & 2 ? `<button class="ib" data-cv="close_cover" data-id="${id}" aria-label="Ab"${!ok || p === 0 ? " disabled" : ""}><ha-icon icon="mdi:arrow-down"></ha-icon></button>` : ""}
        </div></div>`;
    };
    const groups = byArea(H, all).map(([r, ls]) => `${byArea(H, all).length > 1 ? `<div class="rh2"><span>${esc(r)}</span></div>` : ""}<div class="rows">${ls.map((e) => row(e, r === "Ohne Raum" ? "" : r)).join("")}</div>`).join("");
    this._shell(state.key, `
      ${this._head(c.name, state)}
      <div class="top"><div class="big"><span class="num">${open.length}</span><span class="u">von ${all.length} offen</span></div>
        <div class="allb"><button class="pill" data-all="open_cover"><ha-icon icon="mdi:arrow-up"></ha-icon>${this._fbText("all:open_cover") || "Alle auf"}</button>
        <button class="pill" data-all="close_cover"><ha-icon icon="mdi:arrow-down"></ha-icon>${this._fbText("all:close_cover") || "Alle zu"}</button></div></div>
      ${groups}`, ACT_CSS + `
      .top { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
      .allb { display: flex; gap: 6px; }
      .row.cv { cursor: default; }
      .cb { display: flex; gap: 4px; }
      .pos { height: 3px; border-radius: 2px; margin-top: 5px; background: var(--divider-color, rgba(127,127,127,.25)); overflow: hidden; max-width: 160px; }
      .pos i { display: block; height: 100%; border-radius: 2px; background: var(--primary-text-color); opacity: .55; transition: width .6s; }`);
    const R = this.shadowRoot, label = { open_cover: "Öffnet …", close_cover: "Schließt …", stop_cover: "Stoppt …" };
    R.querySelectorAll("[data-cv]").forEach((b) => b.addEventListener("click", () => this._act(b.dataset.id, label[b.dataset.cv], () => H.callService("cover", b.dataset.cv, { entity_id: b.dataset.id }))));
    R.querySelectorAll("[data-all]").forEach((b) => b.addEventListener("click", () => {
      const svc = b.dataset.all, bit = svc === "open_cover" ? 1 : 2, ids = live.filter((e) => (e.attributes.supported_features ?? 15) & bit).map((e) => e.entity_id);
      if (ids.length) this._act("all:" + svc, svc === "open_cover" ? "Öffnet alle …" : "Schließt alle …", () => H.callService("cover", svc, { entity_id: ids }));
    }));
  }
}

/* ================= 19 · Sicherheit ================= */
const ALARM_DE = { disarmed: "Unscharf", armed_home: "Scharf · zu Hause", armed_away: "Scharf · abwesend", armed_night: "Scharf · Nacht", armed_vacation: "Scharf · Urlaub",
  armed_custom_bypass: "Scharf · eigene Zonen", pending: "Alarm in Kürze", arming: "Schaltet scharf …", disarming: "Schaltet unscharf …", triggered: "Alarm ausgelöst" };
const LOCK_DE = { locked: "abgeschlossen", unlocked: "offen", open: "geöffnet", opening: "öffnet …", locking: "schließt ab …", unlocking: "schließt auf …", jammed: "klemmt" };
const DETECT = { smoke: ["Rauch", "mdi:smoke-detector-variant"], carbon_monoxide: ["Kohlenmonoxid", "mdi:molecule-co"], gas: ["Gas", "mdi:gas-cylinder"], moisture: ["Wasser", "mdi:water-alert"] };
class MangfallSecurityCard extends MangfallBase {
  static defaults = { name: "Sicherheit", alarm: "", locks: [], exclude: [] };
  static getStubConfig(hass) { return { alarm: listIds(hass, "alarm_control_panel")[0] || "" }; }
  static getConfigForm() {
    return { schema: [{ name: "alarm", selector: { entity: { domain: "alarm_control_panel" } } },
      { name: "locks", selector: { entity: { multiple: true, filter: { domain: "lock" } } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [
        { name: "exclude", selector: { entity: { multiple: true, filter: [...Object.keys(DETECT).map((device_class) => ({ domain: "binary_sensor", device_class })), { domain: "cover" }] } } },
        { name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ alarm: "Alarmanlage", locks: "Türschlösser", exclude: "Ausblenden", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ alarm: "Leer lassen – die Karte nimmt deine Alarmanlage von selbst.", locks: "Leer lassen – die Karte zeigt alle Schlösser.",
        exclude: "Die Karte findet Rauch-, CO-, Gas- und Wassermelder und Garagentore von selbst. Hier wählst du aus, was sie nicht zeigen soll." }[s.name]) };
  }
  _auto(c, hass) {
    if (!c.alarm) c.alarm = listIds(hass, "alarm_control_panel")[0] || "";
    c._locks = c.locks?.length ? c.locks : listIds(hass, "lock");
    c._det = listIds(hass, "binary_sensor", (e) => DETECT[e.attributes.device_class] && !c.exclude.includes(e.entity_id));
    c._gar = listIds(hass, "cover", (e) => ["garage", "gate"].includes(e.attributes.device_class) && !c.exclude.includes(e.entity_id));
    return c;
  }
  _watch() { const c = this._config; return [c.alarm, ...c._locks, ...c._det, ...c._gar].filter(Boolean); }
  _ask(a) { this._asking = a; this._code = ""; this._lastKey = null; this._render(); }
  _extraSheet(k) {
    if (k !== "l:det") return "";
    const S = this._hass.states, rows = this._config._det.map((id) => S[id]).filter(Boolean).map((e) => `<div class="row" data-entity="${e.entity_id}">
      <ha-icon class="ri" icon="${DETECT[e.attributes.device_class][1]}"></ha-icon><div class="rn">${esc(nice(this._hass, e))}<div class="rs">${esc(areaName(this._hass, e.entity_id) || DETECT[e.attributes.device_class][0])}</div></div>
      <div class="rs">${e.state === "on" ? "Alarm" : isLive(e) ? "ruhig" : "offline"}</div></div>`).join("");
    return this._frame("Melder", `<div class="rows">${rows}</div>`);
  }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, H = this._hass, S = H.states;
    const alarm = c.alarm && S[c.alarm], locks = c._locks.map((id) => S[id]).filter(Boolean), det = c._det.map((id) => S[id]).filter(Boolean), gar = c._gar.map((id) => S[id]).filter(Boolean);
    if (!alarm && !locks.length && !det.length && !gar.length) return this._setup(c.name, "Ich habe weder Alarmanlage noch Schlösser oder Melder gefunden.");
    const fired = det.filter((e) => e.state === "on"), jammed = locks.filter((e) => e.state === "jammed"), openL = locks.filter((e) => ["unlocked", "open", "opening"].includes(e.state));
    const openG = gar.filter((e) => ["open", "opening"].includes(e.state)), offline = [...locks, ...det, ...(alarm ? [alarm] : [])].filter((e) => !isLive(e));
    let state, big, say;
    if (alarm?.state === "triggered" || fired.length) {
      const f = fired[0];
      state = { key: "crit", label: "Alarm", icon: "mdi:alarm-light" };
      big = f ? `${DETECT[f.attributes.device_class][0]} erkannt` : "Alarm ausgelöst";
      say = f ? `${fired.map((e) => nice(H, e)).join(", ")} · ${ago(f.last_changed)}` : esc(fname(alarm));
    } else if (jammed.length) { state = { key: "crit", label: "Schloss klemmt", icon: "mdi:lock-alert" }; big = "Schloss klemmt"; say = jammed.map((e) => nice(H, e)).join(", "); }
    else if (openL.length || openG.length) {
      state = { key: "low", label: `${openL.length + openG.length} offen`, icon: "mdi:lock-open-variant" }; big = "Nicht alles zu";
      say = [...openL, ...openG].map((e) => `${nice(H, e)} ${e.entity_id.startsWith("cover.") ? "offen" : LOCK_DE[e.state] || "offen"}`).join(", ");
    } else { state = { key: "ok", label: "Alles sicher", icon: "mdi:shield-check" }; big = "Alles sicher"; say = offline.length ? `${offline.length} ${offline.length === 1 ? "Gerät meldet" : "Geräte melden"} sich nicht.` : "Türen zu, keine Meldung."; }
    const ask = (key) => { const a = this._asking; if (!a || a.key !== key) return "";
      return `<div class="ask"><b>${esc(a.q)}</b>${a.code ? `<input type="password" autocomplete="off" inputmode="${a.code === "number" ? "numeric" : "text"}" placeholder="Code" aria-label="Code" data-code>` : ""}
        <button class="pill" data-no>Abbrechen</button><button class="pill warn" data-yes>${esc(a.yes)}</button></div>`; };
    let rows = "";
    if (alarm) {
      const f = alarm.attributes.supported_features ?? 0, armed = alarm.state.startsWith("armed") || ["arming", "pending", "triggered"].includes(alarm.state);
      const needArm = alarm.attributes.code_format && alarm.attributes.code_arm_required !== false, needDis = !!alarm.attributes.code_format;
      const arm = [[1, "alarm_arm_home", "Zu Hause"], [2, "alarm_arm_away", "Abwesend"], [4, "alarm_arm_night", "Nacht"]].filter(([b]) => f & b);
      rows += `<div class="row sx"><ha-icon class="ri" icon="${armed ? "mdi:shield-lock" : "mdi:shield-off-outline"}"></ha-icon>
        <div class="rn" data-sheet="e:${c.alarm}">${esc(cleanName(fname(alarm)))}<div class="rs${this._fbText(c.alarm) ? " fb" : ""}">${this._fbText(c.alarm) || ALARM_DE[alarm.state] || stateDe(alarm.state)}</div></div>
        <div class="cb">${!isLive(alarm) ? "" : armed ? `<button class="pill" data-arm="alarm_disarm" data-code-need="${needDis ? alarm.attributes.code_format : ""}">Unscharf</button>`
          : arm.map(([, s, l]) => `<button class="pill" data-arm="${s}" data-code-need="${needArm ? alarm.attributes.code_format : ""}">${l}</button>`).join("")}</div></div>${ask(c.alarm)}`;
    }
    for (const e of locks) {
      const id = e.entity_id, f = e.attributes.supported_features ?? 0, code = e.attributes.code_format ? (/^\^\\d/.test(e.attributes.code_format) ? "number" : "text") : "";
      const lockedNow = e.state === "locked";
      rows += `<div class="row sx"><ha-icon class="ri" icon="${e.state === "jammed" ? "mdi:lock-alert" : lockedNow ? "mdi:lock" : "mdi:lock-open-variant"}"></ha-icon>
        <div class="rn" data-sheet="e:${id}">${esc(nice(H, e))}<div class="rs${this._fbText(id) ? " fb" : ""}">${this._fbText(id) || (isLive(e) ? LOCK_DE[e.state] || stateDe(e.state) : "nicht erreichbar")}</div></div>
        <div class="cb">${!isLive(e) ? "" : lockedNow ? `<button class="pill" data-lock="unlock" data-id="${id}" data-c="${code}">Aufschließen</button>${f & 1 ? `<button class="pill" data-lock="open" data-id="${id}" data-c="${code}">Öffnen</button>` : ""}`
          : `<button class="pill" data-lock="lock" data-id="${id}" data-c="${code}">Abschließen</button>`}</div></div>${ask(id)}`;
    }
    for (const e of gar) {
      const id = e.entity_id, op = ["open", "opening"].includes(e.state);
      rows += `<div class="row sx"><ha-icon class="ri" icon="${op ? "mdi:garage-open-variant" : "mdi:garage-variant"}"></ha-icon>
        <div class="rn" data-sheet="e:${id}">${esc(nice(H, e))}<div class="rs${this._fbText(id) ? " fb" : ""}">${this._fbText(id) || (isLive(e) ? ({ opening: "öffnet …", closing: "schließt …" }[e.state] || (op ? "offen" : "zu")) : "nicht erreichbar")}</div></div>
        <div class="cb">${!isLive(e) ? "" : op ? `<button class="pill" data-gar="close_cover" data-id="${id}">Schließen</button>` : `<button class="pill" data-gar="open_cover" data-id="${id}">Öffnen</button>`}</div></div>${ask(id)}`;
    }
    if (det.length) {
      const kinds = [...new Set(det.map((e) => DETECT[e.attributes.device_class][0]))];
      rows += `<div class="row" data-sheet="l:det"><ha-icon class="ri" icon="${fired.length ? "mdi:alarm-light" : "mdi:smoke-detector-variant"}"></ha-icon>
        <div class="rn">${det.length} ${det.length === 1 ? "Melder" : "Melder"}<div class="rs">${kinds.join(", ")}</div></div>
        <div class="rs">${fired.length ? `${fired.length} Alarm` : det.some((e) => !isLive(e)) ? `${det.filter((e) => !isLive(e)).length} offline` : "alle ruhig"}</div></div>`;
    }
    this._shell(state.key, `
      ${this._head(c.name, state)}
      <div class="big" style="margin-top:6px"><span class="num w">${big}</span></div>
      <div class="muted">${esc(say)}</div>
      <div class="rows">${rows}</div>`, ACT_CSS + `
      .num.w { font-size: 30px; } .st-crit .num.w { color: var(--error-color, #e34948); }
      .st-crit ha-card, ha-card.st-crit { box-shadow: inset 0 0 0 2px color-mix(in srgb, var(--error-color, #e34948) 55%, transparent); }
      .row.sx { cursor: default; } .cb { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }`);
    const R = this.shadowRoot;
    const run = (key, q, yes, codeKind, fn) => this._ask({ key, q: q.text, yes, code: codeKind, fn });
    R.querySelectorAll("[data-arm]").forEach((b) => b.addEventListener("click", () => {
      const svc = b.dataset.arm, need = b.dataset.codeNeed, kind = need ? (need === "number" ? "number" : "text") : "";
      const go = (code) => this._act(c.alarm, svc === "alarm_disarm" ? "Schaltet unscharf …" : "Schaltet scharf …", () => H.callService("alarm_control_panel", svc, { entity_id: c.alarm, ...(code ? { code } : {}) }));
      if (svc === "alarm_disarm" || kind) run(c.alarm, { svc, text: svc === "alarm_disarm" ? "Alarmanlage wirklich unscharf schalten?" : "Code zum Scharfschalten eingeben" }, svc === "alarm_disarm" ? "Unscharf" : "Scharf", kind, go);
      else go();
    }));
    R.querySelectorAll("[data-lock]").forEach((b) => b.addEventListener("click", () => {
      const svc = b.dataset.lock, id = b.dataset.id, kind = b.dataset.c, name = nice(H, S[id]);
      const go = (code) => this._act(id, { lock: "Schließt ab …", unlock: "Schließt auf …", open: "Öffnet …" }[svc], () => H.callService("lock", svc, { entity_id: id, ...(code ? { code } : {}) }));
      if (svc === "lock" && !kind) go(); else run(id, { svc, text: svc === "lock" ? `${name} abschließen?` : svc === "open" ? `${name} wirklich öffnen?` : `${name} wirklich aufschließen?` }, svc === "lock" ? "Abschließen" : svc === "open" ? "Öffnen" : "Aufschließen", kind, go);
    }));
    R.querySelectorAll("[data-gar]").forEach((b) => b.addEventListener("click", () => {
      const svc = b.dataset.gar, id = b.dataset.id, go = () => this._act(id, svc === "open_cover" ? "Öffnet …" : "Schließt …", () => H.callService("cover", svc, { entity_id: id }));
      if (svc === "open_cover") run(id, { svc, text: `${nice(H, S[id])} wirklich öffnen?` }, "Öffnen", "", go); else go();
    }));
    const inp = R.querySelector("[data-code]");
    if (inp) { inp.value = this._code || ""; inp.addEventListener("input", () => { this._code = inp.value; }); if (this._askFocus) inp.focus?.(); this._askFocus = true;
      inp.addEventListener("keydown", (ev) => { if (ev.key === "Enter") R.querySelector("[data-yes]")?.click(); }); }
    R.querySelector("[data-no]")?.addEventListener("click", () => { this._asking = null; this._askFocus = false; this._lastKey = null; this._render(); });
    R.querySelector("[data-yes]")?.addEventListener("click", () => {
      const a = this._asking; if (!a) return;
      if (a.code && !this._code) { inp?.focus?.(); return; }
      const code = this._code; this._asking = null; this._code = ""; this._askFocus = false; a.fn(code);
    });
  }
}

/* ================= 20 · Kameras ================= */
class MangfallCameraCard extends MangfallBase {
  static defaults = { name: "Kameras", entities: [], refresh: 10 };
  static getStubConfig(hass) { return { entities: listIds(hass, "camera").slice(0, 6) }; }
  static getConfigForm() {
    return { schema: [{ name: "entities", selector: { entity: { multiple: true, filter: { domain: "camera" } } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [
        { name: "refresh", selector: { number: { min: 2, max: 120, mode: "box", unit_of_measurement: "s" } } }, { name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ entities: "Kameras", refresh: "Bild erneuern alle", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ entities: "Leer lassen – die Karte zeigt alle Kameras.", refresh: "Antippen vergrößert eine Kamera, sie erneuert sich dann jede Sekunde." }[s.name]) };
  }
  _auto(c, hass) {
    c._cams = c.entities?.length ? c.entities : listIds(hass, "camera").slice(0, 8);
    c._motion = Object.fromEntries(c._cams.map((id) => [id, sameDevice(hass, id, (e) => e.entity_id.startsWith("binary_sensor.") && ["motion", "occupancy", "presence"].includes(e.attributes.device_class))?.entity_id || ""]));
    return c;
  }
  _watch() { const c = this._config; return [...c._cams, ...Object.values(c._motion)].filter(Boolean); }
  disconnectedCallback() { clearInterval(this._tm); this._tm = null; }
  _url(e, fast) { const pic = e?.attributes.entity_picture; if (!pic) return ""; const u = this._hass.hassUrl ? this._hass.hassUrl(pic) : pic; return `${u}${u.includes("?") ? "&" : "?"}_=${Math.floor(Date.now() / (fast ? 1000 : 2000))}`; }
  _tick() {
    if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
    const now = Date.now();
    this.shadowRoot?.querySelectorAll("img[data-cam]").forEach((img) => {
      const fast = img.dataset.cam === this._big, every = fast ? 1000 : Math.max(2, this._config.refresh) * 1000;
      if (now - (+img.dataset.at || 0) < every - 200) return;
      img.dataset.at = now;
      const pre = new Image(), url = this._url(this._hass.states[img.dataset.cam], fast);
      pre.onload = () => { img.src = url; img.classList.remove("err"); };
      pre.onerror = () => img.classList.add("err");
      pre.src = url;
    });
  }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, H = this._hass, cams = c._cams.map((id) => H.states[id]).filter(Boolean);
    if (!cams.length) return this._setup(c.name, "Ich habe keine Kamera in deinem Home Assistant gefunden.");
    if (!this._tm) this._tm = setInterval(() => this._tick(), 1000);
    const moving = cams.filter((e) => H.states[c._motion[e.entity_id]]?.state === "on"), off = cams.filter((e) => !isLive(e));
    const state = moving.length ? { key: "info", label: `Bewegung: ${nice(H, moving[0])}`, icon: "mdi:motion-sensor" }
      : off.length ? { key: "low", label: `${off.length} offline`, icon: "mdi:cctv-off" } : { key: "ok", label: `${cams.length} ${cams.length === 1 ? "Kamera" : "Kameras"}`, icon: "mdi:cctv" };
    if (this._big && !cams.some((e) => e.entity_id === this._big)) this._big = null;
    const tile = (e, big) => {
      const id = e.entity_id, mv = H.states[c._motion[id]]?.state === "on", ok = isLive(e);
      return `<div class="cam${big ? " big" : ""}${ok ? "" : " na"}" ${ok ? `data-cam-tap="${id}"` : `data-entity="${id}"`} role="button" tabindex="0" aria-label="${esc(nice(H, e))} ${ok ? "vergrößern" : "Verlauf"}">
        ${ok ? `<img data-cam="${id}" data-at="${Date.now()}" src="${esc(this._url(e, big))}" alt="" loading="lazy">` : `<div class="off"><ha-icon icon="mdi:cctv-off"></ha-icon>offline</div>`}
        <span class="cn">${mv ? `<i class="mv"></i>` : ""}${esc(nice(H, e))}</span></div>`;
    };
    const big = this._big && H.states[this._big];
    this._shell(state.key, `
      ${this._head(c.name, state)}
      ${big ? `${tile(big, true)}<div class="bb"><button class="pill" data-more="${big.entity_id}"><ha-icon icon="mdi:play-circle-outline"></ha-icon>Live in Home Assistant</button>
        <button class="pill" data-shrink><ha-icon icon="mdi:view-grid-outline"></ha-icon>Alle Kameras</button></div>` : ""}
      <div class="grid${cams.length === 1 ? " one" : ""}">${cams.filter((e) => e.entity_id !== this._big).map((e) => tile(e, false)).join("")}</div>`, ACT_CSS + `
      .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; margin-top: 12px; } .grid.one { grid-template-columns: 1fr; }
      .cam { position: relative; aspect-ratio: 16 / 9; border-radius: 12px; overflow: hidden; cursor: pointer; background: var(--secondary-background-color, rgba(127,127,127,.12)); }
      .cam.big { margin-top: 12px; }
      .cam img { width: 100%; height: 100%; object-fit: cover; display: block; transition: opacity .3s; } .cam img.err { opacity: .35; }
      .cam:focus-visible { outline: 2px solid var(--primary-color, #2a78d6); outline-offset: 2px; }
      .cn { position: absolute; left: 8px; bottom: 6px; right: 8px; display: flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 500; color: #fff;
        text-shadow: 0 1px 3px rgba(0,0,0,.7); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .mv { width: 7px; height: 7px; border-radius: 50%; background: var(--warning-color, #eda100); flex: 0 0 7px; animation: mc-blink 1.4s ease-in-out infinite; }
      @keyframes mc-blink { 50% { opacity: .3; } }
      .off { height: 100%; display: grid; place-content: center; justify-items: center; gap: 4px; font-size: 12px; color: var(--secondary-text-color); }
      .off ha-icon { --mdc-icon-size: 26px; }
      .cam.na .cn { color: var(--primary-text-color); text-shadow: none; }
      .bb { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }`);
    const R = this.shadowRoot;
    R.querySelectorAll("[data-cam-tap]").forEach((el) => {
      const go = () => { this._big = this._big === el.dataset.camTap ? null : el.dataset.camTap; this._lastKey = null; this._render(); };
      el.addEventListener("click", go); el.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); } });
    });
    R.querySelector("[data-shrink]")?.addEventListener("click", () => { this._big = null; this._lastKey = null; this._render(); });
  }
}

/* ================= 21 · Musik & Medien ================= */
const MP = { PAUSE: 1, SEEK: 2, VOL: 4, MUTE: 8, PREV: 16, NEXT: 32, ON: 128, OFF: 256, PLAY: 16384 };
const mmss = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = String(s % 60).padStart(2, "0"); return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`; };
class MangfallMediaCard extends MangfallBase {
  static defaults = { name: "Musik", entities: [], show_off: false };
  static getStubConfig(hass) { return { entities: MangfallMediaCard._find(hass) }; }
  static _find(hass) { return listIds(hass, "media_player").slice(0, 8); }
  static getConfigForm() {
    return { schema: [{ name: "entities", selector: { entity: { multiple: true, filter: { domain: "media_player" } } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [{ name: "show_off", selector: { boolean: {} } }, { name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ entities: "Player", show_off: "Ausgeschaltete Player zeigen", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ entities: "Leer lassen – die Karte zeigt alle Lautsprecher und Fernseher, der aktive zuerst." }[s.name]) };
  }
  _auto(c, hass) { c._mp = c.entities?.length ? c.entities : MangfallMediaCard._find(hass); return c; }
  _watch() { return this._dragging ? [] : this._config._mp; }
  _rank(e) { return { playing: 0, paused: 1, buffering: 1, on: 2, idle: 3, standby: 4, off: 5 }[e.state] ?? 6; }
  _render() {
    if (!this._config || !this._hass || this._dragging) return;
    const c = this._config, H = this._hass, all = c._mp.map((id) => H.states[id]).filter(Boolean);
    if (!all.length) return this._setup(c.name, "Ich habe keinen Lautsprecher oder Fernseher gefunden.");
    const sorted = [...all].sort((a, b) => this._rank(a) - this._rank(b));
    const act = (this._sel && H.states[this._sel]) || sorted[0], a = act.attributes, f = a.supported_features || 0, on = !["off", "standby", "unavailable", "unknown"].includes(act.state);
    const playing = act.state === "playing", id = act.entity_id;
    const state = !isLive(act) ? { key: "low", label: "Nicht erreichbar", icon: "mdi:speaker-off" } : playing ? { key: "ok", label: "Spielt", icon: "mdi:play" }
      : act.state === "paused" ? { key: "info", label: "Pausiert", icon: "mdi:pause" } : on ? { key: "info", label: "Bereit", icon: "mdi:speaker" } : { key: "info", label: "Aus", icon: "mdi:power" };
    const pic = a.entity_picture ? (H.hassUrl ? H.hassUrl(a.entity_picture) : a.entity_picture) : "";
    const title = a.media_title || (on ? a.app_name || a.source || "" : ""), sub = [a.media_artist || a.media_series_title, a.media_album_name].filter(Boolean).join(" · ");
    let prog = "";
    if (on && a.media_duration > 0 && a.media_position != null) {
      const pos = Math.min(a.media_duration, a.media_position + (playing && a.media_position_updated_at ? (Date.now() - toMs(a.media_position_updated_at)) / 1000 : 0));
      const p = (pos / a.media_duration) * 100, rest = a.media_duration - pos;
      prog = `<div class="prog"><i class="${playing ? "run" : ""}" style="--p0:${p.toFixed(2)}%;--dur:${rest.toFixed(1)}s"></i></div><div class="pt"><span>${mmss(pos)}</span><span>${mmss(a.media_duration)}</span></div>`;
    }
    const grp = Array.isArray(a.group_members) && a.group_members.length > 1 ? ` · mit ${a.group_members.length - 1} ${a.group_members.length === 2 ? "weiterem Raum" : "weiteren Räumen"}` : "";
    const vol = typeof a.volume_level === "number" ? Math.round(a.volume_level * 100) : null;
    const others = sorted.filter((e) => e !== act && (c.show_off || !["off", "unavailable", "unknown", "standby"].includes(e.state)));
    const ctrl = on ? `<div class="mc">
        ${f & MP.PREV ? `<button class="ib" data-mp="media_previous_track" aria-label="Zurück"><ha-icon icon="mdi:skip-previous"></ha-icon></button>` : ""}
        ${f & (MP.PAUSE | MP.PLAY) ? `<button class="ib main" data-mp="media_play_pause" aria-label="${playing ? "Pause" : "Abspielen"}"><ha-icon icon="${playing ? "mdi:pause" : "mdi:play"}"></ha-icon></button>` : ""}
        ${f & MP.NEXT ? `<button class="ib" data-mp="media_next_track" aria-label="Weiter"><ha-icon icon="mdi:skip-next"></ha-icon></button>` : ""}
        ${vol !== null && f & MP.VOL ? `<div class="vol"><button class="ib sm" data-mp="volume_mute" aria-label="Stumm"${f & MP.MUTE ? "" : " disabled"}><ha-icon icon="${a.is_volume_muted ? "mdi:volume-off" : "mdi:volume-medium"}"></ha-icon></button>
          <input type="range" min="0" max="100" step="1" value="${vol}" aria-label="Lautstärke" data-vol><span class="vv">${vol} %</span></div>` : ""}
      </div>` : isLive(act) && f & MP.ON ? `<div class="mc"><button class="pill" data-mp="turn_on"><ha-icon icon="mdi:power"></ha-icon>${this._fbText(id) || "Einschalten"}</button></div>` : "";
    this._shell(state.key, `
      ${this._head(c.name, state)}
      <div class="np">
        <div class="art${pic ? "" : " none"}" data-sheet="e:${id}">${pic ? `<img src="${esc(pic)}" alt="">` : `<ha-icon icon="${/tv/.test(lcName(act)) || a.device_class === "tv" ? "mdi:television" : "mdi:speaker"}"></ha-icon>`}</div>
        <div class="meta" data-sheet="e:${id}"><div class="mt">${esc(title || (on ? "Bereit" : isLive(act) ? "Aus" : "Nicht erreichbar"))}</div>
          <div class="ms">${esc(sub)}</div><div class="mr">${esc(cleanName(fname(act)))}${grp}</div></div>
      </div>${prog}${ctrl}
      ${others.length ? `<div class="rows">${others.map((e) => `<div class="row" data-pick="${e.entity_id}" role="button" tabindex="0">
        <ha-icon class="ri" icon="${e.state === "playing" ? "mdi:play-circle" : "mdi:speaker"}"></ha-icon><div class="rn">${esc(cleanName(fname(e)))}
        <div class="rs">${esc(e.attributes.media_title ? [e.attributes.media_title, e.attributes.media_artist].filter(Boolean).join(" · ") : stateDe(e.state))}</div></div></div>`).join("")}</div>` : ""}`, ACT_CSS + `
      .np { display: flex; gap: 14px; align-items: center; margin-top: 10px; }
      .art { flex: 0 0 76px; width: 76px; height: 76px; border-radius: 12px; overflow: hidden; display: grid; place-items: center; background: var(--secondary-background-color, rgba(127,127,127,.12)); }
      .art img { width: 100%; height: 100%; object-fit: cover; } .art ha-icon { --mdc-icon-size: 34px; color: var(--secondary-text-color); }
      .meta { flex: 1; min-width: 0; border-radius: 8px; }
      .mt { font-size: 17px; font-weight: 600; color: var(--primary-text-color); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
      .ms, .mr { font-size: 13px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; } .mr { font-size: 12px; opacity: .8; margin-top: 2px; }
      .prog { height: 3px; border-radius: 2px; margin-top: 12px; background: var(--divider-color, rgba(127,127,127,.25)); overflow: hidden; }
      .prog i { display: block; height: 100%; width: var(--p0); background: var(--primary-text-color); opacity: .7; border-radius: 2px; }
      .prog i.run { animation: mc-prog var(--dur) linear forwards; } @keyframes mc-prog { to { width: 100%; } }
      .pt { display: flex; justify-content: space-between; font-size: 11px; color: var(--secondary-text-color); margin-top: 3px; font-variant-numeric: tabular-nums; }
      .mc { display: flex; align-items: center; justify-content: center; gap: 10px; margin-top: 8px; flex-wrap: wrap; }
      .ib.main { width: 44px; height: 44px; flex-basis: 44px; background: var(--primary-text-color); color: var(--card-background-color, #fff); }
      .ib.main ha-icon { --mdc-icon-size: 24px; } .ib.sm { width: 28px; height: 28px; flex-basis: 28px; }
      .vol { display: flex; align-items: center; gap: 6px; flex: 1 1 160px; min-width: 140px; }
      .vol input { flex: 1; min-width: 0; accent-color: var(--primary-text-color); } .vv { font-size: 11.5px; color: var(--secondary-text-color); width: 34px; text-align: right; font-variant-numeric: tabular-nums; }`);
    const R = this.shadowRoot;
    R.querySelectorAll("[data-mp]").forEach((b) => b.addEventListener("click", () => {
      const svc = b.dataset.mp;
      this._act(id, svc === "turn_on" ? "Schaltet ein …" : "", () => H.callService("media_player", svc, svc === "volume_mute" ? { entity_id: id, is_volume_muted: !a.is_volume_muted } : { entity_id: id }));
    }));
    const v = R.querySelector("[data-vol]");
    if (v) {
      v.addEventListener("pointerdown", () => { this._dragging = true; });
      v.addEventListener("pointerup", () => setTimeout(() => { this._dragging = false; }, 400));
      v.addEventListener("input", () => { const s = R.querySelector(".vv"); if (s) s.textContent = v.value + " %"; });
      v.addEventListener("change", () => { this._dragging = false; H.callService("media_player", "volume_set", { entity_id: id, volume_level: +v.value / 100 }); });
    }
    R.querySelectorAll("[data-pick]").forEach((el) => {
      const go = () => { this._sel = el.dataset.pick; this._lastKey = null; this._render(); };
      el.addEventListener("click", go); el.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); } });
    });
  }
}

/* ================= 22 · Szenen & Schnellaktionen ================= */
const TECH_BTN = /restart|neu.?start|reboot|identify|identifiz|reset|update|firmware|ping|calibrat|kalibr|backup|test|wake|scan|sync|refresh|aktualis|messen|measure|nullpunkt|zero|reconnect|verbinden|aufr(ä|ae)umen|cleanup|factory|werkseinst/;
class MangfallActionsCard extends MangfallBase {
  static defaults = { name: "Schnellaktionen", entities: [] };
  static getStubConfig(hass) { return { entities: MangfallActionsCard._find(hass) }; }
  static _find(hass) {
    const user = (id) => { const r = regOf(hass, id); return !r.entity_category && !r.hidden; };
    const sc = listIds(hass, "scene", (e) => user(e.entity_id)), sr = listIds(hass, "script", (e) => user(e.entity_id));
    const bt = listIds(hass, "button", (e) => user(e.entity_id) && !TECH_BTN.test(lcName(e)) && e.state !== "unavailable");
    return [...sc, ...sr, ...bt].slice(0, 12);
  }
  static getConfigForm() {
    return { schema: [{ name: "entities", selector: { entity: { multiple: true, filter: [{ domain: "scene" }, { domain: "script" }, { domain: "button" }, { domain: "input_button" }] } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [{ name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ entities: "Szenen und Aktionen", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ entities: "Leer lassen – die Karte nimmt deine Szenen und Skripte. Technische Knöpfe wie „Neu starten“ lässt sie weg." }[s.name]) };
  }
  _auto(c, hass) { c._ids = c.entities?.length ? c.entities : MangfallActionsCard._find(hass); return c; }
  _watch() { return this._config._ids; }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, H = this._hass, list = c._ids.map((id) => H.states[id]).filter(Boolean);
    if (!list.length) return this._setup(c.name, "Ich habe noch keine Szenen oder Skripte gefunden. Leg sie in Home Assistant unter Automationen & Szenen an.");
    const running = list.filter((e) => e.entity_id.startsWith("script.") && e.state === "on");
    const state = running.length ? { key: "info", label: `${running.length} läuft`, icon: "mdi:play-circle-outline" } : { key: "ok", label: "Bereit", icon: "mdi:gesture-tap" };
    const DEF = { scene: "mdi:palette-outline", script: "mdi:script-text-play-outline", button: "mdi:gesture-tap-button", input_button: "mdi:gesture-tap-button" };
    const last = (e) => { const t = e.entity_id.startsWith("script.") ? e.attributes.last_triggered : isNaN(toMs(e.state)) ? null : e.state; return t ? ago(t).replace(/^seit /, "vor ") : ""; };
    const tiles = list.map((e) => {
      const id = e.entity_id, dom = id.split(".")[0], fb = this._fbText(id, 2500), run = dom === "script" && e.state === "on", ok = isLive(e) || dom === "scene" || dom === "button";
      return `<button class="act${fb ? " done" : ""}${run ? " run" : ""}" data-act="${id}"${e.state === "unavailable" ? " disabled" : ""}>
        <ha-icon icon="${fb ? "mdi:check" : run ? "mdi:loading" : e.attributes.icon || DEF[dom]}"></ha-icon><span class="an">${esc(cleanName(fname(e)))}</span>
        <span class="as">${fb || (run ? "läuft …" : e.state === "unavailable" ? "nicht verfügbar" : last(e))}</span></button>`;
    }).join("");
    this._shell(state.key, `${this._head(c.name, state)}<div class="acts">${tiles}</div><div class="muted hint">Lange drücken zeigt, wann es zuletzt lief.</div>`, `
      .acts { display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 8px; margin-top: 12px; }
      .act { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; min-height: 84px; padding: 12px; border: none; border-radius: 14px; cursor: pointer; font: inherit; text-align: left;
        color: var(--primary-text-color); background: var(--secondary-background-color, rgba(127,127,127,.08)); transition: background .2s, transform .1s; }
      .act:hover:not([disabled]) { background: color-mix(in srgb, var(--primary-color, #2a78d6) 12%, var(--secondary-background-color, #eee)); }
      .act:active { transform: scale(.97); } .act[disabled] { opacity: .45; cursor: default; }
      .act:focus-visible { outline: 2px solid var(--primary-color, #2a78d6); outline-offset: 2px; }
      .act ha-icon { --mdc-icon-size: 24px; color: var(--secondary-text-color); transition: color .2s; }
      .act.done { background: color-mix(in srgb, var(--success-color, #0ca30c) 16%, var(--secondary-background-color, #eee)); } .act.done ha-icon { color: var(--success-color, #0ca30c); }
      .act.run ha-icon { color: var(--info-color, #2a78d6); animation: mc-spin 1.2s linear infinite; } @keyframes mc-spin { to { transform: rotate(360deg); } }
      .an { font-size: 13.5px; font-weight: 500; line-height: 1.25; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
      .as { font-size: 11px; color: var(--secondary-text-color); margin-top: auto; }
      .hint { font-size: 11.5px; margin-top: 8px; opacity: .75; }`);
    this.shadowRoot.querySelectorAll("[data-act]").forEach((b) => {
      const id = b.dataset.act, dom = id.split(".")[0];
      let t = null, long = false;
      b.addEventListener("pointerdown", () => { long = false; t = setTimeout(() => { long = true; this._toggleSheet("e:" + id); }, 550); });
      ["pointerup", "pointerleave", "pointercancel"].forEach((x) => b.addEventListener(x, () => clearTimeout(t)));
      b.addEventListener("contextmenu", (ev) => { ev.preventDefault(); });
      b.addEventListener("click", () => {
        if (long) return;
        const svc = dom === "button" || dom === "input_button" ? [dom, "press"] : [dom, "turn_on"];
        this._act(id, dom === "scene" ? "Aktiviert" : dom === "script" ? "Gestartet" : "Gedrückt", () => H.callService(svc[0], svc[1], { entity_id: id }));
        clearTimeout(this._fbClr); this._fbClr = setTimeout(() => { this._lastKey = null; this._render(); }, 2600);
      });
    });
  }
}

/* ================= 23 · Stromfresser ================= */
class MangfallConsumersCard extends MangfallBase {
  static product = "WATT";
  static defaults = { name: "Verbraucher", exclude: [], show: 6 };
  static getStubConfig() { return {}; }   // leer = alle Steckdosen und Geräte mit Leistungsmessung
  static getConfigForm() {
    return { schema: [{ name: "exclude", selector: { entity: { multiple: true, filter: { domain: "sensor", device_class: "power" } } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [
        { name: "show", selector: { number: { min: 3, max: 20, mode: "box" } } }, { name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ exclude: "Ausblenden", show: "Anzahl anzeigen", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ exclude: "Die Karte findet Steckdosen und Geräte mit Leistungsmessung von selbst. Waschmaschine, Trockner und Spülmaschine erkennt sie am Namen." }[s.name]) };
  }
  _auto(c, hass) {
    const seen = new Map();
    for (const id of listIds(hass, "sensor", (e) => isConsumer(e) && !c.exclude.includes(e.entity_id))) {   // je Gerät nur ein Leistungssensor
      const d = regOf(hass, id).device_id || id;
      if (!seen.has(d) || /power$|leistung$/.test(id)) seen.set(d, id);
    }
    c._ids = [...seen.values()];
    c._apl = c._ids.filter((id) => APL_RE.test(lcName(hass.states[id]))).slice(0, 4);
    return c;
  }
  _watch() { return this._config._ids; }
  _onHass() { aplFetch(this, this._config._apl); }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, H = this._hass, S = H.states;
    if (!c._ids.length) return this._setup(c.name, "Ich habe keine Steckdose oder kein Gerät mit Leistungsmessung gefunden.");
    const rows = c._ids.map((id) => ({ id, e: S[id], w: toWatt(S[id]) })).filter((r) => r.e);
    const live = rows.filter((r) => r.w !== null).sort((a, b) => b.w - a.w), off = rows.filter((r) => r.w === null);
    const total = live.reduce((s, r) => s + Math.max(0, r.w), 0), on = live.filter((r) => r.w >= 5), max = Math.max(1, ...live.map((r) => r.w));
    const apl = c._apl.map((id) => ({ id, s: aplNow(this, id), t: aplType(lcName(S[id])) })).filter((x) => x.s);
    const done = apl.find((x) => x.s.done);
    const state = done ? { key: "ok", label: `${done.t[0]} fertig`, icon: "mdi:check-circle" } : on.length ? { key: "info", label: `${on.length} ${on.length === 1 ? "Gerät" : "Geräte"} an`, icon: "mdi:power-plug" }
      : { key: "ok", label: "Alles aus", icon: "mdi:power-plug-off-outline" };
    const sub = (r) => {
      const a = apl.find((x) => x.id === r.id);
      if (a?.s.run) return `${a.s.paused ? "pausiert" : "läuft"} seit ${ago(a.s.since).replace(/^seit /, "")}`;
      if (a?.s.done) return `fertig ${ago(a.s.at).replace(/^seit /, "seit ")}`;
      return areaName(H, r.id) || (r.w < 5 ? "aus" : "");
    };
    const icon = (r) => (APL_RE.test(lcName(r.e)) ? aplType(lcName(r.e))[1] : r.w >= 5 ? "mdi:power-plug" : "mdi:power-plug-off-outline");
    const shown = live.slice(0, c.show);
    const list = shown.map((r) => `<div class="row" data-entity="${r.id}"><ha-icon class="ri${r.w >= 5 ? " on" : ""}" icon="${icon(r)}"></ha-icon>
      <div class="rn">${esc(stripArea(cleanName(fname(r.e)).replace(/\s+energy$/i, ""), areaName(H, r.id)))}<div class="rs">${esc(sub(r))}</div></div>
      <div class="bar"><i style="width:${Math.max(r.w >= 1 ? 3 : 0, Math.min(100, (r.w / max) * 100))}%"></i></div><div class="rv">${fmtW(r.w)}</div></div>`).join("");
    if (!live.length) return this._shell("low", `${this._head(c.name, { key: "low", label: "Keine Werte", icon: "mdi:power-plug-off-outline" })}
      <div class="muted pad">${off.length === 1 ? `${esc(cleanName(fname(off[0].e)))} meldet sich gerade nicht.` : `Deine ${off.length} Geräte mit Leistungsmessung melden sich gerade nicht.`}</div>`);
    const [num, u] = wParts(total);
    this._shell(state.key, `
      ${this._head(c.name, state)}
      <div class="big" style="margin-top:6px"><span class="num">${num}</span><span class="u">${u} in ${live.length} ${live.length === 1 ? "Gerät" : "Geräten"}</span></div>
      ${done ? `<div class="muted">${esc(done.t[0])} ist seit ${ago(done.s.at).replace(/^seit /, "")} fertig.</div>` : ""}
      <div class="rows">${list || `<div class="muted pad">Gerade meldet kein Gerät einen Wert.</div>`}</div>
      ${live.length > c.show ? `<div class="muted more">+ ${live.length - c.show} weitere unter ${fmtW(shown[shown.length - 1].w)}</div>` : ""}
      ${off.length ? `<div class="muted more">${off.length} ${off.length === 1 ? "Gerät meldet" : "Geräte melden"} sich nicht.</div>` : ""}`, `
      .row .rn { flex: 0 1 44%; } .ri.on { color: var(--mc-state); }
      .bar { flex: 1; height: 6px; border-radius: 3px; background: var(--secondary-background-color, rgba(127,127,127,.12)); overflow: hidden; }
      .bar i { display: block; height: 100%; border-radius: 3px; background: var(--primary-text-color); opacity: .5; transition: width .6s; }
      .row .rv { min-width: 62px; text-align: right; font-size: 13.5px; } .more { font-size: 12px; }`);
  }
}

/* ================= 24 · Luftqualität ================= */
const AQ = {
  carbon_dioxide: { n: "CO₂", u: "ppm", lv: [800, 1000, 1400] },
  pm25: { n: "Feinstaub", u: "µg/m³", lv: [15, 25, 50] },
  pm10: { n: "PM10", u: "µg/m³", lv: [45, 60, 100] },
  volatile_organic_compounds: { n: "VOC", u: "µg/m³", lv: [300, 1000, 3000] },
  volatile_organic_compounds_parts: { n: "VOC", u: "ppb", lv: [220, 660, 2200] },
};
const AQ_OUT = /au(ß|ss)en|outdoor|outside|garten|balkon|terrasse|wetter|weather|dwd|forecast/;
const aqLevel = (dc, v) => { const l = AQ[dc].lv; return v < l[0] ? 0 : v < l[1] ? 1 : v < l[2] ? 2 : 3; };
class MangfallAirCard extends MangfallBase {
  static defaults = { name: "Luftqualität", sensors: [], co2_warn: 1000, co2_alarm: 1400 };
  static getStubConfig() { return {}; }   // leer = alle Räume mit CO₂-, Feinstaub- oder VOC-Sensor
  static getConfigForm() {
    return { schema: [{ name: "sensors", selector: { entity: { multiple: true, filter: Object.keys(AQ).map((device_class) => ({ domain: "sensor", device_class })) } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [
        { type: "grid", name: "", schema: [
          { name: "co2_warn", selector: { number: { min: 600, max: 2000, step: 50, mode: "box", unit_of_measurement: "ppm" } } },
          { name: "co2_alarm", selector: { number: { min: 800, max: 3000, step: 50, mode: "box", unit_of_measurement: "ppm" } } }] },
        { name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ sensors: "Sensoren", co2_warn: "Bald lüften ab", co2_alarm: "Jetzt lüften ab", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ sensors: "Leer lassen – die Karte findet CO₂-, Feinstaub- und VOC-Sensoren je Raum von selbst." }[s.name]) };
  }
  _auto(c, hass) {
    const ids = c.sensors?.length ? c.sensors : listIds(hass, "sensor", (e) => AQ[e.attributes.device_class] && !AQ_OUT.test(lcName(e)) && !AQ_OUT.test(areaName(hass, e.entity_id).toLowerCase()));
    const rooms = new Map();
    for (const id of ids) {
      const e = hass.states[id]; if (!e) continue;
      const key = areaOf(hass, id) || regOf(hass, id).device_id || id;
      if (!rooms.has(key)) rooms.set(key, { name: areaName(hass, id) || cleanName(fname(e)).replace(/\s*(co2|co₂|carbon dioxide|kohlendioxid|pm2[.,]?5|feinstaub|voc)\s*$/i, "") || fname(e), ids: {} });
      const r = rooms.get(key); if (!r.ids[e.attributes.device_class]) r.ids[e.attributes.device_class] = id;
    }
    c._rooms = [...rooms.values()].slice(0, 10);
    return c;
  }
  _watch() { return this._config._rooms.flatMap((r) => Object.values(r.ids)); }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, S = this._hass.states;
    if (!c._rooms.length) return this._setup(c.name, "Ich habe keinen CO₂-, Feinstaub- oder VOC-Sensor in deinen Räumen gefunden.");
    const co2lv = (v) => (v < 800 ? 0 : v < c.co2_warn ? 1 : v < c.co2_alarm ? 2 : 3);
    const data = c._rooms.map((r) => {
      const vals = Object.entries(r.ids).map(([dc, id]) => ({ dc, id, v: parseFloat(S[id]?.state), live: isLive(S[id]) })).filter((x) => x.live && isFinite(x.v));
      const lv = vals.map((x) => ({ ...x, l: x.dc === "carbon_dioxide" ? co2lv(x.v) : aqLevel(x.dc, x.v) }));
      const worst = lv.reduce((a, b) => (!a || b.l > a.l ? b : a), null);
      return { r, lv, worst, offline: !vals.length };
    });
    const sortd = [...data].sort((a, b) => (b.worst?.l ?? -1) - (a.worst?.l ?? -1));
    const top = sortd[0], tl = top.worst?.l ?? 0, co2 = top.lv.find((x) => x.dc === "carbon_dioxide");
    const tip = (d) => { if (!d.worst) return d.offline ? "meldet sich nicht" : "kein Wert";
      const w = d.worst; return w.l >= 3 ? (w.dc === "carbon_dioxide" ? "jetzt lüften" : "Luft belastet – lüften") : w.l === 2 ? "bald lüften" : w.l === 1 ? "in Ordnung" : "frische Luft"; };
    const state = tl >= 3 ? { key: "crit", label: "Jetzt lüften", icon: "mdi:window-open-variant" } : tl === 2 ? { key: "low", label: "Bald lüften", icon: "mdi:weather-windy" }
      : { key: "ok", label: "Gute Luft", icon: "mdi:leaf" };
    const fmtAQ = (x) => `${fmt(x.v, x.v < 10 ? 1 : 0)} ${AQ[x.dc].u}`;
    const say = tl >= 2 ? `${esc(top.r.name)} ${tl >= 3 ? "jetzt" : "bald"} lüften – ${fmtAQ(top.worst)}` : "Überall frische Luft.";
    const main = co2 || top.worst;
    const rows = sortd.map((d) => `<div class="row"><span class="dot ${["d-ok", "d-ok", "d-low", "d-crit"][d.worst?.l ?? 0]}${d.worst ? "" : " na"}"></span>
      <div class="rn">${esc(d.r.name)}<div class="rs">${tip(d)}</div></div>
      <div class="vals">${d.lv.map((x) => `<span class="aq${x.dc === "carbon_dioxide" ? " main" : ""}" data-sheet="e:${x.id}" title="${AQ[x.dc].n}">${x.dc === "carbon_dioxide" ? "" : `<i>${AQ[x.dc].n}</i>`}${fmtAQ(x)}</span>`).join("")}</div></div>`).join("");
    this._shell(state.key, `
      ${this._head(c.name, state)}
      ${main ? `<div class="big" style="margin-top:6px" data-sheet="e:${main.id}"><span class="num">${fmt(main.v, main.v < 10 ? 1 : 0)}</span><span class="u">${AQ[main.dc].u} ${main.dc === "carbon_dioxide" ? "CO₂" : AQ[main.dc].n} · ${esc(top.r.name)}</span></div>` : ""}
      <div class="say">${say}</div><div class="rows">${rows}</div>`, `
      .say { font-size: 14px; color: var(--primary-text-color); margin-top: 6px; }
      .row { cursor: default; } .dot.na { background: var(--disabled-text-color, #9e9e9e); opacity: .5; }
      .vals { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 4px 10px; }
      .aq { font-size: 12.5px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; white-space: nowrap; border-radius: 6px; padding: 1px 3px; }
      .aq:hover { color: var(--primary-text-color); } .aq.main { font-size: 15px; font-weight: 600; color: var(--primary-text-color); }
      .aq i { font-style: normal; opacity: .7; margin-right: 3px; }`);
  }
}

/* ================= 25 · Termine ================= */
const CAL_COL = ["#2a78d6", "#e36f2c", "#2f9e6a", "#9c5cc8", "#d6457a", "#8a7a2b", "#3a9fb0"];
class MangfallCalendarCard extends MangfallBase {
  static defaults = { name: "Termine", entities: [], days: 7, show: 8 };
  static getStubConfig(hass) { return { entities: MangfallCalendarCard._find(hass) }; }
  static _find(hass) { return listIds(hass, "calendar", (e) => !WASTE_RE.test(lcName(e)) && regOf(hass, e.entity_id).platform !== "waste_collection_schedule").slice(0, 8); }
  static getConfigForm() {
    return { schema: [{ name: "entities", selector: { entity: { multiple: true, filter: { domain: "calendar" } } } },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [
        { type: "grid", name: "", schema: [{ name: "days", selector: { number: { min: 1, max: 31, mode: "box", unit_of_measurement: "Tage" } } }, { name: "show", selector: { number: { min: 3, max: 30, mode: "box" } } }] },
        { name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ entities: "Kalender", days: "Vorschau", show: "Termine anzeigen", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ entities: "Leer lassen – die Karte nimmt alle Kalender außer dem Müllkalender." }[s.name]) };
  }
  _check() { this._ev = null; this._evAt = 0; }
  _auto(c, hass) { c._cals = c.entities?.length ? c.entities : MangfallCalendarCard._find(hass); return c; }
  _watch() { return this._config._cals; }
  disconnectedCallback() { clearInterval(this._clk); this._clk = null; }
  _onHass() {
    if (!this._clk) this._clk = setInterval(() => { const m = Math.floor(Date.now() / 6e4); if (m !== this._min && this._hass) { this._min = m; this._lastKey = null; this._render(); } }, 15000);
    const c = this._config;
    if (!c._cals.length || !this._hass.callApi || this._evBusy || Date.now() - this._evAt < 6e5) return;
    this._evBusy = true; this._evAt = Date.now();
    const s = dayStart(), e = new Date(+s + (c.days + 1) * 864e5), q = `start=${encodeURIComponent(s.toISOString())}&end=${encodeURIComponent(e.toISOString())}`;
    let failed = 0;
    Promise.all(c._cals.map((id, i) => this._hass.callApi("GET", `calendars/${id}?${q}`).then((evs) => (evs || []).map((ev) => {
      const allDay = !!ev.start?.date, a = allDay ? new Date(ev.start.date + "T00:00") : new Date(ev.start?.dateTime || ev.start), z = allDay ? new Date((ev.end?.date || ev.start.date) + "T00:00") : new Date(ev.end?.dateTime || ev.end || a);
      return { id, col: CAL_COL[i % CAL_COL.length], t: ev.summary || "Ohne Titel", loc: ev.location || "", a: +a, z: +z, allDay };
    })).catch(() => { failed++; return []; }))).then((lists) => {
      this._evErr = failed === c._cals.length; if (this._evErr) this._evAt = Date.now() - 6e5 + 3e4;
      this._ev = lists.flat().filter((x) => isFinite(x.a)).sort((x, y) => x.a - y.a || y.allDay - x.allDay);
      this._evBusy = false; this._lastKey = null; this._render();
    });
  }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, now = Date.now(), t0 = +dayStart();
    if (!c._cals.length) return this._setup(c.name, "Ich habe keinen Kalender gefunden. Verbinde zum Beispiel Google- oder iCloud-Kalender in Home Assistant.");
    let evs = this._ev;
    if (!evs) evs = c._cals.map((id, i) => { const a = this._hass.states[id]?.attributes || {}; return a.message && a.start_time ? { id, col: CAL_COL[i % CAL_COL.length], t: a.message, loc: a.location || "",
      a: +new Date(a.start_time.replace(" ", "T")), z: +new Date((a.end_time || a.start_time).replace(" ", "T")), allDay: !!a.all_day } : null; }).filter(Boolean);
    const up = evs.filter((x) => x.z > now || (x.allDay && x.z > t0)).filter((x) => x.a < t0 + (c.days + 1) * 864e5);
    const days = new Map();
    for (const x of up) { const d = Math.max(+dayStart(new Date(x.a)), t0); if (!days.has(d)) days.set(d, []); days.get(d).push(x); }
    const today = days.get(t0) || [], next = up.find((x) => !x.allDay && x.a > now), cur = up.find((x) => !x.allDay && x.a <= now && x.z > now);
    const mins = next ? Math.round((next.a - now) / 6e4) : null;
    const state = cur ? { key: "info", label: "Läuft gerade", icon: "mdi:calendar-clock" } : mins !== null && mins <= 60 ? { key: "low", label: `In ${mins} min`, icon: "mdi:bell-ring-outline" }
      : { key: "ok", label: up.length ? "Nichts Dringendes" : "Freie Tage", icon: "mdi:calendar-check" };
    let n = 0;
    const rows = [...days.entries()].map(([d, xs]) => {
      const items = xs.filter(() => n++ < c.show || d === t0);
      if (!items.length) return "";
      return `<div class="rh2"><span>${dayWord(new Date(d))}</span>${d === t0 ? `<span>${new Date(d).toLocaleDateString("de-DE", { day: "numeric", month: "short" })}</span>` : ""}</div>
        <div class="rows">${items.map((x) => {
          const run = !x.allDay && x.a <= now && x.z > now;
          const when = x.allDay ? "ganztägig" : run ? `bis ${hm(x.z)}` : hm(x.a);
          return `<div class="row" data-more="${x.id}" role="button" tabindex="0"><span class="dot" style="background:${x.col}"></span>
            <div class="rn">${esc(x.t)}${x.loc ? `<div class="rs">${esc(x.loc)}</div>` : ""}</div><div class="rs${run ? " now" : ""}">${when}</div></div>`; }).join("")}</div>`;
    }).join("");
    const loadingNote = this._evErr && !this._ev?.length ? "Termine nicht abrufbar – ich versuche es gleich noch einmal." : "";
    this._shell(state.key, `
      ${this._head(c.name, state)}
      <div class="big" style="margin-top:6px"><span class="num">${today.length}</span><span class="u">${today.length === 1 ? "Termin" : "Termine"} heute</span></div>
      <div class="muted">${loadingNote || (cur ? `Jetzt: ${esc(cur.t)}` : next ? `${mins !== null && mins < 120 ? `In ${mins} min` : `${dayWord(new Date(next.a))}, ${hm(next.a)}`}: ${esc(next.t)}` : up.length ? "" : `Keine Termine in den nächsten ${c.days} Tagen.`)}</div>
      ${rows}`, ACT_CSS + `.row .rs.now { color: var(--mc-state); font-weight: 500; } .row .rn { white-space: normal; }`);
  }
}

/* ================= 26 · Haus-Check ================= */
class MangfallSystemCard extends MangfallBase {
  static defaults = { name: "Haus-Check", backup_days: 3, disk_warn: 85 };
  static getStubConfig() { return {}; }
  static getConfigForm() {
    return { schema: [{ type: "grid", name: "", schema: [
        { name: "backup_days", selector: { number: { min: 1, max: 30, mode: "box", unit_of_measurement: "Tage" } } },
        { name: "disk_warn", selector: { number: { min: 50, max: 99, mode: "box", unit_of_measurement: "%" } } }] },
      { type: "expandable", name: "", title: "Weitere Einstellungen", flatten: true, schema: [{ name: "name", selector: { text: {} } }, BRANDING] }],
      computeLabel: (s) => ({ backup_days: "Backup älter als", disk_warn: "Speicher voll ab", name: "Name", show_branding: "MANGFALL-Zeichen anzeigen" }[s.name]),
      computeHelper: (s) => ({ backup_days: "Die Karte findet Updates, Backups, Geräte offline und den Speicherplatz von selbst." }[s.name]) };
  }
  _auto(c, hass) {
    const S = hass.states;
    c._upd = listIds(hass, "update");
    c._backup = pick(hass, (e) => e.entity_id.startsWith("sensor.") && e.attributes.device_class === "timestamp" && /backup|sicherung/.test(lcName(e)),
      (n) => 3 * has(/erfolgreich|successful|last.?success|letzte/, n) - 6 * has(/n(ä|ae)chst|next|versucht|attempt/, n), 3);
    c._disk = pick(hass, (e) => e.entity_id.startsWith("sensor.") && unitOf(e) === "%" && /disk|datentr(ä|ae)ger|festplatte|storage|speicherplatz/.test(lcName(e)),
      (n) => 3 * has(/use|usage|belegt|nutzung|used/, n) - 6 * has(/free|frei/, n), 3);
    // Geräte, bei denen alles „nicht verfügbar“ meldet
    const dev = new Map();
    for (const e of Object.values(S)) {
      const r = regOf(hass, e.entity_id); if (!r.device_id || r.hidden || e.entity_id.startsWith("update.")) continue;
      if (!dev.has(r.device_id)) dev.set(r.device_id, []); dev.get(r.device_id).push(e);
    }
    const prefix = (es) => {   // Gerätename aus dem gemeinsamen Anfang der Entitätsnamen, falls Home Assistant keinen mitliefert
      for (const k of [2, 1]) {   // häufigster Anfang aus zwei bzw. einem Wort
        const cnt = {}; es.forEach((e) => { const p = fname(e).split(/\s+/).slice(0, k).join(" "); if (p.split(" ").length === k) cnt[p] = (cnt[p] || 0) + 1; });
        const top = Object.entries(cnt).sort((x, y) => y[1] - x[1])[0];
        if (top && top[1] >= 2 && top[1] >= es.length / 3) return top[0];
      }
      return cleanName(fname(es[0])) || es[0].entity_id;
    };
    c._off = [...dev.entries()].filter(([, es]) => es.every((e) => e.state === "unavailable")).map(([d, es]) => ({ d, id: es[0].entity_id,
      name: hass.devices?.[d]?.name_by_user || hass.devices?.[d]?.name || prefix(es) }));
    c._bats = listIds(hass, "sensor", (e) => e.attributes.device_class === "battery" && unitOf(e) === "%" && isLive(e) && parseFloat(e.state) <= 15 && !PHONE_RE.test(lcName(e)));
    return c;
  }
  _watch() { const c = this._config; return [...c._upd, c._backup, c._disk, ...c._bats].filter(Boolean); }
  _extraSheet(k) {
    const c = this._config, S = this._hass.states;
    if (k === "l:upd") return this._frame("Updates", `<div class="rows">${c._upd.map((id) => S[id]).filter((e) => e?.state === "on").map((e) => `<div class="row" data-more="${e.entity_id}" role="button" tabindex="0">
      <div class="rn">${esc(e.attributes.title || cleanName(fname(e)).replace(/\s*update$/i, ""))}<div class="rs">${esc(e.attributes.installed_version || "")} → ${esc(e.attributes.latest_version || "")}</div></div>
      <div class="rs">${e.attributes.in_progress ? "installiert …" : "Details"}</div></div>`).join("")}</div>`);
    if (k === "l:off") return this._frame("Geräte offline", `<div class="rows">${c._off.map((o) => `<div class="row" data-entity="${o.id}"><div class="rn">${esc(o.name)}
      <div class="rs">${esc(areaName(this._hass, o.id) || "")}</div></div><div class="rs">${S[o.id] ? ago(S[o.id].last_changed) : ""}</div></div>`).join("")}</div>`);
    if (k === "l:bat") return this._frame("Akkus fast leer", `<div class="rows">${c._bats.map((id) => S[id]).filter(Boolean).map((e) => `<div class="row" data-entity="${e.entity_id}">
      <div class="rn">${esc(cleanName(fname(e)))}</div><div class="rv">${fmtNum(parseFloat(e.state), "%")}</div></div>`).join("")}</div>`);
    return "";
  }
  _render() {
    if (!this._config || !this._hass) return;
    const c = this._config, H = this._hass, S = H.states, todo = [], items = [];
    const upd = c._upd.map((id) => S[id]).filter((e) => e?.state === "on" && !e.attributes.skipped_version);
    const core = upd.find((e) => /home_assistant_(core|operating_system|supervisor)/.test(e.entity_id));
    items.push(upd.length
      ? { k: "l:upd", icon: "mdi:package-up", lv: core ? "low" : "info", t: `${upd.length} ${upd.length === 1 ? "Update" : "Updates"} verfügbar`, s: upd.slice(0, 2).map((e) => e.attributes.title || cleanName(fname(e)).replace(/\s*update$/i, "")).join(", ") + (upd.length > 2 ? " …" : "") }
      : c._upd.length ? { icon: "mdi:package-check", lv: "ok", t: "Alles aktuell", s: `${c._upd.length} Komponenten geprüft` } : null);
    const b = S[c._backup];
    if (c._backup) {
      const t = toMs(b?.state), age = (Date.now() - t) / 864e5;
      items.push(!isFinite(t) ? { e: c._backup, icon: "mdi:backup-restore", lv: "low", t: "Noch kein Backup", s: "Richte unter Einstellungen › System › Backups eins ein" }
        : { e: c._backup, icon: "mdi:backup-restore", lv: age > c.backup_days * 2 ? "crit" : age > c.backup_days ? "low" : "ok", t: `Backup ${ago(b.state).replace(/^seit /, "vor ")}`,
          s: new Date(t).toLocaleString("de-DE", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) });
    }
    if (c._off.length) items.push({ k: "l:off", icon: "mdi:lan-disconnect", lv: "low", t: `${c._off.length} ${c._off.length === 1 ? "Gerät" : "Geräte"} offline`, s: c._off.slice(0, 3).map((o) => o.name).join(", ") + (c._off.length > 3 ? " …" : "") });
    const dk = S[c._disk], dv = parseFloat(dk?.state);
    if (c._disk && isFinite(dv)) items.push({ e: c._disk, icon: "mdi:harddisk", lv: dv >= Math.max(c.disk_warn + 10, 95) ? "crit" : dv >= c.disk_warn ? "low" : "ok", t: `Speicher ${fmt(dv)} % belegt`, s: dv >= c.disk_warn ? "Alte Backups oder Aufzeichnungen löschen" : "genug Platz" });
    if (c._bats.length) items.push({ k: "l:bat", icon: "mdi:battery-alert-variant-outline", lv: "low", t: `${c._bats.length} ${c._bats.length === 1 ? "Akku" : "Akkus"} fast leer`, s: "unter 15 %" });
    const list = items.filter(Boolean);
    if (!list.length) return this._setup(c.name, "Ich finde weder Updates noch Backups oder Systemsensoren.");
    const bad = list.filter((x) => x.lv !== "ok"), worst = bad.some((x) => x.lv === "crit") ? "crit" : bad.some((x) => x.lv === "low") ? "low" : bad.length ? "info" : "ok";
    const state = { key: worst, label: bad.length ? (worst === "crit" ? "Dringend" : "Bitte ansehen") : "Alles in Ordnung", icon: bad.length ? "mdi:clipboard-list-outline" : "mdi:check-circle" };
    const rows = list.map((x) => `<div class="row"${x.k ? ` data-sheet="${x.k}"` : x.e ? ` data-entity="${x.e}"` : ""}><span class="dot d-${x.lv === "info" ? "low" : x.lv}"></span>
      <ha-icon class="ri" icon="${x.icon}"></ha-icon><div class="rn">${esc(x.t)}<div class="rs">${esc(x.s)}</div></div>${x.k || x.e ? `<ha-icon class="ri chev" icon="mdi:chevron-right"></ha-icon>` : ""}</div>`).join("");
    this._shell(state.key, `
      ${this._head(c.name, state)}
      <div class="big" style="margin-top:6px">${bad.length ? `<span class="num">${bad.length}</span><span class="u">${bad.length === 1 ? "Punkt" : "Punkte"} zu erledigen</span>` : `<span class="num w">Alles in Ordnung</span>`}</div>
      <div class="rows">${rows}</div>`, `
      .num.w { font-size: 28px; } .row:not([data-sheet]):not([data-entity]) { cursor: default; } .ri.chev { opacity: .5; --mdc-icon-size: 18px; }
      .dot.d-info { background: var(--info-color, #2a78d6); }`);
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
  ["mangfall-energy-history-card", MangfallEnergyHistoryCard, "MANGFALL · Energieverlauf", "Verbrauch, Solar und Speicher als Säulen für Tag, Woche, Monat und Jahr, mit Vergleich."],
  ["mangfall-waste-card", MangfallWasteCard, "MANGFALL · Müllabfuhr", "Welche Tonne als Nächstes dran ist, in Tonnenfarben, mit Erinnerung am Vorabend."],
  ["mangfall-weather-card", MangfallWeatherCard, "MANGFALL · Wetter", "Jetzt-Wert, feine Linie für die nächsten Stunden und ein Satz wie „Ab 15 Uhr Regen“."],
  ["mangfall-presence-card", MangfallPresenceCard, "MANGFALL · Anwesenheit", "Wer zu Hause ist, mit Bild oder Initialen und seit wann."],
  ["mangfall-lights-card", MangfallLightsCard, "MANGFALL · Lichter", "Alle Lampen nach Räumen, Antippen schaltet, dazu „Alle aus“."],
  ["mangfall-vacuum-card", MangfallVacuumCard, "MANGFALL · Saugroboter", "Status, Akku und Steuerung für jeden Saugroboter."],
  ["mangfall-price-card", MangfallPriceCard, "MANGFALL · Strompreis", "Dynamischer Preis jetzt, Balken für den Tag und die günstigsten Stunden."],
  ["mangfall-ev-card", MangfallEvCard, "MANGFALL · E-Auto", "Ladestand, Reichweite, Ladeleistung und wann das Auto voll ist."],
  ["mangfall-heating-card", MangfallHeatingCard, "MANGFALL · Heizung", "Ist- und Soll-Temperatur je Raum, mit Plus und Minus direkt in der Karte."],
  ["mangfall-home-card", MangfallHomeCard, "MANGFALL · Haus auf einen Blick", "Begrüßung, Uhrzeit und kluge Hinweise wie „2 Fenster offen“ oder „Morgen Gelber Sack“."],
  ["mangfall-cover-card", MangfallCoverCard, "MANGFALL · Rollläden", "Alle Rollläden und Jalousien nach Räumen, Auf, Stopp, Ab und „Alle zu“."],
  ["mangfall-security-card", MangfallSecurityCard, "MANGFALL · Sicherheit", "Alarmanlage, Schlösser, Rauch-, CO-, Gas- und Wassermelder: ruhig „Alles sicher“ oder ein klarer Alarm."],
  ["mangfall-camera-card", MangfallCameraCard, "MANGFALL · Kameras", "Standbilder aller Kameras als Raster, die sich selbst erneuern, Antippen vergrößert."],
  ["mangfall-media-card", MangfallMediaCard, "MANGFALL · Musik & Medien", "Was gerade läuft, mit Cover, Steuerung und Lautstärke, der aktive Player zuerst."],
  ["mangfall-actions-card", MangfallActionsCard, "MANGFALL · Schnellaktionen", "Szenen, Skripte und Knöpfe als große Kacheln mit kurzer Bestätigung."],
  ["mangfall-consumers-card", MangfallConsumersCard, "MANGFALL · Verbraucher", "Wer gerade Strom braucht, sortiert, und wann die Waschmaschine fertig ist."],
  ["mangfall-air-card", MangfallAirCard, "MANGFALL · Luftqualität", "CO₂, Feinstaub und VOC je Raum mit klarer Lüftungsempfehlung."],
  ["mangfall-calendar-card", MangfallCalendarCard, "MANGFALL · Termine", "Die nächsten Termine aus allen Kalendern, Heute und Morgen klar getrennt."],
  ["mangfall-system-card", MangfallSystemCard, "MANGFALL · Haus-Check", "Updates, Backup, Geräte offline und Speicherplatz – „Alles in Ordnung“ oder eine kurze Liste."],
  ["mangfall-flat-card", MangfallFlatCard, "MANGFALL · Wasserwaage (FLAT)", "Wohnwagen oder Wohnmobil gerade stellen: welches Rad wie viele Zentimeter Keil braucht."],
];
if (!customElements.get("mangfall-level-card-editor")) customElements.define("mangfall-level-card-editor", MangfallLevelEditor);
window.customCards = window.customCards || [];
for (const [tag, cls, name, description] of CARDS) {
  if (!customElements.get(tag)) customElements.define(tag, cls);
  window.customCards.push({ type: tag, name, description, preview: true, documentationURL: "https://mangfall.eu" });
}
console.info(`%c MANGFALL CARDS %c ${MC_VERSION} `, "background:#184f95;color:#fff;font-weight:700", "background:#e8eefa;color:#184f95");
