import logging
from collections import defaultdict
from statistics import median as _median

from sqlalchemy.orm import Session

from app.models.location import Location
from app.models.property import Property, PriceHistory
from app.models.enums import CurrencyType
from app.utils.currency import convert_to_usd

logger = logging.getLogger(__name__)

MIN_COMPARABLES = 5


def compute_all_scores(db: Session, usd_ars_rate: float) -> dict:
    """Orchestrator: backfill USD prices, invalidate bad ones, then compute scores."""
    backfilled = _backfill_usd_prices(db, usd_ars_rate)
    db.flush()  # ensure backfilled values are visible to the next query
    invalidated = _invalidate_bad_prices(db)
    db.flush()  # ensure invalidated NULLs are visible before scoring
    scored = _score_properties(db)
    db.commit()
    return {
        "properties_backfilled": backfilled,
        "properties_invalidated": invalidated,
        "properties_scored": scored,
        "usd_ars_rate": usd_ars_rate,
    }


def _backfill_usd_prices(db: Session, rate: float) -> int:
    """Fill current_price_usd and price_per_m2_usd for properties missing them."""
    props = (
        db.query(Property)
        .filter(
            Property.is_active == True,
            Property.current_price.isnot(None),
            Property.current_currency.isnot(None),
            Property.current_price_usd.is_(None),
        )
        .all()
    )

    count = 0
    for prop in props:
        price_usd = convert_to_usd(prop.current_price, prop.current_currency.value, rate)
        if price_usd is not None:
            prop.current_price_usd = price_usd
            if prop.total_area_m2 and prop.total_area_m2 > 0:
                prop.price_per_m2_usd = round(price_usd / prop.total_area_m2, 2)
            count += 1

    # Also backfill PriceHistory.price_usd
    histories = (
        db.query(PriceHistory)
        .filter(PriceHistory.price_usd.is_(None))
        .all()
    )
    for ph in histories:
        ph.price_usd = convert_to_usd(ph.price, ph.currency.value, rate)
        ph.usd_ars_rate = rate

    logger.info(f"Backfilled {count} properties, {len(histories)} price_history records")
    return count


# Minimum valid USD price per listing type — below these values are considered data errors
MIN_VALID_PRICE_USD = {
    "sale": 500.0,          # Nothing in Tucumán sells for under USD 500
    "rent": 10.0,           # Nothing rents for under USD 10/month
    "temporary_rent": 5.0,  # Nothing rents temporarily for under USD 5
}


def _invalidate_bad_prices(db: Session) -> int:
    """Null out current_price_usd for properties with clearly wrong USD values."""
    props = (
        db.query(Property)
        .filter(
            Property.is_active == True,
            Property.current_price_usd.isnot(None),
        )
        .all()
    )

    invalidated = 0
    for p in props:
        min_price = MIN_VALID_PRICE_USD.get(p.listing_type.value, 0)
        if p.current_price_usd < min_price:
            p.current_price_usd = None
            p.price_per_m2_usd = None
            p.price_score = None
            invalidated += 1

    logger.info(f"Invalidated {invalidated} properties with bad USD prices")
    return invalidated


def _score_properties(db: Session) -> int:
    """Score properties by price_per_m2_usd vs median of similar properties.

    Uses location cascade: barrio -> ciudad -> departamento -> global.
    Falls back to simple price percentile for properties without area data.
    """
    # Load location hierarchy
    all_locations = {loc.id: loc for loc in db.query(Location).all()}

    # Properties with price_per_m2 (primary scoring)
    props_with_m2 = (
        db.query(Property)
        .filter(
            Property.is_active == True,
            Property.price_per_m2_usd.isnot(None),
            Property.price_per_m2_usd > 0,
        )
        .all()
    )

    # Properties with price but no area (fallback scoring)
    props_no_m2 = (
        db.query(Property)
        .filter(
            Property.is_active == True,
            Property.current_price_usd.isnot(None),
            Property.price_per_m2_usd.is_(None),
        )
        .all()
    )

    scored = 0

    # --- Primary: price/m2 vs median with location cascade ---
    type_groups: dict[tuple, list[Property]] = defaultdict(list)
    for p in props_with_m2:
        key = (p.property_type, p.listing_type)
        type_groups[key].append(p)

    for (ptype, ltype), members in type_groups.items():
        # Build location-based sub-groups for this type
        loc_prices: dict[int, list[float]] = defaultdict(list)
        for p in members:
            if p.location_id:
                loc_prices[p.location_id].append(p.price_per_m2_usd)

        all_prices = [p.price_per_m2_usd for p in members]

        for p in members:
            comparables, scope = _find_comparables(
                p, loc_prices, all_locations, all_prices
            )

            if len(comparables) < MIN_COMPARABLES:
                p.price_score = None
                p.score_median_per_m2 = None
                p.score_num_comparables = None
                p.score_comparison_scope = None
                continue

            med = _median(comparables)

            if med > 0:
                ratio = (med - p.price_per_m2_usd) / med
            else:
                ratio = 0

            # Map: -0.5 (50% above median) -> 1, 0 (at median) -> 50, +0.5 (50% below) -> 100
            score = 50 + ratio * 100
            score = max(1, min(100, round(score)))

            p.price_score = score
            p.score_median_per_m2 = round(med, 2)
            p.score_num_comparables = len(comparables)
            p.score_comparison_scope = scope
            scored += 1

    # --- Fallback: simple price percentile for properties without area ---
    fallback_groups: dict[tuple, list[Property]] = defaultdict(list)
    for p in props_no_m2:
        min_price = MIN_VALID_PRICE_USD.get(p.listing_type.value, 0)
        if p.current_price_usd >= min_price:
            key = (p.property_type, p.listing_type)
            fallback_groups[key].append(p)
        else:
            p.price_score = None

    for key, members in fallback_groups.items():
        if len(members) < MIN_COMPARABLES:
            for p in members:
                p.price_score = None
            continue

        values = sorted([p.current_price_usd for p in members])
        total = len(values)
        for p in members:
            count_le = sum(1 for v in values if v <= p.current_price_usd)
            percentile = count_le / total
            score = round((1 - percentile) * 100)
            p.price_score = max(1, min(100, score))
            p.score_median_per_m2 = None
            p.score_num_comparables = total
            p.score_comparison_scope = "tipo_global"
            scored += 1

    logger.info(f"Scored {scored} properties ({len(props_with_m2)} with m2, {len(props_no_m2)} fallback)")
    return scored


