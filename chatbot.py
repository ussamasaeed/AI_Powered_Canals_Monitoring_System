"""
chatbot.py - Retrieval-Augmented Generation (RAG) chatbot for the
Canal Monitoring System.

Pieces (all Hugging Face open-source):
  - Embedding model : sentence-transformers model, run locally, turns text
                       into vectors.
  - Vector store     : Chroma (persistent, on disk at ./chroma_store),
                        collection configured for cosine-similarity search.
  - External knowledge:
      * Local SQLite always supplies the current structure/state - canals,
        link canals, sensors and their latest reading, activity log.
      * If a PostgreSQL/MySQL server is connected from the app's "Connect
        Database" screen (see app.EXTERNAL_DB), that server holds the full
        time-series history for each sensor (one DB per canal, one table
        per sensor - see app.write_external_reading). This module reads
        the most recent rows from there too and adds trend/history
        documents, so questions like "how has S-101's water level trended"
        can be answered even though SQLite only keeps the latest value.
      Every question rebuilds the knowledge base from whichever of these
      are available, so answers never go stale.
  - Chat model       : an open-source instruction-tuned chat model. Two
                        providers are supported:
                          * "ollama" (default) - runs fully locally via a
                            local Ollama server (http://localhost:11434).
                            No internet dependency, no external API to go
                            down. Pick a small model (3B or under) since
                            this machine has 8GB RAM.
                          * "hf" - calls the Hugging Face Inference API
                            (remote, needs HF_API_TOKEN and an internet
                            connection - this is what used to break).

Configuration lives in `.env` (see .env in this folder):
    LLM_PROVIDER     - "ollama" (default) or "hf"
    OLLAMA_HOST      - base URL of the local Ollama server (default
                        http://localhost:11434)
    OLLAMA_MODEL     - Ollama model tag to use, e.g. qwen2.5:3b
    HF_API_TOKEN     - your Hugging Face access token (only used if
                        LLM_PROVIDER=hf)
    EMBEDDING_MODEL  - sentence-transformers model name
    CHAT_MODEL       - HF chat/instruct model name (only used if
                        LLM_PROVIDER=hf)
    RAG_TOP_K        - how many context chunks to retrieve per question
"""

import json
import os
import sqlite3
import threading
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

import chromadb
from chromadb.utils import embedding_functions

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "canal_monitoring.db")
CHROMA_DIR = os.path.join(BASE_DIR, "chroma_store")
EXTERNAL_HISTORY_LIMIT = 200

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").strip().rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b").strip()
# Generous default - a small CPU-bound model chewing through a big "report
# of all sensors" style context can genuinely take a couple of minutes on
# an 8GB machine, especially on the first call after Ollama loads the model.
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))

HF_TOKEN = os.getenv("HF_API_TOKEN", "").strip()
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2").strip()
CHAT_MODEL = os.getenv("CHAT_MODEL", "Qwen/Qwen2.5-7B-Instruct").strip()
TOP_K = int(os.getenv("RAG_TOP_K", "5"))

_lock = threading.Lock()

# ----------------------------------------------------------------------
# Embedding model + Chroma vector store (cosine similarity)
# ----------------------------------------------------------------------
_embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name=EMBEDDING_MODEL
)
_chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
_collection = _chroma_client.get_or_create_collection(
    name="canal_knowledge",
    embedding_function=_embedding_fn,
    metadata={"hnsw:space": "cosine"},
)

# ----------------------------------------------------------------------
# Chat model
# ----------------------------------------------------------------------
# HF client is only created (and huggingface_hub only imported) if the HF
# provider is actually selected, so a plain local/Ollama setup doesn't need
# the huggingface_hub package installed at all.
_hf_client = None
if LLM_PROVIDER == "hf" and HF_TOKEN:
    from huggingface_hub import InferenceClient
    _hf_client = InferenceClient(token=HF_TOKEN, timeout=45)


def _ollama_available() -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def _call_ollama_chat(model: str, question: str, context: str) -> str:
    """One attempt at calling a local Ollama model via its /api/chat
    endpoint (no extra SDK needed - plain HTTP)."""
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
        ],
        "stream": False,
        "options": {"temperature": 0.2},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Ollama HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Can't reach Ollama at {OLLAMA_HOST} ({e.reason}). "
            f"Is 'ollama serve' running?"
        ) from e

    return (body.get("message", {}) or {}).get("content", "").strip()


