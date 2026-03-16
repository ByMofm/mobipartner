"""Geocoding service using Nominatim (OpenStreetMap).

Rate limited to ~1 request per 1.5 seconds. Backs off on 429s and aborts
early after too many consecutive failures.  Caches results by skipping
properties that already have lat/lng.
"""

import asyncio
import logging

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.property import Property

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "mobiPartner/0.1 (real-estate-aggregator)"

# Semaphore to enforce sequential requests
_rate_limiter = asyncio.Semaphore(1)

# After this many consecutive 429s, abort the batch early
MAX_CONSECUTIVE_429 = 5


class RateLimitExceeded(Exception):
    """Raised when Nominatim returns too many consecutive 429s."""


async def geocode_address(address: str, city: str = "", region: str = "Tucumán") -> dict | None:
    """Geocode an address using Nominatim.

    Returns {"lat": float, "lon": float} or None.
    Raises RateLimitExceeded if a 429 response is received.
    """
    query = f"{address}, {city}, {region}, Argentina" if city else f"{address}, {region}, Argentina"

    async with _rate_limiter:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    NOMINATIM_URL,
                    params={
                        "q": query,
                        "format": "json",
                        "limit": 1,
                        "countrycodes": "ar",
                    },
                    headers={"User-Agent": USER_AGENT},
                    timeout=10,
                )
                if resp.status_code == 429:
                    raise RateLimitExceeded(f"429 for '{query}'")
                resp.raise_for_status()
                results = resp.json()

                if results:
                    return {
                        "lat": float(results[0]["lat"]),
                        "lon": float(results[0]["lon"]),
                    }

                # Fallback: try with just city + region
                if city and address != city:
                    await asyncio.sleep(1.5)
                    fallback_query = f"{city}, {region}, Argentina"
                    resp2 = await client.get(
                        NOMINATIM_URL,
                        params={
                            "q": fallback_query,
                            "format": "json",
                            "limit": 1,
                            "countrycodes": "ar",
                        },
                        headers={"User-Agent": USER_AGENT},
                        timeout=10,
                    )
                    if resp2.status_code == 429:
                        raise RateLimitExceeded(f"429 for fallback '{fallback_query}'")
                    resp2.raise_for_status()
                    results2 = resp2.json()
                    if results2:
                        return {
                            "lat": float(results2[0]["lat"]),
                            "lon": float(results2[0]["lon"]),
                        }

        except RateLimitExceeded:
            raise  # propagate to batch handler
        except Exception as e:
            logger.warning(f"Geocoding failed for '{query}': {e}")

        # Rate limit: wait between requests
        await asyncio.sleep(1.5)

    return None


def extract_city_from_address(address: str) -> str:
    """Try to extract city name from an address string."""
    if not address:
        return ""

    parts = [p.strip() for p in address.split(",")]

    # Common city names in Tucumán
    known_cities = [
        "San Miguel de Tucumán",
        "Yerba Buena",
        "Tafí Viejo",
        "Banda del Río Salí",
        "Alderetes",
        "Lules",
        "Famaillá",
        "Monteros",
        "Concepción",
        "Tafí del Valle",
        "Aguilares",
    ]

    for part in parts:
        for city in known_cities:
            if city.lower() in part.lower():
                return city

    # If address has multiple parts, last meaningful part is often the city
    if len(parts) >= 2:
        return parts[-1].strip()

    return "San Miguel de Tucumán"  # default for Tucumán


async def geocode_property(property: Property) -> bool:
    """Geocode a single property. Returns True if geocoded successfully."""
    if property.latitude and property.longitude:
        return False  # Already geocoded

    if not property.address:
        return False

    city = extract_city_from_address(property.address)
    result = await geocode_address(property.address, city=city)

    if result:
        property.latitude = result["lat"]
        property.longitude = result["lon"]
        # Set PostGIS geometry
        property.geom = func.ST_SetSRID(
            func.ST_MakePoint(result["lon"], result["lat"]), 4326
        )
        logger.info(f"Geocoded property {property.id}: {result['lat']}, {result['lon']}")
        return True

    return False


async def geocode_batch(db: Session, batch_size: int = 100) -> dict:
    """Geocode a batch of properties that don't have coordinates.

    Deduplicates by address to avoid wasting API requests on identical addresses.
    Commits every 10 successful geocodes so progress is not lost.
    Aborts early after MAX_CONSECUTIVE_429 rate-limit responses.
    Returns stats about the geocoding run.
    """
    stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0}

    properties = (
        db.query(Property)
        .filter(
            Property.is_active == True,
            Property.latitude.is_(None),
            Property.address.isnot(None),
        )
        .limit(batch_size)
        .all()
    )

    stats["total"] = len(properties)
    logger.info(f"Geocoding batch of {len(properties)} properties")

    # Cache results by address to avoid duplicate API calls
    address_cache: dict[str, dict | None] = {}
    consecutive_429 = 0
    uncommitted = 0

    for prop in properties:
        address_key = prop.address.strip().lower() if prop.address else ""

        if address_key in address_cache:
            result = address_cache[address_key]
            if result:
                prop.latitude = result["lat"]
                prop.longitude = result["lon"]
                prop.geom = func.ST_SetSRID(
                    func.ST_MakePoint(result["lon"], result["lat"]), 4326
                )
                stats["success"] += 1
                uncommitted += 1
            else:
                stats["skipped"] += 1
            continue

        city = extract_city_from_address(prop.address)
        try:
            result = await geocode_address(prop.address, city=city)
            consecutive_429 = 0  # reset on any non-429 response
        except RateLimitExceeded:
            consecutive_429 += 1
            logger.warning(
                f"Nominatim 429 ({consecutive_429}/{MAX_CONSECUTIVE_429}) for property {prop.id}"
            )
            if consecutive_429 >= MAX_CONSECUTIVE_429:
                logger.error(
                    f"Aborting geocoding: {MAX_CONSECUTIVE_429} consecutive 429s. "
                    f"Committing {uncommitted} successful geocodes."
                )
                break
            # Back off before next attempt
            await asyncio.sleep(consecutive_429 * 5)
            result = None

        address_cache[address_key] = result

        if result:
            prop.latitude = result["lat"]
            prop.longitude = result["lon"]
            prop.geom = func.ST_SetSRID(
                func.ST_MakePoint(result["lon"], result["lat"]), 4326
            )
            logger.info(f"Geocoded property {prop.id}: {result['lat']}, {result['lon']}")
            stats["success"] += 1
            uncommitted += 1
        else:
            stats["failed"] += 1

        # Periodic commit to avoid losing progress and keep DB connection alive
        if uncommitted >= 10:
            db.commit()
            logger.info(f"Geocoding checkpoint: {stats}")
            uncommitted = 0

    if uncommitted > 0:
        db.commit()
    logger.info(f"Geocoding complete: {stats}")
    return stats
