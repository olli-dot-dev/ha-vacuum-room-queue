/* Vacuum Room Queue card.
 *
 * Shows the room queue of one vacuum (the sensor of the Vacuum Room Queue
 * integration) and lets you reorder it by drag & drop, add and remove
 * rooms and start/pause/stop - also while the vacuum is cleaning. The card
 * holds no state of its own: every change is a service call, and the
 * sensor's attributes are the truth. Between a drop and the sensor update
 * the new order is shown optimistically.
 *
 * Drag & drop uses pointer events instead of the HTML5 drag API, which does
 * not work on touch screens (phones, wall tablets).
 */

const DOMAIN = "vacuum_room_queue";
const CARD_TYPE = "vacuum-room-queue-card";

// The card's strings live in card_translations/<lang>.json and come from
// the vacuum_room_queue/get_translations command, which falls back to
// English for unknown languages. Fetched once per language for all cards on
// the page.
const TRANSLATIONS = new Map();
function loadTranslations(hass, language) {
  if (!TRANSLATIONS.has(language)) {
    TRANSLATIONS.set(
      language,
      hass.callWS({ type: `${DOMAIN}/get_translations`, language }).catch((err) => {
        TRANSLATIONS.delete(language); // try again on the next update
        throw err;
      }),
    );
  }
  return TRANSLATIONS.get(language);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function sameList(a, b) {
  return a.length === b.length && a.every((item, i) => item === b[i]);
}

const STYLE = `
  :host { display: block; }
  ha-card { padding: 16px; }
  .header { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
  .header ha-icon { color: var(--state-icon-color, var(--primary-color)); }
  .title { flex: 1; min-width: 0; }
  .title .name { font-size: 1.2em; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .title .vacuum { color: var(--secondary-text-color); font-size: 0.9em; }
  .chip { border-radius: 999px; padding: 2px 10px; font-size: 0.85em; font-weight: 500;
          background: var(--secondary-background-color); color: var(--primary-text-color); white-space: nowrap; }
  .chip.running { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .chip.paused { background: var(--warning-color, #ffa600); color: #fff; }
  .chip.error { background: var(--error-color, #db4437); color: #fff; }
  .error-text { color: var(--error-color, #db4437); margin: 0 0 12px; }
  .label { color: var(--secondary-text-color); font-size: 0.8em; font-weight: 500;
           text-transform: uppercase; letter-spacing: 0.05em; margin: 12px 0 6px; }
  .current { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-radius: 12px;
             background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.12); }
  .current .room { flex: 1; font-weight: 500; }
  .current ha-icon { color: var(--primary-color); }
  ol { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  li { display: flex; align-items: center; gap: 4px; padding: 4px 4px 4px 0; border-radius: 12px;
       border: 1px solid var(--divider-color); background: var(--card-background-color);
       transition: box-shadow 120ms, transform 120ms; }
  li.dragging { box-shadow: 0 6px 16px rgba(0,0,0,0.25); transform: scale(1.02);
                border-color: var(--primary-color); position: relative; z-index: 1; }
  li .pos { width: 1.6em; text-align: center; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
  li .room { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  button { font: inherit; color: inherit; background: none; border: none; cursor: pointer; }
  button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .icon-btn { display: inline-flex; align-items: center; justify-content: center; width: 40px; height: 40px;
              border-radius: 50%; color: var(--secondary-text-color); flex: none; }
  .icon-btn:hover { background: var(--secondary-background-color); }
  .handle { cursor: grab; touch-action: none; }
  li.dragging .handle { cursor: grabbing; }
  .empty, .hint { color: var(--secondary-text-color); font-size: 0.9em; padding: 8px 0; }
  .rooms { display: flex; flex-wrap: wrap; gap: 8px; }
  .add { display: inline-flex; align-items: center; gap: 4px; padding: 6px 12px 6px 8px; border-radius: 999px;
         border: 1px dashed var(--divider-color); }
  .add:hover { border-color: var(--primary-color); color: var(--primary-color); }
  .add ha-icon { --mdc-icon-size: 18px; }
  .add small { color: var(--secondary-text-color); font-size: 0.8em; margin-left: 4px; }
  .done { color: var(--secondary-text-color); font-size: 0.9em; display: flex; flex-wrap: wrap; gap: 4px 12px; }
  .done span::before { content: "✓ "; color: var(--success-color, #43a047); }
  .done time { opacity: 0.7; font-size: 0.9em; margin-left: 2px; }
  .done.previous { font-size: 0.85em; opacity: 0.8; }
  .label .when { text-transform: none; letter-spacing: normal; font-weight: normal; }
  .controls { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
  .btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; border-radius: 999px;
         background: var(--secondary-background-color); font-weight: 500; }
  .btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .btn[disabled] { opacity: 0.4; cursor: default; }
  .btn ha-icon { --mdc-icon-size: 20px; }
  .spacer { flex: 1; }
`;

class VacuumRoomQueueCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._signature = null;
    this._pending = null; // optimistic queue order after a drop
    this._pendingTimer = null;
    this._drag = null;
    this._onMove = this._onMove.bind(this);
    this._onUp = this._onUp.bind(this);
  }

  setConfig(config) {
    // Without an entity (e.g. the preview in the card picker before any
    // queue is set up) the card shows a hint instead of an error.
    if (!config) {
      throw new Error("Invalid configuration");
    }
    this._config = config;
    this._signature = null;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const language = hass.locale?.language || hass.language || "en";
    if (language !== this._language) {
      this._language = language;
      loadTranslations(hass, language).then(
        ({ strings }) => {
          if (this._language !== language) return;
          this._i18n = strings;
          this._i18nError = false;
          this._signature = null;
          this._render();
        },
        () => {
          this._i18nError = true;
          this._language = null;
          this._signature = null;
          this._render();
        },
      );
    }
    this._render();
  }

  getCardSize() {
    const attrs = this._stateObj()?.attributes;
    return 3 + (attrs?.queue?.length || 0);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  static getConfigForm() {
    return {
      schema: [
        {
          name: "entity",
          required: true,
          selector: { entity: { filter: { integration: DOMAIN, domain: "sensor" } } },
        },
        { name: "title", selector: { text: {} } },
      ],
    };
  }

  static getStubConfig(hass) {
    const entity = Object.values(hass.states).find(
      (s) =>
        s.entity_id.startsWith("sensor.") &&
        Array.isArray(s.attributes.rooms) &&
        "vacuum_entity_id" in s.attributes,
    );
    return { entity: entity ? entity.entity_id : "" };
  }

  // ------------------------------------------------------------------

  _doneList(rooms, name, withTime = true) {
    return rooms
      .map(
        (r) =>
          `<span>${name(r.area)}${withTime && r.finished ? ` <time>${escapeHtml(this._time(r.finished))}</time>` : ""}</span>`,
      )
      .join("");
  }

  _time(iso, withDate = false) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const lang = this._hass?.locale?.language || this._hass?.language || "en";
    const sameDay = date.toDateString() === new Date().toDateString();
    const opts = { hour: "2-digit", minute: "2-digit" };
    if (withDate && !sameDay) Object.assign(opts, { day: "numeric", month: "numeric" });
    return date.toLocaleString(lang, opts);
  }

  // "09:47–09:52", or with the date when it was not today.
  _period(run) {
    const times = run.rooms.map((r) => r.finished).filter(Boolean);
    const start = run.started || times[0];
    const end = times[times.length - 1];
    if (!start) return "";
    const from = this._time(start, true);
    return end ? `${from}–${this._time(end)}` : from;
  }

  // "vor 5 Min.", "gestern", "vor 3 Wochen" - in the user's language.
  _ago(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const lang = this._hass?.locale?.language || this._hass?.language || "en";
    const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto", style: "short" });
    const now = new Date();
    const minutes = Math.max(0, Math.round((now - date) / 60000));
    if (minutes < 1) return rtf.format(0, "second");
    if (minutes < 60) return rtf.format(-minutes, "minute");
    const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const days = Math.round((startOfDay(now) - startOfDay(date)) / 86400000);
    if (days === 0 || minutes < 12 * 60) return rtf.format(-Math.round(minutes / 60), "hour");
    if (days < 14) return rtf.format(-days, "day");
    if (days < 60) return rtf.format(-Math.round(days / 7), "week");
    return rtf.format(-Math.round(days / 30), "month");
  }

  _lastCleaned(last) {
    if (!last) return { text: this._t("never"), title: this._t("never") };
    const date = new Date(last);
    const lang = this._hass?.locale?.language || this._hass?.language || "en";
    const full = Number.isNaN(date.getTime())
      ? ""
      : date.toLocaleString(lang, { dateStyle: "medium", timeStyle: "short" });
    return { text: this._ago(last), title: this._t("last_cleaned").replace("{time}", full) };
  }

  _t(key) {
    return this._i18n?.[key] ?? key;
  }

  _stateObj() {
    return this._hass && this._config ? this._hass.states[this._config.entity] : undefined;
  }

  _queue(attrs) {
    return this._pending ?? attrs.queue ?? [];
  }

  _render() {
    if (!this._hass || !this._config || this._drag) return;
    if (!this._i18n) {
      // Only the moment before the strings arrive (or if they can't be
      // loaded at all - then without them, in English).
      this._signature = null;
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card>${
        this._i18nError ? '<div class="error-text">Could not load the card texts.</div>' : ""
      }</ha-card>`;
      return;
    }
    const stateObj = this._stateObj();
    const attrs = stateObj?.attributes || {};
    const vacuum = attrs.vacuum_entity_id ? this._hass.states[attrs.vacuum_entity_id] : undefined;

    if (this._pending && sameList(this._pending, attrs.queue || [])) {
      this._clearPending();
    }

    const signature = JSON.stringify([
      stateObj?.state,
      attrs.current_area,
      attrs.queue,
      attrs.done,
      attrs.history,
      attrs.rooms,
      attrs.rooms_hint,
      attrs.last_cleaned,
      Math.floor(Date.now() / 60000), // keeps "5 min ago" current
      attrs.error,
      vacuum?.state,
      vacuum?.attributes?.friendly_name,
      this._pending,
      this._config,
      this._i18n,
    ]);
    if (signature === this._signature) return;
    this._signature = signature;

    if (!this._config.entity) {
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div class="header">
        <ha-icon icon="mdi:robot-vacuum"></ha-icon><div class="title"><div class="name">Vacuum Room Queue</div></div>
      </div><div class="hint">${escapeHtml(this._t("choose_entity"))}</div></ha-card>`;
      return;
    }
    if (!stateObj) {
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div class="error-text">${escapeHtml(
        this._t("not_found"),
      )}: ${escapeHtml(this._config.entity)}</div></ha-card>`;
      return;
    }

    const status = stateObj.state;
    const rooms = attrs.rooms || [];
    const names = Object.fromEntries(rooms.map((r) => [r.id, r.name]));
    const name = (id) => escapeHtml(names[id] || this._hass.areas?.[id]?.name || id);
    // A room may be queued several times (and also be the current one).
    const queue = this._queue(attrs);
    const current = attrs.current_area;
    const available = rooms;
    // Every finished entry counts: with "clean again" the current room may
    // have been finished before in this run.
    const history = attrs.history || {};
    const done = history.current?.rooms || (attrs.done || []).map((area) => ({ area }));
    const previous = history.previous?.rooms?.length ? history.previous : null;
    const title = this._config.title || vacuum?.attributes?.friendly_name || stateObj.attributes.friendly_name;
    const vacuumState = vacuum
      ? this._hass.formatEntityState
        ? this._hass.formatEntityState(vacuum)
        : vacuum.state
      : "";
    const icon = (name, cls = "") => `<ha-icon class="${cls}" icon="mdi:${name}"></ha-icon>`;
    const running = status === "running";
    const paused = status === "paused";
    const error = status === "error";
    const startLabel = paused ? "resume" : error ? "retry" : "start";
    const canStart = !running && (current || queue.length > 0);

    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="header">
          ${icon("robot-vacuum")}
          <div class="title">
            <div class="name">${escapeHtml(title)}</div>
            <div class="vacuum">${escapeHtml(vacuumState)}</div>
          </div>
          <span class="chip ${escapeHtml(status)}">${escapeHtml(this._t(`status_${status}`))}</span>
        </div>
        ${attrs.error ? `<p class="error-text">${escapeHtml(this._t(`error_${attrs.error}`))}</p>` : ""}
        ${
          current
            ? `<div class="label">${escapeHtml(this._t("now"))}</div>
               <div class="current ${escapeHtml(status)}">
                 ${icon(running ? "robot-vacuum" : paused ? "pause" : "alert-circle-outline")}
                 <span class="room">${name(current)}</span>
                 <button class="icon-btn" data-action="repeat" data-area="${escapeHtml(current)}" data-index="-1" title="${escapeHtml(this._t("repeat"))}" aria-label="${escapeHtml(this._t("repeat"))}: ${name(current)}">${icon("repeat")}</button>
                 <button class="icon-btn" data-action="skip" title="${escapeHtml(this._t("skip"))}" aria-label="${escapeHtml(this._t("skip"))}">${icon("skip-next")}</button>
               </div>`
            : ""
        }
        <div class="label">${escapeHtml(this._t("next"))}</div>
        ${
          queue.length
            ? `<ol role="list">${queue
                .map(
                  (id, i) => `
                <li data-area="${escapeHtml(id)}">
                  <button class="icon-btn handle" data-handle title="${escapeHtml(this._t("drag"))}" aria-label="${escapeHtml(this._t("drag"))}: ${name(id)}">${icon("drag")}</button>
                  <span class="pos">${i + 1}</span>
                  <span class="room">${name(id)}</span>
                  <button class="icon-btn" data-action="repeat" data-area="${escapeHtml(id)}" data-index="${i}" title="${escapeHtml(this._t("repeat"))}" aria-label="${escapeHtml(this._t("repeat"))}: ${name(id)}">${icon("repeat")}</button>
                  <button class="icon-btn" data-action="remove" data-area="${escapeHtml(id)}" data-index="${i}" title="${escapeHtml(this._t("remove"))}" aria-label="${escapeHtml(this._t("remove"))}: ${name(id)}">${icon("close")}</button>
                </li>`,
                )
                .join("")}</ol>`
            : rooms.length || current
              ? `<div class="empty">${escapeHtml(this._t("empty"))}</div>`
              : ""
        }
        ${
          available.length
            ? `<div class="label">${escapeHtml(this._t("add"))}</div>
               <div class="rooms">${available
                 .map(
                   (r) => {
                     const last = this._lastCleaned(attrs.last_cleaned?.[r.id]);
                     return `<button class="add" data-action="add" data-area="${escapeHtml(r.id)}" title="${escapeHtml(last.title)}">${icon("plus")}${escapeHtml(r.name)}<small>${escapeHtml(last.text)}</small></button>`;
                   },
                 )
                 .join("")}</div>`
            : `<div class="hint">${escapeHtml(this._t(attrs.rooms_hint === "no_mapping" ? "no_mapping" : "no_rooms"))}</div>
               ${
                 attrs.rooms_hint === "no_mapping" && attrs.vacuum_entity_id
                   ? `<button class="btn" data-action="open-vacuum">${icon("robot-vacuum")}${escapeHtml(this._t("open_vacuum"))}</button>`
                   : ""
               }`
        }
        ${
          done.length
            ? `<div class="label">${escapeHtml(this._t("done"))}</div>
               <div class="done">${this._doneList(done, name)}</div>`
            : ""
        }
        ${
          previous
            ? `<div class="label">${escapeHtml(this._t("previous"))} <span class="when">· ${escapeHtml(this._period(previous))}</span></div>
               <div class="done previous">${this._doneList(previous.rooms, name, false)}</div>`
            : ""
        }
        <div class="controls">
          ${
            running
              ? `<button class="btn primary" data-action="pause">${icon("pause")}${escapeHtml(this._t("pause"))}</button>`
              : `<button class="btn primary" data-action="start" ${canStart ? "" : "disabled"}>${icon("play")}${escapeHtml(this._t(startLabel))}</button>`
          }
          <button class="btn" data-action="stop" ${status === "idle" ? "disabled" : ""}>${icon("home-import-outline")}${escapeHtml(this._t("stop"))}</button>
          <span class="spacer"></span>
          <button class="btn" data-action="clear" ${queue.length ? "" : "disabled"}>${icon("playlist-remove")}${escapeHtml(this._t("clear"))}</button>
        </div>
      </ha-card>`;

    this.shadowRoot.querySelectorAll("[data-action]").forEach((el) =>
      el.addEventListener("click", () =>
        this._action(el.dataset.action, el.dataset.area, Number(el.dataset.index)),
      ),
    );
    this.shadowRoot.querySelectorAll("[data-handle]").forEach((el) => {
      el.addEventListener("pointerdown", (ev) => this._onDown(ev, el.closest("li")));
      el.addEventListener("keydown", (ev) => this._onKey(ev, el.closest("li")));
    });
  }

  // ------------------------------------------------------------------
  // Services

  _call(service, data = {}) {
    return this._hass
      .callService(DOMAIN, service, { entity_id: this._config.entity, ...data })
      .catch((err) => {
        this._clearPending();
        this._signature = null;
        this._render();
        this.dispatchEvent(
          new CustomEvent("hass-notification", {
            detail: { message: err?.message || String(err) },
            bubbles: true,
            composed: true,
          }),
        );
      });
  }

  _action(action, area, index) {
    switch (action) {
      case "add":
        return this._call("add_area", { area_id: area });
      case "repeat":
        // Right after this entry; for the current room (-1) as next room.
        return this._call("add_area", { area_id: area, position: index + 1 });
      case "remove":
        // By position: the same room may be queued more than once.
        return this._call("remove_area", { area_id: area, position: index });
      case "start":
      case "pause":
      case "skip":
      case "stop":
      case "clear":
        return this._call(action);
      case "open-vacuum":
        // More-info dialog of the vacuum; its settings hold the area mapping.
        return this.dispatchEvent(
          new CustomEvent("hass-more-info", {
            detail: { entityId: this._stateObj()?.attributes?.vacuum_entity_id },
            bubbles: true,
            composed: true,
          }),
        );
    }
  }

  _commitOrder(order) {
    const attrs = this._stateObj()?.attributes || {};
    if (sameList(order, this._queue(attrs))) return;
    this._pending = order;
    clearTimeout(this._pendingTimer);
    // If the sensor never confirms (error, or someone else changed the
    // queue at the same time), fall back to what the sensor says.
    this._pendingTimer = setTimeout(() => {
      this._clearPending();
      this._signature = null;
      this._render();
    }, 5000);
    this._signature = null;
    this._render();
    this._call("set_queue", { areas: order });
  }

  _clearPending() {
    this._pending = null;
    clearTimeout(this._pendingTimer);
    this._pendingTimer = null;
  }

  // ------------------------------------------------------------------
  // Drag & drop

  _order() {
    return [...this.shadowRoot.querySelectorAll("ol > li")].map((li) => li.dataset.area);
  }

  _onDown(ev, li) {
    if (!li || (ev.pointerType === "mouse" && ev.button !== 0)) return;
    ev.preventDefault();
    this._drag = { li, pointerId: ev.pointerId, before: this._order() };
    li.classList.add("dragging");
    // Listening on window instead of using pointer capture: the rows are
    // moved around in the DOM while dragging.
    window.addEventListener("pointermove", this._onMove, { passive: false });
    window.addEventListener("pointerup", this._onUp);
    window.addEventListener("pointercancel", this._onUp);
  }

  _onMove(ev) {
    const drag = this._drag;
    if (!drag || ev.pointerId !== drag.pointerId) return;
    ev.preventDefault();
    const { li } = drag;
    const list = li.parentElement;
    const y = ev.clientY;
    const mid = (el) => {
      const r = el.getBoundingClientRect();
      return r.top + r.height / 2;
    };
    // Only the neighbours move, the dragged row stays in the DOM.
    let prev = li.previousElementSibling;
    while (prev && y < mid(prev)) {
      list.insertBefore(prev, li.nextElementSibling);
      prev = li.previousElementSibling;
    }
    let next = li.nextElementSibling;
    while (next && y > mid(next)) {
      list.insertBefore(next, li);
      next = li.nextElementSibling;
    }
    this._renumber();
  }

  _onUp(ev) {
    const drag = this._drag;
    if (!drag || ev.pointerId !== drag.pointerId) return;
    window.removeEventListener("pointermove", this._onMove);
    window.removeEventListener("pointerup", this._onUp);
    window.removeEventListener("pointercancel", this._onUp);
    drag.li.classList.remove("dragging");
    this._drag = null;
    const order = this._order();
    if (ev.type === "pointercancel") {
      this._signature = null;
      this._render();
      return;
    }
    this._commitOrder(order);
    if (sameList(order, drag.before)) {
      // Nothing changed, but updates may have been held back while dragging.
      this._render();
    }
  }

  _onKey(ev, li) {
    if (ev.key !== "ArrowUp" && ev.key !== "ArrowDown") return;
    ev.preventDefault();
    const order = this._order();
    const i = [...li.parentElement.children].indexOf(li);
    const j = ev.key === "ArrowUp" ? i - 1 : i + 1;
    if (j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    this._commitOrder(order);
    // Keep the focus on the moved room.
    this.shadowRoot.querySelectorAll("ol > li [data-handle]")[j]?.focus();
  }

  _renumber() {
    this.shadowRoot.querySelectorAll("ol > li .pos").forEach((el, i) => {
      el.textContent = String(i + 1);
    });
  }

  disconnectedCallback() {
    if (this._drag) {
      this._onUp({ pointerId: this._drag.pointerId, type: "pointercancel" });
    }
  }
}

// The integration loads this file early (extra module URL), possibly before
// the frontend installs its scoped custom element registry polyfill, which
// replaces window.customElements. A definition made before that ends up in
// the old registry and Lovelace reports "Configuration error" (same fix as in
// ha-mapflights). So define the card once the frontend itself is defined,
// always in the current registry. It is also loaded as Lovelace resource,
// hence the guards against a double load.
const register = () => {
  if (!window.customElements.get(CARD_TYPE)) {
    window.customElements.define(CARD_TYPE, VacuumRoomQueueCard);
  }
};
window.customElements.whenDefined("home-assistant").then(register);

// The card picker shows this before any hass object exists, so the language
// comes from the page (HA sets <html lang>).
const PICKER_DESCRIPTION = {
  en: "Drag & drop room order for your robot vacuum, changeable while it cleans.",
  de: "Raumreihenfolge für deinen Saugroboter per Drag & Drop – auch während er saugt änderbar.",
  nl: "Kamervolgorde voor je robotstofzuiger met slepen en neerzetten – ook tijdens het reinigen aan te passen.",
  fr: "Ordre des pièces pour votre robot aspirateur par glisser-déposer – modifiable même pendant le nettoyage.",
  es: "Orden de habitaciones para tu robot aspirador con arrastrar y soltar, modificable incluso mientras limpia.",
  pl: "Kolejność pomieszczeń dla robota sprzątającego metodą przeciągnij i upuść – można ją zmieniać także podczas sprzątania.",
  ru: "Порядок комнат для робота-пылесоса перетаскиванием – можно менять даже во время уборки.",
};
const pickerLang = (document.documentElement.lang || navigator.language || "en").split("-")[0];

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === CARD_TYPE)) {
  window.customCards.push({
    type: CARD_TYPE,
    name: "Vacuum Room Queue",
    description: PICKER_DESCRIPTION[pickerLang] || PICKER_DESCRIPTION.en,
    preview: true,
    documentationURL: "https://github.com/olli-dot-dev/ha-vacuum-room-queue",
  });
}
