"""LiveTrail race tracking pages (utmb.v3.livetrail.net/…/runners/<bib>?raceId=<race>): your times at
every checkpoint, your rank there, the aid station stops, the course's checkpoints (distance,
altitude, climb, GPS, cutoff), and the same times for the race winners (first man, first woman).

The runner page embeds its data (Next.js payload): the checkpoints and your "passings". The race's
leaders come from LiveTrail's public API, which the site itself calls with the event code
(X-Tenant, e.g. utmb_2026, also visible in the page's file paths). Read once, saved with the
activity (activity_links / livetrail_data).
"""

import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy.orm import Session

from zachy.models import Activity, ActivityLink, LiveTrailData

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh) Zachy personal running tracker"}
API = "https://api.v3.livetrail.net/api/events"


def parse_url(url: str) -> dict | None:
    """{"host", "bib", "race_id", "year"} from a LiveTrail runner link (new pages), or
    {"archive", "event", "bib"} for the older archive (livetrail.net/histo/<event>/coureur.php?rech=<bib>;
    the archive's own address often has no bib — then bib is None and you're found by name)."""
    u = urlparse(url)
    m = re.search(r"/histo/([^/]+)/coureur\.php", u.path)
    if "livetrail" in u.netloc and m:
        bib = parse_qs(u.query).get("rech", [None])[0]
        return {"archive": True, "host": u.netloc, "event": m.group(1), "bib": int(bib) if bib and bib.isdigit() else None}
    m = re.search(r"/(\d{4})/runners/(\d+)", u.path)
    race = parse_qs(u.query).get("raceId", [None])[0]
    if "livetrail" not in u.netloc or not m or not race:
        return None
    return {"host": u.netloc, "year": m.group(1), "bib": int(m.group(2)), "race_id": race,
            "lang": u.path.strip("/").split("/")[0]}


def _payload(html: str) -> str:
    """The page's embedded data as one text (each chunk is a JS string)."""
    out = []
    for chunk in re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.S):
        try:
            out.append(json.loads(f'"{chunk}"'))
        except json.JSONDecodeError:
            pass
    return "".join(out)


def _array_after(text: str, start: int) -> list | None:
    """The JSON array starting at text[start] == '['."""
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            esc = not esc and c == "\\"
            if c == '"' and not esc:
                in_str = False
            if c != "\\":
                esc = False
        elif c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _runner_page(info: dict, bib: int) -> tuple[list, list, str | None]:
    """(checkpoints, passings, tenant) from a runner page."""
    url = f"https://{info['host']}/{info['lang']}/{info['year']}/runners/{bib}?raceId={info['race_id']}"
    r = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
    r.raise_for_status()
    text = _payload(r.text)
    points = []
    i = text.find('[{"access":')
    if i >= 0:
        points = _array_after(text, i) or []
    j = text.find('"passings":[')
    passings = _array_after(text, j + len('"passings":')) if j >= 0 else []
    tenant = re.search(r"livetrailv3\.s3\.[a-z.]+/([a-z0-9_]+)/", r.text)
    return points, passings or [], tenant.group(1) if tenant else None


# ---------- the older archive (XML pages) ----------

def _archive_xml(info: dict, page: str, **params) -> "ET.Element":
    import xml.etree.ElementTree as ET
    r = httpx.get(f"https://{info['host']}/histo/{info['event']}/{page}", params=params, headers=HEADERS,
                  timeout=30, follow_redirects=True)
    r.raise_for_status()
    return ET.fromstring(r.content)


def _hms(t: str | None) -> int | None:
    if not t or ":" not in t:
        return None
    h, m, sec = (int(float(x)) for x in t.split(":"))
    return h * 3600 + m * 60 + sec


