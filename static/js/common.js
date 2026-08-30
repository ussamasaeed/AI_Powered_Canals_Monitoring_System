/* =========================================================
   Canal Monitoring System - common.js
   Hamburger menu, modal system, API helpers, shared forms
   ========================================================= */

const API = {
  async get(url) {
    const r = await fetch(url);
    if (!r.ok) throw await r.json().catch(() => ({ error: "Request failed" }));
    return r.json();
  },
  async post(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw await r.json().catch(() => ({ error: "Request failed" }));
    return r.json();
  },
  async put(url, body) {
    const r = await fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw await r.json().catch(() => ({ error: "Request failed" }));
    return r.json();
  },
  async del(url) {
    const r = await fetch(url, { method: "DELETE" });
    if (!r.ok) throw await r.json().catch(() => ({ error: "Request failed" }));
    return r.json();
  },
};

function showToast(message, type = "success") {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.className = `toast show ${type}`;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => {
    toast.className = "toast";
  }, 2800);
}

/* ---------------- Hamburger menu ---------------- */
const hamburgerBtn = document.getElementById("hamburgerBtn");
const menuDropdown = document.getElementById("menuDropdown");

if (hamburgerBtn) {
  hamburgerBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    menuDropdown.classList.toggle("open");
  });
  document.addEventListener("click", (e) => {
    if (!menuDropdown.contains(e.target) && e.target !== hamburgerBtn) {
      menuDropdown.classList.remove("open");
    }
  });
  menuDropdown.querySelectorAll(".menu-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      menuDropdown.classList.remove("open");
      handleMenuAction(btn.dataset.action);
    });
  });
}

/* ---------------- Modal system ---------------- */
const modalOverlay = document.getElementById("modalOverlay");
const modalBox = document.getElementById("modalBox");

function openModal(html) {
  modalBox.innerHTML = html;
  modalOverlay.classList.add("open");
}
function closeModal() {
  modalOverlay.classList.remove("open");
  modalBox.innerHTML = "";
}
modalOverlay.addEventListener("click", (e) => {
  if (e.target === modalOverlay) closeModal();
});

/* ---------------- Menu action router ---------------- */
async function handleMenuAction(action) {
  switch (action) {
    case "add-canal": return openAddCanalModal();
    case "add-link-canal": return openAddLinkCanalModal();
    case "add-sensor": return openAddSensorModal();
    case "modify-sensor": return openModifySensorModal();
    case "delete": return openDeleteModal();
    case "log": return openLogModal();
    case "auto-threshold": return openAutoThresholdModal();
    case "connect-db": return openConnectDbModal();
  }
}

/* ---------------- Task 7: Add Canal ---------------- */
function openAddCanalModal() {
  openModal(`
    <h3>Add Canal</h3>
    <div class="form-group">
      <label for="canalName">Canal Name</label>
      <input id="canalName" type="text" placeholder="e.g. Main Canal - South" autofocus>
    </div>
    <div class="modal-actions">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="saveCanalBtn">Done</button>
    </div>
  `);
  document.getElementById("saveCanalBtn").addEventListener("click", async () => {
    const name = document.getElementById("canalName").value.trim();
    if (!name) return showToast("Please enter a canal name", "error");
    try {
      await API.post("/api/canals", { name });
      showToast("Canal added successfully");
      closeModal();
      window.dispatchEvent(new Event("data-changed"));
    } catch (err) {
      showToast(err.error || "Failed to add canal", "error");
    }
  });
}

