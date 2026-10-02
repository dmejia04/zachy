from fastapi import APIRouter

from zachy.config import settings

router = APIRouter()


@router.get("/")
def frontend_config():
    """Settings the frontend needs that live in .env (public values only)."""
    return {"mapbox_token": settings.mapbox_token or None}