def _archive_runner(info: dict, bib: int) -> tuple[dict, list, list, str | None]:
    """(identity + final state, checkpoints, passings in the new pages' format, race id)."""
    from datetime import date as _date, timedelta
    root = _archive_xml(info, "coureur.php", rech=bib)
    fiche = root.find("fiche")
    if fiche is None:
        return {}, [], [], None
    ident, state = fiche.find("identite"), fiche.find("state")
    points = [{"pointId": int(p.get("idpt")), "name": p.get("n"), "shortName": p.get("nc"),
               "distance": float(p.get("km") or 0) * 1000, "elevationGain": float(p.get("d") or 0),
               "altitude": float(p.get("a") or 0), "lat": float(p.get("lat")) if p.get("lat") else None,
               "lon": float(p.get("lon")) if p.get("lon") else None, "cutoff": None, "isAssistance": False, "services": []}
              for p in fiche.iter("pt")]
    passes = list(fiche.find("pass") or [])
    start = passes[0] if passes else None
    start_day = _date.fromisoformat(start.get("date")) if start is not None and start.get("date") else None
    start_wd = int(start.get("jd")) if start is not None and start.get("jd") else None
    tz = int(start.get("tz") or 0) if start is not None else 0

    def stamp(day_idx, hhmmss):
        if not (start_day and hhmmss and day_idx is not None and start_wd is not None):
            return None
        d = start_day + timedelta(days=(int(day_idx) - start_wd) % 7)
        return f"{d.isoformat()}T{hhmmss}{'+' if tz >= 0 else '-'}{abs(tz):02d}:00"

    passings = []
    for e in passes:
        arrival = stamp(e.get("ja") or e.get("jd"), e.get("ha") or e.get("hd"))
        departure = stamp(e.get("jd") or e.get("ja"), e.get("hd")) if e.get("hd") else arrival
        rest = 0
        if arrival and departure:
            rest = max(int((datetime.fromisoformat(departure) - datetime.fromisoformat(arrival)).total_seconds()), 0)
        rank = e.get("clt")
        passings.append({"pointId": int(e.get("idpt")), "dateTime": arrival, "dateTimeOut": departure,
                         "raceTime": _hms(e.get("tps")), "restTime": rest,
                         "ranking": {"scratch": int(rank)} if rank and rank.isdigit() else None})
    runner = {"firstName": ident.get("prenom") if ident is not None else None, "lastName": ident.get("nom") if ident is not None else None,
              "category": ident.get("descat") if ident is not None else None, "status": "FINISHER" if state is not None and state.get("code") == "f" else None,
              "ranking": {"scratch": int(state.get("clt")), "sex": int(state.get("cltsx")), "category": int(state.get("cltcat"))}
                         if state is not None and (state.get("clt") or "").isdigit() else None,
              "raceTime": passings[-1]["raceTime"] if passings else None,
              "startDate": passings[0]["dateTime"] if passings else None,
              "sex": ident.get("sx") if ident is not None else None,
              "race_name": next((c.get("n") for c in root.iter("c") if c.get("id") == fiche.get("c") and c.get("n")), None)}
    return runner, points, passings, fiche.get("c")


def _find_bib(info: dict, me: dict | None) -> int:
    """Your bib in an archived event, searched by your surname (the archive's search), matched on
    your UTMB runner id or first name."""
    if not me or not me.get("last"):
        raise ValueError("This LiveTrail link has no bib: open your runner page and copy its link (…coureur.php?rech=<bib>), "
                         "or add your UTMB runner page in the Profile so you can be found by name")
    root = _archive_xml(info, "coureur.php", rech=me["last"])
    found = [(f.get("doss"), f.find("identite")) for f in root.iter("fiche")]
    found = [(d, i) for d, i in found if d and d.isdigit() and i is not None]
    pick = next((d for d, i in found if me.get("cid") and i.get("cid") == me["cid"]), None) or \
           next((d for d, i in found if (i.get("prenom") or "").lower() == (me.get("first") or "").lower()), None)
    if not pick:
        raise ValueError(f"No runner named {me['last'].upper()} found in this LiveTrail archive: copy your runner page's link (…coureur.php?rech=<bib>)")
    return int(pick)


def _me(db: Session) -> dict | None:
    """Your names and UTMB runner id, from the UTMB runner link in the Profile (…/runner/<id>.<first>.<last>)."""
    from zachy.models import Profile
    p = db.get(Profile, 1)
    m = re.search(r"/runner/(\d+)\.([^./?#]+)\.([^/?#]+)", (p.utmb_url if p else "") or "")
    return {"cid": m.group(1), "first": m.group(2).replace("-", " "), "last": m.group(3).replace("-", " ")} if m else None


