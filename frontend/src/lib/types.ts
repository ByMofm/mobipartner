export type PropertyType =
  | "apartment"
  | "house"
  | "ph"
  | "land"
  | "commercial"
  | "office"
  | "garage"
  | "warehouse";

export type ListingType = "sale" | "rent" | "temporary_rent";

export type CurrencyType = "ARS" | "USD";

export type SourceType = "zonaprop" | "argenprop" | "mercadolibre";

export interface PropertyListItem {
  id: number;
  property_type: PropertyType;
  listing_type: ListingType;
  address: string | null;
  current_price: number | null;
  current_currency: CurrencyType | null;
  current_price_usd: number | null;
  total_area_m2: number | null;
  covered_area_m2: number | null;
  bedrooms: number | null;
  bathrooms: number | null;
  garages: number | null;
  latitude: number | null;
  longitude: number | null;
  price_score: number | null;
  price_per_m2_usd: number | null;
  zone_score: number | null;
  condition_score: number | null;
  overall_score: number | null;
  is_active: boolean;
  first_seen_at: string;
  last_seen_at: string;
  thumbnail_url: string | null;
}

export interface PropertyListResponse {
  items: PropertyListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface Listing {
  id: number;
  source: SourceType;
  source_url: string;
  source_id: string;
  original_title: string | null;
  original_address: string | null;
  original_price: number | null;
  original_currency: CurrencyType | null;
  image_urls: string[];
  is_active: boolean;
  created_at: string;
}

export interface PriceHistory {
  id: number;
  price: number;
  currency: CurrencyType;
  price_usd: number | null;
  scraped_at: string;
}

export interface PriceContext {
  price_usd: number;
  median_usd: number;
  min_usd: number;
  max_usd: number;
  comparables_count: number;
  property_type: string;
  listing_type: string;
  price_per_m2_usd: number | null;
  median_per_m2_usd: number | null;
  num_comparables: number | null;
  comparison_scope: string | null;
  pct_vs_median: number | null;
}

export interface ImageAnalysis {
  condition_score: number | null;
  condition_label: string | null;
  renovation_state: string | null;
  natural_light: number | null;
  cleanliness: number | null;
  images_analyzed: number;
}

export interface PropertyDetail extends PropertyListItem {
  location_id: number | null;
  address_normalized: string | null;
  floor_number: number | null;
  rooms: number | null;
  has_pool: boolean;
  has_gym: boolean;
  has_laundry: boolean;
  has_security: boolean;
  has_balcony: boolean;
  price_score: number | null;
  price_per_m2_usd: number | null;
  price_context: PriceContext | null;
  image_analysis: ImageAnalysis | null;
  expenses_ars: number | null;
  listings: Listing[];
  price_history: PriceHistory[];
}

export interface Location {
  id: number;
  name: string;
  level: string;
  parent_id: number | null;
  children: Location[];
}

export interface StatsOverview {
  total_properties: number;
  active_properties: number;
  avg_price_usd_sale: number | null;
  avg_price_usd_rent: number | null;
  total_listings: number;
  sources: Record<string, number>;
  without_coords: number;
}

export interface PropertyMapItem {
  id: number;
  latitude: number | null;
  longitude: number | null;
  current_price: number | null;
  current_price_usd: number | null;
  current_currency: CurrencyType | null;
  property_type: PropertyType;
  listing_type: ListingType;
  address: string | null;
  total_area_m2: number | null;
  bedrooms: number | null;
  thumbnail_url: string | null;
}

export type OrderBy = "score_desc" | "price_asc" | "price_desc" | "newest";

export interface PropertyFilters {
  property_type?: PropertyType[];
  listing_type?: ListingType;
  min_price?: number;
  max_price?: number;
  min_area?: number;
  max_area?: number;
  location_id?: number;
  bedrooms?: number;
  apto_credito?: boolean;
  order_by?: OrderBy;
  page?: number;
  page_size?: number;
}
