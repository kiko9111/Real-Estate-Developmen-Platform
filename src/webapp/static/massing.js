/* Plan view, 3D massing view and boundary outline for the site layout pages.
   No dependencies. Geometry is in metres; the frontage lies along y = 0.

   Massing.plan(svg, geo)        2D plan as SVG
   Massing.view3d(canvas, geo)   rotatable 3D massing on a canvas
   Massing.legend(el, geo)       colour key
   Massing.outline(svg, pts, e)  boundary outline with numbered edges
*/
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const PALETTE = ["#d9822b", "#e7b04a", "#c45a3b", "#4f8fd6", "#2f5f9e", "#7c5cc4", "#c23b4a", "#2a9d8f",
                   "#8d6e63", "#5c7c3a", "#b5838d", "#457b9d"];
  const C = {site: "#f3efe4", road: "#b9bec4", green: "#cfe6c3", park: "#a9d39a", services: "#d7c9ec",
             plot: "#f7f4ec", edge: "#6b7480", sea: "#9cc9e8"};

  function colorOf(i) { return PALETTE[i % PALETTE.length]; }

  function svgEl(tag, attrs) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }
  function pts(ring) { return ring.map((p) => p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" "); }

  function plan(svg, geo) {
    const [x0, y0, x1, y1] = geo.bounds;
    const pad = Math.max(x1 - x0, y1 - y0) * 0.04;
    const W = x1 - x0 + 2 * pad, H = y1 - y0 + 2 * pad + pad * 2;
    svg.setAttribute("viewBox", `${x0 - pad} ${-(y1 + pad)} ${W} ${H}`);
    svg.innerHTML = "";
    const g = svgEl("g", {transform: "scale(1,-1)"});
    svg.appendChild(g);
    // the water or road in front of the frontage
    g.appendChild(svgEl("rect", {x: x0 - pad, y: -pad * 3, width: W, height: pad * 3, fill: C.sea}));
    g.appendChild(svgEl("polygon", {points: pts(geo.site), fill: C.green, stroke: C.edge, "stroke-width": 2}));
    for (const r of geo.roads) g.appendChild(svgEl("polygon", {points: pts(r), fill: C.road}));
    for (const b of geo.blocks) {
      const fill = b.use === "park" ? C.park : b.use === "services" ? C.services : C.plot;
      const poly = svgEl("polygon", {points: pts(b.ring), fill: fill, stroke: "#d8d2c2", "stroke-width": 0.6});
      const t = b.typ !== null && b.typ !== undefined ? geo.typologies[b.typ] : null;
      const title = svgEl("title", {});
      title.textContent = b.use === "park" ? "Park" : b.use === "services" ? "Services: " + (t ? t.name : "") : (t ? t.name : "");
      poly.appendChild(title);
      g.appendChild(poly);
      if (b.typ === null || b.typ === undefined) continue;
      const col = colorOf(b.typ);
      for (const q of b.b) g.appendChild(svgEl("rect", {x: q[0], y: q[1], width: q[2], height: q[3], fill: col,
                                                         stroke: "rgba(0,0,0,.35)", "stroke-width": 0.4}));
    }
    for (const band of geo.bands || []) {
      if (!band.from) continue;
      g.appendChild(svgEl("line", {x1: x0, x2: x1, y1: band.from, y2: band.from, stroke: "#c0392b",
                                   "stroke-width": 1.5, "stroke-dasharray": "8 6"}));
    }
    const fs = Math.max(W, H) / 45;
    const lab = svgEl("text", {x: (x0 + x1) / 2, y: pad * 2, "font-size": fs, "text-anchor": "middle", fill: "#1d4e6e"});
    lab.textContent = "Frontage";
    svg.appendChild(lab);
    for (const band of geo.bands || []) {
      const t = svgEl("text", {x: x0 + fs * 0.4, y: -(band.from || 0) - fs * 0.5, "font-size": fs * 0.8, fill: "#c0392b"});
      t.textContent = band.name;
      svg.appendChild(t);
    }
  }

  function legend(el, geo) {
    const used = new Set(geo.blocks.filter((b) => b.typ !== null && b.typ !== undefined && b.b.length).map((b) => b.typ));
    const items = [...used].sort((a, b) => a - b).map((i) => [colorOf(i), geo.typologies[i].name]);
    items.push([C.park, "Park"], [C.services, "Services plot"], [C.road, "Road"], [C.green, "Setback / landscape"]);
    el.innerHTML = items.map(([c, n]) => `<span><i style="background:${c}"></i>${n}</span>`).join("");
  }

  function shade(hex, k) {
    const n = parseInt(hex.slice(1), 16);
    const r = Math.min(255, Math.round(((n >> 16) & 255) * k));
    const g = Math.min(255, Math.round(((n >> 8) & 255) * k));
    const b = Math.min(255, Math.round((n & 255) * k));
    return `rgb(${r},${g},${b})`;
  }

  function view3d(canvas, geo) {
    const [x0, y0, x1, y1] = geo.bounds;
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const span = Math.max(x1 - x0, y1 - y0);
    let yaw = -0.6, pitch = 0.75, zoom = 1, panX = 0, panY = 0;
    const boxes = [];
    for (const b of geo.blocks) {
      if (b.typ === null || b.typ === undefined) continue;
      const col = colorOf(b.typ);
      for (const q of b.b) boxes.push({x: q[0], y: q[1], w: q[2], d: q[3], h: q[5], col: col});
    }
    const ctx = canvas.getContext("2d");
    function size() {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth || 900, h = canvas.clientHeight || 560;
      canvas.width = w * dpr; canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return [w, h];
    }
    let [Wc, Hc] = size();
    function proj(x, y, z) {
      const dx = x - cx, dy = y - cy;
      const rx = dx * Math.cos(yaw) - dy * Math.sin(yaw);
      const ry = dx * Math.sin(yaw) + dy * Math.cos(yaw);
      const s = Math.min(Wc, Hc) / span * 0.9 * zoom;
      return [Wc / 2 + panX + rx * s, Hc / 2 + panY - (ry * Math.sin(pitch) + z * Math.cos(pitch)) * s,
              ry * Math.cos(pitch) - z * Math.sin(pitch)];
    }
    function poly(ring, z, fill, stroke) {
      ctx.beginPath();
      ring.forEach((p, i) => { const q = proj(p[0], p[1], z); i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); });
      ctx.closePath();
      ctx.fillStyle = fill; ctx.fill();
      if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 0.5; ctx.stroke(); }
    }
    function draw() {
      ctx.clearRect(0, 0, Wc, Hc);
      ctx.fillStyle = "#eef2f4"; ctx.fillRect(0, 0, Wc, Hc);
      poly([[x0 - span * 0.1, -span * 0.12], [x1 + span * 0.1, -span * 0.12], [x1 + span * 0.1, 0], [x0 - span * 0.1, 0]], 0, C.sea);
      poly(geo.site, 0, C.green, C.edge);
      for (const r of geo.roads) poly(r, 0, C.road);
      for (const b of geo.blocks) poly(b.ring, 0, b.use === "park" ? C.park : b.use === "services" ? C.services : C.plot);
      const sorted = boxes.map((b) => [proj(b.x + b.w / 2, b.y + b.d / 2, b.h / 2)[2], b]).sort((a, b) => b[0] - a[0]);
      for (const [, b] of sorted) {
        const c = [[b.x, b.y], [b.x + b.w, b.y], [b.x + b.w, b.y + b.d], [b.x, b.y + b.d]];
        // side faces whose outward normal points towards the viewer
        const normals = [[0, -1], [1, 0], [0, 1], [-1, 0]];
        for (let i = 0; i < 4; i++) {
          const n = normals[i];
          const ny = n[0] * Math.sin(yaw) + n[1] * Math.cos(yaw);
          if (ny >= 0) continue;
          const a = c[i], d = c[(i + 1) % 4];
          const p = [proj(a[0], a[1], 0), proj(d[0], d[1], 0), proj(d[0], d[1], b.h), proj(a[0], a[1], b.h)];
          ctx.beginPath(); ctx.moveTo(p[0][0], p[0][1]);
          for (let k = 1; k < 4; k++) ctx.lineTo(p[k][0], p[k][1]);
          ctx.closePath();
          const nx = n[0] * Math.cos(yaw) - n[1] * Math.sin(yaw);
          ctx.fillStyle = shade(b.col, 0.62 + 0.18 * nx);
          ctx.fill();
          ctx.strokeStyle = "rgba(0,0,0,.25)"; ctx.lineWidth = 0.4; ctx.stroke();
        }
        poly(c, b.h, shade(b.col, 1.08), "rgba(0,0,0,.3)");
      }
    }
    let drag = null;
    canvas.addEventListener("mousedown", (e) => { drag = [e.clientX, e.clientY, e.shiftKey || e.button === 2]; });
    canvas.addEventListener("contextmenu", (e) => e.preventDefault());
    window.addEventListener("mouseup", () => { drag = null; });
    window.addEventListener("mousemove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag[0], dy = e.clientY - drag[1];
      if (drag[2]) { panX += dx; panY += dy; }
      else { yaw += dx * 0.008; pitch = Math.max(0.15, Math.min(Math.PI / 2, pitch + dy * 0.006)); }
      drag[0] = e.clientX; drag[1] = e.clientY;
      draw();
    });
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      zoom = Math.max(0.4, Math.min(12, zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
      draw();
    }, {passive: false});
    let touch = null;
    canvas.addEventListener("touchstart", (e) => { if (e.touches.length === 1) touch = [e.touches[0].clientX, e.touches[0].clientY]; });
    canvas.addEventListener("touchmove", (e) => {
      if (!touch || e.touches.length !== 1) return;
      e.preventDefault();
      yaw += (e.touches[0].clientX - touch[0]) * 0.008;
      pitch = Math.max(0.15, Math.min(Math.PI / 2, pitch + (e.touches[0].clientY - touch[1]) * 0.006));
      touch = [e.touches[0].clientX, e.touches[0].clientY];
      draw();
    }, {passive: false});
    window.addEventListener("resize", () => { [Wc, Hc] = size(); draw(); });
    draw();
    return {draw: draw};
  }

  function outline(svg, points, frontage) {
    if (!svg || !points) return;
    const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const span = Math.max(x1 - x0, y1 - y0), pad = span * 0.12;
    svg.setAttribute("viewBox", `${x0 - pad} ${-(y1 + pad)} ${x1 - x0 + 2 * pad} ${y1 - y0 + 2 * pad}`);
    svg.innerHTML = "";
    const ring = points.map((p) => [p[0], -p[1]]);
    svg.appendChild(svgEl("polygon", {points: pts(ring), fill: C.site, stroke: C.edge, "stroke-width": span / 250}));
    const fs = span / 18;
    for (let i = 0; i < ring.length; i++) {
      const a = ring[i], b = ring[(i + 1) % ring.length];
      if (i === frontage) svg.appendChild(svgEl("line", {x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: "#1f78b4", "stroke-width": span / 60}));
      const t = svgEl("text", {x: (a[0] + b[0]) / 2, y: (a[1] + b[1]) / 2, "font-size": fs, "text-anchor": "middle",
                              fill: i === frontage ? "#1f78b4" : "#444", "font-weight": "600"});
      t.textContent = String(i + 1);
      svg.appendChild(t);
    }
  }

  function lonlatArea(latlngs) {
    const R = 6371008.8;
    const lat0 = latlngs.reduce((s, p) => s + p.lat, 0) / latlngs.length * Math.PI / 180;
    const xy = latlngs.map((p) => [p.lng * Math.PI / 180 * R * Math.cos(lat0), p.lat * Math.PI / 180 * R]);
    let a = 0;
    for (let i = 0; i < xy.length; i++) { const j = (i + 1) % xy.length; a += xy[i][0] * xy[j][1] - xy[j][0] * xy[i][1]; }
    return Math.abs(a) / 2;
  }

  window.Massing = {plan: plan, view3d: view3d, legend: legend, outline: outline, lonlatArea: lonlatArea};
})();
