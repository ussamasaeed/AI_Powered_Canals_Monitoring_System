"""
Canal Monitoring System - Backend
----------------------------------
FastAPI + SQLite backend that serves the web interface and provides a
REST API for canals, link canals and sensors.

Run:
    pip install -r requirements.txt
    uvicorn app:app --reload

Then open http://127.0.0.1:8000/
"""

import os
import sqlite3
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# Optional external-database drivers. The app runs fine on SQLite alone if
# these aren't installed; they're only needed when the user actually
# connects to a real PostgreSQL / MySQL server from the "Connect Database"
# screen.
try:
    import psycopg2
    import psycopg2.extensions
except ImportError:
    psycopg2 = None

try:
    import mysql.connector
except ImportError:
    mysql = None

try:
    import chatbot
except ImportError:
    chatbot = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "canal_monitoring.db")

app = FastAPI(title="Canal Monitoring System")

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Cache-busting query param for static JS/CSS - changes whenever those
# files are edited, so browsers always fetch the latest version instead of
# serving a stale cached script (e.g. an old chatbot.js missing new buttons).
def _asset_version() -> str:
    try:
        js = os.path.join(BASE_DIR, "static", "js", "chatbot.js")
        css = os.path.join(BASE_DIR, "static", "css", "style.css")
        return str(int(max(os.path.getmtime(js), os.path.getmtime(css))))
    except OSError:
        return "1"

templates.env.globals["asset_version"] = _asset_version()