def _find_comparables(
    prop: Property,
    loc_prices: dict[int, list[float]],
    all_locations: dict[int, Location],
    all_prices: list[float],
) -> tuple[list[float], str]:
    """Find comparison prices using location cascade: barrio -> ciudad -> departamento -> global."""

    # Try exact location (barrio)
    if prop.location_id and prop.location_id in loc_prices:
        prices = loc_prices[prop.location_id]
        if len(prices) >= MIN_COMPARABLES:
            return prices, "barrio"

    # Try parent location (ciudad) — collect all sibling barrios
    if prop.location_id and prop.location_id in all_locations:
        parent_id = all_locations[prop.location_id].parent_id
        if parent_id:
            child_ids = [
                lid for lid, loc in all_locations.items()
                if loc.parent_id == parent_id
            ]
            child_ids.append(parent_id)
            prices = []
            for lid in child_ids:
                prices.extend(loc_prices.get(lid, []))
            if len(prices) >= MIN_COMPARABLES:
                return prices, "ciudad"

            # Try grandparent (departamento)
            if parent_id in all_locations:
                gp_id = all_locations[parent_id].parent_id
                if gp_id:
                    desc_ids = _collect_descendants(gp_id, all_locations)
                    prices = []
                    for lid in desc_ids:
                        prices.extend(loc_prices.get(lid, []))
                    if len(prices) >= MIN_COMPARABLES:
                        return prices, "departamento"

    # Global fallback
    if len(all_prices) >= MIN_COMPARABLES:
        return all_prices, "tipo_global"

    return [], "none"


def _collect_descendants(parent_id: int, all_locations: dict[int, Location]) -> list[int]:
    """Collect all descendant location IDs (including the parent itself)."""
    result = [parent_id]
    queue = [parent_id]
    while queue:
        current = queue.pop()
        for lid, loc in all_locations.items():
            if loc.parent_id == current and lid not in result:
                result.append(lid)
                queue.append(lid)
    return result


def compute_overall_scores(db: Session) -> int:
    """Compute overall_score as average of available sub-scores (price, zone, condition).

    - 3 scores available: average of all 3
    - 2 scores available: average of 2
    - 1 score available: overall = that score
    - 0 scores: overall = None
    """
    props = (
        db.query(Property)
        .filter(Property.is_active == True)
        .all()
    )

    scored = 0
    for prop in props:
        scores = [s for s in (prop.price_score, prop.zone_score, prop.condition_score) if s is not None]
        if scores:
            prop.overall_score = round(sum(scores) / len(scores))
            scored += 1
        else:
            prop.overall_score = None

    db.commit()
    logger.info(f"Computed overall scores for {scored} properties")
    return scored


def get_price_context(db: Session, property_id: int) -> dict | None:
    """Get price context for a property within its group (property_type, listing_type)."""
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop or prop.current_price_usd is None:
        return None

    peers = (
        db.query(Property.current_price_usd)
        .filter(
            Property.is_active == True,
            Property.property_type == prop.property_type,
            Property.listing_type == prop.listing_type,
            Property.current_price_usd.isnot(None),
        )
        .all()
    )

    values = sorted([r[0] for r in peers])
    if len(values) < MIN_COMPARABLES:
        return None

    mid = len(values) // 2
    median_price = values[mid] if len(values) % 2 == 1 else round((values[mid - 1] + values[mid]) / 2, 2)

    # Percent vs median for price/m2
    pct_vs_median = None
    if prop.score_median_per_m2 and prop.price_per_m2_usd and prop.score_median_per_m2 > 0:
        pct_vs_median = round(
            (prop.price_per_m2_usd - prop.score_median_per_m2) / prop.score_median_per_m2 * 100, 1
        )

    return {
        "price_usd": round(prop.current_price_usd, 0),
        "median_usd": round(median_price, 0),
        "min_usd": round(values[0], 0),
        "max_usd": round(values[-1], 0),
        "comparables_count": len(values),
        "property_type": prop.property_type.value,
        "listing_type": prop.listing_type.value,
        "price_per_m2_usd": prop.price_per_m2_usd,
        "median_per_m2_usd": prop.score_median_per_m2,
        "num_comparables": prop.score_num_comparables,
        "comparison_scope": prop.score_comparison_scope,
        "pct_vs_median": pct_vs_median,
    }