def _fetch_archive(url: str, info: dict, me: dict | None = None) -> dict:
    if info["bib"] is None:
        info = {**info, "bib": _find_bib(info, me)}
        url = f"https://{info['host']}/histo/{info['event']}/coureur.php?rech={info['bib']}"
    runner, points, passings, race_id = _archive_runner(info, info["bib"])
    if not points or not passings:
        raise ValueError("No race data on this LiveTrail archive page")
    leaders = {}
    ranking = _archive_xml(info, "classement.php", course=race_id, cat="scratch").find("classement")
    rows = list(ranking) if ranking is not None else []
    for key, pick in (("male", lambda c: c.get("sx") == "H"), ("female", lambda c: c.get("sx") == "F")):
        top = next((c for c in rows if pick(c)), None)
        if top is None:
            continue
        doss = int(top.get("doss"))
        their = passings if doss == info["bib"] else _archive_runner(info, doss)[2]
        leaders[key] = {"bib": doss, "name": f"{top.get('prenom', '')} {top.get('nom', '')}".strip(),
                        "race_time": _hms(top.get("tps")), "passings": their}
    last = points[-1] if points else {}
    return {"url": url, "tenant": info["event"], "race_id": race_id, "bib": info["bib"],
            "race": {"fullName": runner.pop("race_name", None) or race_id, "distance": last.get("distance"), "elevationGain": last.get("elevationGain"),
                     "startPlace": points[0]["name"] if points else None, "finishPlace": last.get("name"),
                     "startDate": runner.get("startDate")},
            "runner": runner, "points": points, "passings": passings, "leaders": leaders}


def fetch(url: str, me: dict | None = None) -> dict:
    """Everything for one race from a LiveTrail runner link (new pages or the older archive; an
    archive link without a bib: you're found by name, me = _me())."""
    info = parse_url(url)
    if not info:
        raise ValueError("Not a LiveTrail runner link (…/<year>/runners/<bib>?raceId=… or …/histo/<event>/coureur.php?rech=<bib>)")
    if info.get("archive"):
        return _fetch_archive(url, info, me)
    points, passings, tenant = _runner_page(info, info["bib"])
    if not points or not passings or not tenant:
        raise ValueError("No race data on this LiveTrail page")
    h = {**HEADERS, "Accept": "application/json", "X-Tenant": tenant}
    me = httpx.get(f"{API}/runners/{info['bib']}", headers=h, timeout=30).json()
    race = httpx.get(f"{API}/races/{info['race_id']}", headers=h, timeout=30).json()
    live = httpx.get(f"{API}/races/{info['race_id']}/live", headers=h, timeout=30).json()
    leaders = {}
    for key, ranking in (("male", "scratch"), ("female", "female")):
        top = next((r for r in (live.get("headRanking") or {}).get(ranking) or []
                    if key == "female" or r.get("sex", "MALE") != "FEMALE"), None)
        if not top:
            continue
        _, their, _ = _runner_page(info, top["bib"])
        leaders[key] = {"bib": top["bib"], "name": f"{top.get('firstName', '')} {top.get('lastName', '')}".strip(),
                        "race_time": top.get("raceTime"), "passings": their}
    return {"url": url, "tenant": tenant, "race_id": info["race_id"], "bib": info["bib"],
            "race": {k: race.get(k) for k in ("fullName", "distance", "elevationGain", "startPlace", "finishPlace",
                                              "startDate", "startWeather", "finishWeather")},
            "runner": {k: me.get(k) for k in ("firstName", "lastName", "status", "category", "raceTime", "ranking",
                                              "runnerUrl", "startDate")},
            "points": [{k: p.get(k) for k in ("pointId", "name", "shortName", "distance", "altitude", "elevationGain",
                                              "lat", "lon", "cutoff", "isAssistance", "services")} for p in points],
            "passings": passings, "leaders": leaders}


