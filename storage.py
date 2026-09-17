"""Transactional local state. CSV files are imported once and never modified."""
import csv
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
DEFAULT_SETTINGS = {"daily_search_limit": 16, "monthly_search_limit": 800,
                    "daily_llm_limit": 40, "auto_watch": False, "watch_hour": 9}
STATUSES = {"discovered", "shortlisted", "preparing", "review_needed", "approved", "prepared",
            "draft_created", "applied", "interview", "offer", "rejected", "archived"}


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical_url(url):
    parts = urlsplit(str(url).strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Une URL publique http(s) est requise.")
    host = parts.hostname.lower().removeprefix("www.")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if host.endswith("indeed.com") and (query.get("jk") or query.get("vjk")):
        return f"https://{host}/viewjob?jk={query.get('jk') or query['vjk']}"
    if host.endswith("linkedin.com") and "/jobs/view/" in parts.path:
        import re
        match = re.search(r"(\d+)/?$", parts.path)
        if match:
            return f"https://linkedin.com/jobs/view/{match[1]}"
    query = {k: v for k, v in query.items() if not k.lower().startswith("utm_") and
             k.lower() not in {"trackingid", "refid", "ref", "source", "trk", "fbclid", "gclid"}}
    netloc = host + (f":{parts.port}" if parts.port and parts.port not in {80, 443} else "")
    return urlunsplit(("https", netloc, parts.path.rstrip("/") or "/", urlencode(sorted(query.items())), ""))


class Store:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv("JAMIL_DB", str(ROOT / "data" / "jamil.sqlite3")))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS applications(id TEXT PRIMARY KEY, canonical TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs((1)) WHERE status IN ('queued','running','cancelling');
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, created_at TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, created_at TEXT, amount INTEGER);
            CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, created_at TEXT, data TEXT);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connection() as db:
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def settings(self):
        return {**DEFAULT_SETTINGS, **self.get("settings", {})}

    def migrate(self):
        if self.get("legacy_imported"):
            return
        path = ROOT / "data" / "applications.csv"
        if path.exists():
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    try:
                        row["qualification_reasons"] = [s.strip() for s in row.get("qualification_reasons", "").split(";") if s.strip()]
                        row["qualification_score"] = float(row["qualification_score"]) if row.get("qualification_score") else None
                        self.add_application(row)
                    except (ValueError, TypeError):
                        continue
        profile_path = ROOT / "profile.json"
        if not self.get("profile") and profile_path.exists():
            self.set("profile", json.loads(profile_path.read_text(encoding="utf-8")))
        company_path = ROOT / "data" / "companies.json"
        if company_path.exists():
            self.set("companies", [{**c, "id": uuid.uuid4().hex} for c in json.loads(company_path.read_text(encoding="utf-8"))])
        self.set("legacy_imported", True)

    def applications(self):
        with self.connection() as db:
            rows = [json.loads(r[0]) for r in db.execute("SELECT data FROM applications")]
        return sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)

    def application(self, id):
        with self.connection() as db:
            row = db.execute("SELECT data FROM applications WHERE id=?", (id,)).fetchone()
        if not row:
            raise KeyError("Candidature introuvable")
        return json.loads(row[0])

    def add_application(self, values):
        url = canonical_url(values["url"])
        id = hashlib.sha256(url.encode()).hexdigest()[:24]
        data = {"id": id, "url": url, "title": "Offre a examiner", "company": "", "location": "",
                "status": "discovered", "source": urlsplit(url).hostname, "created_at": now(), "updated_at": now(),
                "content": "", "qualification_score": None, "qualification_reasons": [], "missing_information": [],
                "email_subject": "", "email_body": "", "recipient": "", "notes": "", "artifacts": [], "review": {},
                **values}
        data.update(id=id, url=url)
        with self.connection() as db:
            cur = db.execute("INSERT OR IGNORE INTO applications VALUES (?,?,?)", (id, url, json.dumps(data, ensure_ascii=False)))
            added = bool(cur.rowcount)
        return self.application(id), added

    def update_application(self, id, changes):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM applications WHERE id=?", (id,)).fetchone()
            if not row:
                raise KeyError("Candidature introuvable")
            data = json.loads(row[0])
            data.update(changes, updated_at=now())
            db.execute("UPDATE applications SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), id))
        return data

    def new_run(self, kind="hunt", payload=None):
        run = {"id": uuid.uuid4().hex, "kind": kind, "status": "queued", "stage": "orchestrator",
               "created_at": now(), "summary": "Mission en attente", "errors": [], "payload": payload or {}, "checkpoint": {}}
        try:
            with self.connection() as db:
                db.execute("INSERT INTO runs VALUES (?,?,?,?)", (run["id"], run["status"], run["created_at"], json.dumps(run)))
        except sqlite3.IntegrityError as exc:
            raise ValueError("Une mission est deja active.") from exc
        return run

    def runs(self):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM runs ORDER BY created_at DESC LIMIT 30")]

    def run(self, id):
        with self.connection() as db:
            row = db.execute("SELECT data FROM runs WHERE id=?", (id,)).fetchone()
        if not row:
            raise KeyError("Mission introuvable")
        return json.loads(row[0])

    def update_run(self, id, **changes):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM runs WHERE id=?", (id,)).fetchone()
            if not row:
                raise KeyError("Mission introuvable")
            data = {**json.loads(row[0]), **changes}
            db.execute("UPDATE runs SET status=?,data=? WHERE id=?", (data["status"], json.dumps(data, ensure_ascii=False), id))
        return data

    def recover(self):
        with self.connection() as db:
            active = [json.loads(r[0]) for r in db.execute("SELECT data FROM runs WHERE status IN ('queued','running','cancelling')")]
        for run in active:
            self.update_run(run["id"], status="interrupted", summary="Interruption du serveur. Reprise disponible.")

    def event(self, run_id, agent, recipient, message, event_type="handoff", payload=None):
        event = {"run_id": run_id, "agent": agent, "recipient": recipient, "message": message,
                 "event_type": event_type, "created_at": now(), "payload": payload or {}}
        with self.connection() as db:
            db.execute("INSERT INTO events(run_id,created_at,data) VALUES (?,?,?)", (run_id, event["created_at"], json.dumps(event, ensure_ascii=False)))

    def events(self):
        with self.connection() as db:
            rows = db.execute("SELECT id,data FROM events ORDER BY id DESC LIMIT 200").fetchall()
        return [{**json.loads(r["data"]), "id": r["id"]} for r in rows]

    def usage(self):
        day, month = now()[:10], now()[:7]
        with self.connection() as db:
            def total(kind, prefix):
                return db.execute("SELECT coalesce(sum(amount),0) FROM usage WHERE kind=? AND created_at LIKE ?", (kind, prefix + "%")).fetchone()[0]
            return {"search_today": total("search", day), "search_month": total("search", month), "llm_today": total("llm", day)}

    def reserve(self, kind, amount=1):
        if kind not in {"search", "llm"} or type(amount) is not int or amount <= 0:
            raise ValueError("Reservation positive entiere requise")
        settings = self.settings()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            def total(prefix):
                return db.execute("SELECT coalesce(sum(amount),0) FROM usage WHERE kind=? AND created_at LIKE ?", (kind, prefix + "%")).fetchone()[0]
            limit = settings["daily_search_limit" if kind == "search" else "daily_llm_limit"]
            if total(now()[:10]) + amount > limit:
                raise BudgetExceeded("Plafond quotidien atteint. Reprends demain ou ajuste le budget.")
            if kind == "search" and total(now()[:7]) + amount > settings["monthly_search_limit"]:
                raise BudgetExceeded("Plafond mensuel de recherche atteint.")
            db.execute("INSERT INTO usage(kind,created_at,amount) VALUES (?,?,?)", (kind, now(), amount))

    def cached(self, key, ttl_hours=24):
        with self.connection() as db:
            row = db.execute("SELECT created_at,data FROM cache WHERE key=?", (key,)).fetchone()
        if row and (datetime.now(timezone.utc) - datetime.fromisoformat(row[0])).total_seconds() < ttl_hours * 3600:
            return json.loads(row[1])

    def cache(self, key, value):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, now(), json.dumps(value, ensure_ascii=False)))


class BudgetExceeded(RuntimeError):
    pass
