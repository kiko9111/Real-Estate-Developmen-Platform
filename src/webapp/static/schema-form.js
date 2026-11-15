/* Builds an editable form from a JSON schema (as pydantic writes it) and
   reads the edited value back as JSON. No dependencies.

   SchemaForm.mount(container, schema, value) -> { get() }
*/
(function () {
  "use strict";

  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children || []) if (c) e.appendChild(c);
    return e;
  }

  function pretty(name) {
    const s = String(name).replace(/_/g, " ").replace(/\bm2\b/g, "m²").replace(/\bpct\b/g, "%");
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function makeResolver(root) {
    const defs = root.$defs || root.definitions || {};
    function resolve(s) {
      if (!s) return {};
      if (s.$ref) {
        const name = s.$ref.split("/").pop();
        const merged = Object.assign({}, defs[name] || {}, s);
        delete merged.$ref;
        return resolve(merged);
      }
      if (s.allOf && s.allOf.length === 1) {
        const merged = Object.assign({}, resolve(s.allOf[0]), s);
        delete merged.allOf;
        return merged;
      }
      if (s.anyOf) {
        const nonNull = s.anyOf.filter((x) => x.type !== "null");
        if (nonNull.length === 1 && nonNull.length < s.anyOf.length) {
          const base = resolve(nonNull[0]);
          return Object.assign({}, base, {
            nullable: true,
            title: s.title || base.title,
            description: s.description || base.description,
            default: "default" in s ? s.default : base.default,
          });
        }
        if (nonNull.length >= 1) return Object.assign({}, resolve(nonNull[0]), { title: s.title, description: s.description });
      }
      if (Array.isArray(s.type)) {
        const t = s.type.filter((x) => x !== "null");
        return Object.assign({}, s, { type: t[0], nullable: s.type.includes("null") });
      }
      if (s.const !== undefined && !s.enum) return Object.assign({}, s, { enum: [s.const] });
      return s;
    }
    return resolve;
  }

  function SchemaFormImpl(root) {
    const resolve = makeResolver(root);

    function defaultFor(s) {
      s = resolve(s);
      if ("default" in s) return JSON.parse(JSON.stringify(s.default));
      if (s.nullable) return null;
      if (s.type === "object") {
        if (s.properties) {
          const o = {};
          for (const [k, p] of Object.entries(s.properties)) {
            if ((s.required || []).includes(k)) o[k] = defaultFor(p);
          }
          return o;
        }
        return {};
      }
      if (s.type === "array") return [];
      if (s.enum) return s.enum[0];
      if (s.type === "boolean") return false;
      if (s.type === "string") return "";
      return undefined;
    }

    function wrap(label, help, control, required, inline) {
      const lab = el("label", { class: "sf-label" }, [
        el("span", { text: label + (required ? " *" : "") }),
      ]);
      const row = el("div", { class: "sf-field" + (inline ? " sf-inline" : "") }, [lab, control]);
      if (help) row.appendChild(el("div", { class: "sf-help", text: help }));
      return row;
    }

    function build(schema, value, name, required, depth) {
      const s = resolve(schema);
      const label = name ? pretty(name) : (s.title || "");
      const help = s.description || "";
      if (value === undefined) value = defaultFor(s);

      // enum -> select
      if (s.enum) {
        const sel = el("select", {});
        if (s.nullable) sel.appendChild(el("option", { value: "", text: "(blank)" }));
        for (const v of s.enum) sel.appendChild(el("option", { value: JSON.stringify(v), text: v === "" ? "(none)" : String(v).replace(/_/g, " ") }));
        sel.value = value === null || value === undefined ? "" : JSON.stringify(value);
        return { el: wrap(label, help, sel, required), get: () => (sel.value === "" ? null : JSON.parse(sel.value)) };
      }
      if (s.type === "boolean") {
        const cb = el("input", { type: "checkbox" });
        cb.checked = !!value;
        return { el: wrap(label, help, cb, false, true), get: () => cb.checked };
      }
      if (s.type === "number" || s.type === "integer") {
        const inp = el("input", { type: "number", step: s.type === "integer" ? "1" : "any", inputmode: "decimal" });
        if (value !== null && value !== undefined) inp.value = value;
        if (s.minimum !== undefined) inp.min = s.minimum;
        if (s.maximum !== undefined) inp.max = s.maximum;
        return {
          el: wrap(label, help, inp, required),
          get: () => {
            if (inp.value.trim() === "") return s.nullable ? null : undefined;
            const n = Number(inp.value);
            return Number.isFinite(n) ? n : inp.value;
          },
        };
      }
      if (s.type === "string") {
        const long = /notes|description|terms|summary/.test(name || "");
        const inp = long ? el("textarea", { rows: 2 }) : el("input", { type: "text" });
        inp.value = value === null || value === undefined ? "" : value;
        return {
          el: wrap(label, help, inp, required),
          get: () => (inp.value === "" && s.nullable ? null : inp.value),
        };
      }
      if (s.type === "array") return buildArray(s, value || [], label, help, depth);
      if (s.type === "object" && s.properties) return buildObject(s, value, label, help, depth, name);
      if (s.type === "object") return buildDict(s, value || {}, label, help);
      // unknown: raw JSON
      const ta = el("textarea", { rows: 2 });
      ta.value = JSON.stringify(value);
      return { el: wrap(label, help, ta, required), get: () => JSON.parse(ta.value || "null") };
    }

    function buildObject(s, value, label, help, depth, name) {
      const body = el("div", { class: "sf-body" });
      const children = [];
      let on = !(s.nullable && (value === null || value === undefined));
      const props = s.properties;
      function fill(v) {
        body.innerHTML = "";
        children.length = 0;
        for (const [k, p] of Object.entries(props)) {
          const c = build(p, v ? v[k] : undefined, k, (s.required || []).includes(k), depth + 1);
          children.push([k, c]);
          body.appendChild(c.el);
        }
      }
      if (on) fill(value || defaultFor(Object.assign({}, s, { nullable: false })));
      const box = el(depth === 0 ? "div" : "details", { class: "sf-object depth-" + Math.min(depth, 3) });
      if (depth > 0) {
        if (depth <= 1) box.open = true;
        const sum = el("summary", {}, [el("span", { class: "sf-title", text: label })]);
        box.appendChild(sum);
        if (help) box.appendChild(el("div", { class: "sf-help", text: help }));
      }
      if (s.nullable) {
        const cb = el("input", { type: "checkbox" });
        cb.checked = on;
        cb.addEventListener("change", () => {
          on = cb.checked;
          if (on && !children.length) fill(defaultFor(Object.assign({}, s, { nullable: false })));
          body.hidden = !on;
        });
        box.appendChild(el("label", { class: "sf-toggle" }, [cb, el("span", { text: " Include " + label.toLowerCase() })]));
        body.hidden = !on;
      }
      box.appendChild(body);
      return {
        el: box,
        get: () => {
          if (!on) return null;
          const o = {};
          for (const [k, c] of children) {
            const v = c.get();
            if (v !== undefined) o[k] = v;
          }
          return o;
        },
      };
    }

    function buildArray(s, value, label, help, depth) {
      const items = s.items || {};
      const list = el("div", { class: "sf-list" });
      const rows = [];
      const itemSchema = resolve(items);
      const simple = !(itemSchema.type === "object");
      function add(v) {
        const c = build(items, v, simple ? "" : "item " + (rows.length + 1), false, depth + 1);
        const rm = el("button", { type: "button", class: "sf-remove", title: "Remove", text: "Remove" });
        const row = el("div", { class: "sf-item" + (simple ? " sf-simple" : "") }, [c.el, rm]);
        const entry = { c, row };
        rm.addEventListener("click", () => {
          rows.splice(rows.indexOf(entry), 1);
          row.remove();
        });
        rows.push(entry);
        list.appendChild(row);
      }
      for (const v of value) add(v);
      const addBtn = el("button", { type: "button", class: "sf-add", text: "+ Add" });
      addBtn.addEventListener("click", () => add(undefined));
      const box = el("details", { class: "sf-array depth-" + Math.min(depth, 3) }, [
        el("summary", {}, [el("span", { class: "sf-title", text: label }), el("span", { class: "sf-count", text: "" })]),
      ]);
      box.open = depth <= 1 || value.length > 0;
      if (help) box.appendChild(el("div", { class: "sf-help", text: help }));
      box.appendChild(list);
      box.appendChild(addBtn);
      return { el: box, get: () => rows.map((r) => r.c.get()).filter((v) => v !== undefined) };
    }

    function buildDict(s, value, label, help) {
      const vs = resolve(s.additionalProperties || { type: "string" });
      const list = el("div", { class: "sf-list" });
      const rows = [];
      function add(k, v) {
        const key = el("input", { type: "text", placeholder: "name" });
        key.value = k || "";
        const val = el("input", { type: vs.type === "number" ? "number" : "text", step: "any", placeholder: "value" });
        if (v !== undefined && v !== null) val.value = v;
        const rm = el("button", { type: "button", class: "sf-remove", text: "Remove" });
        const row = el("div", { class: "sf-kv" }, [key, val, rm]);
        const entry = { key, val };
        rm.addEventListener("click", () => {
          rows.splice(rows.indexOf(entry), 1);
          row.remove();
        });
        rows.push(entry);
        list.appendChild(row);
      }
      for (const [k, v] of Object.entries(value)) add(k, v);
      const addBtn = el("button", { type: "button", class: "sf-add", text: "+ Add" });
      addBtn.addEventListener("click", () => add("", undefined));
      const box = el("div", { class: "sf-field sf-dict" }, [el("label", { class: "sf-label", text: label })]);
      if (help) box.appendChild(el("div", { class: "sf-help", text: help }));
      box.appendChild(list);
      box.appendChild(addBtn);
      return {
        el: box,
        get: () => {
          const o = {};
          for (const r of rows) {
            if (!r.key.value.trim()) continue;
            o[r.key.value.trim()] = vs.type === "number" ? Number(r.val.value) : r.val.value;
          }
          return o;
        },
      };
    }

    return { build };
  }

  function mount(container, schema, value) {
    container.innerHTML = "";
    const impl = SchemaFormImpl(schema);
    const root = impl.build(schema, value, "", true, 0);
    container.appendChild(root.el);
    return { get: root.get };
  }

  /* Wires a form with a visual editor, a raw JSON editor and a hidden field. */
  function attach(opts) {
    const form = document.getElementById(opts.form);
    const visual = document.getElementById(opts.visual);
    const raw = document.getElementById(opts.raw);
    const hidden = document.getElementById(opts.hidden);
    const schema = JSON.parse(document.getElementById(opts.schema).textContent);
    let value = JSON.parse(document.getElementById(opts.value).textContent);
    let mode = "visual";
    let handle = mount(visual, schema, value);
    const tabs = document.querySelectorAll("[data-sf-tab]");
    function show(m) {
      if (m === mode) return;
      try {
        if (m === "raw") {
          raw.value = JSON.stringify(handle.get(), null, 2);
        } else {
          value = JSON.parse(raw.value);
          handle = mount(visual, schema, value);
        }
      } catch (e) {
        alertBox("The JSON could not be read: " + e.message);
        return;
      }
      mode = m;
      visual.hidden = m !== "visual";
      raw.hidden = m !== "raw";
      tabs.forEach((t) => t.classList.toggle("active", t.dataset.sfTab === m));
    }
    function alertBox(msg) {
      const box = document.getElementById(opts.errors);
      if (box) {
        box.textContent = msg;
        box.hidden = false;
      }
    }
    tabs.forEach((t) => t.addEventListener("click", () => show(t.dataset.sfTab)));
    raw.hidden = true;
    form.addEventListener("submit", (ev) => {
      try {
        hidden.value = mode === "raw" ? JSON.stringify(JSON.parse(raw.value)) : JSON.stringify(handle.get());
      } catch (e) {
        ev.preventDefault();
        alertBox("The JSON could not be read: " + e.message);
      }
    });
  }

  window.SchemaForm = { mount, attach };
})();
