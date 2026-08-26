/* =========================================================
   Canal Monitoring System - map.js
   Draws canals as straight lines, link canals as connected
   lines, and sensors as colored circles along those lines.

   Drag & drop editor:
   - Unassigned sensors sit together in a tray strip at the
     top of the map. Drag one onto a canal / link canal to
     install it there (sets main/link canal + position).
   - Installed sensors can be dragged to reposition along a
     line, move to a different line, or dragged back up into
     the tray to uninstall them.
   - Each link canal has two drag handles: the top handle
     (on the main canal) slides the whole link canal left/
     right along the main canal; the end handle stretches it
     up or down away from the main canal.
   ========================================================= */

const SVG_NS = "http://www.w3.org/2000/svg";
const svg = document.getElementById("canalMap");
const tooltip = document.getElementById("mapTooltip");

const TRAY_Y = 50;
const TRAY_ZONE_BOTTOM = 95;
const SNAP_THRESHOLD = 34;

let latestCanals = [];
let latestLinks = [];
let latestSensors = [];
let canalPositions = {};
let linkPositions = {};
let suppressReload = false; // true while a drag is in-flight so the 8s poll doesn't yank the element mid-drag

function el(tag, attrs = {}) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}

function pointAt(x1, y1, x2, y2, ratio) {
  return { x: x1 + (x2 - x1) * ratio, y: y1 + (y2 - y1) * ratio };
}

function toSvgPoint(evt) {
  const pt = svg.createSVGPoint();
  pt.x = evt.clientX;
  pt.y = evt.clientY;
  const ctm = svg.getScreenCTM();
  return pt.matrixTransform(ctm.inverse());
}