def _get_app_module():
    """Lazy import of app.py - it imports this module too, so importing it
    at the top of this file would be circular. By the time a chat request
    comes in, app.py has finished loading, so this is safe."""
    import app as app_module
    return app_module


def _fetch_external_history(app_module, canal_name: str, sensor_name: str,
                             link_name: Optional[str]) -> List[Dict]:
    """Pull the most recent readings for one sensor from the connected
    PostgreSQL/MySQL server (app.EXTERNAL_DB). Returns [] if no external
    server is connected, or if that sensor has no table there yet."""
    ext = app_module.EXTERNAL_DB
    if not ext["connected"] or not canal_name:
        return []

    db_name = app_module.canal_db_name(canal_name)
    table_name = app_module.sensor_table_name(sensor_name, link_name)
    db_type = ext["db_type"]

    conn = app_module._external_server_connect(
        db_type, ext["host"], ext["port"], ext["username"], ext["password"], database=db_name
    )
    try:
        cur = conn.cursor()
        quoted = f'"{table_name}"' if db_type == "PostgreSQL" else f"`{table_name}`"
        cur.execute(
            f"SELECT recorded_at, velocity, distance_measured, water_level, flow_rate, status "
            f"FROM {quoted} ORDER BY recorded_at DESC LIMIT %s",
            (EXTERNAL_HISTORY_LIMIT,),
        )
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        return rows
    finally:
        conn.close()


def _external_history_document(sensor_id, sensor_name, db_type, rows: List[Dict]) -> Optional[Dict]:
    """Turn a batch of external time-series rows into a short summary
    document (trend, min/max, latest, and any past dead/warning events)
    rather than one row per reading - keeps the vector store small while
    still being able to answer 'was this sensor ever dead' questions."""
    if not rows:
        return None
    levels = [r["water_level"] for r in rows if r.get("water_level") is not None]
    flows = [r["flow_rate"] for r in rows if r.get("flow_rate") is not None]
    newest, oldest = rows[0], rows[-1]

    trend = "flat"
    if levels and len(levels) > 1:
        if newest["water_level"] > oldest["water_level"]:
            trend = "rising"
        elif newest["water_level"] < oldest["water_level"]:
            trend = "falling"

    # rows are newest-first; walk oldest-first so timestamps read naturally
    dead_events = [r for r in reversed(rows) if r.get("status") == "dead"]
    warning_events = [r for r in reversed(rows) if r.get("status") == "warning"]

    def _fmt_events(events, label, cap=10):
        if not events:
            return f"No readings were ever flagged '{label}' in this history."
        times = ", ".join(str(e["recorded_at"]) for e in events[:cap])
        more = f" (+{len(events) - cap} more)" if len(events) > cap else ""
        return f"Flagged '{label}' at {len(events)} reading(s): {times}{more}."

    if not (levels and flows):
        text = (
            f"History for sensor '{sensor_name}' from the connected {db_type} database has "
            f"{len(rows)} readings but no usable water_level/flow_rate values yet."
        )
    else:
        text = (
            f"History for sensor '{sensor_name}' from the connected {db_type} database "
            f"(last {len(rows)} readings): water_level trend is {trend}, ranging "
            f"{min(levels):.2f}-{max(levels):.2f} (latest {newest['water_level']}). "
            f"flow_rate ranging {min(flows):.2f}-{max(flows):.2f} (latest {newest['flow_rate']}). "
            f"Most recent reading at {newest['recorded_at']}, status '{newest['status']}'. "
            f"Oldest of these readings at {oldest['recorded_at']}. "
            f"{_fmt_events(dead_events, 'dead')} {_fmt_events(warning_events, 'warning')}"
        )
    return {"id": f"sensor-{sensor_id}-history", "text": text}


