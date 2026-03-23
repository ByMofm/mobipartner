"""Property deduplication service.

Finds duplicate properties across different sources by comparing
address similarity, area, rooms, price, and geographic distance.
"""

import logging
import re
import unicodedata

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.property import Property, PropertyListing
from app.models.enums import PropertyType, ListingType

logger = logging.getLogger(__name__)

# Address normalization replacements
ADDRESS_REPLACEMENTS = {
    r"\bav\.?\b": "avenida",
    r"\bbv\.?\b": "boulevard",
    r"\bblvd\.?\b": "boulevard",
    r"\bcalle\b": "",
    r"\bpje\.?\b": "pasaje",
    r"\bpsje\.?\b": "pasaje",
    r"\bgral\.?\b": "general",
    r"\bsn\b": "sin numero",
    r"\bs/n\b": "sin numero",
    r"\bnro\.?\b": "",
    r"\bn°\b": "",
    r"\bn º\b": "",
    r"\bpiso\b": "",
    r"\bdpto\.?\b": "",
    r"\bdepto\.?\b": "",
    r"\bdto\.?\b": "",
    r"\b(\d+)(bis)\b": r"\1",
}

# Similarity weights
WEIGHT_ADDRESS = 0.40
WEIGHT_AREA = 0.25
WEIGHT_ROOMS = 0.15
WEIGHT_PRICE = 0.10
WEIGHT_DISTANCE = 0.10

MERGE_THRESHOLD = 0.75


def normalize_address(address: str | None) -> str:
    """Normalize an address for comparison."""
    if not address:
        return ""

    # Lowercase
    text = address.lower().strip()

    # Remove accents
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))

    # Apply replacements
    for pattern, replacement in ADDRESS_REPLACEMENTS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Remove extra whitespace
    text = re.sub(r"\s+", " ", text).strip()

    # Remove trailing location info after comma (city, province)
    parts = text.split(",")
    if len(parts) > 1:
        text = parts[0].strip()

    return text


def compute_area_similarity(area1: float | None, area2: float | None) -> float:
    """Compare two areas, return 0-1 similarity."""
    if area1 is None or area2 is None or area1 == 0 or area2 == 0:
        return 0.5  # neutral when missing
    ratio = min(area1, area2) / max(area1, area2)
    return ratio


def compute_rooms_similarity(rooms1: int | None, rooms2: int | None) -> float:
    """Compare room counts, return 0-1 similarity."""
    if rooms1 is None or rooms2 is None:
        return 0.5
    if rooms1 == rooms2:
        return 1.0
    diff = abs(rooms1 - rooms2)
    if diff == 1:
        return 0.5
    return 0.0


def compute_price_similarity(price1: float | None, price2: float | None) -> float:
    """Compare two prices, return 0-1 similarity."""
    if price1 is None or price2 is None or price1 == 0 or price2 == 0:
        return 0.5
    ratio = min(price1, price2) / max(price1, price2)
    return ratio


def compute_distance_similarity(
    lat1: float | None,
    lng1: float | None,
    lat2: float | None,
    lng2: float | None,
) -> float:
    """Compare geographic proximity, return 0-1 similarity.

    Uses a simple approximation. Within 100m = 1.0, over 1km = 0.0.
    """
    if lat1 is None or lng1 is None or lat2 is None or lng2 is None:
        return 0.5

    # Approximate distance in meters (at Tucumán latitude)
    dlat = abs(lat1 - lat2) * 111_000
    dlng = abs(lng1 - lng2) * 111_000 * 0.87  # cos(-26.8)
    dist = (dlat**2 + dlng**2) ** 0.5

    if dist < 100:
        return 1.0
    elif dist < 500:
        return 0.7
    elif dist < 1000:
        return 0.3
    return 0.0


def find_duplicate(
    db: Session,
    listing: PropertyListing,
    property_type: PropertyType,
    listing_type: ListingType,
    address_normalized: str,
    total_area: float | None,
    rooms: int | None,
    price: float | None,
    lat: float | None,
    lng: float | None,
) -> Property | None:
    """Find the best matching existing property for a new listing.

    Returns the matching Property if score > MERGE_THRESHOLD, else None.
    """
    if not address_normalized:
        return None

    # Find candidates: same type and listing type
    candidates = (
        db.query(Property)
        .filter(
            Property.property_type == property_type,
            Property.listing_type == listing_type,
            Property.is_active == True,
        )
    )

    # Narrow by area if available (±30% to cast a wider net for candidates)
    if total_area and total_area > 0:
        candidates = candidates.filter(
            Property.total_area_m2.between(total_area * 0.7, total_area * 1.3)
        )

    # Use pg_trgm to pre-filter by address similarity > 0.3
    if address_normalized:
        candidates = candidates.filter(
            func.similarity(Property.address_normalized, address_normalized) > 0.3
        )

    candidates = candidates.limit(50).all()

    if not candidates:
        return None

    best_match = None
    best_score = 0.0

    for candidate in candidates:
        # Address similarity via pg_trgm
        addr_sim = db.execute(
            text("SELECT similarity(:a, :b)"),
            {"a": address_normalized, "b": candidate.address_normalized or ""},
        ).scalar() or 0.0

        area_sim = compute_area_similarity(total_area, candidate.total_area_m2)
        rooms_sim = compute_rooms_similarity(rooms, candidate.rooms)
        price_sim = compute_price_similarity(price, candidate.current_price)
        dist_sim = compute_distance_similarity(lat, lng, candidate.latitude, candidate.longitude)

        score = (
            WEIGHT_ADDRESS * addr_sim
            + WEIGHT_AREA * area_sim
            + WEIGHT_ROOMS * rooms_sim
            + WEIGHT_PRICE * price_sim
            + WEIGHT_DISTANCE * dist_sim
        )

        if score > best_score:
            best_score = score
            best_match = candidate

    if best_score >= MERGE_THRESHOLD and best_match:
        logger.info(
            f"Dedup match: listing {listing.source_id} -> property {best_match.id} "
            f"(score={best_score:.2f})"
        )
        return best_match

    return None


