#!/usr/bin/env python3
"""Standalone post-processing pipeline.

Runs: backfill apto_credito → assign locations → geocode → score → dedup.
Designed for GitHub Actions (no Playwright/browser needed).
Requires DATABASE_URL env var pointing to the production database.
"""
import asyncio
import json
import logging
import os
import signal
import sys
import unicodedata
from datetime import datetime, timezone

# Add parent dir to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text as sa_text

from app.config import settings
from app.database import SessionLocal
from app.models.location import Location
from app.models.property import Property, PropertyListing
from app.services.dedup import run_dedup_pass
from app.services.geocoding import geocode_batch
from app.services.pricing import compute_all_scores
from app.utils.currency import get_usd_ars_blue_rate_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Timeout per step (seconds) — prevents any single step from hanging
STEP_TIMEOUTS = {
    "backfill_apto_credito": 300,   # 5 min
    "assign_locations": 300,         # 5 min
    "geocode": 600,                  # 10 min
    "score": 300,                    # 5 min
    "dedup": 900,                    # 15 min
}


class StepTimeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise StepTimeout("Step timed out")


def _normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def step(name: str, fn, critical: bool = False):
    """Run a post-process step with timeout.

    Args:
        name: Step name for logging.
        fn: Callable to execute.
        critical: If True, failure is fatal. If False, log and continue.
    """
    logger.info(f"[postprocess] starting: {name}")
    start = datetime.now(timezone.utc)
    timeout = STEP_TIMEOUTS.get(name, 600)

    # Set alarm-based timeout (Unix only)
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)

    try:
        result = fn()
        signal.alarm(0)  # Cancel alarm
        elapsed = round((datetime.now(timezone.utc) - start).total_seconds())
        logger.info(f"[postprocess] {name} ok ({elapsed}s): {result}")
        return True
    except StepTimeout:
        elapsed = round((datetime.now(timezone.utc) - start).total_seconds())
        logger.error(f"[postprocess] {name} TIMED OUT after {elapsed}s (limit: {timeout}s)")
        return False
    except Exception as e:
        signal.alarm(0)
        elapsed = round((datetime.now(timezone.utc) - start).total_seconds())
        logger.error(f"[postprocess] {name} error ({elapsed}s): {e}")
        return False
    finally:
        signal.signal(signal.SIGALRM, old_handler)


def main():
    critical_errors = 0

    # 1. Backfill apto_credito (non-critical)
    def backfill():
        db = SessionLocal()
        try:
            KEYWORDS = ["apto-credito", "apto_credito", "crédito", "credito", "hipotecario"]
            all_listings = db.query(PropertyListing).all()
            to_flag = set()
            for listing in all_listings:
                text = " ".join([
                    listing.source_url or "",
                    listing.original_title or "",
                    json.dumps(listing.raw_data or {}),
                ]).lower()
                if any(kw in text for kw in KEYWORDS):
                    to_flag.add(listing.property_id)
            updated = 0
            for prop in db.query(Property).filter(Property.id.in_(to_flag)).all():
                if not prop.apto_credito:
                    prop.apto_credito = True
                    updated += 1
            db.commit()
            return {"updated": updated}
        finally:
            db.close()

    if not step("backfill_apto_credito", backfill):
        logger.warning("[postprocess] backfill_apto_credito failed, continuing...")

    # 2. Assign locations (non-critical)
    def assign_locations():
        db = SessionLocal()
        try:
            LEVEL_ORDER = {"barrio": 0, "ciudad": 1, "departamento": 2, "provincia": 3}
            locations = sorted(db.query(Location).all(), key=lambda l: LEVEL_ORDER.get(l.level, 9))
            props = db.query(Property).filter(Property.is_active == True, Property.address.isnot(None)).all()
            assigned = 0
            for prop in props:
                addr_norm = _normalize(prop.address)
                for loc in locations:
                    if loc.level == "provincia":
                        continue
                    if _normalize(loc.name) in addr_norm:
                        if prop.location_id != loc.id:
                            prop.location_id = loc.id
                            assigned += 1
                        break
            db.commit()
            return {"assigned": assigned}
        finally:
            db.close()

    if not step("assign_locations", assign_locations):
        logger.warning("[postprocess] assign_locations failed, continuing...")

    # 3. Geocode (non-critical)
    def geocode():
        db = SessionLocal()
        try:
            return asyncio.run(geocode_batch(db, batch_size=200))
        finally:
            db.close()

    if not step("geocode", geocode):
        logger.warning("[postprocess] geocode failed, continuing...")

    # 4. Score (critical — affects user-facing data)
    def score():
        db = SessionLocal()
        try:
            rate = get_usd_ars_blue_rate_sync(fallback=settings.usd_ars_rate_fallback)
            return compute_all_scores(db, rate)
        finally:
            db.close()

    if not step("score", score, critical=True):
        critical_errors += 1

    # 5. Dedup (critical — affects data integrity)
    def dedup():
        db = SessionLocal()
        try:
            # Set statement timeout to prevent runaway queries
            db.execute(sa_text("SET statement_timeout = '600s'"))
            return run_dedup_pass(db)
        finally:
            db.close()

    if not step("dedup", dedup, critical=True):
        critical_errors += 1

    if critical_errors:
        logger.warning(f"[postprocess] finished with {critical_errors} critical error(s)")
        sys.exit(1)
    else:
        logger.info("[postprocess] finished successfully")


if __name__ == "__main__":
    main()