def _rows(query: str, params=()) -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def build_documents() -> List[Dict]:
    """Turn the current state of the app's SQLite database into small text
    documents that can be embedded and searched. This is the 'external
    knowledge' the RAG system is grounded on."""
    docs: List[Dict] = []

    canals = _rows("SELECT * FROM canals")
    for c in canals:
        docs.append({
            "id": f"canal-{c['id']}",
            "text": f"Main canal '{c['name']}' (id {c['id']}) was created at {c['created_at']}.",
        })

    links = _rows(
        "SELECT lc.*, c.name AS main_canal_name FROM link_canals lc "
        "JOIN canals c ON lc.main_canal_id = c.id"
    )
    for l in links:
        docs.append({
            "id": f"link-{l['id']}",
            "text": (
                f"Link canal '{l['name']}' (id {l['id']}) branches off main canal "
                f"'{l['main_canal_name']}', created at {l['created_at']}."
            ),
        })

    sensors = _rows(
        "SELECT s.*, c.name AS canal_name, lc.name AS link_name FROM sensors s "
        "LEFT JOIN canals c ON s.main_canal_id = c.id "
        "LEFT JOIN link_canals lc ON s.link_canal_id = lc.id"
    )

    app_module = None
    try:
        app_module = _get_app_module()
    except Exception:
        pass  # app.py not importable yet (e.g. run standalone) - skip external history

    for s in sensors:
        if s.get("canal_name"):
            location = f"on main canal '{s['canal_name']}'"
            if s.get("link_name"):
                location += f", link canal '{s['link_name']}'"
        else:
            location = "unassigned (sitting in the map tray, not yet placed on a canal)"
        docs.append({
            "id": f"sensor-{s['id']}",
            "text": (
                f"Sensor '{s['name']}' (id {s['id']}, type {s['sensor_type']}) is {location}. "
                f"Canal width {s['width']}, depth {s['depth']}, sensor mount height "
                f"{s['sensor_mount_height']}. Latest reading: distance_measured="
                f"{s['distance_measured']}, velocity={s['velocity']}, water_level="
                f"{s['water_level']}, flow_rate={s['flow_rate']}. Low-water threshold is "
                f"{s['threshold']}. Current status is '{s['status']}', last seen at "
                f"{s['last_seen']}."
            ),
        })

        # Time-series history only exists on the connected external server
        # (SQLite only ever keeps the latest reading per sensor).
        if app_module is not None and app_module.EXTERNAL_DB["connected"] and s.get("canal_name"):
            try:
                rows = _fetch_external_history(app_module, s["canal_name"], s["name"], s.get("link_name"))
                hist_doc = _external_history_document(
                    s["id"], s["name"], app_module.EXTERNAL_DB["db_type"], rows
                )
                if hist_doc:
                    docs.append(hist_doc)
            except Exception:
                # Missing table, connection hiccup, etc. - never let this
                # break the rest of the knowledge base.
                pass

    logs = _rows("SELECT * FROM logs ORDER BY id DESC LIMIT 100")
    for lg in logs:
        docs.append({
            "id": f"log-{lg['id']}",
            "text": f"Activity log #{lg['id']}: {lg['action']} - {lg['details']} (at {lg['created_at']}).",
        })

    return docs


def refresh_index() -> None:
    """Rebuild the Chroma collection from the current database. Called
    before every chat query so answers reflect live data - the dataset is
    small so this is cheap enough to redo each time."""
    docs = build_documents()
    with _lock:
        existing = _collection.get()
        if existing and existing.get("ids"):
            _collection.delete(ids=existing["ids"])
        if docs:
            _collection.add(
                ids=[d["id"] for d in docs],
                documents=[d["text"] for d in docs],
            )


def retrieve(question: str, top_k: int = TOP_K) -> List[str]:
    """Embed the question and run a cosine-similarity search in Chroma to
    find the most relevant knowledge documents."""
    count = _collection.count()
    if count == 0:
        return []
    results = _collection.query(query_texts=[question], n_results=min(top_k, count))
    if results and results.get("documents"):
        return results["documents"][0]
    return []


SYSTEM_PROMPT = (
    "You are the assistant embedded in the Canal Monitoring System. Answer "
    "questions about canals, link canals, sensors, water levels, flow rates, "
    "reading history/trends and the activity log using ONLY the context "
    "given below. If the answer isn't in the context, say you don't have "
    "that information in the current data instead of guessing. Be concise "
    "and factual."
)


# If CHAT_MODEL isn't servable on the account's enabled providers, try these
# widely-hosted models in order before giving up - keeps the HF path working
# even when HF's model/provider availability shifts under you.
FALLBACK_MODELS = [
    m for m in [
        CHAT_MODEL,
        "Qwen/Qwen2.5-7B-Instruct",
        "meta-llama/Llama-3.1-8B-Instruct",
        "mistralai/Mistral-7B-Instruct-v0.3",
        "HuggingFaceH4/zephyr-7b-beta",
    ] if m
]
# de-dupe while keeping order
FALLBACK_MODELS = list(dict.fromkeys(FALLBACK_MODELS))

