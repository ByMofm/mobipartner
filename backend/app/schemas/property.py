from datetime import datetime

from pydantic import BaseModel

from app.models.enums import PropertyType, ListingType, CurrencyType, SourceType


class PropertyBase(BaseModel):
    property_type: PropertyType
    listing_type: ListingType
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    current_price: float | None = None
    current_currency: CurrencyType | None = None
    current_price_usd: float | None = None
    total_area_m2: float | None = None
    covered_area_m2: float | None = None
    rooms: int | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    garages: int | None = None
    age_years: int | None = None
    expenses_ars: float | None = None


class PropertyListItem(BaseModel):
    id: int
    property_type: PropertyType
    listing_type: ListingType
    address: str | None = None
    current_price: float | None = None
    current_currency: CurrencyType | None = None
    current_price_usd: float | None = None
    total_area_m2: float | None = None
    covered_area_m2: float | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    garages: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    price_score: int | None = None
    price_per_m2_usd: float | None = None
    zone_score: int | None = None
    condition_score: int | None = None
    overall_score: int | None = None
    apto_credito: bool = False
    is_active: bool
    first_seen_at: datetime
    last_seen_at: datetime
    thumbnail_url: str | None = None

    model_config = {"from_attributes": True}


class PropertyMapItem(BaseModel):
    id: int
    latitude: float | None = None
    longitude: float | None = None
    current_price: float | None = None
    current_price_usd: float | None = None
    current_currency: CurrencyType | None = None
    property_type: PropertyType
    listing_type: ListingType
    address: str | None = None
    total_area_m2: float | None = None
    bedrooms: int | None = None
    thumbnail_url: str | None = None

    model_config = {"from_attributes": True}


class ListingSchema(BaseModel):
    id: int
    source: SourceType
    source_url: str
    source_id: str
    original_title: str | None = None
    original_address: str | None = None
    original_price: float | None = None
    original_currency: CurrencyType | None = None
    image_urls: list[str] = []
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class PriceHistorySchema(BaseModel):
    id: int
    price: float
    currency: CurrencyType
    price_usd: float | None = None
    scraped_at: datetime

    model_config = {"from_attributes": True}


class PriceContextSchema(BaseModel):
    price_usd: float
    median_usd: float
    min_usd: float
    max_usd: float
    comparables_count: int
    property_type: str
    listing_type: str
    price_per_m2_usd: float | None = None
    median_per_m2_usd: float | None = None
    num_comparables: int | None = None
    comparison_scope: str | None = None  # barrio, ciudad, departamento, tipo_global
    pct_vs_median: float | None = None  # positive = above median, negative = below


class ImageAnalysisSchema(BaseModel):
    condition_score: int | None = None
    condition_label: str | None = None
    renovation_state: str | None = None
    natural_light: int | None = None
    cleanliness: int | None = None
    images_analyzed: int = 0

    model_config = {"from_attributes": True}


class PropertyDetail(PropertyListItem):
    location_id: int | None = None
    address_normalized: str | None = None
    floor_number: int | None = None
    has_pool: bool = False
    has_gym: bool = False
    has_laundry: bool = False
    has_security: bool = False
    has_balcony: bool = False
    price_score: int | None = None
    price_per_m2_usd: float | None = None
    price_context: PriceContextSchema | None = None
    image_analysis: ImageAnalysisSchema | None = None
    listings: list[ListingSchema] = []
    price_history: list[PriceHistorySchema] = []


class PropertyListResponse(BaseModel):
    items: list[PropertyListItem]
    total: int
    page: int
    page_size: int


class LocationSchema(BaseModel):
    id: int
    name: str
    level: str
    parent_id: int | None = None
    children: list["LocationSchema"] = []

    model_config = {"from_attributes": True}


class StatsOverview(BaseModel):
    total_properties: int
    active_properties: int
    avg_price_usd_sale: float | None = None
    avg_price_usd_rent: float | None = None
    total_listings: int
    sources: dict[str, int]
    without_coords: int = 0
