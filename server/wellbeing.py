"""Storage extracted from the private workbench; no publishing/account features."""
import datetime as dt
import json
import math
import os
import sqlite3
import time
from pathlib import Path
import sleep_detail
from health_metrics import METRICS, metric_record, workout_record

DATA = Path(os.environ.get('HEALTH_DATA_DIR', './data')).expanduser()


def today():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()


def connect():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(DATA / "wellbeing.sqlite3", timeout=15)
    db.row_factory = sqlite3.Row
    db.executescript("""
      CREATE TABLE IF NOT EXISTS sleep_days(day TEXT PRIMARY KEY, summary TEXT NOT NULL, received REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS health_sync(source TEXT PRIMARY KEY, state TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS health_metrics(day TEXT, name TEXT, summary TEXT NOT NULL, received REAL NOT NULL, PRIMARY KEY(day,name));
      CREATE TABLE IF NOT EXISTS workouts(id TEXT PRIMARY KEY, day TEXT NOT NULL, summary TEXT NOT NULL, received REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS sleep_segments(source TEXT, start REAL, end REAL, stage TEXT, received REAL NOT NULL, PRIMARY KEY(source,start,end,stage));
    """)
    return db


def sync_identity(body):
    source = body.get("sync_source", "manual")
    interval = body.get("sync_interval", 900 if source != "manual" else 7200)
    if source not in ("manual", "mac-bridge", "iphone", "iphone-workouts", "iphone-sleep", "iphone-native") or isinstance(interval, bool) or not isinstance(interval, int) or not 60 <= interval <= 86400:
        raise ValueError("同步来源或周期格式错误")
    return source, interval


def record_sync(db, source, interval, status, changed=0, latest_day=None, error=""):
    old = db.execute("SELECT state FROM health_sync WHERE source=?", (source,)).fetchone()
    state = json.loads(old[0]) if old else {}
    now = time.time()
    state.update(source=source, interval=interval, status=status, checked=now,
                 runs=state.get("runs", 0) + 1, changed_count=changed, error=error)
    if status == "ok":
        state.update(success=now, latest_day=latest_day or state.get("latest_day"))
    if changed:
        state["changed"] = now
    db.execute("INSERT INTO health_sync VALUES (?,?) ON CONFLICT(source) DO UPDATE SET state=excluded.state", (source, json.dumps(state)))


def report_sync(body):
    source, interval = sync_identity(body)
    status, error = body.get("status"), body.get("error", "")
    if status not in ("empty", "error") or error not in ("", "source_unavailable", "source_rejected", "invalid_source_data", "no_records"):
        raise ValueError("同步状态格式错误")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        record_sync(db, source, interval, status, error=error)
    return {"recorded": True}