/* ---------------- Task 8: Add Link Canal ---------------- */
async function openAddLinkCanalModal() {
  let canals = [];
  try {
    canals = await API.get("/api/canals");
  } catch (e) { /* ignore */ }

  if (canals.length === 0) {
    openModal(`
      <h3>Add Link Canal</h3>
      <p style="color:var(--text-dim); font-size:0.88rem;">No main canals exist yet. Please add a canal first.</p>
      <div class="modal-actions">
        <button class="btn btn-secondary" onclick="closeModal()">Close</button>
      </div>
    `);
    return;
  }

  openModal(`
    <h3>Add Link Canal</h3>
    <div class="form-group">
      <label for="mainCanalSelect">Main Canal</label>
      <select id="mainCanalSelect">
        ${canals.map((c) => `<option value="${c.id}">${c.name}</option>`).join("")}
      </select>
    </div>
    <div class="form-group">
      <label for="linkCanalName">Link Canal Name</label>
      <input id="linkCanalName" type="text" placeholder="e.g. Link Canal - West">
    </div>
    <div class="modal-actions">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="saveLinkCanalBtn">Done</button>
    </div>
  `);
  document.getElementById("saveLinkCanalBtn").addEventListener("click", async () => {
    const main_canal_id = document.getElementById("mainCanalSelect").value;
    const name = document.getElementById("linkCanalName").value.trim();
    if (!name) return showToast("Please enter a link canal name", "error");
    try {
      await API.post("/api/link-canals", { main_canal_id, name });
      showToast("Link canal added successfully");
      closeModal();
      window.dispatchEvent(new Event("data-changed"));
    } catch (err) {
      showToast(err.error || "Failed to add link canal", "error");
    }
  });
}

/* ---------------- Task 9: Add Sensor ---------------- */
async function openAddSensorModal() {
  let canals = [], links = [];
  try {
    [canals, links] = await Promise.all([API.get("/api/canals"), API.get("/api/link-canals")]);
  } catch (e) { /* ignore */ }

  openModal(`
    <h3>Add Sensor</h3>
    <div class="form-group">
      <label for="sMainCanal">Main Canal</label>
      <select id="sMainCanal">
        <option value="">Unassigned — place on map tray, install later</option>
        ${canals.map((c) => `<option value="${c.id}">${c.name}</option>`).join("")}
      </select>
    </div>
    <div class="form-group">
      <label for="sLinkCanal">Link Canal</label>
      <select id="sLinkCanal" ${canals.length === 0 ? "disabled" : ""}>
        <option value="">None (placed on main canal)</option>
        ${links.map((l) => `<option value="${l.id}" data-main="${l.main_canal_id}">${l.name} (${l.main_canal_name})</option>`).join("")}
      </select>
    </div>
    <div class="form-group">
      <label for="sType">Sensor Type</label>
      <select id="sType">
        <option value="Canal">Canal</option>
        <option value="Water Course">Water Course</option>
      </select>
    </div>
    <div class="form-group">
      <label for="sName">Sensor Name</label>
      <input id="sName" type="text" placeholder="e.g. S-301">
    </div>
    <div class="form-group">
      <label for="sWidth">Width (m)</label>
      <input id="sWidth" type="number" step="0.1" placeholder="e.g. 12.5">
    </div>
    <div class="form-group">
      <label for="sDepth">Empty Canal Depth (m)</label>
      <input id="sDepth" type="number" step="0.1" placeholder="e.g. 4.2">
    </div>
    <div class="form-group">
      <label for="sMountHeight">Sensor Mount Height (m)</label>
      <input id="sMountHeight" type="number" step="0.1" placeholder="e.g. 0.3">
    </div>
    <p style="color:var(--text-dim); font-size:0.8rem; margin-top:-4px;">
      Leave the main canal unassigned to drop the sensor in the map tray, then drag it onto a canal to install it.
    </p>
    <div class="modal-actions">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="saveSensorBtn">Done</button>
    </div>
  `);

  document.getElementById("saveSensorBtn").addEventListener("click", async () => {
    const payload = {
      main_canal_id: document.getElementById("sMainCanal").value || null,
      link_canal_id: document.getElementById("sLinkCanal").value || null,
      sensor_type: document.getElementById("sType").value,
      name: document.getElementById("sName").value.trim(),
      width: parseFloat(document.getElementById("sWidth").value),
      depth: parseFloat(document.getElementById("sDepth").value),
      sensor_mount_height: parseFloat(document.getElementById("sMountHeight").value),
    };
    if (!payload.name || isNaN(payload.width) || isNaN(payload.depth) || isNaN(payload.sensor_mount_height)) {
      return showToast("Please fill all required fields", "error");
    }
    try {
      await API.post("/api/sensors", payload);
      showToast("Sensor added successfully");
      closeModal();
      window.dispatchEvent(new Event("data-changed"));
    } catch (err) {
      showToast(err.error || "Failed to add sensor", "error");
    }
  });
}