# ----------------------------------------------------------------------
# Database helpers
# ----------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 8000")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA busy_timeout = 8000")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS canals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            x1 REAL DEFAULT 60, y1 REAL DEFAULT 200,
            x2 REAL DEFAULT 520, y2 REAL DEFAULT 200,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS link_canals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            main_canal_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            x1 REAL DEFAULT 300, y1 REAL DEFAULT 200,
            x2 REAL DEFAULT 300, y2 REAL DEFAULT 380,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (main_canal_id) REFERENCES canals(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS sensors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sensor_type TEXT NOT NULL,           -- 'Canal' or 'Water Course'
            main_canal_id INTEGER,               -- NULL while sitting unassigned in the tray
            link_canal_id INTEGER,               -- NULL if placed directly on main canal
            width REAL NOT NULL,
            depth REAL NOT NULL,
            sensor_mount_height REAL DEFAULT 0,   -- height of the sensor above the empty canal bed
            distance_measured REAL DEFAULT 0,     -- raw distance reading from the sensor to the water surface
            velocity REAL DEFAULT 0,              -- raw velocity reading from the sensor
            water_level REAL DEFAULT 0,
            flow_rate REAL DEFAULT 0,
            threshold REAL DEFAULT 1.5,           -- per-sensor low-water threshold
            pos_ratio REAL DEFAULT 0.5,           -- position along the line (0-1) for map view
            status TEXT DEFAULT 'ok',             -- ok | warning | dead
            last_seen TEXT DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (main_canal_id) REFERENCES canals(id) ON DELETE CASCADE,
            FOREIGN KEY (link_canal_id) REFERENCES link_canals(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS db_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            db_type TEXT NOT NULL,
            host TEXT, port TEXT, username TEXT,
            connected INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.commit()

    # ---- migration: remember the password too (encrypted-at-rest would be
    # nicer, but this stays consistent with the rest of the app which is a
    # single-user local tool) so a real external connection can be silently
    # restored the next time the app starts, instead of forcing the user to
    # type it in again every session.
    dbc_cols = {c["name"] for c in db.execute("PRAGMA table_info(db_connections)").fetchall()}
    if "password" not in dbc_cols:
        db.execute("ALTER TABLE db_connections ADD COLUMN password TEXT")
        db.commit()

    # ---- migration: older DBs had sensors.main_canal_id as NOT NULL.
    # Rebuild the table (preserving data) so sensors can sit unassigned
    # in the map tray until they are dragged onto a canal.
    col = db.execute("PRAGMA table_info(sensors)").fetchall()
    main_canal_col = next((c for c in col if c["name"] == "main_canal_id"), None)
    if main_canal_col is not None and main_canal_col["notnull"] == 1:
        db.executescript(
            """
            ALTER TABLE sensors RENAME TO sensors_old;
            CREATE TABLE sensors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sensor_type TEXT NOT NULL,
                main_canal_id INTEGER,
                link_canal_id INTEGER,
                width REAL NOT NULL,
                depth REAL NOT NULL,
                water_level REAL DEFAULT 0,
                flow_rate REAL DEFAULT 0,
                pos_ratio REAL DEFAULT 0.5,
                status TEXT DEFAULT 'ok',
                last_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (main_canal_id) REFERENCES canals(id) ON DELETE CASCADE,
                FOREIGN KEY (link_canal_id) REFERENCES link_canals(id) ON DELETE CASCADE
            );
            INSERT INTO sensors SELECT * FROM sensors_old;
            DROP TABLE sensors_old;
            """
        )
        db.commit()

    # ---- migration: add the flow-rate / threshold columns to older DBs
    # that were created before this feature existed.
    existing_cols = {c["name"] for c in db.execute("PRAGMA table_info(sensors)").fetchall()}
    new_cols = {
        "sensor_mount_height": "REAL DEFAULT 0",
        "distance_measured": "REAL DEFAULT 0",
        "velocity": "REAL DEFAULT 0",
        "threshold": "REAL DEFAULT 1.5",
    }
    for col_name, col_def in new_cols.items():
        if col_name not in existing_cols:
            db.execute(f"ALTER TABLE sensors ADD COLUMN {col_name} {col_def}")
    db.commit()

    # seed sample data only if empty
    cur = db.execute("SELECT COUNT(*) AS c FROM canals")
    if cur.fetchone()["c"] == 0:
        db.execute(
            "INSERT INTO canals (name, x1, y1, x2, y2) VALUES (?, ?, ?, ?, ?)",
            ("Main Canal - North", 60, 160, 640, 160),
        )
        main_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        db.execute(
            "INSERT INTO link_canals (main_canal_id, name, x1, y1, x2, y2) VALUES (?, ?, ?, ?, ?, ?)",
            (main_id, "Link Canal - East", 380, 160, 380, 420),
        )
        link_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        db.execute(
            """INSERT INTO sensors
               (name, sensor_type, main_canal_id, link_canal_id, width, depth,
                water_level, flow_rate, pos_ratio, status)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("S-101", "Canal", main_id, None, 12.5, 4.2, 3.1, 22.4, 0.25, "ok"),
        )
        db.execute(
            """INSERT INTO sensors
               (name, sensor_type, main_canal_id, link_canal_id, width, depth,
                water_level, flow_rate, pos_ratio, status)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("S-102", "Canal", main_id, None, 12.5, 4.2, 1.1, 6.2, 0.65, "warning"),
        )
        db.execute(
            """INSERT INTO sensors
               (name, sensor_type, main_canal_id, link_canal_id, width, depth,
                water_level, flow_rate, pos_ratio, status)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("S-201", "Water Course", main_id, link_id, 5.0, 2.1, 0.0, 0.0, 0.5, "dead"),
        )
        db.execute(
            """INSERT INTO sensors
               (name, sensor_type, main_canal_id, link_canal_id, width, depth,
                water_level, flow_rate, pos_ratio, status)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("S-301", "Canal", None, None, 8.0, 3.0, 2.4, 14.0, 0.5, "ok"),
        )
        db.commit()
    db.close()


def now_str():
    """Current time on this machine (the laptop running the app), not any
    timestamp coming from sensor data. SQLite's own CURRENT_TIMESTAMP is UTC,
    so we stamp rows with the local system clock instead."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_action(db, action, details=""):
    ts = now_str()
    db.execute(
        "INSERT INTO logs (action, details, created_at) VALUES (?, ?, ?)",
        (action, details, ts),
    )
    db.commit()
    try:
        write_external_log(action, details, ts)
    except Exception:
        # Never let a log-mirroring failure break the actual app action.
        pass


# threshold rules used for computing status from readings
LOW_LEVEL_THRESHOLD = 1.5  # fallback, used until a sensor gets its own threshold


def compute_status(water_level, last_seen, current_status, threshold=None):
    """Recompute status unless it has been manually forced to 'dead'."""
    if current_status == "dead":
        return "dead"
    if water_level is None:
        return "dead"
    t = threshold if threshold is not None else LOW_LEVEL_THRESHOLD
    if water_level < t:
        return "warning"
    return "ok"


def compute_flow(width, depth, sensor_mount_height, distance_measured, velocity):
    """Flow_rate = Area * Velocity, where:
    water_depth = (Empty_canal_depth + sensor_mount_height) - Distance_measured_by_sensor
    Area = canal_width * water_depth
    Velocity and Distance_measured_by_sensor come directly from the sensor readings;
    sensor_mount_height is set when the sensor is added/modified.
    """
    width = width or 0
    depth = depth or 0
    sensor_mount_height = sensor_mount_height or 0
    distance_measured = distance_measured or 0
    velocity = velocity or 0

    water_depth = (depth + sensor_mount_height) - distance_measured
    water_depth = max(0.0, water_depth)
    area = width * water_depth
    flow_rate = area * velocity
    return round(water_depth, 3), round(flow_rate, 3)


def _sanitize_name(name: str) -> str:
    """Turn a canal/sensor name into a safe database/table name fragment:
    letters, digits and underscores only, spaces collapsed to underscores."""
    cleaned = "".join(ch if (ch.isalnum() or ch in ("_", " ")) else "_" for ch in (name or "").strip())
    return "_".join(cleaned.split())


def canal_db_name(main_canal_name: str) -> str:
    """Each main canal gets its own database, named after the canal itself.
    Example: main canal "South Canal" -> database "South_Canal"."""
    return _sanitize_name(main_canal_name)


# All activity-log entries (Add Canal, Modify Sensor, Connect Database, etc.)
# are mirrored into this single, separate database on the external server -
# it holds the project's log, not any canal's sensor readings.
LOG_DB_NAME = "Canal_Monitoring_Log"
LOG_TABLE_NAME = "activity_log"


def sensor_table_name(sensor_name: str, link_canal_name: Optional[str] = None) -> str:
    """Table name for a sensor inside its main canal's database.
    - Sensor installed on a link canal -> "<LinkCanalName>_<SensorName>"
    - Sensor installed directly on the main canal -> "_<SensorName>"
    """
    sensor_part = _sanitize_name(sensor_name)
    if link_canal_name:
        return f"{_sanitize_name(link_canal_name)}_{sensor_part}"
    return f"_{sensor_part}"


# ----------------------------------------------------------------------
# Real external database connection (PostgreSQL / MySQL)
# ----------------------------------------------------------------------
# In-memory record of the currently connected external server. Nothing here
# is faked: EXTERNAL_DB is only populated after a real connection to the
# real host/port/user/password has succeeded.
EXTERNAL_DB = {
    "connected": False,
    "db_type": None,
    "host": None,
    "port": None,
    "username": None,
    "password": None,
}


def _external_server_connect(db_type, host, port, username, password, database=None):
    """Open a real connection to the external server. Raises an exception
    with a real driver error message if the host/port/credentials are wrong
    or the server is unreachable - it never pretends to succeed."""
    # Treat an empty/blank password the same as "no password given" so the
    # driver falls back to the server's own auth (trust/peer/.pgpass/etc.)
    # instead of literally trying to authenticate with an empty string.
    password = password or None

    if db_type == "PostgreSQL":
        if psycopg2 is None:
            raise RuntimeError(
                "psycopg2 is not installed. Run: pip install psycopg2-binary"
            )
        return psycopg2.connect(
            host=host, port=int(port) if port else 5432,
            user=username, password=password,
            dbname=database or "postgres",
            connect_timeout=5,
        )
    elif db_type == "MySQL":
        if mysql is None:
            raise RuntimeError(
                "mysql-connector-python is not installed. Run: pip install mysql-connector-python"
            )
        return mysql.connector.connect(
            host=host, port=int(port) if port else 3306,
            user=username, password=password,
            database=database,
            connection_timeout=5,
        )
    else:
        raise RuntimeError(f"Unsupported db_type for a real connection: {db_type}")


def test_external_connection(db_type, host, port, username, password):
    """Actually attempt to reach the server with the given credentials.
    Raises with a real error message on failure."""
    conn = _external_server_connect(db_type, host, port, username, password)
    conn.close()


def ensure_external_database(db_type, host, port, username, password, db_name):
    """Create the per-canal database on the real server if it doesn't exist yet."""
    conn = _external_server_connect(db_type, host, port, username, password)
    try:
        if db_type == "PostgreSQL":
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{db_name}"')
            cur.close()
        elif db_type == "MySQL":
            cur = conn.cursor()
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}`")
            cur.close()
    finally:
        conn.close()


def ensure_external_table(db_type, host, port, username, password, db_name, table_name):
    """Create the per-sensor readings table inside its canal's database if it
    doesn't exist yet."""
    conn = _external_server_connect(db_type, host, port, username, password, database=db_name)
    try:
        cur = conn.cursor()
        if db_type == "PostgreSQL":
            cur.execute(
                f'''CREATE TABLE IF NOT EXISTS "{table_name}" (
                        id SERIAL PRIMARY KEY,
                        recorded_at TIMESTAMP NOT NULL,
                        velocity DOUBLE PRECISION,
                        distance_measured DOUBLE PRECISION,
                        water_level DOUBLE PRECISION,
                        flow_rate DOUBLE PRECISION,
                        status TEXT
                    )'''
            )
            conn.commit()
        elif db_type == "MySQL":
            cur.execute(
                f"""CREATE TABLE IF NOT EXISTS `{table_name}` (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        recorded_at DATETIME NOT NULL,
                        velocity DOUBLE,
                        distance_measured DOUBLE,
                        water_level DOUBLE,
                        flow_rate DOUBLE,
                        status VARCHAR(20)
                    )"""
            )
            conn.commit()
        cur.close()
    finally:
        conn.close()


def ensure_external_log_table(db_type, host, port, username, password):
    """Create the dedicated log database/table on the external server if it
    doesn't exist yet."""
    ensure_external_database(db_type, host, port, username, password, LOG_DB_NAME)
    conn = _external_server_connect(db_type, host, port, username, password, database=LOG_DB_NAME)
    try:
        cur = conn.cursor()
        if db_type == "PostgreSQL":
            cur.execute(
                f'''CREATE TABLE IF NOT EXISTS "{LOG_TABLE_NAME}" (
                        id SERIAL PRIMARY KEY,
                        action TEXT,
                        details TEXT,
                        created_at TIMESTAMP NOT NULL
                    )'''
            )
            conn.commit()
        elif db_type == "MySQL":
            cur.execute(
                f"""CREATE TABLE IF NOT EXISTS `{LOG_TABLE_NAME}` (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        action VARCHAR(255),
                        details TEXT,
                        created_at DATETIME NOT NULL
                    )"""
            )
            conn.commit()
        cur.close()
    finally:
        conn.close()


def write_external_log(action, details, created_at):
    """Insert one row into the dedicated Canal_Monitoring_Log database.
    Silently does nothing if no external server is connected."""
    if not EXTERNAL_DB["connected"]:
        return
    db_type = EXTERNAL_DB["db_type"]
    ensure_external_log_table(db_type, EXTERNAL_DB["host"], EXTERNAL_DB["port"],
                               EXTERNAL_DB["username"], EXTERNAL_DB["password"])
    conn = _external_server_connect(db_type, EXTERNAL_DB["host"], EXTERNAL_DB["port"],
                                     EXTERNAL_DB["username"], EXTERNAL_DB["password"], database=LOG_DB_NAME)
    try:
        cur = conn.cursor()
        cur.execute(
            f'INSERT INTO "{LOG_TABLE_NAME}" (action, details, created_at) VALUES (%s,%s,%s)'
            if db_type == "PostgreSQL" else
            f"INSERT INTO `{LOG_TABLE_NAME}` (action, details, created_at) VALUES (%s,%s,%s)",
            (action, details, created_at),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def sync_all_external_databases():
    """Walk every main canal / sensor and make sure the matching database
    and table exist on the connected external server. Called right after a
    successful connect, and again whenever a canal or sensor is added."""
    if not EXTERNAL_DB["connected"]:
        return []
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    created = []
    try:
        # The project-wide activity log always gets its own database.
        ensure_external_log_table(EXTERNAL_DB["db_type"], EXTERNAL_DB["host"], EXTERNAL_DB["port"],
                                   EXTERNAL_DB["username"], EXTERNAL_DB["password"])
        created.append(f"{LOG_DB_NAME}.{LOG_TABLE_NAME}")

        canals = db.execute("SELECT id, name FROM canals ORDER BY name").fetchall()
        for c in canals:
            db_name = canal_db_name(c["name"])
            ensure_external_database(
                EXTERNAL_DB["db_type"], EXTERNAL_DB["host"], EXTERNAL_DB["port"],
                EXTERNAL_DB["username"], EXTERNAL_DB["password"], db_name,
            )
            sensors = db.execute(
                """SELECT s.name AS sensor_name, lc.name AS link_name
                   FROM sensors s LEFT JOIN link_canals lc ON s.link_canal_id = lc.id
                   WHERE s.main_canal_id = ?""",
                (c["id"],),
            ).fetchall()
            for s in sensors:
                table_name = sensor_table_name(s["sensor_name"], s["link_name"])
                ensure_external_table(
                    EXTERNAL_DB["db_type"], EXTERNAL_DB["host"], EXTERNAL_DB["port"],
                    EXTERNAL_DB["username"], EXTERNAL_DB["password"], db_name, table_name,
                )
                created.append(f"{db_name}.{table_name}")
    finally:
        db.close()
    return created


def write_external_reading(main_canal_name, sensor_name, link_canal_name,
                            velocity, distance_measured, water_level, flow_rate, status):
    """Insert one reading row into the sensor's real table. Silently does
    nothing if no external server is connected (SQLite-only mode)."""
    if not EXTERNAL_DB["connected"] or not main_canal_name:
        return
    db_name = canal_db_name(main_canal_name)
    table_name = sensor_table_name(sensor_name, link_canal_name)
    db_type = EXTERNAL_DB["db_type"]
    ensure_external_database(db_type, EXTERNAL_DB["host"], EXTERNAL_DB["port"],
                              EXTERNAL_DB["username"], EXTERNAL_DB["password"], db_name)
    ensure_external_table(db_type, EXTERNAL_DB["host"], EXTERNAL_DB["port"],
                           EXTERNAL_DB["username"], EXTERNAL_DB["password"], db_name, table_name)
    conn = _external_server_connect(db_type, EXTERNAL_DB["host"], EXTERNAL_DB["port"],
                                     EXTERNAL_DB["username"], EXTERNAL_DB["password"], database=db_name)
    try:
        cur = conn.cursor()
        placeholder = "%s"
        cur.execute(
            f'''INSERT INTO "{table_name}" (recorded_at, velocity, distance_measured, water_level, flow_rate, status)
                VALUES ({placeholder},{placeholder},{placeholder},{placeholder},{placeholder},{placeholder})'''
            if db_type == "PostgreSQL" else
            f"""INSERT INTO `{table_name}` (recorded_at, velocity, distance_measured, water_level, flow_rate, status)
                VALUES (%s,%s,%s,%s,%s,%s)""",
            (now_str(), velocity, distance_measured, water_level, flow_rate, status),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


init_db()


def _restore_saved_external_connection():
    """On app startup, silently reconnect to whichever external database
    was last connected and never explicitly disconnected - so a connection
    made once stays in effect (across restarts) until the user removes it."""
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute(
            "SELECT * FROM db_connections WHERE connected = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row or row["db_type"] not in ("PostgreSQL", "MySQL"):
            return
        try:
            test_external_connection(row["db_type"], row["host"], row["port"],
                                      row["username"], row["password"])
        except Exception:
            # Server unreachable at startup - leave EXTERNAL_DB disconnected;
            # the user can reconnect manually from "Connect Database".
            return
        EXTERNAL_DB.update(
            connected=True, db_type=row["db_type"], host=row["host"], port=row["port"],
            username=row["username"], password=row["password"],
        )
        try:
            sync_all_external_databases()
        except Exception:
            pass
    finally:
        db.close()


_restore_saved_external_connection()


# ----------------------------------------------------------------------
# Pydantic request models
# ----------------------------------------------------------------------
class CanalIn(BaseModel):
    name: str


class LinkCanalIn(BaseModel):
    main_canal_id: int
    name: str


class LinkCanalUpdate(BaseModel):
    main_canal_id: Optional[int] = None
    name: Optional[str] = None
    x1: Optional[float] = None
    y1: Optional[float] = None
    x2: Optional[float] = None
    y2: Optional[float] = None


class SensorIn(BaseModel):
    name: str
    sensor_type: str
    main_canal_id: Optional[int] = None
    link_canal_id: Optional[int] = None
    width: float
    depth: float
    sensor_mount_height: float = 0


class SensorUpdate(BaseModel):
    name: Optional[str] = None
    sensor_type: Optional[str] = None
    main_canal_id: Optional[int] = None
    link_canal_id: Optional[int] = None
    width: Optional[float] = None
    depth: Optional[float] = None
    sensor_mount_height: Optional[float] = None
    distance_measured: Optional[float] = None
    velocity: Optional[float] = None
    threshold: Optional[float] = None
    pos_ratio: Optional[float] = None
    clear_main_canal: bool = False
    clear_link_canal: bool = False


class AutoThresholdIn(BaseModel):
    main_canal_id: int
    link_canal_id: Optional[int] = None


class SensorReadingItem(BaseModel):
    name: str
    velocity: Optional[float] = None
    distance_measured: Optional[float] = None


class SensorReadingsUpload(BaseModel):
    readings: List[SensorReadingItem]
    mode: str  # "threshold" -> save readings + set threshold from them
               # "check"     -> save readings + compare against existing threshold


class ChatIn(BaseModel):
    message: str


class DbConnectionIn(BaseModel):
    db_type: str
    host: Optional[str] = None
    port: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    # No db_name here on purpose: every main canal gets its own database,
    # named after the canal itself (see canal_db_name()). The user never
    # types a database name manually.


# ----------------------------------------------------------------------
# Page routes
# ----------------------------------------------------------------------
@app.get("/", name="home")
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"active": "home"})


@app.get("/map", name="map_page")
def map_page(request: Request):
    return templates.TemplateResponse(request, "map.html", {"active": "map"})


@app.get("/field", name="field_view")
def field_view():
    """Mobile, read-only status page for use out at the canal.

    Served as a plain static file (no template context) so it stays fully
    self-contained and independent of the desktop UI. It only ever calls
    GET /api/sensors, so it cannot modify any data.
    """
    return FileResponse(os.path.join(BASE_DIR, "static", "field.html"))


# ----------------------------------------------------------------------
# API: Canals
# ----------------------------------------------------------------------
@app.get("/api/canals")
def api_get_canals(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM canals ORDER BY name").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/canals", status_code=201)
def api_add_canal(payload: CanalIn, db: sqlite3.Connection = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Canal name is required")
    try:
        db.execute("INSERT INTO canals (name, created_at) VALUES (?, ?)", (name, now_str()))
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="A canal with this name already exists")

    if EXTERNAL_DB["connected"]:
        try:
            ensure_external_database(EXTERNAL_DB["db_type"], EXTERNAL_DB["host"], EXTERNAL_DB["port"],
                                      EXTERNAL_DB["username"], EXTERNAL_DB["password"], canal_db_name(name))
        except Exception as exc:
            log_action(db, "External DB Create Failed", f"{name}: {exc}")

    log_action(db, "Add Canal", name)
    return {"message": "Canal added", "name": name}


@app.delete("/api/canals/{canal_id}")
def api_delete_canal(canal_id: int, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT name FROM canals WHERE id=?", (canal_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Canal not found")
    db.execute("DELETE FROM canals WHERE id=?", (canal_id,))
    db.commit()
    log_action(db, "Delete Canal", row["name"])
    return {"message": "Canal deleted"}


# ----------------------------------------------------------------------
# API: Link canals
# ----------------------------------------------------------------------
@app.get("/api/link-canals")
def api_get_link_canals(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        """SELECT lc.*, c.name AS main_canal_name
           FROM link_canals lc JOIN canals c ON lc.main_canal_id = c.id
           ORDER BY lc.name"""
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/link-canals", status_code=201)
def api_add_link_canal(payload: LinkCanalIn, db: sqlite3.Connection = Depends(get_db)):
    name = payload.name.strip()
    if not payload.main_canal_id or not name:
        raise HTTPException(status_code=400, detail="Main canal and link canal name are required")
    db.execute(
        "INSERT INTO link_canals (main_canal_id, name, created_at) VALUES (?, ?, ?)",
        (payload.main_canal_id, name, now_str()),
    )
    db.commit()
    log_action(db, "Add Link Canal", name)
    return {"message": "Link canal added"}


@app.put("/api/link-canals/{link_id}")
def api_update_link_canal(link_id: int, payload: LinkCanalUpdate, db: sqlite3.Connection = Depends(get_db)):
    """Used by the map's drag-and-drop editor to reposition a link canal:
    dragging the branch point slides it left/right along the main canal,
    dragging the free end extends it up or down from the main canal."""
    row = db.execute("SELECT * FROM link_canals WHERE id=?", (link_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Link canal not found")

    fields = ["main_canal_id", "name", "x1", "y1", "x2", "y2"]
    data = payload.dict(exclude_unset=True)
    updates, values = [], []
    for f in fields:
        if f in data and data[f] is not None:
            updates.append(f"{f} = ?")
            values.append(data[f])
    if updates:
        values.append(link_id)
        db.execute(f"UPDATE link_canals SET {', '.join(updates)} WHERE id = ?", values)
        db.commit()
    log_action(db, "Move Link Canal", row["name"])
    return {"message": "Link canal updated"}


@app.delete("/api/link-canals/{link_id}")
def api_delete_link_canal(link_id: int, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT name FROM link_canals WHERE id=?", (link_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Link canal not found")
    db.execute("DELETE FROM link_canals WHERE id=?", (link_id,))
    db.commit()
    log_action(db, "Delete Link Canal", row["name"])
    return {"message": "Link canal deleted"}


# ----------------------------------------------------------------------
# API: Sensors
# ----------------------------------------------------------------------
@app.get("/api/sensors")
def api_get_sensors(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        """SELECT s.*, c.name AS canal_name, lc.name AS link_name
           FROM sensors s
           LEFT JOIN canals c ON s.main_canal_id = c.id
           LEFT JOIN link_canals lc ON s.link_canal_id = lc.id
           ORDER BY s.name"""
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["status"] = compute_status(d["water_level"], d["last_seen"], d["status"], d.get("threshold"))
        # Where this sensor's readings will be stored: one database per main
        # canal, one table per sensor.
        if d.get("canal_name"):
            d["db_name"] = canal_db_name(d["canal_name"])
            d["table_name"] = sensor_table_name(d["name"], d.get("link_name"))
        else:
            d["db_name"] = None
            d["table_name"] = None
        result.append(d)
    return result


@app.post("/api/sensors", status_code=201)
def api_add_sensor(payload: SensorIn, db: sqlite3.Connection = Depends(get_db)):
    name = payload.name.strip()
    if not name or not payload.sensor_type or payload.width is None or payload.depth is None:
        raise HTTPException(status_code=400, detail="Name, type, width and depth are required")

    # No physical sensor hardware is connected yet - Velocity and
    # Distance_measured_sensor only arrive later via an uploaded JSON
    # reading file (see /api/sensors/upload-readings). Until that happens,
    # the sensor has no real data to report, so it starts out "dead".
    db.execute(
        """INSERT INTO sensors
           (name, sensor_type, main_canal_id, link_canal_id, width, depth,
            sensor_mount_height, distance_measured, velocity,
            water_level, flow_rate, threshold, status, last_seen, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'dead', ?, ?)""",
        (name, payload.sensor_type, payload.main_canal_id, payload.link_canal_id,
         payload.width, payload.depth, payload.sensor_mount_height,
         None, None, 0, 0, LOW_LEVEL_THRESHOLD, now_str(), now_str()),
    )
    db.commit()

    if EXTERNAL_DB["connected"] and payload.main_canal_id:
        canal_row = db.execute("SELECT name FROM canals WHERE id=?", (payload.main_canal_id,)).fetchone()
        link_row = db.execute("SELECT name FROM link_canals WHERE id=?", (payload.link_canal_id,)).fetchone()
        if canal_row:
            try:
                db_name = canal_db_name(canal_row["name"])
                ensure_external_database(EXTERNAL_DB["db_type"], EXTERNAL_DB["host"], EXTERNAL_DB["port"],
                                          EXTERNAL_DB["username"], EXTERNAL_DB["password"], db_name)
                table_name = sensor_table_name(name, link_row["name"] if link_row else None)
                ensure_external_table(EXTERNAL_DB["db_type"], EXTERNAL_DB["host"], EXTERNAL_DB["port"],
                                       EXTERNAL_DB["username"], EXTERNAL_DB["password"], db_name, table_name)
            except Exception as exc:
                log_action(db, "External DB Create Failed", f"{name}: {exc}")

    log_action(db, "Add Sensor", name)
    return {"message": "Sensor added"}


@app.put("/api/sensors/{sensor_id}")
def api_update_sensor(sensor_id: int, payload: SensorUpdate, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM sensors WHERE id=?", (sensor_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Sensor not found")

    # Note: "status" is intentionally excluded here - it is never set manually.
    # It is always derived automatically from incoming sensor data
    # (see compute_status() and /api/sensors/upload-readings).
    fields = ["name", "sensor_type", "main_canal_id", "link_canal_id",
              "width", "depth", "sensor_mount_height", "distance_measured",
              "velocity", "threshold", "pos_ratio"]
    data = payload.dict(exclude_unset=True)
    merged = dict(row)
    updates, values = [], []
    for f in fields:
        if f in data and data[f] is not None:
            merged[f] = data[f]
            updates.append(f"{f} = ?")
            values.append(data[f])

    # Recompute Flow_rate = Area * Velocity whenever any input to that
    # formula (width/depth/mount height/distance/velocity) changes.
    flow_inputs = {"width", "depth", "sensor_mount_height", "distance_measured", "velocity"}
    if flow_inputs & set(data.keys()):
        water_level, flow_rate = compute_flow(
            merged["width"], merged["depth"], merged["sensor_mount_height"],
            merged["distance_measured"], merged["velocity"],
        )
        updates += ["water_level = ?", "flow_rate = ?"]
        values += [water_level, flow_rate]

    # Explicit "uninstall" flags let the map's drag-and-drop send a clean
    # null even though a normal missing field is left untouched.
    if payload.clear_main_canal:
        updates.append("main_canal_id = ?")
        values.append(None)
    if payload.clear_link_canal:
        updates.append("link_canal_id = ?")
        values.append(None)

    if updates:
        updates.append("last_seen = ?")
        values.append(now_str())
        values.append(sensor_id)
        db.execute(f"UPDATE sensors SET {', '.join(updates)} WHERE id = ?", values)
        db.commit()

        # Mirror the reading into the real external database (if connected).
        if flow_inputs & set(data.keys()):
            canal_row = db.execute("SELECT name FROM canals WHERE id=?", (merged.get("main_canal_id"),)).fetchone()
            link_row = db.execute("SELECT name FROM link_canals WHERE id=?", (merged.get("link_canal_id"),)).fetchone()
            status = compute_status(water_level, now_str(), merged.get("status", "ok"), merged.get("threshold"))
            try:
                write_external_reading(
                    canal_row["name"] if canal_row else None, merged["name"],
                    link_row["name"] if link_row else None,
                    merged.get("velocity"), merged.get("distance_measured"),
                    water_level, flow_rate, status,
                )
            except Exception as exc:
                log_action(db, "External DB Write Failed", f"{merged['name']}: {exc}")

    log_action(db, "Modify Sensor", row["name"])
    return {"message": "Sensor updated"}


@app.post("/api/sensors/auto-threshold")
def api_auto_threshold(payload: AutoThresholdIn, db: sqlite3.Connection = Depends(get_db)):
    """Set each matching sensor's threshold to its current water level reading.
    Applied per-sensor, separately, for every sensor in the chosen scope:
    a single link canal if one is given, otherwise every sensor on the
    given main canal (including ones installed on its link canals)."""
    if payload.link_canal_id:
        rows = db.execute(
            "SELECT id, water_level FROM sensors WHERE link_canal_id = ?",
            (payload.link_canal_id,),
        ).fetchall()
        scope = f"link canal #{payload.link_canal_id}"
    else:
        rows = db.execute(
            "SELECT id, water_level FROM sensors WHERE main_canal_id = ?",
            (payload.main_canal_id,),
        ).fetchall()
        scope = f"main canal #{payload.main_canal_id}"

    for r in rows:
        db.execute(
            "UPDATE sensors SET threshold = ? WHERE id = ?",
            (r["water_level"], r["id"]),
        )
    db.commit()
    log_action(db, "Auto Set Threshold", f"{len(rows)} sensor(s) on {scope}")
    return {"message": f"Threshold set for {len(rows)} sensor(s)", "count": len(rows)}


@app.post("/api/sensors/upload-readings")
def api_upload_readings(payload: SensorReadingsUpload, db: sqlite3.Connection = Depends(get_db)):
    """Feed Velocity / Distance_measured_sensor readings from an uploaded JSON
    file, since no physical sensors are wired up yet.

    mode = "threshold": save the readings for each matching sensor, recompute
      its water level, and set that water level as the sensor's own low-water
      threshold (same effect as Auto Set Threshold, but sourced from a file).
    mode = "check": save the readings for each matching sensor, recompute its
      water level, and compare it against the threshold already on file,
      raising a low-water (warning) status when the level has dropped below it.

    In both modes, any sensor whose reading is missing Velocity or
    Distance_measured_sensor is marked "dead" instead, since it means that
    sensor did not report usable data.
    """
    matched = 0
    dead_count = 0
    warning_count = 0
    unmatched = []

    for item in payload.readings:
        row = db.execute("SELECT * FROM sensors WHERE name = ?", (item.name,)).fetchone()
        if not row:
            unmatched.append(item.name)
            continue
        matched += 1
        merged = dict(row)

        if item.velocity is None or item.distance_measured is None:
            # Sensor reported without Velocity and/or Distance_measured_sensor -
            # treat it as offline/faulty rather than guessing values.
            db.execute(
                "UPDATE sensors SET status = 'dead', last_seen = ? WHERE id = ?",
                (now_str(), row["id"]),
            )
            dead_count += 1
            canal_row = db.execute("SELECT name FROM canals WHERE id=?", (merged.get("main_canal_id"),)).fetchone()
            link_row = db.execute("SELECT name FROM link_canals WHERE id=?", (merged.get("link_canal_id"),)).fetchone()
            try:
                write_external_reading(
                    canal_row["name"] if canal_row else None, merged["name"],
                    link_row["name"] if link_row else None,
                    item.velocity, item.distance_measured, merged.get("water_level"), merged.get("flow_rate"),
                    "dead",
                )
            except Exception as exc:
                log_action(db, "External DB Write Failed", f"{merged['name']}: {exc}")
            continue

        water_level, flow_rate = compute_flow(
            merged["width"], merged["depth"], merged["sensor_mount_height"],
            item.distance_measured, item.velocity,
        )

        threshold_value = merged["threshold"]
        new_status = "ok"
        if payload.mode == "check" and water_level < (threshold_value if threshold_value is not None else LOW_LEVEL_THRESHOLD):
            new_status = "warning"
            warning_count += 1

        db.execute(
            """UPDATE sensors
               SET velocity = ?, distance_measured = ?, water_level = ?, flow_rate = ?,
                   status = ?, last_seen = ?
               WHERE id = ?""",
            (item.velocity, item.distance_measured, water_level, flow_rate, new_status, now_str(), row["id"]),
        )

        if payload.mode == "threshold":
            db.execute("UPDATE sensors SET threshold = ? WHERE id = ?", (water_level, row["id"]))

        canal_row = db.execute("SELECT name FROM canals WHERE id=?", (merged.get("main_canal_id"),)).fetchone()
        link_row = db.execute("SELECT name FROM link_canals WHERE id=?", (merged.get("link_canal_id"),)).fetchone()
        try:
            write_external_reading(
                canal_row["name"] if canal_row else None, merged["name"],
                link_row["name"] if link_row else None,
                item.velocity, item.distance_measured, water_level, flow_rate, new_status,
            )
        except Exception as exc:
            log_action(db, "External DB Write Failed", f"{merged['name']}: {exc}")

    db.commit()
    log_action(
        db, "Upload Sensor Readings",
        f"mode={payload.mode}, matched={matched}, dead={dead_count}, warning={warning_count}"
        + (f", unmatched={','.join(unmatched)}" if unmatched else ""),
    )

    if payload.mode == "threshold":
        message = f"Threshold set from {matched} sensor reading(s)"
        if dead_count:
            message += f", {dead_count} marked dead (missing data)"
    else:
        message = f"Checked {matched} sensor reading(s)"
        if warning_count:
            message += f" — {warning_count} low water warning(s)"
        if dead_count:
            message += f", {dead_count} marked dead (missing data)"

    return {
        "message": message,
        "matched": matched,
        "dead": dead_count,
        "warning": warning_count,
        "unmatched": unmatched,
    }


@app.delete("/api/sensors/{sensor_id}")
def api_delete_sensor(sensor_id: int, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT name FROM sensors WHERE id=?", (sensor_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Sensor not found")
    db.execute("DELETE FROM sensors WHERE id=?", (sensor_id,))
    db.commit()
    log_action(db, "Delete Sensor", row["name"])
    return {"message": "Sensor deleted"}


# ----------------------------------------------------------------------
# API: Logs
# ----------------------------------------------------------------------
@app.get("/api/logs")
def api_get_logs(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 200").fetchall()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------
# API: Chatbot (RAG over the app's own data)
# ----------------------------------------------------------------------
@app.post("/api/chat")
def api_chat(payload: ChatIn):
    if chatbot is None:
        raise HTTPException(
            status_code=503,
            detail="Chatbot dependencies aren't installed. Run: pip install -r requirements.txt",
        )
    try:
        return chatbot.answer_query(payload.message)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chatbot error: {exc}")


# ----------------------------------------------------------------------
# API: Database connection (config only - simulated)
# ----------------------------------------------------------------------
@app.get("/api/db-connection")
def api_get_db_connection(db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM db_connections ORDER BY id DESC LIMIT 1").fetchone()
    result = dict(row) if row else {}
    result.pop("password", None)  # never send the stored password back to the client
    # "connected" here reflects the real, live external connection in this
    # running process - not just a row saved in the log. When a real
    # external server is connected, report its actual live details (they
    # are the source of truth, e.g. right after startup auto-reconnect).
    result["live_connected"] = EXTERNAL_DB["connected"]
    if EXTERNAL_DB["connected"]:
        result["db_type"] = EXTERNAL_DB["db_type"]
        result["host"] = EXTERNAL_DB["host"]
        result["port"] = EXTERNAL_DB["port"]
        result["username"] = EXTERNAL_DB["username"]
    return result


@app.post("/api/db-connection", status_code=201)
def api_set_db_connection(payload: DbConnectionIn, db: sqlite3.Connection = Depends(get_db)):
    if payload.db_type == "SQLite":
        # SQLite is this app's own local database - already connected by
        # definition, nothing external to reach.
        EXTERNAL_DB.update(connected=False, db_type="SQLite", host=None, port=None,
                            username=None, password=None)
        db.execute(
            """INSERT INTO db_connections (db_type, host, port, username, password, connected, created_at)
               VALUES (?,?,?,?,?,1,?)""",
            (payload.db_type, payload.host, payload.port, payload.username, None, now_str()),
        )
        db.commit()
        log_action(db, "Connect Database", "SQLite (local, built-in)")
        return {"message": "Using the built-in local SQLite database", "databases": []}

    if payload.db_type not in ("PostgreSQL", "MySQL"):
        raise HTTPException(status_code=400, detail=f"Unsupported database type: {payload.db_type}")
    if not payload.host or not payload.username:
        raise HTTPException(status_code=400, detail="Host and username are required")

    # Actually try to reach the server with the given credentials. If this
    # fails, the person gets the real driver error back - no fake success.
    try:
        test_external_connection(payload.db_type, payload.host, payload.port,
                                  payload.username, payload.password)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not connect: {exc}")

    EXTERNAL_DB.update(
        connected=True, db_type=payload.db_type, host=payload.host, port=payload.port,
        username=payload.username, password=payload.password,
    )

    # Now that we know the server is really reachable, create/verify the
    # per-canal database and per-sensor tables on it.
    try:
        created = sync_all_external_databases()
    except Exception as exc:
        EXTERNAL_DB.update(connected=False)
        raise HTTPException(status_code=400, detail=f"Connected, but failed to create databases/tables: {exc}")

    db.execute(
        """INSERT INTO db_connections (db_type, host, port, username, password, connected, created_at)
           VALUES (?,?,?,?,?,1,?)""",
        (payload.db_type, payload.host, payload.port, payload.username, payload.password, now_str()),
    )
    db.commit()
    log_action(
        db, "Connect Database",
        f"{payload.db_type} @ {payload.host}:{payload.port} — tables: " + (", ".join(created) if created else "none yet"),
    )
    return {"message": f"Connected to {payload.db_type} @ {payload.host}", "databases": created}


@app.delete("/api/db-connection")
def api_delete_db_connection(db: sqlite3.Connection = Depends(get_db)):
    """Disconnect the currently connected external database (the 'Remove'
    button next to the active connection). Falls back to the built-in
    SQLite database - nothing about the app stops working."""
    if not EXTERNAL_DB["connected"]:
        raise HTTPException(status_code=400, detail="No external database is currently connected")

    details = f"{EXTERNAL_DB['db_type']} @ {EXTERNAL_DB['host']}:{EXTERNAL_DB['port']}"

    EXTERNAL_DB.update(connected=False, db_type=None, host=None, port=None,
                        username=None, password=None)

    # Mark the stored row as disconnected so a future app restart doesn't
    # try to silently reconnect to it again.
    db.execute("UPDATE db_connections SET connected = 0 WHERE connected = 1")
    db.commit()
    log_action(db, "Disconnect Database", details)
    return {"message": f"Disconnected from {details}"}


# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
