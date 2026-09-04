# Canal Monitoring System

An AI-powered web interface for monitoring irrigation canals, link canals,
and their sensors in real time — built with **FastAPI**, SQLite, and
vanilla JavaScript (no frontend framework required).

## Features

- **Home page** — left panel lists every sensor (name, canal, link canal,
  water level, flow rate, depth, width). Right panel shows live alerts:
  yellow for low water level, red (with a pulsing alarm icon) for a dead
  sensor.
- **Map page** — canals are drawn as straight lines; link canals branch off
  and touch their main canal; sensors sit on the lines as colored circles
  (green = working, yellow = low level, red = dead), matching the colors
  used on the Home page.
  - **Sensor tray** — any sensor without a canal assignment sits in a
    dashed tray strip at the top of the map, all grouped together in one
    place.
  - **Drag & drop install** — drag a sensor from the tray onto any canal or
    link canal line to install it there; drag an installed sensor to
    reposition it along its line, move it to a different line, or drag it
    back into the tray to uninstall it.
  - **Drag & drop link canals** — each link canal has two handles: the
    handle sitting on the main canal slides left/right to reposition where
    it branches off; the far-end handle drags up or down to extend the
    link canal above or below the main canal.
- **☰ Menu** (top right) — Add Canal, Add Link Canal, Add Sensor, Modify
  Sensor, Delete (sensor / canal / link canal), Log, Connect Database.
- **Dark, transparent, glassy theme** with a stylised canal/water
  background.

## Project structure

```
canal_monitoring/
├── app.py                 # FastAPI backend + REST API + SQLite models
├── requirements.txt
├── canal_monitoring.db    # SQLite database (auto-created on first run)
├── templates/
│   ├── base.html          # shared layout: topbar, hamburger menu, tabs
│   ├── index.html         # Home page (sensor list + alerts)
│   └── map.html           # Map page (canal network diagram)
└── static/
    ├── css/
    │   └── style.css      # dark transparent theme + drag & drop styles
    ├── js/
    │   ├── common.js      # menu, modals, API calls, all add/edit/delete forms
    │   ├── home.js        # renders sensor list + alert panel
    │   └── map.js         # draws canal/link-canal lines, sensor tray, and
    │                       # all pointer-based drag & drop logic
    └── img/                # (reserved for custom background images)
```

## Setup & run

```bash
cd canal_monitoring
pip install -r requirements.txt
uvicorn app:app --reload
```

Then open **http://127.0.0.1:8000/** in your browser.

(`--reload` is optional — drop it for a plain production-style run, or use
`python app.py` which starts the same uvicorn server on port 8000.)

The SQLite database (`canal_monitoring.db`) is created automatically on
first run, seeded with one sample canal, one link canal, three sample
sensors installed on the canal/link canal (one healthy, one low-level, one
dead), and one extra sensor left **unassigned** in the map tray so you can
try the drag-to-install flow immediately.

## How the pieces fit together

- **Add Canal** — just a name. Draws a new straight line on the Map page.
- **Add Link Canal** — pick the main canal (dropdown) + type a link canal
  name (textbox). Drawn as a line branching from, and touching, its main
  canal. Its position can be fine-tuned afterwards by dragging its handles
  on the Map page.
- **Add Sensor** — choose main canal ("Unassigned" to drop it in the map
  tray and install it later by dragging), link canal ("None" if the sensor
  sits directly on the main canal), sensor type (`Canal` or
  `Water Course`), name, width and depth. The sensor appears as a colored
  dot on both the Home page list and the Map page (or in the tray, if
  unassigned).
- **Modify Sensor** — edit any sensor's readings/status directly (useful
  for testing alerts, or wiring in real sensor feeds later).
- **Delete** — choose whether you're deleting a sensor, a canal, or a link
  canal, then pick which one.
- **Log** — a running activity log of every add/edit/delete/move action.
- **Connect Database** — a form to record which external database
  (PostgreSQL/MySQL/MongoDB/etc.) the system should talk to. By default the
  app already uses a local SQLite database; this screen lets you point it
  at a different one.

## Map

-**Working** - This time from map for only display purpose, Beause this
time not available real sensors when we working on from real sensors
every sensor throught its location 
-**Example** - one canal install 100 sensors on different location all
throw its location we draw ln touch all 100 sensors so, automatically
create canal map.

## Auto Set Threshold Value

-**Use** - If we set whole canal sensors value in one click.
-**Working** -when Canal is full every sensors is in working state.
So, you press button every sensors Present reading make its threshold.

## Status thresholds

- **Green (working)** — water level is at or above the configured
  threshold and the sensor has reported recently.
- **Yellow (low level warning)** — water level has dropped below the
  threshold (`LOW_LEVEL_THRESHOLD = 1.5 m` in `app.py`, adjustable).
- **Red (dead)** — sensor has been manually marked dead, or has stopped
  reporting; also triggers the pulsing alarm icon on the Home page.

## Notes on extending this

- Swap the `LOW_LEVEL_THRESHOLD` constant in `app.py`, or make it
  per-sensor, to match real-world canal specs.
- Wire up a real sensor feed by calling `PUT /api/sensors/<id>` with fresh
  `water_level` / `flow_rate` readings on a schedule (e.g., from an MQTT
  bridge or a cron job).
- The "Connect Database" screen currently stores connection *settings*;
  swap the SQLite calls in `app.py` for `psycopg2`/`PyMySQL`/`pymongo`
  calls if you want to point production data at Postgres/MySQL/MongoDB.

  ## This time not avaiable real sensors so we test genrate data in json file
  ## upload option for test purpose only if you connect real sensors i provided
  ## comments help you connect real sensors.