def deduplicate_listing(db: Session, listing: PropertyListing) -> Property:
    """Process a listing for deduplication.

    If a matching property is found, link the listing to it.
    Otherwise, create a new property.
    Returns the linked/created Property.
    """
    address_normalized = normalize_address(listing.original_address)

    if listing.property_id:
        # Already linked, just update the normalized address on the property
        prop = db.query(Property).get(listing.property_id)
        if prop and not prop.address_normalized:
            prop.address_normalized = address_normalized
        return prop

    # Determine property type/listing type from existing property or parse from listing
    # We need these from the pipeline, which should have set them
    # Look at the existing property if linked, otherwise we need them from context
    property_type = None
    listing_type = None

    if listing.property_id:
        prop = db.query(Property).get(listing.property_id)
        if prop:
            property_type = prop.property_type
            listing_type = prop.listing_type

    if not property_type:
        # Default - this shouldn't happen if pipeline works correctly
        return None

    match = find_duplicate(
        db=db,
        listing=listing,
        property_type=property_type,
        listing_type=listing_type,
        address_normalized=address_normalized,
        total_area=None,
        rooms=None,
        price=listing.original_price,
        lat=None,
        lng=None,
    )

    if match:
        listing.property_id = match.id
        return match

    return None


def run_dedup_pass(db: Session, max_merges: int = 500) -> dict:
    """Run deduplication across all active properties.

    Two-phase approach to avoid Supabase statement timeouts:
    1. Read-only phase: normalize addresses and collect merge pairs (no writes).
    2. Write phase: execute each merge in an isolated fresh session.

    price_history.property_id is NOT updated here — it's a denormalized
    shortcut; the authoritative link is property_listing_id which IS updated.
    """
    from app.database import SessionLocal

    stats = {"checked": 0, "merged": 0, "failed": 0}

    # --- Phase 1: normalize addresses (batched commits, safe) ---
    to_normalize = db.query(Property).filter(Property.address_normalized.is_(None)).all()
    for i, prop in enumerate(to_normalize):
        prop.address_normalized = normalize_address(prop.address)
        if i % 100 == 99:
            db.commit()
    if to_normalize:
        db.commit()

    # --- Phase 1b: collect merge pairs (read-only, no writes) ---
    all_properties = (
        db.query(Property)
        .filter(Property.is_active == True, Property.address_normalized.isnot(None))
        .order_by(Property.id)
        .all()
    )

    groups: dict[tuple, list] = {}
    for prop in all_properties:
        key = (prop.property_type, prop.listing_type)
        groups.setdefault(key, []).append(prop)

    # Collect (keep_id, discard_id) pairs to merge
    merge_pairs: list[tuple[int, int]] = []
    discarded: set[int] = set()

    for (ptype, ltype), props in groups.items():
        if len(merge_pairs) >= max_merges:
            break
        for i, prop in enumerate(props):
            if prop.id in discarded:
                continue
            stats["checked"] += 1

            for other in props[i + 1:]:
                if other.id in discarded:
                    continue
                if len(merge_pairs) >= max_merges:
                    break

                # Quick area filter
                if prop.total_area_m2 and other.total_area_m2:
                    ratio = min(prop.total_area_m2, other.total_area_m2) / max(
                        prop.total_area_m2, other.total_area_m2
                    )
                    if ratio < 0.7:
                        continue

                addr_sim = 0.0
                if prop.address_normalized and other.address_normalized:
                    addr_sim = db.execute(
                        text("SELECT similarity(:a, :b)"),
                        {"a": prop.address_normalized, "b": other.address_normalized},
                    ).scalar() or 0.0

                score = (
                    WEIGHT_ADDRESS * addr_sim
                    + WEIGHT_AREA * compute_area_similarity(prop.total_area_m2, other.total_area_m2)
                    + WEIGHT_ROOMS * compute_rooms_similarity(prop.rooms, other.rooms)
                    + WEIGHT_PRICE * compute_price_similarity(prop.current_price, other.current_price)
                    + WEIGHT_DISTANCE * compute_distance_similarity(
                        prop.latitude, prop.longitude, other.latitude, other.longitude
                    )
                )

                if score >= MERGE_THRESHOLD:
                    merge_pairs.append((prop.id, other.id))
                    discarded.add(other.id)
                    logger.info(f"Dedup: will merge {other.id} -> {prop.id} (score={score:.2f})")

    logger.info(f"Dedup: {stats['checked']} checked, {len(merge_pairs)} pairs to merge")

    # --- Phase 2: execute each merge in its own isolated session ---
    for keep_id, discard_id in merge_pairs:
        merge_db = SessionLocal()
        try:
            # Reassign listings from discard -> keep
            merge_db.query(PropertyListing).filter(
                PropertyListing.property_id == discard_id
            ).update({"property_id": keep_id}, synchronize_session=False)

            # Deactivate the duplicate property
            merge_db.query(Property).filter(
                Property.id == discard_id
            ).update({"is_active": False}, synchronize_session=False)

            merge_db.commit()
            stats["merged"] += 1
            logger.info(f"Dedup: merged {discard_id} -> {keep_id}")
        except Exception as e:
            merge_db.rollback()
            stats["failed"] += 1
            logger.error(f"Dedup: failed merge {discard_id} -> {keep_id}: {e}")
        finally:
            merge_db.close()

    logger.info(f"Dedup pass complete: {stats}")
    return stats