/* ---------------- Modify Sensor ---------------- */
async function openModifySensorModal() {
  let sensors = [];
  try { sensors = await API.get("/api/sensors"); } catch (e) {}

  if (sensors.length === 0) {
    openModal(`
      <h3>Modify Sensor</h3>
      <p style="color:var(--text-dim); font-size:0.88rem;">No sensors available.</p>
      <div class="modal-actions"><button class="btn btn-secondary" onclick="closeModal()">Close</button></div>
    `);
    return;
  }

  openModal(`
    <h3>Modify Sensor</h3>
    <div class="form-group">
      <label for="mSensorSelect">Select Sensor</label>
      <select id="mSensorSelect">
        ${sensors.map((s) => `<option value="${s.id}">${s.name} (${s.canal_name})</option>`).join("")}
      </select>
    </div>
    <div id="mSensorFields"></div>
    <div class="modal-actions">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="saveModifyBtn">Save Changes</button>
    </div>
  `);

  const fieldsDiv = document.getElementById("mSensorFields");
  function renderFields(sensor) {
    fieldsDiv.innerHTML = `
      <div class="form-group">
        <label for="mName">Sensor Name</label>
        <input id="mName" type="text" value="${sensor.name}">
      </div>
      <div class="form-group">
        <label for="mWidth">Canal Width (m)</label>
        <input id="mWidth" type="number" step="0.1" value="${sensor.width}">
      </div>
      <div class="form-group">
        <label for="mDepth">Empty Canal Depth (m)</label>
        <input id="mDepth" type="number" step="0.1" value="${sensor.depth}">
      </div>
      <div class="form-group">
        <label for="mMountHeight">Sensor Mount Height (m)</label>
        <input id="mMountHeight" type="number" step="0.1" value="${sensor.sensor_mount_height ?? 0}">
      </div>
      <div class="form-group">
        <label for="mDistance">Distance Measured by Sensor (m)</label>
        <input id="mDistance" type="number" step="0.1" value="${sensor.distance_measured ?? 0}">
      </div>
      <div class="form-group">
        <label for="mVelocity">Velocity (m/s)</label>
        <input id="mVelocity" type="number" step="0.1" value="${sensor.velocity ?? 0}">
      </div>
      <p style="color:var(--text-dim); font-size:0.8rem; margin-top:-4px;">
        Auto-calculated — Water Level: <b>${sensor.water_level} m</b> &nbsp;·&nbsp;
        Flow Rate: <b>${sensor.flow_rate} m&sup3;/s</b>
      </p>
      <div class="form-group">
        <label for="mStatus">Status</label>
        <select id="mStatus">
          <option value="ok" ${sensor.status === "ok" ? "selected" : ""}>Working</option>
          <option value="warning" ${sensor.status === "warning" ? "selected" : ""}>Low Level Warning</option>
          <option value="dead" ${sensor.status === "dead" ? "selected" : ""}>Dead</option>
        </select>
      </div>
    `;
  }
  renderFields(sensors[0]);
  document.getElementById("mSensorSelect").addEventListener("change", (e) => {
    const sel = sensors.find((s) => String(s.id) === e.target.value);
    renderFields(sel);
  });

  document.getElementById("saveModifyBtn").addEventListener("click", async () => {
    const id = document.getElementById("mSensorSelect").value;
    const payload = {
      name: document.getElementById("mName").value.trim(),
      width: parseFloat(document.getElementById("mWidth").value),
      depth: parseFloat(document.getElementById("mDepth").value),
      sensor_mount_height: parseFloat(document.getElementById("mMountHeight").value),
      distance_measured: parseFloat(document.getElementById("mDistance").value),
      velocity: parseFloat(document.getElementById("mVelocity").value),
      status: document.getElementById("mStatus").value,
    };
    try {
      await API.put(`/api/sensors/${id}`, payload);
      showToast("Sensor updated successfully");
      closeModal();
      window.dispatchEvent(new Event("data-changed"));
    } catch (err) {
      showToast(err.error || "Failed to update sensor", "error");
    }
  });
}

