"""Original FIT files: download from Garmin, parse, and store.

For each activity:
  data/fit/<garmin_id>.zip          the original file exactly as Garmin serves it (raw backup)
  data/parquet/<garmin_id>.parquet  every record field, one row per record (for analysis)
  records table (SQLite)            a fixed set of core fields (for the website)
  fit_files table (SQLite)          status of the above
"""

import io
import warnings
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import fitdecode
import numpy as np
import pandas as pd
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)
from sqlalchemy import insert
from sqlalchemy.orm import Session

from zachy.models import Activity, FitFile, Record

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
FIT_DIR = DATA_DIR / "fit"
PARQUET_DIR = DATA_DIR / "parquet"

SEMICIRCLES_TO_DEG = 180 / 2**31
# FIT stores running cadence as strides/min (one foot); Garmin shows steps/min.
FOOT_SPORTS = {"running", "walking", "hiking"}
TIMER_START = {"start"}
TIMER_STOP = {"stop", "stop_all", "stop_disable", "stop_disable_all"}
# Power can come from the watch ("power") or a developer field from an external pod.
POWER_FIELDS = ["power", "Power", "RP_Power"]


def zip_path(garmin_id: str) -> Path:
    return FIT_DIR / f"{garmin_id}.zip"


def parquet_path(garmin_id: str) -> Path:
    return PARQUET_DIR / f"{garmin_id}.parquet"


def load_records(garmin_id: str) -> pd.DataFrame:
    """Every recorded field for one activity, for analysis (pandas)."""
    return pd.read_parquet(parquet_path(garmin_id))


# ---------- download ----------

def download_original(client, garmin_id: str) -> bytes | None:
    """The original upload as a zip, or None if Garmin has no file (e.g. manual entries)."""
    try:
        return client.download_activity(garmin_id, dl_fmt=client.ActivityDownloadFormat.ORIGINAL)
    except GarminConnectNotFoundError:
        return None


def fit_bytes_from_zip(raw: bytes) -> bytes | None:
    """The .fit file inside Garmin's zip, or None (e.g. activities uploaded as GPX/TCX)."""
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".fit")]
        return z.read(names[0]) if names else None


# ---------- parse ----------

def read_fit(data: bytes) -> tuple[pd.DataFrame, str | None]:
    """All `record` messages as a DataFrame (scalar fields only, unknown_* dropped), plus the sport."""
    rows, timer_events, sport = [], [], None

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")   # CRC / minor format warnings: keep what parses
        try:
            with fitdecode.FitReader(io.BytesIO(data)) as reader:
                for frame in reader:
                    if not isinstance(frame, fitdecode.FitDataMessage):
                        continue
                    if frame.name == "record":
                        row = {}
                        for f in frame.fields:
                            v = f.value
                            if v is None or f.name.startswith("unknown") or isinstance(v, (tuple, list)):
                                continue
                            row[f.name] = v
                        if "timestamp" in row:
                            rows.append(row)
                    elif frame.name == "event":
                        if frame.get_value("event", fallback=None) == "timer":
                            timer_events.append((frame.get_value("timestamp", fallback=None),
                                                 frame.get_value("event_type", fallback=None)))
                    elif frame.name == "session" and sport is None:
                        s = frame.get_value("sport", fallback=None)
                        sport = str(s) if s is not None else None
        except fitdecode.FitError:
            pass   # truncated/corrupt tail: keep the records read so far

    df = pd.DataFrame(rows)
    if df.empty:
        return df, sport

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp", kind="stable").reset_index(drop=True)

    for src, dst in (("position_lat", "latitude"), ("position_long", "longitude")):
        if src in df:
            df[dst] = df.pop(src) * SEMICIRCLES_TO_DEG

    start = df["timestamp"].iloc[0]
    df["elapsed_s"] = (df["timestamp"] - start).dt.total_seconds()
    df["timer_s"] = df["elapsed_s"] - _paused_before(df["timestamp"], timer_events)

    # Mixed-type object columns (rare, from developer fields) can't go to Parquet as-is.
    for col in df.columns[df.dtypes == object]:
        if not df[col].dropna().map(type).eq(str).all():
            df[col] = df[col].map(lambda v: None if v is None else str(v))

    return df, sport