def sections(data: dict) -> list[dict]:
    """Checkpoint to checkpoint: distance, climb, time, rank change, stop, cutoff margin, and the
    gap to the winners at each checkpoint."""
    pts = {p["pointId"]: p for p in data["points"]}
    lead = {k: {p["pointId"]: p for p in v["passings"]} for k, v in data.get("leaders", {}).items()}
    mine = [p for p in data["passings"] if p["pointId"] in pts]
    out, prev = [], None
    for p in mine:
        pt = pts[p["pointId"]]
        row = {"point_id": p["pointId"], "name": pt["name"], "short": pt.get("shortName"), "km": pt["distance"] / 1000,
               "altitude": pt.get("altitude"), "lat": pt.get("lat"), "lon": pt.get("lon"),
               "race_time": p.get("raceTime"), "arrival": p.get("dateTime"), "departure": p.get("dateTimeOut"),
               "rest_s": p.get("restTime") or 0, "rank": (p.get("ranking") or {}).get("scratch"),
               "rank_sex": (p.get("ranking") or {}).get("sex"), "cutoff": pt.get("cutoff"),
               "aid": bool(pt.get("isAssistance")) or "FOOD" in (pt.get("services") or []) or "DRINK" in (pt.get("services") or [])}
        if row["cutoff"] and row["arrival"]:
            row["cutoff_margin_s"] = int((datetime.fromisoformat(row["cutoff"]) - datetime.fromisoformat(row["arrival"])).total_seconds())
        for k, ps in lead.items():
            their = ps.get(p["pointId"])
            if their and their.get("raceTime") is not None and p.get("raceTime") is not None:
                row[f"gap_{k}_s"] = p["raceTime"] - their["raceTime"]
        if prev:
            row.update({"section_km": round(row["km"] - prev["km"], 2),
                        "section_gain": (pt.get("elevationGain") or 0) - (pts[prev["point_id"]].get("elevationGain") or 0),
                        "section_s": (row["race_time"] or 0) - (prev["race_time"] or 0),
                        "rank_change": (prev["rank"] - row["rank"]) if prev.get("rank") and row["rank"] else None})
        out.append(row)
        prev = row
    return out


OVERNIGHT_S = 3 * 3600      # a stop this long ends a stage (stage races are one LiveTrail race)


def _group(db: Session, activity_id: int) -> list[int]:
    """The activities sharing a LiveTrail link: every stage of a stage race, or just this one."""
    from zachy.analytics.race_results import stage_ids
    return stage_ids(db, activity_id) or [activity_id]


def _data_row(db: Session, activity_id: int) -> LiveTrailData | None:
    return next((r for r in (db.get(LiveTrailData, i) for i in [activity_id, *_group(db, activity_id)]) if r), None)


def save_link(db: Session, activity: Activity, url: str) -> dict:
    """Store a results link for an activity; a LiveTrail link also gets its race data read. A stage
    race has one LiveTrail page for all its stages: the link is shared by them."""
    data = fetch(url, _me(db)) if parse_url(url) else None   # read first: a link that can't be read isn't kept
    link = db.query(ActivityLink).filter_by(activity_id=activity.id, url=url).first() or ActivityLink(activity_id=activity.id, url=url)
    link.kind = "livetrail" if data else "web"
    link.label = "LiveTrail" if data else urlparse(url).netloc.replace("www.", "")
    db.add(link)
    db.commit()
    warning = None
    if data:
        row = db.get(LiveTrailData, activity.id) or LiveTrailData(activity_id=activity.id)
        row.url, row.data, row.fetched_at = url, json.dumps(data), datetime.now()
        db.add(row)
        db.commit()
        acts = [db.get(Activity, i) for i in _group(db, activity.id)]
        duration = sum(a.duration_s or 0 for a in acts)
        official = (data["runner"] or {}).get("raceTime")
        if official and duration and not 0.85 <= official / duration <= 1.5:
            warning = "The LiveTrail time doesn't look like this activity's: check the link."
        start = (data["runner"] or {}).get("startDate") or (data["race"] or {}).get("startDate")
        if start and abs((datetime.fromisoformat(start).date() - acts[0].date).days) > 2:
            warning = "The LiveTrail race date doesn't match this activity's date: check the link."
    return {"links": links(db, activity.id), "warning": warning}


def links(db: Session, activity_id: int) -> list[dict]:
    return [{"id": l.id, "url": l.url, "kind": l.kind, "label": l.label, "activity_id": l.activity_id}
            for l in db.query(ActivityLink).filter(ActivityLink.activity_id.in_(_group(db, activity_id))).order_by(ActivityLink.id)]