# Small (<=3B) Ollama models to try if OLLAMA_MODEL isn't pulled locally yet -
# all comfortably fit an 8GB RAM machine alongside the rest of the app.
OLLAMA_FALLBACK_MODELS = list(dict.fromkeys([
    m for m in [OLLAMA_MODEL, "qwen2.5:3b", "llama3.2:3b", "phi3.5", "gemma2:2b"] if m
]))

_NOT_SUPPORTED_MARKERS = ("not supported by any provider", "model_not_supported")


def _call_chat_model(model: str, question: str, context: str):
    """One attempt at calling a given model. Raises on failure."""
    kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
        ],
        max_tokens=512,
        temperature=0.2,
    )
    try:
        return _hf_client.chat_completion(provider="auto", **kwargs)
    except TypeError:
        # Installed huggingface_hub version too old to accept `provider`.
        return _hf_client.chat_completion(**kwargs)


def _generate_answer(question: str, context_docs: List[str]) -> str:
    context = "\n".join(f"- {d}" for d in context_docs) if context_docs else "(no matching data found)"

    if LLM_PROVIDER == "ollama":
        return _generate_answer_ollama(question, context, context_docs)
    return _generate_answer_hf(question, context, context_docs)


def _generate_answer_ollama(question: str, context: str, context_docs: List[str]) -> str:
    if not _ollama_available():
        if not context_docs:
            return (
                f"Can't reach the local Ollama server at {OLLAMA_HOST}. Start it with "
                f"'ollama serve' (and make sure you've pulled a model, e.g. "
                f"'ollama pull {OLLAMA_MODEL}'). I also couldn't find anything "
                f"relevant in the current data."
            )
        bullets = "\n".join(f"- {d}" for d in context_docs[:3])
        return (
            f"Can't reach the local Ollama server at {OLLAMA_HOST}. Start it with "
            f"'ollama serve' (and make sure you've pulled a model, e.g. "
            f"'ollama pull {OLLAMA_MODEL}'). Closest matching data:\n{bullets}"
        )

    last_error: Optional[Exception] = None
    for model in OLLAMA_FALLBACK_MODELS:
        try:
            content = _call_ollama_chat(model, question, context)
            if not content:
                last_error = RuntimeError(f"{model} returned an empty response")
                continue
            return content
        except Exception as exc:
            last_error = exc
            msg = str(exc).lower()
            if "not found" in msg or "404" in msg:
                continue  # model not pulled locally - try the next fallback
            break  # connection error etc. - stop trying more models

    return (
        f"Sorry, I couldn't get an answer from Ollama ({last_error}). Make sure "
        f"the model is pulled locally, e.g.:\n  ollama pull {OLLAMA_MODEL}\n"
        f"Here is the relevant data:\n{context}"
    )


def _generate_answer_hf(question: str, context: str, context_docs: List[str]) -> str:
    if _hf_client is None:
        if not context_docs:
            return (
                "The Hugging Face chat model isn't configured yet - add an access "
                "token to HF_API_TOKEN in the .env file to enable full answers. "
                "I also couldn't find anything relevant in the current data."
            )
        bullets = "\n".join(f"- {d}" for d in context_docs[:3])
        return (
            "The Hugging Face chat model isn't configured yet - add an access "
            "token to HF_API_TOKEN in the .env file for a proper answer. "
            f"Closest matching data:\n{bullets}"
        )

    last_error: Optional[Exception] = None
    for model in FALLBACK_MODELS:
        try:
            response = _call_chat_model(model, question, context)
            content = (response.choices[0].message.content or "").strip()
            if not content:
                last_error = RuntimeError(f"{model} returned an empty response")
                continue
            return content
        except Exception as exc:
            last_error = exc
            msg = str(exc).lower()
            if any(marker in msg for marker in _NOT_SUPPORTED_MARKERS):
                continue  # try the next fallback model
            break  # a real error (timeout, auth, etc.) - stop trying more models

    return (
        f"Sorry, I couldn't reach the chat model ({last_error}). This usually "
        f"means no Inference Provider is enabled on your Hugging Face account: "
        f"go to https://huggingface.co/settings/inference-providers and turn one "
        f"on (e.g. 'HF Inference'), and make sure your token in .env has "
        f"'Make calls to Inference Providers' permission at "
        f"https://huggingface.co/settings/tokens. Here is the relevant data:\n{context}"
    )