/* ---------------- Task 10: Delete ---------------- */
function openDeleteModal() {
  openModal(`
    <h3>Delete</h3>
    <p style="color:var(--text-dim); font-size:0.85rem; margin-top:-8px;">What would you like to delete?</p>
    <div class="delete-choice">
      <button data-target="sensor">🗑 Sensor</button>
      <button data-target="canal">🗑 Canal</button>
      <button data-target="link-canal">🗑 Link Canal</button>
    </div>
    <div class="modal-actions">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
    </div>
  `);
  modalBox.querySelectorAll(".delete-choice button").forEach((btn) => {
    btn.addEventListener("click", () => openDeleteStep2(btn.dataset.target));
  });
}

async function openDeleteStep2(target) {
  const map = {
    sensor: { url: "/api/sensors", label: "Sensor" },
    canal: { url: "/api/canals", label: "Canal" },
    "link-canal": { url: "/api/link-canals", label: "Link Canal" },
  };
  const conf = map[target];
  let items = [];
  try { items = await API.get(conf.url); } catch (e) {}

  if (items.length === 0) {
    openModal(`
      <h3>Delete ${conf.label}</h3>
      <p style="color:var(--text-dim); font-size:0.88rem;">Nothing to delete.</p>
      <div class="modal-actions"><button class="btn btn-secondary" onclick="closeModal()">Close</button></div>
    `);
    return;
  }

  openModal(`
    <h3>Delete ${conf.label}</h3>
    <div class="form-group">
      <label for="delSelect">Select ${conf.label}</label>
      <select id="delSelect">
        ${items.map((i) => `<option value="${i.id}">${i.name}</option>`).join("")}
      </select>
    </div>
    <div class="modal-actions">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-danger" id="confirmDeleteBtn">Delete</button>
    </div>
  `);
  document.getElementById("confirmDeleteBtn").addEventListener("click", async () => {
    const id = document.getElementById("delSelect").value;
    try {
      await API.del(`${conf.url}/${id}`);
      showToast(`${conf.label} deleted`);
      closeModal();
      window.dispatchEvent(new Event("data-changed"));
    } catch (err) {
      showToast(err.error || "Delete failed", "error");
    }
  });
}

/* ---------------- Log ---------------- */
async function openLogModal() {
  let logs = [];
  try { logs = await API.get("/api/logs"); } catch (e) {}

  openModal(`
    <h3>Activity Log</h3>
    <div class="log-list">
      ${logs.length ? logs.map((l) => `
        <div class="log-row">
          <div class="log-action">${l.action} ${l.details ? "&mdash; " + l.details : ""}</div>
          <div class="log-time">${new Date(l.created_at + "Z").toLocaleString()}</div>
        </div>
      `).join("") : `<p style="color:var(--text-dim); font-size:0.88rem;">No activity logged yet.</p>`}
    </div>
    <div class="modal-actions">
      <button class="btn btn-secondary" onclick="closeModal()">Close</button>
    </div>
  `);
}

