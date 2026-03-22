"""Pipeline health monitoring service."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.enums import SourceType
from app.models.property import Property, ScrapeRun

logger = logging.getLogger(__name__)

ALL_SPIDERS = [s.value for s in SourceType]


def get_pipeline_health(db: Session) -> dict:
    """Query DB for comprehensive pipeline health metrics."""
    now = datetime.utcnow()
    spider_statuses = _get_spider_statuses(db, now)
    property_counts = _get_property_counts(db, now)
    overall = _compute_overall_health(spider_statuses)

    return {
        "checked_at": now.isoformat(),
        "overall_health": overall,
        "spiders": spider_statuses,
        "properties": property_counts,
    }


def _get_spider_statuses(db: Session, now: datetime) -> list[dict]:
    """Get last run info and alerts for each spider."""
    statuses = []

    for spider_name in ALL_SPIDERS:
        # Last run
        last_run = (
            db.query(ScrapeRun)
            .filter(ScrapeRun.source == spider_name)
            .order_by(ScrapeRun.started_at.desc())
            .first()
        )

        # Last 3 runs for failure detection
        recent_runs = (
            db.query(ScrapeRun)
            .filter(ScrapeRun.source == spider_name)
            .order_by(ScrapeRun.started_at.desc())
            .limit(3)
            .all()
        )

        alerts = []

        if last_run is None:
            alerts.append("never_scraped")
            statuses.append({
                "source": spider_name,
                "last_run": None,
                "alerts": alerts,
            })
            continue

        finished = last_run.finished_at or last_run.started_at
        hours_ago = (now - finished).total_seconds() / 3600

        if hours_ago > 48:
            alerts.append("no_scrape_48h")

        # Check if last 3 runs are all failing (more errors than new items)
        if len(recent_runs) >= 3:
            all_failing = all(
                r.items_errors > (r.items_new + r.items_updated)
                for r in recent_runs
            )
            if all_failing:
                alerts.append("consistently_failing")

        if last_run.items_found == 0 and last_run.finished_at is not None:
            alerts.append("zero_items_last_run")

        statuses.append({
            "source": spider_name,
            "last_run": {
                "started_at": last_run.started_at.isoformat() if last_run.started_at else None,
                "finished_at": last_run.finished_at.isoformat() if last_run.finished_at else None,
                "items_found": last_run.items_found,
                "items_new": last_run.items_new,
                "items_updated": last_run.items_updated,
                "items_errors": last_run.items_errors,
                "hours_ago": round(hours_ago, 1),
            },
            "alerts": alerts,
        })

    return statuses


def _get_property_counts(db: Session, now: datetime) -> dict:
    """Get property statistics."""
    active_filter = Property.is_active == True

    total_active = db.query(func.count(Property.id)).filter(active_filter).scalar() or 0

    added_24h = (
        db.query(func.count(Property.id))
        .filter(active_filter, Property.first_seen_at > now - timedelta(hours=24))
        .scalar() or 0
    )

    added_7d = (
        db.query(func.count(Property.id))
        .filter(active_filter, Property.first_seen_at > now - timedelta(days=7))
        .scalar() or 0
    )

    with_price_score = (
        db.query(func.count(Property.id))
        .filter(active_filter, Property.price_score.isnot(None))
        .scalar() or 0
    )

    with_zone_score = (
        db.query(func.count(Property.id))
        .filter(active_filter, Property.zone_score.isnot(None))
        .scalar() or 0
    )

    with_overall_score = (
        db.query(func.count(Property.id))
        .filter(active_filter, Property.overall_score.isnot(None))
        .scalar() or 0
    )

    without_coords = (
        db.query(func.count(Property.id))
        .filter(active_filter, Property.latitude.is_(None), Property.address.isnot(None))
        .scalar() or 0
    )

    with_location = (
        db.query(func.count(Property.id))
        .filter(active_filter, Property.location_id.isnot(None))
        .scalar() or 0
    )

    return {
        "total_active": total_active,
        "added_24h": added_24h,
        "added_7d": added_7d,
        "with_price_score": with_price_score,
        "with_zone_score": with_zone_score,
        "with_overall_score": with_overall_score,
        "without_coords": without_coords,
        "with_location": with_location,
    }


def _compute_overall_health(spider_statuses: list[dict]) -> str:
    """Derive overall health from spider alerts."""
    critical_alerts = {"no_scrape_48h", "consistently_failing", "never_scraped"}
    spiders_with_issues = 0

    for s in spider_statuses:
        if any(a in critical_alerts for a in s["alerts"]):
            spiders_with_issues += 1

    if spiders_with_issues == 0:
        return "healthy"
    elif spiders_with_issues <= 4:
        return "degraded"
    else:
        return "unhealthy"