STATUS_KEYWORDS = {
    "dead": "dead",
    "offline": "dead",
    "warning": "warning",
    "low": "warning",
    "active": "active",
    "ok": "active",
    "normal": "active",
    "healthy": "active",
    "working": "active",
}


HISTORY_KEYWORDS = (
    "past", "previous", "previously", "before", "used to", "earlier",
    "history", "historical", "trend", "was", "were", "had been", "ever",
    "summary", "report", "overview",
)


def _direct_status_answer(question: str) -> Optional[str]:
    """Fast, exact answer for 'which/how many sensors are CURRENTLY dead/
    warning/active' style questions, computed straight from SQLite instead
    of relying on similarity search - the activity log also contains the
    word 'dead' (e.g. 'dead=2') which can outrank the actual sensor rows in
    retrieval, so this avoids that mix-up entirely.

    Deliberately steps aside for anything phrased about the past/history/a
    summary - those need the full RAG pass so the Postgres/MySQL reading
    history (which SQLite doesn't have) gets a chance to answer instead."""
    q = question.lower()
    if any(kw in q for kw in HISTORY_KEYWORDS):
        return None

    matched_status = None
    for kw, status in STATUS_KEYWORDS.items():
        if kw in q:
            matched_status = status
            break
    if not matched_status or "sensor" not in q:
        return None

    sensors = _rows(
        "SELECT s.name, c.name AS canal_name FROM sensors s "
        "LEFT JOIN canals c ON s.main_canal_id = c.id "
        "WHERE s.status = ?",
        (matched_status,),
    )
    if not sensors:
        return f"No sensors currently have status '{matched_status}'."

    listed = ", ".join(
        f"{s['name']} ({s['canal_name']})" if s.get("canal_name") else s["name"]
        for s in sensors
    )
    return f"{len(sensors)} sensor(s) are currently '{matched_status}': {listed}."


def _direct_sensor_report(question: str) -> Optional[str]:
    """Fast, exact 'report of all sensors' answer built straight from
    SQLite. Similarity search alone isn't reliable for this: with 100+
    activity-log documents in the collection, a generic 'sensors report'
    query often surfaces mostly log entries instead of the sensors
    themselves, and it also blows up the context sent to the local model
    for no benefit. Steps aside for anything about history/trends so the
    full RAG pass (with external DB history) still handles those."""
    q = question.lower()
    if any(kw in q for kw in HISTORY_KEYWORDS):
        return None
    if "sensor" not in q:
        return None
    if not any(kw in q for kw in ("report", "summary", "overview", "all sensor", "list sensor")):
        return None

    sensors = _rows(
        "SELECT s.*, c.name AS canal_name, lc.name AS link_name FROM sensors s "
        "LEFT JOIN canals c ON s.main_canal_id = c.id "
        "LEFT JOIN link_canals lc ON s.link_canal_id = lc.id "
        "ORDER BY s.name"
    )
    if not sensors:
        return "There are no sensors in the system yet."

    lines = [f"Sensor report - {len(sensors)} sensor(s):"]
    for s in sensors:
        if s.get("canal_name"):
            location = f"canal '{s['canal_name']}'"
            if s.get("link_name"):
                location += f", link '{s['link_name']}'"
        else:
            location = "unassigned"
        lines.append(
            f"- {s['name']} ({location}): status '{s['status']}', "
            f"water_level={s['water_level']}, flow_rate={s['flow_rate']}, "
            f"last seen {s['last_seen']}."
        )
    return "\n".join(lines)


def answer_query(question: str) -> Dict:
    """Full RAG pipeline: refresh knowledge base -> embed + retrieve ->
    generate a grounded answer with the chat model."""
    question = (question or "").strip()
    if not question:
        return {"answer": "Please ask a question.", "sources": []}

    refresh_index()

    direct = _direct_status_answer(question)
    if direct is not None:
        return {"answer": direct, "sources": []}

    direct_report = _direct_sensor_report(question)
    if direct_report is not None:
        return {"answer": direct_report, "sources": []}

    q_lower = question.lower()
    broad = any(kw in q_lower for kw in ("summary", "report", "overview", "all sensor"))
    # Cap broad queries instead of dumping the entire collection (which was
    # timing out the local model on an 8GB machine) - top_k=40 comfortably
    # covers most broad questions without a huge context.
    top_k = min(_collection.count(), 40) if broad else TOP_K
    docs = retrieve(question, top_k=top_k)
    answer = _generate_answer(question, docs)
    return {"answer": answer, "sources": docs}