/* ---------------- Auto Set Threshold ---------------- */
async function openAutoThresholdModal() {
  let canals = [];
  try { canals = await API.get("/api/canals"); } catch (e) { /* ignore */ }

  if (canals.length === 0) {
    openModal(`
      <h3>Auto Set Threshold</h3>
      <p style="color:var(--text-dim); font-size:0.88rem;">No canals available.</p>
      <div class="modal-actions"><button class="btn btn-secondary" onclick="closeModal()">Close</button></div>
    `);
    return;
  }

  openModal(`
    <h3>Auto Set Threshold</h3>
    <p style="color:var(--text-dim); font-size:0.8rem; margin-top:-4px;">
      Sets each sensor's low-water threshold to its current reading. Applied
      separately, sensor by sensor.
    </p>
    <div class="form-group">
      <label for="atMainCanal">Main Canal</label>
      <select id="atMainCanal">
        ${canals.map((c) => `<option value="${c.id}">${c.name}</option>`).join("")}
      </select>
    </div>
    <div class="form-group">
      <label for="atLinkCanal">Link Canal</label>
      <select id="atLinkCanal">
        <option value="">All sensors on this main canal</option>
      </select>
    </div>
    <div class="modal-actions">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="setThresholdBtn">Set</button>
    </div>
  `);

  let allLinks = [];
  try { allLinks = await API.get("/api/link-canals"); } catch (e) { /* ignore */ }

  function refreshLinkOptions() {
    const mainId = document.getElementById("atMainCanal").value;
    const filtered = allLinks.filter((l) => String(l.main_canal_id) === String(mainId));
    const sel = document.getElementById("atLinkCanal");
    sel.innerHTML = `<option value="">All sensors on this main canal</option>` +
      filtered.map((l) => `<option value="${l.id}">${l.name}</option>`).join("");
  }
  document.getElementById("atMainCanal").addEventListener("change", refreshLinkOptions);
  refreshLinkOptions();

  document.getElementById("setThresholdBtn").addEventListener("click", async () => {
    const main_canal_id = document.getElementById("atMainCanal").value;
    const link_canal_id = document.getElementById("atLinkCanal").value || null;
    try {
      const res = await API.post("/api/sensors/auto-threshold", { main_canal_id, link_canal_id });
      showToast(res.message || "Threshold updated");
      closeModal();
      window.dispatchEvent(new Event("data-changed"));
    } catch (err) {
      showToast(err.error || "Failed to set threshold", "error");
    }
  });
}

/* ---------------- Connect Database ---------------- */
function openConnectDbModal() {
  openModal(`
    <h3>Connect Database</h3>
    <div class="form-group">
      <label for="dbType">Database Type</label>
      <select id="dbType">
        <option value="SQLite">SQLite (default, local)</option>
        <option value="PostgreSQL">PostgreSQL</option>
        <option value="MySQL">MySQL</option>
        <option value="MongoDB">MongoDB</option>
      </select>
    </div>
    <div class="form-group">
      <label for="dbHost">Host</label>
      <input id="dbHost" type="text" placeholder="e.g. localhost">
    </div>
    <div class="form-group">
      <label for="dbPort">Port</label>
      <input id="dbPort" type="text" placeholder="e.g. 5432">
    </div>
    <div class="form-group">
      <label for="dbName">Database Name</label>
      <input id="dbName" type="text" placeholder="e.g. canal_monitoring">
    </div>
    <div class="form-group">
      <label for="dbUser">Username</label>
      <input id="dbUser" type="text" placeholder="e.g. admin">
    </div>
    <div class="modal-actions">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="connectDbBtn">Connect</button>
    </div>
  `);
  document.getElementById("connectDbBtn").addEventListener("click", async () => {
    const payload = {
      db_type: document.getElementById("dbType").value,
      host: document.getElementById("dbHost").value.trim(),
      port: document.getElementById("dbPort").value.trim(),
      db_name: document.getElementById("dbName").value.trim(),
      username: document.getElementById("dbUser").value.trim(),
    };
    try {
      await API.post("/api/db-connection", payload);
      showToast("Database connected");
      closeModal();
    } catch (err) {
      showToast(err.error || "Connection failed", "error");
    }
  });
}
