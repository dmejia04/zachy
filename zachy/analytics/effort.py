"""How hard an activity was, according to Garmin and to you:

- training effect: aerobic and anaerobic (0-5), the label Garmin gives the session (Tempo,
  Base, VO2 max…) and its training load;
- your own rating at the end on the watch: RPE (Garmin stores 10-100, shown as 1-10) and
  "how did you feel" (0 very weak … 100 very strong);
- stamina at the start and end, and the body battery change.

Only in the activity details (not in the activity list), so fetched once per activity and cached.
"""

import json
from datetime import datetime

from sqlalchemy.orm import Session

from zachy.models import Activity, ActivityEffort


def _convert(d: dict) -> dict:
    s = (d or {}).get("summaryDTO", {})
    rpe = s.get("directWorkoutRpe")
    return {
        "aerobic": s.get("trainingEffect"), "anaerobic": s.get("anaerobicTrainingEffect"),
        "label": s.get("trainingEffectLabel"), "load": s.get("activityTrainingLoad"),
        "rpe": rpe / 10 if rpe is not None else None, "feel": s.get("directWorkoutFeel"),
        "stamina_start": s.get("beginPotentialStamina"), "stamina_end": s.get("endPotentialStamina"),
        "body_battery": s.get("differenceBodyBattery"),
    }


def activity_effort(db: Session, activity: Activity, client=None) -> dict:
    row = db.get(ActivityEffort, activity.id)
    # The RPE can be added later in the Garmin app: ask again for the last week's activities.
    if row is not None and json.loads(row.data).get("rpe") is None \
            and (datetime.now().date() - activity.date).days <= 7 and (datetime.now() - row.fetched_at).total_seconds() > 600:
        db.delete(row)
        db.commit()
        row = None
    if row is None:
        if client is None:
            from zachy.analytics.weather import _garmin
            client = _garmin()
        data = _convert(client.get_activity(activity.garmin_id))
        row = ActivityEffort(activity_id=activity.id, data=json.dumps(data), fetched_at=datetime.now())
        db.add(row)
        db.commit()
    return json.loads(row.data)