function closestPointOnSegment(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const lenSq = dx * dx + dy * dy || 1;
  let t = ((px - x1) * dx + (py - y1) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  const x = x1 + dx * t, y = y1 + dy * t;
  const dist = Math.hypot(px - x, py - y);
  return { t, x, y, dist };
}

async function loadMap() {
  if (suppressReload) return;
  let canals = [], links = [], sensors = [];
  try {
    [canals, links, sensors] = await Promise.all([
      API.get("/api/canals"),
      API.get("/api/link-canals"),
      API.get("/api/sensors"),
    ]);
  } catch (e) {
    showToast("Unable to load map data", "error");
    return;
  }
  latestCanals = canals;
  latestLinks = links;
  latestSensors = sensors;
  drawMap(canals, links, sensors);
}

function drawMap(canals, links, sensors) {
  svg.innerHTML = "";
  canalPositions = {};
  linkPositions = {};

  const width = 900, marginX = 80;
  const canalGap = 150;
  const canalStartY = 190;

  // ---- tray zone (unassigned sensors live here, all together) ----
  const trayBg = el("rect", {
    class: "tray-zone", x: 10, y: 12, width: width - 20, height: TRAY_ZONE_BOTTOM - 12, rx: 10,
  });
  svg.appendChild(trayBg);
  const trayLabel = el("text", { class: "tray-label", x: 24, y: 28 });
  trayLabel.textContent = "Sensor Tray — drag a sensor onto a canal to install it";
  svg.appendChild(trayLabel);

  // ---- main canals ----
  canals.forEach((c, i) => {
    const y = canalStartY + i * canalGap;
    const x1 = marginX, x2 = width - marginX;
    canalPositions[c.id] = { x1, y1: y, x2, y2: y };
    const line = el("line", { class: "canal-line", "data-line-type": "canal", "data-line-id": c.id, x1, y1: y, x2, y2: y });
    svg.appendChild(line);
    const label = el("text", { class: "canal-label", x: x1, y: y - 20 });
    label.textContent = c.name;
    svg.appendChild(label);
  });

  // ---- link canals (draggable) ----
  const linksByMain = {};
  links.forEach((l) => {
    (linksByMain[l.main_canal_id] = linksByMain[l.main_canal_id] || []).push(l);
  });

  links.forEach((l) => {
    const main = canalPositions[l.main_canal_id];
    // use stored coordinates if the link has already been dragged into place,
    // otherwise fall back to an auto-spread default near its main canal
    let x1 = l.x1, y1 = main ? main.y1 : l.y1, x2 = l.x2, y2 = l.y2;
    if (main) {
      // keep the branch point pinned to the main canal's y and within its span
      x1 = Math.max(main.x1 + 20, Math.min(main.x2 - 20, x1 ?? (main.x1 + main.x2) / 2));
      x2 = x1;
      y1 = main.y1;
      if (y2 === undefined || y2 === null || Math.abs(y2 - y1) < 40) {
        y2 = y1 + 90;
      }
    }
    linkPositions[l.id] = { x1, y1, x2, y2, main_canal_id: l.main_canal_id };

    const line = el("line", {
      class: "link-canal-line", "data-line-type": "link", "data-line-id": l.id,
      x1, y1, x2, y2,
    });
    svg.appendChild(line);
    const labelY = y2 < y1 ? y2 - 10 : y2 + 18;
    const label = el("text", { class: "link-label", x: x2 + 10, y: labelY });
    label.textContent = l.name;
    svg.appendChild(label);

    // handle 1: branch point on the main canal - drag left/right
    const branchHandle = el("circle", {
      class: "drag-handle branch-handle", cx: x1, cy: y1, r: 7,
      "data-link-id": l.id, "data-handle": "branch",
    });
    svg.appendChild(branchHandle);
    attachLinkHandleDrag(branchHandle, l.id, "branch");

    // handle 2: free end - drag up/down
    const endHandle = el("circle", {
      class: "drag-handle end-handle", cx: x2, cy: y2, r: 7,
      "data-link-id": l.id, "data-handle": "end",
    });
    svg.appendChild(endHandle);
    attachLinkHandleDrag(endHandle, l.id, "end");
  });

  // ---- sensors ----
  const unassigned = [];
  sensors.forEach((s) => {
    if (!s.main_canal_id) {
      unassigned.push(s);
      return;
    }
    let coords;
    if (s.link_canal_id && linkPositions[s.link_canal_id]) {
      const lp = linkPositions[s.link_canal_id];
      coords = pointAt(lp.x1, lp.y1, lp.x2, lp.y2, s.pos_ratio ?? 0.5);
    } else if (canalPositions[s.main_canal_id]) {
      const cp = canalPositions[s.main_canal_id];
      coords = pointAt(cp.x1, cp.y1, cp.x2, cp.y2, s.pos_ratio ?? 0.5);
    } else {
      unassigned.push(s);
      return;
    }
    const g = drawSensorNode(s, coords.x, coords.y);
    attachSensorDrag(g, s);
  });

  // place unassigned sensors together in the tray, side by side
  const traySpacing = Math.min(64, (width - 140) / Math.max(1, unassigned.length));
  unassigned.forEach((s, i) => {
    const x = 70 + i * traySpacing;
    const y = TRAY_Y;
    const g = drawSensorNode(s, x, y);
    attachSensorDrag(g, s);
  });

  if (canals.length === 0) {
    const msg = el("text", { x: 450, y: 300, "text-anchor": "middle", fill: "#9db6c4", "font-size": "14" });
    msg.textContent = "No canals added yet. Use the menu (☰) to add a canal.";
    svg.appendChild(msg);
  }
}

function drawSensorNode(s, x, y) {
  const g = el("g", {
    class: `sensor-node ${s.status}`, transform: `translate(${x}, ${y})`,
    "data-sensor-id": s.id,
  });
  g.appendChild(el("circle", { class: "pulse", r: 9, cx: 0, cy: 0 }));
  g.appendChild(el("circle", { class: "core", r: 9, cx: 0, cy: 0 }));
  const text = el("text", { x: 14, y: 4 });
  text.textContent = `${s.name} (${s.sensor_type})`;
  g.appendChild(text);

  g.addEventListener("mousemove", (e) => showTooltip(e, s));
  g.addEventListener("mouseleave", hideTooltip);
  svg.appendChild(g);
  return g;
}

/* ---------------- Sensor drag & drop (install / reposition / uninstall) ---------------- */
function attachSensorDrag(g, sensor) {
  let dragging = false;
  let startPt = null;

  g.style.cursor = "grab";

  g.addEventListener("pointerdown", (evt) => {
    evt.preventDefault();
    dragging = true;
    suppressReload = true;
    startPt = toSvgPoint(evt);
    g.setPointerCapture(evt.pointerId);
    g.classList.add("dragging");
    hideTooltip();
  });

  g.addEventListener("pointermove", (evt) => {
    if (!dragging) return;
    const p = toSvgPoint(evt);
    g.setAttribute("transform", `translate(${p.x}, ${p.y})`);
    if (sensor.main_canal_id) {
      highlightOwnLine(sensor);
    } else {
      highlightNearestLine(p.x, p.y);
    }
  });

  g.addEventListener("pointerup", async (evt) => {
    if (!dragging) return;
    dragging = false;
    g.classList.remove("dragging");
    const p = toSvgPoint(evt);
    clearLineHighlights();

    // dropped back in the tray zone -> uninstall
    if (p.y <= TRAY_ZONE_BOTTOM) {
      try {
        await API.put(`/api/sensors/${sensor.id}`, { clear_main_canal: true, clear_link_canal: true });
        showToast(`${sensor.name} moved back to the tray`);
      } catch (err) {
        showToast(err.error || "Failed to update sensor", "error");
      }
      suppressReload = false;
      loadMap();
      return;
    }

    const isInstalled = !!sensor.main_canal_id;
    let target;
    if (isInstalled) {
      // installed sensors can only be repositioned along the line they're
      // already on - they can't jump to a different canal by drag
      const ownLine = sensor.link_canal_id ? linkPositions[sensor.link_canal_id] : canalPositions[sensor.main_canal_id];
      if (ownLine) {
        const r = closestPointOnSegment(p.x, p.y, ownLine.x1, ownLine.y1, ownLine.x2, ownLine.y2);
        target = { t: r.t };
      }
    } else {
      // unassigned tray sensors can be installed onto any canal / link canal
      const nearest = findNearestLine(p.x, p.y);
      if (nearest && nearest.dist <= SNAP_THRESHOLD) target = nearest;
    }

    if (target) {
      const payload = { pos_ratio: Math.max(0.04, Math.min(0.96, target.t)) };
      if (!isInstalled) {
        if (target.type === "canal") {
          payload.main_canal_id = target.id;
          payload.clear_link_canal = true;
        } else {
          payload.link_canal_id = target.id;
          payload.main_canal_id = linkPositions[target.id]?.main_canal_id ?? sensor.main_canal_id;
        }
      }
      try {
        await API.put(`/api/sensors/${sensor.id}`, payload);
        showToast(isInstalled ? `${sensor.name} repositioned` : `${sensor.name} installed`);
      } catch (err) {
        showToast(err.error || "Failed to update sensor", "error");
      }
    } else if (!isInstalled) {
      showToast("Drop the sensor on a canal line to install it", "error");
    }
    suppressReload = false;
    loadMap();
  });

  g.addEventListener("pointercancel", () => {
    dragging = false;
    g.classList.remove("dragging");
    clearLineHighlights();
    suppressReload = false;
    loadMap();
  });
}

function findNearestLine(x, y) {
  let best = null;
  Object.entries(canalPositions).forEach(([id, c]) => {
    const r = closestPointOnSegment(x, y, c.x1, c.y1, c.x2, c.y2);
    if (!best || r.dist < best.dist) best = { ...r, type: "canal", id: Number(id) };
  });
  Object.entries(linkPositions).forEach(([id, l]) => {
    const r = closestPointOnSegment(x, y, l.x1, l.y1, l.x2, l.y2);
    if (!best || r.dist < best.dist) best = { ...r, type: "link", id: Number(id) };
  });
  return best;
}

function highlightNearestLine(x, y) {
  clearLineHighlights();
  if (y <= TRAY_ZONE_BOTTOM) {
    document.querySelector(".tray-zone")?.classList.add("tray-zone-active");
    return;
  }
  const nearest = findNearestLine(x, y);
  if (nearest && nearest.dist <= SNAP_THRESHOLD) {
    const sel = nearest.type === "canal"
      ? `[data-line-type="canal"][data-line-id="${nearest.id}"]`
      : `[data-line-type="link"][data-line-id="${nearest.id}"]`;
    svg.querySelector(sel)?.classList.add("line-highlight");
  }
}

function highlightOwnLine(sensor) {
  clearLineHighlights();
  const sel = sensor.link_canal_id
    ? `[data-line-type="link"][data-line-id="${sensor.link_canal_id}"]`
    : `[data-line-type="canal"][data-line-id="${sensor.main_canal_id}"]`;
  svg.querySelector(sel)?.classList.add("line-highlight");
}

function clearLineHighlights() {
  svg.querySelectorAll(".line-highlight").forEach((l) => l.classList.remove("line-highlight"));
  document.querySelector(".tray-zone-active")?.classList.remove("tray-zone-active");
}

/* ---------------- Link canal drag handles ---------------- */
function attachLinkHandleDrag(handle, linkId, handleType) {
  let dragging = false;

  handle.addEventListener("pointerdown", (evt) => {
    evt.preventDefault();
    dragging = true;
    suppressReload = true;
    handle.setPointerCapture(evt.pointerId);
    handle.classList.add("dragging");
  });

  handle.addEventListener("pointermove", (evt) => {
    if (!dragging) return;
    const p = toSvgPoint(evt);
    const lp = linkPositions[linkId];
    const line = svg.querySelector(`[data-line-type="link"][data-line-id="${linkId}"]`);
    const label = line?.nextSibling;

    if (handleType === "branch") {
      const main = canalPositions[lp.main_canal_id];
      let x = p.x;
      if (main) x = Math.max(main.x1 + 20, Math.min(main.x2 - 20, x));
      lp.x1 = x; lp.x2 = x;
      handle.setAttribute("cx", x);
    } else {
      // end handle: vertical drag only, extend up or down from the branch point
      let y = p.y;
      if (Math.abs(y - lp.y1) < 40) {
        y = y >= lp.y1 ? lp.y1 + 40 : lp.y1 - 40;
      }
      y = Math.max(TRAY_ZONE_BOTTOM + 15, Math.min(600, y));
      lp.y2 = y;
      handle.setAttribute("cy", y);
    }

    if (line) {
      line.setAttribute("x1", lp.x1);
      line.setAttribute("y1", lp.y1);
      line.setAttribute("x2", lp.x2);
      line.setAttribute("y2", lp.y2);
    }
    // move the other handle + label in lockstep so the drag feels live
    const branchHandle = svg.querySelector(`.branch-handle[data-link-id="${linkId}"]`);
    const endHandle = svg.querySelector(`.end-handle[data-link-id="${linkId}"]`);
    branchHandle?.setAttribute("cx", lp.x1);
    branchHandle?.setAttribute("cy", lp.y1);
    endHandle?.setAttribute("cx", lp.x2);
    endHandle?.setAttribute("cy", lp.y2);
    if (label) {
      label.setAttribute("x", lp.x2 + 10);
      label.setAttribute("y", lp.y2 < lp.y1 ? lp.y2 - 10 : lp.y2 + 18);
    }

    // carry every sensor installed on this link canal along with it live
    latestSensors.forEach((s) => {
      if (s.link_canal_id !== linkId) return;
      const node = svg.querySelector(`[data-sensor-id="${s.id}"]`);
      if (!node) return;
      const coords = pointAt(lp.x1, lp.y1, lp.x2, lp.y2, s.pos_ratio ?? 0.5);
      node.setAttribute("transform", `translate(${coords.x}, ${coords.y})`);
    });
  });

  handle.addEventListener("pointerup", async () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove("dragging");
    const lp = linkPositions[linkId];
    try {
      await API.put(`/api/link-canals/${linkId}`, { x1: lp.x1, y1: lp.y1, x2: lp.x2, y2: lp.y2 });
      showToast("Link canal updated");
    } catch (err) {
      showToast(err.error || "Failed to move link canal", "error");
    }
    suppressReload = false;
    loadMap();
  });

  handle.addEventListener("pointercancel", () => {
    dragging = false;
    handle.classList.remove("dragging");
    suppressReload = false;
    loadMap();
  });
}

function showTooltip(evt, s) {
  const wrapper = document.querySelector(".map-wrapper").getBoundingClientRect();
  tooltip.innerHTML = `
    <div class="tt-title">${s.name}</div>
    <div class="tt-row">Type: ${s.sensor_type}</div>
    <div class="tt-row">Canal: ${s.canal_name || "Unassigned"}</div>
    <div class="tt-row">Link: ${s.link_name || "—"}</div>
    <div class="tt-row">Water Level: ${s.water_level} m</div>
    <div class="tt-row">Flow Rate: ${s.flow_rate} m³/s</div>
    <div class="tt-row">Status: ${STATUS_LABEL_MAP[s.status]}</div>
  `;
  tooltip.style.left = (evt.clientX - wrapper.left + 16) + "px";
  tooltip.style.top = (evt.clientY - wrapper.top + 10) + "px";
  tooltip.classList.add("show");
}
function hideTooltip() {
  tooltip.classList.remove("show");
}
const STATUS_LABEL_MAP = { ok: "Working", warning: "Low Level Warning", dead: "Dead" };

loadMap();
window.addEventListener("data-changed", loadMap);
setInterval(loadMap, 8000);
