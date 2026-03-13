from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    Enum,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry

from app.database import Base
from app.models.enums import PropertyType, ListingType, CurrencyType, SourceType


class Property(Base):
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True)

    # Classification
    property_type = Column(Enum(PropertyType, create_type=False), nullable=False)
    listing_type = Column(Enum(ListingType), nullable=False)

    # Location
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    address = Column(String(500), nullable=True)
    address_normalized = Column(String(500), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    geom = Column(Geometry("POINT", srid=4326), nullable=True)

    # Current price (denormalized)
    current_price = Column(Float, nullable=True)
    current_currency = Column(Enum(CurrencyType), nullable=True)
    current_price_usd = Column(Float, nullable=True)

    # Features
    total_area_m2 = Column(Float, nullable=True)
    covered_area_m2 = Column(Float, nullable=True)
    rooms = Column(Integer, nullable=True)
    bedrooms = Column(Integer, nullable=True)
    bathrooms = Column(Integer, nullable=True)
    garages = Column(Integer, nullable=True)
    age_years = Column(Integer, nullable=True)
    floor_number = Column(Integer, nullable=True)

    # Financing
    apto_credito = Column(Boolean, default=False)

    # Amenities
    has_pool = Column(Boolean, default=False)
    has_gym = Column(Boolean, default=False)
    has_laundry = Column(Boolean, default=False)
    has_security = Column(Boolean, default=False)
    has_balcony = Column(Boolean, default=False)

    # Expenses
    expenses_ars = Column(Float, nullable=True)

    # Analysis (Phase 3)
    price_score = Column(Integer, nullable=True)
    price_per_m2_usd = Column(Float, nullable=True)

    # Scoring dimensions (Phase 4)
    zone_score = Column(Integer, nullable=True)       # 1-100, from zone quality
    condition_score = Column(Integer, nullable=True)   # 1-100, from image analysis
    overall_score = Column(Integer, nullable=True)     # 1-100, composite

    # Tracking
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    # Relationships
    location = relationship("Location", back_populates="properties")
    listings = relationship("PropertyListing", back_populates="property")
    price_history = relationship("PriceHistory", back_populates="property")


class PropertyListing(Base):
    __tablename__ = "property_listings"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_source_source_id"),)

    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=True)
    source = Column(Enum(SourceType), nullable=False)
    source_url = Column(String(1000), nullable=False)
    source_id = Column(String(255), nullable=False)

    original_title = Column(String(500), nullable=True)
    original_address = Column(String(500), nullable=True)
    original_price = Column(Float, nullable=True)
    original_currency = Column(Enum(CurrencyType), nullable=True)

    image_urls = Column(ARRAY(Text), default=[])
    raw_data = Column(JSONB, nullable=True)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    property = relationship("Property", back_populates="listings")
    price_history = relationship("PriceHistory", back_populates="listing")


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True)
    property_listing_id = Column(Integer, ForeignKey("property_listings.id"), nullable=False)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)
    price = Column(Float, nullable=False)
    currency = Column(Enum(CurrencyType), nullable=False)
    price_usd = Column(Float, nullable=True)
    usd_ars_rate = Column(Float, nullable=True)
    scraped_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    listing = relationship("PropertyListing", back_populates="price_history")
    property = relationship("Property", back_populates="price_history")


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id = Column(Integer, primary_key=True)
    source = Column(Enum(SourceType), nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    items_found = Column(Integer, default=0)
    items_new = Column(Integer, default=0)
    items_updated = Column(Integer, default=0)
    items_errors = Column(Integer, default=0)
    error_log = Column(Text, nullable=True)
