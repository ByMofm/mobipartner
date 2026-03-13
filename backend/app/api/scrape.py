import asyncio
import subprocess
import unicodedata

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models.enums import SourceType
from app.models.location import Location
from app.models.property import Property
from app.services.dedup import run_dedup_pass
from app.services.geocoding import geocode_batch
from app.services.pricing import compute_all_scores, compute_overall_scores
from app.services.zone_scoring import compute_zone_scores
from app.utils.currency import get_usd_ars_blue_rate_sync
from app.config import settings

router = APIRouter()


@router.post("/trigger/{source}")
def trigger_scrape(source: SourceType):
    spider_map = {
        SourceType.ZONAPROP: "zonaprop",
        SourceType.ARGENPROP: "argenprop",
        SourceType.MERCADOLIBRE: "mercadolibre",
    }

    spider_name = spider_map.get(source)
    if not spider_name:
        raise HTTPException(status_code=400, detail=f"Unknown source: {source}")

    import os
    scrapers_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "scrapers")
    if not os.path.isdir(scrapers_dir):
        raise HTTPException(
            status_code=501,
            detail="Scrapers not available in this deployment. Use GitHub Actions to run scrapes.",
        )

    try:
        process = subprocess.Popen(
            ["python", "-m", "scrapy", "crawl", spider_name],
            cwd=scrapers_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return {
            "status": "started",
            "spider": spider_name,
            "pid": process.pid,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/dedup")
def trigger_dedup(db: Session = Depends(get_db)):
    """Run deduplication pass across all properties."""
    stats = run_dedup_pass(db)
    return {"status": "completed", **stats}


async def _run_geocode_background(pending: int):
    """Background task: geocode all properties missing coordinates."""
    db = SessionLocal()
    try:
        await geocode_batch(db, batch_size=pending)
    finally:
        db.close()


@router.post("/geocode")
async def trigger_geocode(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Start geocoding in background for all active properties missing coordinates."""
    pending = (
        db.query(func.count(Property.id))
        .filter(
            Property.is_active == True,
            Property.latitude.is_(None),
            Property.address.isnot(None),
        )
        .scalar() or 0
    )

    if pending == 0:
        return {"status": "nothing_to_do", "pending": 0}

    background_tasks.add_task(_run_geocode_background, pending)
    return {"status": "started", "pending": pending}


def _normalize(text: str) -> str:
    """Lowercase + remove accents for fuzzy matching."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


@router.post("/backfill-apto-credito")
def backfill_apto_credito(db: Session = Depends(get_db)):
    """Retroactively set apto_credito=True based on stored listing URLs and raw_data."""
    import json as _json

    KEYWORDS = ["apto-credito", "apto_credito", "crédito", "credito", "hipotecario"]

    listings = db.query(Property.id).join(
        Property.listings
    ).distinct().all()  # just to warm up — actually query listings directly

    from app.models.property import PropertyListing
    all_listings = db.query(PropertyListing).all()

    property_ids_to_flag: set[int] = set()
    for listing in all_listings:
        text_parts = [
            listing.source_url or "",
            listing.original_title or "",
            _json.dumps(listing.raw_data or {}),
        ]
        combined = " ".join(text_parts).lower()
        if any(kw in combined for kw in KEYWORDS):
            property_ids_to_flag.add(listing.property_id)

    updated = 0
    for prop in db.query(Property).filter(Property.id.in_(property_ids_to_flag)).all():
        if not prop.apto_credito:
            prop.apto_credito = True
            updated += 1

    db.commit()
    return {"status": "completed", "updated": updated, "flagged_properties": len(property_ids_to_flag)}


@router.post("/assign-locations")
def assign_locations(db: Session = Depends(get_db)):
    """Assign location_id to properties by text-matching their address against known locations."""
    LEVEL_ORDER = {"barrio": 0, "ciudad": 1, "departamento": 2, "provincia": 3}

    locations = db.query(Location).all()
    # Sort most-specific first so we assign the deepest match
    locations_sorted = sorted(locations, key=lambda l: LEVEL_ORDER.get(l.level, 9))

    props = (
        db.query(Property)
        .filter(Property.is_active == True, Property.address.isnot(None))
        .all()
    )

    assigned = 0
    for prop in props:
        addr_norm = _normalize(prop.address)
        best: Location | None = None
        for loc in locations_sorted:
            if loc.level == "provincia":
                continue  # skip province-level — too generic
            if _normalize(loc.name) in addr_norm:
                best = loc
                break  # already sorted by specificity; first match wins
        if best and prop.location_id != best.id:
            prop.location_id = best.id
            assigned += 1

    db.commit()
    return {"status": "completed", "assigned": assigned, "total": len(props)}


@router.get("/schedule")
def get_schedule():
    """Return scheduler status and last pipeline run info."""
    from app.scheduler import get_status
    return get_status()


@router.post("/run-pipeline")
def trigger_pipeline():
    """Manually trigger the full daily pipeline in background."""
    from app.scheduler import run_pipeline
    import threading
    t = threading.Thread(target=run_pipeline, daemon=True)
    t.start()
    return {"status": "started"}


@router.post("/score")
def trigger_score(db: Session = Depends(get_db)):
    """Run USD backfill and price scoring on all properties."""
    rate = get_usd_ars_blue_rate_sync(fallback=settings.usd_ars_rate_fallback)
    if rate is None:
        raise HTTPException(status_code=503, detail="Could not obtain USD/ARS rate")
    stats = compute_all_scores(db, rate)
    return {"status": "completed", **stats}


@router.post("/zone-score")
def trigger_zone_score(db: Session = Depends(get_db)):
    """Compute zone scores for properties based on location zone quality data."""
    scored = compute_zone_scores(db)
    return {"status": "completed", "properties_scored": scored}


@router.post("/analyze-images")
async def trigger_image_analysis(db: Session = Depends(get_db)):
    """Run image analysis on properties using Ollama vision model."""
    if not settings.image_analysis_enabled:
        raise HTTPException(
            status_code=400,
            detail="Image analysis is disabled. Set IMAGE_ANALYSIS_ENABLED=true to enable.",
        )

    from app.services.image_analysis import batch_analyze
    analyzed = await batch_analyze(db, settings.image_analysis_max_per_run)
    return {"status": "completed", "properties_analyzed": analyzed}


@router.post("/compute-overall")
def trigger_overall_score(db: Session = Depends(get_db)):
    """Compute overall composite score from price, zone, and condition scores."""
    scored = compute_overall_scores(db)
    return {"status": "completed", "properties_scored": scored}