def _stages(secs: list[dict]) -> list[list[dict]]:
    """Checkpoints split into stages at the overnight stops (the night itself isn't a stop)."""
    out = [[]]
    for s in secs:
        if out[-1] and out[-1][-1]["rest_s"] >= OVERNIGHT_S:
            out[-1][-1]["rest_s"] = 0
            out.append([])
            s["rest_s"] = 0   # the next morning's start repeats the night as its stop
            for k in ("section_km", "section_gain", "section_s", "rank_change"):
                s.pop(k, None)
        out[-1].append(s)
    return out


def _measure(db: Session, activity: Activity, secs: list[dict]) -> None:
    """Moving time (the stop at the start of each section removed), pace and race-model grade-
    adjusted pace on the watch track (official distances scaled to the watch's total)."""
    from zachy.analytics.gap import split_paces
    km0 = secs[0]["km"]
    for s in secs:
        s["km_total"], s["km"] = s["km"], round(s["km"] - km0, 3)
    official_km = secs[-1]["km"]
    scale = activity.distance_km / official_km if official_km and activity.distance_km else 1
    for prev, cur in zip(secs, secs[1:]):
        cur["moving_s"] = max((cur.get("section_s") or 0) - (prev["rest_s"] or 0), 0)
    pseudo = [{"end_km": s["km"] * scale, "distance_km": (s.get("section_km") or 0) * scale, "duration_s": s.get("moving_s") or 0}
              for s in secs]
    for s, gap in zip(secs, split_paces(db, activity, pseudo, "race")):
        if s.get("section_km"):
            s["gap_race"] = gap
            s["pace"] = round(s["moving_s"] / 60 / s["section_km"], 3) if s.get("moving_s") else None
    for s in secs:
        s["watch_km"] = round(s["km"] * scale, 3)   # where the checkpoint is on your watch's distance


def livetrail(db: Session, activity_id: int, overall: bool = False) -> dict | None:
    """The linked race's checkpoints and sections. For a stage race (one LiveTrail race over several
    days), the checkpoints of this activity's stage, distances from the stage start and gaps to the
    winners gained or lost on the stage; overall=True: every stage, end to end, with overall gaps."""
    row = _data_row(db, activity_id)
    if not row:
        return None
    data = json.loads(row.data)
    secs = sections(data)
    if "/histo/" in data["url"]:
        # Aid stations given as two checkpoints (entrance / exit, a few hundred metres apart, in the
        # older archive pages): the time between them is the stop, counted at the entrance.
        for prev, cur in zip(secs, secs[1:]):
            if (cur.get("section_km") or 9) < 0.6 and not prev["rest_s"] and cur.get("section_s"):
                prev["rest_s"], cur["section_s"] = cur["section_s"], 0
    ids = _group(db, activity_id)
    stages = _stages(secs)
    if len(stages) != len(ids):
        stages, ids = [secs], [activity_id]
    acts = [db.get(Activity, i) for i in ids]
    watch0, summary = 0.0, []
    for n, (part, a) in enumerate(zip(stages, acts), 1):
        _measure(db, a, part)
        for s in part:
            s["stage"] = n
        summary.append({"stage": n, "activity_id": a.id, "date": a.date.isoformat(), "km": part[-1]["km"],
                        "race_time": (part[-1]["race_time"] or 0) - (part[0]["race_time"] or 0),
                        "rest_s": sum(s["rest_s"] for s in part), "rank": part[-1]["rank"],
                        "gain": sum(s.get("section_gain") or 0 for s in part)})
        if overall:
            for s in part:
                s["km"], s["watch_km"] = s["km_total"], round(s["watch_km"] + watch0, 3)
            watch0 += a.distance_km or 0
        else:
            for k in ("gap_male_s", "gap_female_s"):
                start = part[0].get(k)
                for s in part:
                    if s.get(k) is not None and start is not None:
                        s[k] = s[k] - start   # won or lost on this stage
    if overall:
        out = [s for part in stages for s in part]
    else:
        out = stages[ids.index(activity_id)] if activity_id in ids else secs
    return {**{k: data[k] for k in ("url", "race", "runner")},
            "leaders": {k: {"name": v["name"], "race_time": v["race_time"]} for k, v in data.get("leaders", {}).items()},
            "sections": out, "total_rest_s": sum(s["rest_s"] for s in out),
            "stages": summary if len(stages) > 1 else None,
            "stage": next((x["stage"] for x in summary if x["activity_id"] == activity_id), None) if len(stages) > 1 and not overall else None}