def _paused_before(ts: pd.Series, timer_events: list) -> np.ndarray:
    """Seconds spent paused (timer stopped) before each timestamp."""
    # Work in plain epoch seconds: pandas' internal datetime unit (ns vs us) varies by version.
    pauses, stopped_at = [], None
    for when, kind in sorted((e for e in timer_events if e[0] is not None), key=lambda e: e[0]):
        when = pd.Timestamp(when)
        when = (when if when.tzinfo else when.tz_localize("UTC")).timestamp()
        if kind in TIMER_STOP and stopped_at is None:
            stopped_at = when
        elif kind in TIMER_START and stopped_at is not None:
            pauses.append((when, when - stopped_at))
            stopped_at = None
    if not pauses:
        return np.zeros(len(ts))

    resume_times = np.array([p[0] for p in pauses])   # ascending
    cumulative = np.cumsum([p[1] for p in pauses])
    record_times = (ts - pd.Timestamp(0, tz="UTC")).dt.total_seconds().to_numpy()
    idx = np.searchsorted(resume_times, record_times, side="right")
    return np.where(idx > 0, cumulative[np.maximum(idx - 1, 0)], 0.0)


def core_records(df: pd.DataFrame, sport: str | None) -> pd.DataFrame:
    """The fixed set of columns stored in SQLite for the website."""
    def col(*names):
        for n in names:
            if n in df:
                return pd.to_numeric(df[n], errors="coerce")
        return pd.Series(np.nan, index=df.index)

    cadence = col("cadence")
    if sport in FOOT_SPORTS:
        cadence = (cadence + col("fractional_cadence").fillna(0)) * 2

    return pd.DataFrame({
        "elapsed_s": df["elapsed_s"].round().astype(int),
        "timer_s": df["timer_s"].round().astype(int),
        "distance_km": col("distance") / 1000,
        "speed_ms": col("enhanced_speed", "speed"),
        "hr": col("heart_rate"),
        "cadence": cadence,
        "elevation": col("enhanced_altitude", "altitude"),
        "latitude": col("latitude"),
        "longitude": col("longitude"),
        "power": col(*POWER_FIELDS),
        "temperature": col("temperature"),
    })


# ---------- store ----------

def process_activity(client, db: Session, activity: Activity, redownload: bool = False) -> FitFile:
    """Make sure the activity's FIT file is downloaded, parsed and stored. Commits.

    Uses the local zip when present (no network) unless `redownload` is set. Rate-limit and
    auth errors propagate so the caller can back off; anything else is recorded as status=error.
    """
    FIT_DIR.mkdir(parents=True, exist_ok=True)
    PARQUET_DIR.mkdir(parents=True, exist_ok=True)

    fit = db.get(FitFile, activity.id) or FitFile(activity_id=activity.id, garmin_id=activity.garmin_id)
    fit.error = None
    path = zip_path(activity.garmin_id)

    if redownload or not path.exists():
        try:
            raw = download_original(client, activity.garmin_id)
        except (GarminConnectTooManyRequestsError, GarminConnectAuthenticationError):
            raise
        except Exception as e:  # network trouble, 5xx…: record it and move on
            fit.error = f"download: {type(e).__name__}: {e}"[:500]
            return _save(db, fit, "error")
        fit.downloaded_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if raw is None:
            return _save(db, fit, "not_found")
        path.write_bytes(raw)

    try:
        data = fit_bytes_from_zip(path.read_bytes())
        if data is None:
            return _save(db, fit, "no_fit")

        df, sport = read_fit(data)
        fit.sport = sport
        db.query(Record).filter(Record.activity_id == activity.id).delete()

        if df.empty:
            fit.n_records, fit.fields = 0, None
            parquet_path(activity.garmin_id).unlink(missing_ok=True)
            return _save(db, fit, "no_records")

        df.to_parquet(parquet_path(activity.garmin_id), index=False)

        core = core_records(df, sport)
        core.insert(0, "activity_id", activity.id)
        rows = core.astype(object).where(core.notna(), None).to_dict("records")
        db.execute(insert(Record), rows)

        fit.n_records = len(df)
        fit.fields = ",".join(df.columns)
        return _save(db, fit, "ok")
    except Exception as e:  # keep going on one bad file; it is retried with --retry-errors
        db.rollback()
        fit = db.get(FitFile, activity.id) or FitFile(activity_id=activity.id, garmin_id=activity.garmin_id)
        fit.error = f"{type(e).__name__}: {e}"[:500]
        return _save(db, fit, "error")


def _save(db: Session, fit: FitFile, status: str) -> FitFile:
    fit.status = status
    db.merge(fit)
    db.commit()
    return fit