def import_sleep(body):
    if isinstance(body.get('data'), dict) and 'native' in body['data']:
        from native_health import to_export
        body = to_export(body)
    source, interval = sync_identity(body)
    metrics = body.get("data", {}).get("metrics", [])
    if not isinstance(metrics, list):
        raise ValueError("睡眠数据格式错误")
    rows, quantity_rows, workout_rows, segments = [], {}, {}, []
    recognized = False
    for metric in metrics:
        if not isinstance(metric, dict):
            raise ValueError("健康指标格式错误")
        name = metric.get("name")
        if not isinstance(name, str):
            raise ValueError("健康指标名称格式错误")
        if name in METRICS:
            recognized = True
            if not isinstance(metric.get("data"), list):
                raise ValueError("健康指标需要按天汇总")
            for raw in metric["data"]:
                record = metric_record(name, metric.get("units"), raw)
                identity = (record["day"], name)
                if identity in quantity_rows and quantity_rows[identity] != record:
                    raise ValueError("同一天有多条不同指标，请在手机选择按天汇总")
                quantity_rows[identity] = record
            continue
        if name != "sleep_analysis":
            continue
        recognized = True
        if metric.get("units") != "hr" or not isinstance(metric.get("data"), list):
            raise ValueError("请选择按天汇总的睡眠数据，单位为小时")
        if any(isinstance(raw, dict) and 'value' in raw for raw in metric['data']):
            segments.extend(sleep_detail.parse_segments(metric['data']))
            continue
        for raw in metric["data"]:
            if not isinstance(raw, dict):
                raise ValueError("睡眠记录格式错误")
            day = str(raw.get("date") or raw.get("end") or raw.get("start") or "")[:10]
            dt.date.fromisoformat(day)
            record = {"day": day}
            for key in ("totalSleep", "core", "deep", "rem", "awake", "asleep", "inBed"):
                value = raw.get(key)
                if value is None and key != "totalSleep":
                    continue
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 24:
                    raise ValueError("睡眠时长需要是 0 到 24 小时的有限数值")
                record[key] = value
            # Daily aggregation boundaries are not actual bedtime/wake time.
            for key in ("sleepStart", "sleepEnd", "inBedStart", "inBedEnd", "sources"):
                if isinstance(raw.get(key), str):
                    record[key] = raw[key][:300]
            rows.append((day, json.dumps(record, ensure_ascii=False), time.time()))
    if "workouts" in body.get("data", {}):
        recognized = True
        workouts = body["data"]["workouts"]
        if not isinstance(workouts, list) or len(workouts) > 500:
            raise ValueError("训练记录需要是列表，一次最多 500 条")
        for raw in workouts:
            try:
                record = workout_record(raw)
            except (KeyError, TypeError, AttributeError) as error:
                raise ValueError("请使用 V2 格式的训练记录") from error
            workout_rows[record["id"]] = record
    if not recognized or len(rows) > 370 or len(quantity_rows) > 6000:
        raise ValueError("需要支持的按天健康指标或训练记录")
    segments = sorted(set(segments))
    changed = 0
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for day, encoded, received in rows:
            old = db.execute("SELECT summary FROM sleep_days WHERE day=?", (day,)).fetchone()
            if old and json.loads(old[0]) == json.loads(encoded):
                continue
            db.execute("INSERT INTO sleep_days VALUES (?,?,?) ON CONFLICT(day) DO UPDATE SET summary=excluded.summary,received=excluded.received", (day, encoded, received))
            changed += 1
        for (day, name), record in quantity_rows.items():
            encoded = json.dumps(record, ensure_ascii=False, sort_keys=True)
            old = db.execute("SELECT summary FROM health_metrics WHERE day=? AND name=?", (day, name)).fetchone()
            if old and json.loads(old[0]) == record:
                continue
            db.execute("INSERT INTO health_metrics VALUES (?,?,?,?) ON CONFLICT(day,name) DO UPDATE SET summary=excluded.summary,received=excluded.received", (day,name,encoded,time.time()))
            changed += 1
        for identity, record in workout_rows.items():
            encoded = json.dumps(record, ensure_ascii=False, sort_keys=True)
            old = db.execute("SELECT summary FROM workouts WHERE id=?", (identity,)).fetchone()
            if old and json.loads(old[0]) == record:
                continue
            db.execute("INSERT INTO workouts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET day=excluded.day,summary=excluded.summary,received=excluded.received", (identity,record['day'],encoded,time.time()))
            changed += 1
        changed += sleep_detail.store_segments(db, segments, time.time())
        days = [r[0] for r in rows] + [d for d, _ in quantity_rows] + [r['day'] for r in workout_rows.values()]
        if segments:
            days.append(sleep_detail.latest_day(segments))
        record_sync(db, source, interval, "ok", changed, max(days) if days else None)
    return {"imported": len({r[0] for r in rows}) + len(quantity_rows) + len(workout_rows) + len(segments), "changed": changed}


def sleep_day_detail(day):
    dt.date.fromisoformat(day)
    with connect() as db:
        row = db.execute('SELECT summary,received FROM sleep_days WHERE day=?', (day,)).fetchone()
        return sleep_detail.detail(db, {**json.loads(row['summary']), 'received': row['received']}) if row else None


def health_snapshot():
    with connect() as db:
        sleep = [{**json.loads(r["summary"]), "received": r["received"]} for r in db.execute("SELECT * FROM sleep_days ORDER BY day DESC LIMIT 30")]
        sources = [json.loads(r[0]) for r in db.execute("SELECT state FROM health_sync")]
        since = (dt.date.fromisoformat(today()) - dt.timedelta(days=30)).isoformat()
        metrics = [{**json.loads(r[0]), "received": r[1]} for r in db.execute("SELECT summary,received FROM health_metrics WHERE day>=? ORDER BY day DESC,name", (since,))]
        workouts = [{**json.loads(r[0]), "received": r[1]} for r in db.execute("SELECT summary,received FROM workouts ORDER BY day DESC LIMIT 60")]
    automatic = [s for s in sources if s["source"] != "manual"]
    phone = [s for s in automatic if s['source'].startswith('iphone')]
    native = [s for s in phone if s['source'] == 'iphone-native' and s.get('success')]
    if native:
        phone = native
    if any(s.get('success') for s in phone):
        automatic = phone
    live = [s for s in automatic if time.time() - s["checked"] <= max(1800, s["interval"] * 2.5)]
    status = ("error" if any(s["status"] != "ok" for s in live) else
              "delayed" if len(live) < len(automatic) else "ok" if live else "unconfigured")
    return {"today": today(), "sleep": sleep, "metrics": metrics, "workouts": workouts,
            "sync": {"status": status, "sources": sources, "primary": "iphone" if automatic == phone and phone else "mac-bridge"}}
