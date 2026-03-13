from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str | None = Security(_api_key_header)):
    """Validate X-API-Key header against settings.api_key or admin_api_key.
    If api_key is not configured (empty), auth is disabled (dev mode).
    """
    if not settings.api_key:
        return  # auth disabled in dev
    valid_keys = {settings.api_key}
    if settings.admin_api_key:
        valid_keys.add(settings.admin_api_key)
    if not api_key or api_key not in valid_keys:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


async def verify_admin_key(api_key: str | None = Security(_api_key_header)):
    """Validate X-API-Key header against settings.admin_api_key.
    Used for admin-only endpoints (scraping, pipeline triggers).
    """
    if not settings.admin_api_key:
        return  # auth disabled in dev
    if not api_key or api_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing admin API key")
