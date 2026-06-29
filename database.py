"""
database.py — Toda a persistência do app.
Tudo é salvo em SQLite em ~/.ondabinaural/data.db
Sobrevive a reinicializações do PC.
"""
import os, sqlite3, datetime, json

DB_DIR  = os.path.join(os.path.expanduser("~"), ".ondabinaural")
DB_PATH = os.path.join(DB_DIR, "data.db")
os.makedirs(DB_DIR, exist_ok=True)


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at    TEXT,
            ended_at      TEXT,
            date          TEXT,              -- YYYY-MM-DD
            hour          INTEGER,
            preset        TEXT,
            objective     TEXT,
            duration_sec  REAL,
            target_sec    REAL,
            completed     INTEGER,           -- 1 se atingiu o alvo
            base_hz       REAL,
            beat_hz       REAL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS favorites (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT,
            config     TEXT,                 -- JSON com tudo (timer, ondas, sons, volumes)
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS protocols (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT,
            config     TEXT,                 -- JSON
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS achievements (
            key        TEXT PRIMARY KEY,
            unlocked_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sound_usage (
            sound  TEXT PRIMARY KEY,
            secs   REAL DEFAULT 0
        );
        """)
    _seed_defaults()


def _seed_defaults():
    defaults = {
        "xp": "0",
        "daily_goal_min": "240",        # 4h
        "theme": "dark",
        "open_with_windows": "0",
        "invisible_mode": "0",
        "always_on_top": "1",
        "smart_pause": "1",
        "rest_reminders": "1",
        "last_session_config": "{}",
        "fav_sounds": "[]",
    }
    with _conn() as c:
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))


# ─────────────────────────────────────────────────────────────── settings ────
def get(key, default=None):
    with _conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

def get_int(key, default=0):
    try: return int(get(key, default))
    except: return default

def get_json(key, default=None):
    raw = get(key)
    if raw is None: return default
    try: return json.loads(raw)
    except: return default

def set(key, value):
    with _conn() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(value)))

def set_json(key, obj):
    set(key, json.dumps(obj, ensure_ascii=False))


# ─────────────────────────────────────────────────────────────── sessions ────
def save_session(started_at, preset, objective, duration_sec, target_sec,
                 completed, base_hz, beat_hz):
    now = datetime.datetime.now()
    with _conn() as c:
        c.execute("""INSERT INTO sessions
            (started_at,ended_at,date,hour,preset,objective,duration_sec,
             target_sec,completed,base_hz,beat_hz)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (started_at, now.strftime("%Y-%m-%d %H:%M:%S"),
             now.strftime("%Y-%m-%d"), now.hour, preset, objective,
             duration_sec, target_sec, int(bool(completed)), base_hz, beat_hz))

def recent_sessions(limit=200):
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM sessions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


# ─────────────────────────────────────────────────────────────── stats ───────
def stats_overview():
    with _conn() as c:
        today = datetime.date.today().strftime("%Y-%m-%d")
        # ranges
        def _sum(where, params=()):
            r = c.execute(f"SELECT COALESCE(SUM(duration_sec),0) s, COUNT(*) n "
                          f"FROM sessions {where}", params).fetchone()
            return r["s"], r["n"]

        week_start  = (datetime.date.today() -
                       datetime.timedelta(days=datetime.date.today().weekday())).strftime("%Y-%m-%d")
        month_start = datetime.date.today().replace(day=1).strftime("%Y-%m-%d")
        year_start  = datetime.date.today().replace(month=1, day=1).strftime("%Y-%m-%d")

        today_s, today_n   = _sum("WHERE date = ?", (today,))
        week_s,  week_n     = _sum("WHERE date >= ?", (week_start,))
        month_s, month_n    = _sum("WHERE date >= ?", (month_start,))
        year_s,  year_n     = _sum("WHERE date >= ?", (year_start,))
        all_s,   all_n      = _sum("")

        # biggest session
        big = c.execute("SELECT MAX(duration_sec) m FROM sessions").fetchone()["m"] or 0

        # completion rate (pomodoros com target)
        comp = c.execute("SELECT COUNT(*) c FROM sessions WHERE completed=1 AND target_sec>0").fetchone()["c"]
        with_target = c.execute("SELECT COUNT(*) c FROM sessions WHERE target_sec>0").fetchone()["c"]
        comp_rate = (comp / with_target * 100) if with_target else 0

        # average per active day
        days = c.execute("SELECT COUNT(DISTINCT date) d FROM sessions").fetchone()["d"] or 1
        avg_day = all_s / days

        # most productive hour
        ph = c.execute("""SELECT hour, SUM(duration_sec) s FROM sessions
                          GROUP BY hour ORDER BY s DESC LIMIT 1""").fetchone()
        peak_hour = ph["hour"] if ph else None

        # avg session length
        avg_sess = c.execute("SELECT COALESCE(AVG(duration_sec),0) a FROM sessions").fetchone()["a"]

        # favorite preset
        fp = c.execute("""SELECT preset, COUNT(*) n FROM sessions
                          GROUP BY preset ORDER BY n DESC LIMIT 1""").fetchone()
        fav_preset = fp["preset"] if fp else "—"

        return {
            "today_min":  round(today_s/60),  "today_n":  today_n,
            "week_min":   round(week_s/60),   "week_n":   week_n,
            "month_min":  round(month_s/60),  "month_n":  month_n,
            "year_min":   round(year_s/60),   "year_n":   year_n,
            "all_min":    round(all_s/60),    "all_n":    all_n,
            "all_hours":  round(all_s/3600,1),
            "biggest_min": round(big/60),
            "comp_rate":  round(comp_rate),
            "avg_day_min": round(avg_day/60),
            "peak_hour":  peak_hour,
            "avg_sess_min": round(avg_sess/60,1),
            "fav_preset": fav_preset,
            "pomodoros":  comp,
        }


def daily_series(days=30):
    """Retorna [(date_str, minutes), ...] dos últimos N dias (preenche zeros)."""
    with _conn() as c:
        rows = {r["date"]: r["s"] for r in c.execute(
            "SELECT date, SUM(duration_sec) s FROM sessions GROUP BY date").fetchall()}
    out = []
    for i in range(days - 1, -1, -1):
        d = (datetime.date.today() - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        out.append((d, round(rows.get(d, 0) / 60)))
    return out


def hourly_series_7d():
    """Retorna [(hour, minutes), ...] para horas 0-23 dos últimos 7 dias."""
    since = (datetime.date.today() - datetime.timedelta(days=6)).strftime("%Y-%m-%d")
    with _conn() as c:
        rows = c.execute(
            "SELECT hour, SUM(duration_sec) s FROM sessions WHERE date >= ? GROUP BY hour",
            (since,)
        ).fetchall()
    data = {r["hour"]: r["s"] for r in rows}
    return [(h, round(data.get(h, 0) / 60)) for h in range(24)]


def delete_session(sid):
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE id=?", (sid,))


def streak():
    """Dias consecutivos (até hoje ou ontem) com pelo menos uma sessão."""
    with _conn() as c:
        dates = {r["date"] for r in c.execute("SELECT DISTINCT date FROM sessions").fetchall()}
    if not dates:
        return 0
    today = datetime.date.today()
    # streak conta a partir de hoje; se não houve hoje mas houve ontem, ainda conta
    if today.strftime("%Y-%m-%d") not in dates and \
       (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d") not in dates:
        return 0
    streak = 0
    cur = today
    if today.strftime("%Y-%m-%d") not in dates:
        cur = today - datetime.timedelta(days=1)
    while cur.strftime("%Y-%m-%d") in dates:
        streak += 1
        cur -= datetime.timedelta(days=1)
    return streak


def sound_add_usage(sound, secs):
    with _conn() as c:
        c.execute("INSERT INTO sound_usage(sound,secs) VALUES(?,?) "
                  "ON CONFLICT(sound) DO UPDATE SET secs = secs + ?",
                  (sound, secs, secs))

def fav_sound():
    with _conn() as c:
        r = c.execute("SELECT sound FROM sound_usage ORDER BY secs DESC LIMIT 1").fetchone()
        return r["sound"] if r else "—"


# ─────────────────────────────────────────────────────────────── favorites ───
def add_favorite(name, config):
    with _conn() as c:
        c.execute("INSERT INTO favorites(name,config,created_at) VALUES(?,?,?)",
                  (name, json.dumps(config, ensure_ascii=False),
                   datetime.datetime.now().isoformat()))

def list_favorites():
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM favorites ORDER BY id DESC").fetchall()]

def delete_favorite(fid):
    with _conn() as c:
        c.execute("DELETE FROM favorites WHERE id=?", (fid,))


# ─────────────────────────────────────────────────────────────── protocols ───
def add_protocol(name, config):
    with _conn() as c:
        c.execute("INSERT INTO protocols(name,config,created_at) VALUES(?,?,?)",
                  (name, json.dumps(config, ensure_ascii=False),
                   datetime.datetime.now().isoformat()))

def list_protocols():
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM protocols ORDER BY id DESC").fetchall()]

def delete_protocol(pid):
    with _conn() as c:
        c.execute("DELETE FROM protocols WHERE id=?", (pid,))


# ─────────────────────────────────────────────────────────────── achievements ─
def unlock(key):
    """Retorna True se desbloqueou agora (não estava antes)."""
    with _conn() as c:
        exists = c.execute("SELECT 1 FROM achievements WHERE key=?", (key,)).fetchone()
        if exists:
            return False
        c.execute("INSERT INTO achievements(key,unlocked_at) VALUES(?,?)",
                  (key, datetime.datetime.now().isoformat()))
        return True

def unlocked_achievements():
    with _conn() as c:
        return {r["key"] for r in c.execute("SELECT key FROM achievements").fetchall()}


# ─────────────────────────────────────────────────────────────── XP ──────────
def add_xp(amount):
    cur = get_int("xp", 0)
    set("xp", cur + int(amount))
    return cur + int(amount)

def get_xp():
    return get_int("xp", 0)

def level_for_xp(xp):
    """Curva simples: nível N exige 100*N XP acumulado incremental."""
    lvl, need, total = 1, 100, 0
    while xp >= total + need:
        total += need
        lvl += 1
        need = int(need * 1.15)
    # progresso dentro do nível
    into = xp - total
    return lvl, into, need


# ─────────────────────────────────────────────────────────────── export ──────
def export_csv(path):
    import csv
    with _conn() as c, open(path, "w", newline="", encoding="utf-8") as f:
        rows = c.execute("SELECT * FROM sessions ORDER BY id").fetchall()
        if not rows:
            f.write("sem dados\n"); return
        w = csv.writer(f)
        w.writerow(rows[0].keys())
        for r in rows:
            w.writerow(list(r))

def export_json(path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "sessions": recent_sessions(100000),
            "stats": stats_overview(),
            "xp": get_xp(),
            "achievements": list(unlocked_achievements()),
        }, f, ensure_ascii=False, indent=2)


init()
