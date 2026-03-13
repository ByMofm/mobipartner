import json
import logging
import unicodedata
from datetime import datetime, timezone

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings

logger = logging.getLogger(__name__)

# Shared state — readable via API
pipeline_state: dict = {
    "last_run_at": None,
    "last_run_status": None,   # "running" | "completed" | "error"
    "last_run_steps": [],
    "next_run_at": None,
}

_scheduler: BackgroundScheduler | None = None


def _step(name: str, fn):
    """Run a pipeline step, record result, continue on error."""
    logger.info(f"[pipeline] step: {name}")
    start = datetime.now(timezone.utc)
    try:
        result = fn()
        elapsed = round((datetime.now(timezone.utc) - start).total_seconds())
        entry = {"step": name, "status": "ok", "elapsed_s": elapsed, "result": result}
        logger.info(f"[pipeline] {name} ok ({elapsed}s): {result}")
    except Exception as e:
        elapsed = round((datetime.now(timezone.utc) - start).total_seconds())
        entry = {"step": name, "status": "error", "elapsed_s": elapsed, "error": str(e)}
        logger.error(f"[pipeline] {name} error: {e}")
    pipeline_state["last_run_steps"].append(entry)


def trigger_github_workflow():
    """Trigger the Daily Scrape Pipeline workflow on GitHub Actions."""
    if not settings.github_token:
        raise RuntimeError("GITHUB_TOKEN not configured — cannot trigger GitHub Actions")

    url = f"https://api.github.com/repos/{settings.github_repo}/actions/workflows/scrape.yml/dispatches"
    resp = httpx.post(
        url,
        json={"ref": "master"},
        headers={
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return {"status": "triggered", "github_status": resp.status_code}


def run_pipeline():
    """Full daily pipeline: trigger scraping via GitHub Actions, then run post-processing locally."""
    from app.database import SessionLocal
    from app.services.pricing import compute_all_scores
    from app.services.dedup import run_dedup_pass
    from app.utils.currency import get_usd_ars_blue_rate_sync
    from app.models.location import Location
    from app.models.property import Property, PropertyListing

    pipeline_state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    pipeline_state["last_run_status"] = "running"
    pipeline_state["last_run_steps"] = []
    logger.info("=== Daily pipeline started ===")

    # 1. Trigger GitHub Actions scrape workflow
    _step("trigger_scrape_workflow", trigger_github_workflow)

    # Note: scraping runs async on GitHub Actions.
    # Post-processing steps below work on whatever data is already in the DB.
    # GitHub Actions also runs post-processing after scraping completes.

    db = SessionLocal()
    try:
        # 2. Backfill apto_credito from stored URLs/raw_data
        def _backfill_apto():
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
        _step("backfill_apto_credito", _backfill_apto)

        # 3. Assign locations by text matching
        def _assign_locations():
            LEVEL_ORDER = {"barrio": 0, "ciudad": 1, "departamento": 2, "provincia": 3}
            SKIP = {"provincia"}

            def _norm(text: str) -> str:
                nfkd = unicodedata.normalize("NFKD", text.lower())
                return "".join(c for c in nfkd if not unicodedata.combining(c))

            locations = sorted(db.query(Location).all(), key=lambda l: LEVEL_ORDER.get(l.level, 9))
            props = db.query(Property).filter(Property.is_active == True, Property.address.isnot(None)).all()
            assigned = 0
            for prop in props:
                addr_norm = _norm(prop.address)
                for loc in locations:
                    if loc.level in SKIP:
                        continue
                    if _norm(loc.name) in addr_norm:
                        if prop.location_id != loc.id:
                            prop.location_id = loc.id
                            assigned += 1
                        break
            db.commit()
            return {"assigned": assigned}
        _step("assign_locations", _assign_locations)

        # 4. Compute USD prices and scores
        def _score():
            rate = get_usd_ars_blue_rate_sync(fallback=settings.usd_ars_rate_fallback)
            return compute_all_scores(db, rate)
        _step("score", _score)

        # 5. Dedup
        def _dedup():
            return run_dedup_pass(db)
        _step("dedup", _dedup)

    finally:
        db.close()

    any_error = any(s["status"] == "error" for s in pipeline_state["last_run_steps"])
    pipeline_state["last_run_status"] = "error" if any_error else "completed"
    _update_next_run()
    logger.info(f"=== Daily pipeline finished: {pipeline_state['last_run_status']} ===")


def _update_next_run():
    if _scheduler:
        jobs = _scheduler.get_jobs()
        if jobs:
            pipeline_state["next_run_at"] = jobs[0].next_run_time.isoformat() if jobs[0].next_run_time else None


def start_scheduler():
    global _scheduler
    if not settings.scrape_enabled:
        logger.info("Scheduler disabled (SCRAPE_ENABLED=false)")
        return

    _scheduler = BackgroundScheduler(timezone="America/Argentina/Buenos_Aires")
    trigger = CronTrigger.from_crontab(settings.scrape_schedule, timezone="America/Argentina/Buenos_Aires")
    _scheduler.add_job(run_pipeline, trigger, id="daily_pipeline", name="Daily scrape pipeline")
    _scheduler.start()
    _update_next_run()
    logger.info(f"Scheduler started — cron: '{settings.scrape_schedule}'")


def stop_scheduler():
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)


def get_status() -> dict:
    _update_next_run()
    return {
        "enabled": settings.scrape_enabled,
        "schedule": settings.scrape_schedule,
        **pipeline_state,
    }
