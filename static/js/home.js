/* =========================================================
   Canal Monitoring System - home.js
   Left panel: sensor list. Right panel: alerts.
   ========================================================= */

const STATUS_LABEL = { ok: "Working", warning: "Low Level", dead: "Dead" };

async function loadHome() {
  let sensors = [];
  try {
    sensors = await API.get("/api/sensors");
  } catch (e) {
    showToast("Unable to load sensors", "error");
    return;
  }
  renderSensorList(sensors);
  renderAlerts(sensors);
}

function renderSensorList(sensors) {
  const list = document.getElementById("sensorList");
  document.getElementById("sensorCount").textContent = `${sensors.length} device${sensors.length === 1 ? "" : "s"}`;

  if (sensors.length === 0) {
    list.innerHTML = `<div class="alert-empty">No sensors added yet. Use the menu (☰) to add one.</div>`;
    return;
  }

  list.innerHTML = sensors.map((s) => `
    <div class="sensor-card status-${s.status}">
      <div class="sensor-card-top">
        <span class="sensor-name">${s.name}</span>
        <span class="status-badge ${s.status}">${STATUS_LABEL[s.status]}</span>
      </div>
      <div class="sensor-grid">
        <div>Canal: <b>${s.canal_name}</b></div>
        <div>Link: <b>${s.link_name || "—"}</b></div>
        <div>Water Level: <b>${s.water_level} m</b></div>
        <div>Flow Rate: <b>${s.flow_rate} m³/s</b></div>
        <div>Depth: <b>${s.depth} m</b></div>
        <div>Width: <b>${s.width} m</b></div>
      </div>
    </div>
  `).join("");
}

function renderAlerts(sensors) {
  const list = document.getElementById("alertList");
  const alerts = sensors.filter((s) => s.status !== "ok");
  document.getElementById("alertCount").textContent = `${alerts.length} active`;

  if (alerts.length === 0) {
    list.innerHTML = `<div class="alert-empty">✓ All sensors are operating normally.</div>`;
    return;
  }

  list.innerHTML = alerts.map((s) => {
    const isDead = s.status === "dead";
    return `
      <div class="alert-card ${s.status}">
        <span class="alert-icon">${isDead ? "⛔" : "⚠"}</span>
        <div>
          <div class="alert-title">${s.name} — ${isDead ? "Sensor Dead" : "Water Level Low"}</div>
          <div class="alert-desc">${s.canal_name}${s.link_name ? " / " + s.link_name : ""} · ${isDead ? "No signal received" : `Level ${s.water_level} m (below threshold)`}</div>
        </div>
      </div>
    `;
  }).join("");
}

loadHome();
window.addEventListener("data-changed", loadHome);
setInterval(loadHome, 8000); // periodic refresh to simulate live monitoring
