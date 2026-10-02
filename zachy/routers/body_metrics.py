from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from zachy.database import get_db
from zachy.models import BodyMetric
from zachy.schemas.body_metric import BodyMetricOut

router = APIRouter()


@router.get("/", response_model=list[BodyMetricOut])
def list_body_metrics(
    limit: int = Query(90, le=3000),
    db: Session = Depends(get_db),
):
    """List daily body metrics, most recent first."""
    return (
        db.query(BodyMetric)
        .order_by(BodyMetric.date.desc())
        .limit(limit)
        .all()
    )