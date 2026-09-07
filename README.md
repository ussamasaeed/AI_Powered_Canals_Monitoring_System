# Canal Monitoring System AI Powered

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
- **☰ Menu** (top right) — Add Canal, Add Link Canal, Add Sensor, Modify
  Sensor, Delete (sensor / canal / link canal), Log, Connect Database.
- **AI assistant** — a chat panel that answers questions about your canals
  and sensors in plain language, grounded in the live database. See
  [AI assistant (chatbot)](#ai-assistant-chatbot) for setup.
- **Dark, transparent, glassy theme** with a stylised canal/water
  background.

## Project structure

```
AI_Powered_Canals_Monitoring_System/
├── app.py                 # FastAPI backend + REST API + SQLite models
├── chatbot.py             # RAG chatbot: Chroma vector store + Ollama / HF model
├── requirements.txt
├── .env.example           # template for .env (LLM provider, models, token)
├── canal_monitoring.db    # SQLite database (auto-created on first run)
├── templates/
│   ├── base.html          # shared layout: topbar, hamburger menu, tabs
│   ├── index.html         # Home page (sensor list + alerts)
│   └── map.html           # Map page (canal network diagram)
└── static/
    ├── css/
    │   └── style.css      # dark transparent theme + drag & drop styles
    └── js/
        ├── chatbot.js     # chat panel UI + calls to POST /api/chat
        ├── common.js      # menu, modals, API calls, all add/edit/delete forms
        ├── home.js        # renders sensor list + alert panel
        └── map.js         # draws canal/link-canal lines, sensor tray, and
                            # all pointer-based drag & drop logic
```

## Setup & run

```bash
cd AI_Powered_Canals_Monitoring_System
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

## AI assistant (chatbot)

The app ships with a RAG (retrieval-augmented generation) chatbot that answers
questions about your own canals and sensors, such as "which sensors are dead?"
or "what is the water level on S-101?". It reads the live SQLite data (plus the
external database if one is connected from the **Connect Database** screen),
indexes it in a local Chroma vector store, and sends only the relevant pieces
to a chat model.

The chatbot is **optional**. The monitoring system runs fine without it, and
`POST /api/chat` returns a clear 503 message if the extra packages are missing.

### 1. Install the packages

```bash
pip install -r requirements.txt
```

Note that this pulls in `sentence-transformers`, which installs PyTorch. It is
a large download (roughly 2 GB), so allow time for it.

### 2. Create your `.env`

```bash
cp .env.example .env
```

`.env` is listed in `.gitignore`, so your token never gets committed.

### 3. Choose a chat model backend

Set `LLM_PROVIDER` in `.env` to either `ollama` or `hf`.

**Option A: `ollama` (default, runs locally, no API key)**

Install Ollama from https://ollama.com/download, then pull a small model and
start the server:

```bash
ollama serve
ollama pull qwen2.5:3b
```

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Where the Ollama server listens |
| `OLLAMA_MODEL` | `qwen2.5:3b` | Model tag to use |
| `OLLAMA_TIMEOUT_SECONDS` | `180` | How long to wait for an answer |

If `OLLAMA_MODEL` has not been pulled yet, `chatbot.py` falls back to other
small models it finds locally (`llama3.2:3b`, `phi3.5`, `gemma2:2b`).

**Option B: `hf` (Hugging Face Inference API, needs a free token)**

Get a token at https://huggingface.co/settings/tokens, then in `.env`:

```
LLM_PROVIDER=hf
HF_API_TOKEN="hf_your_token_here"
CHAT_MODEL=Qwen/Qwen2.5-7B-Instruct
```

### Shared settings

| Variable | Default | Meaning |
|---|---|---|
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model, always runs locally |
| `RAG_TOP_K` | `5` | How many context chunks to retrieve per question |

### Troubleshooting

| Message | Fix |
|---|---|
| `Chatbot dependencies aren't installed` | Run `pip install -r requirements.txt` |
| `Can't reach Ollama at ...` | Start the server with `ollama serve` |
| Answers say no token is configured | Set `HF_API_TOKEN` in `.env`, or switch to `LLM_PROVIDER=ollama` |

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

## Test data (no real sensors yet)

Real sensors are not connected yet, so for testing we generate readings in a
JSON file and load them through the upload option. This is for test purposes
only. If you connect real sensors, the comments in `app.py` show you where to
wire them in.
