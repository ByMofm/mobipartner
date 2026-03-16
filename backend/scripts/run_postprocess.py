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
import sys
import unicodedata
from datetime import datetime, timezone

# Add parent dir to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def _normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def step(name: str, fn):
    logger.info(f"[postprocess] starting: {name}")
    start = datetime.now(timezone.utc)
    try:
        result = fn()
        elapsed = round((datetime.now(timezone.utc) - start).total_seconds())
        logger.info(f"[postprocess] {name} ok ({elapsed}s): {result}")
        return True
    except Exception as e:
        elapsed = round((datetime.now(timezone.utc) - start).total_seconds())
        logger.error(f"[postprocess] {name} error ({elapsed}s): {e}")
        return False


def main():
    errors = 0

    # 1. Backfill apto_credito
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
        errors += 1

    # 2. Assign locations
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
        errors += 1

    # 3. Geocode
    def geocode():
        db = SessionLocal()
        try:
            return asyncio.run(geocode_batch(db, batch_size=200))
        finally:
            db.close()

    if not step("geocode", geocode):
        errors += 1

    # 4. Score
    def score():
        db = SessionLocal()
        try:
            rate = get_usd_ars_blue_rate_sync(fallback=settings.usd_ars_rate_fallback)
            return compute_all_scores(db, rate)
        finally:
            db.close()

    if not step("score", score):
        errors += 1

    # 5. Dedup
    def dedup():
        db = SessionLocal()
        try:
            return run_dedup_pass(db)
        finally:
            db.close()

    if not step("dedup", dedup):
        errors += 1

    if errors:
        logger.warning(f"[postprocess] finished with {errors} error(s)")
        sys.exit(1)
    else:
        logger.info("[postprocess] finished successfully")


if __name__ == "__main__":
    main()
