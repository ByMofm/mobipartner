import {
  PropertyListResponse,
  PropertyDetail,
  PropertyFilters,
  PropertyListItem,
  Location,
  StatsOverview,
} from "./types";

// In production, API calls go through the Next.js proxy (/api/proxy/*)
// which adds the API key server-side, keeping it secret from the browser.
const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/proxy";

function buildUrl(path: string): URL {
  const full = `${API_URL}${path}`;
  // Relative URLs (e.g. /api/proxy/...) need a base when running in the browser
  if (full.startsWith("/")) {
    const base = typeof window !== "undefined" ? window.location.origin : "http://localhost:3000";
    return new URL(full, base);
  }
  return new URL(full);
}

async function fetchApi<T>(path: string, params?: Record<string, string | string[]>): Promise<T> {
  const url = buildUrl(path);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value === undefined || value === "") return;
      if (Array.isArray(value)) {
        value.forEach((v) => url.searchParams.append(key, v));
      } else {
        url.searchParams.set(key, value);
      }
    });
  }

  const res = await fetch(url.toString(), { headers: {} });
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export async function getProperties(
  filters: PropertyFilters = {}
): Promise<PropertyListResponse> {
  const params: Record<string, string | string[]> = {};
  if (filters.property_type?.length) params.property_type = filters.property_type;
  if (filters.listing_type) params.listing_type = filters.listing_type;
  if (filters.min_price) params.min_price = String(filters.min_price);
  if (filters.max_price) params.max_price = String(filters.max_price);
  if (filters.min_area) params.min_area = String(filters.min_area);
  if (filters.max_area) params.max_area = String(filters.max_area);
  if (filters.location_id) params.location_id = String(filters.location_id);
  if (filters.bedrooms) params.bedrooms = String(filters.bedrooms);
  if (filters.apto_credito) params.apto_credito = "true";
  if (filters.order_by) params.order_by = filters.order_by;
  if (filters.page) params.page = String(filters.page);
  if (filters.page_size) params.page_size = String(filters.page_size);
  return fetchApi<PropertyListResponse>("/properties", params);
}

export async function getProperty(id: number): Promise<PropertyDetail> {
  return fetchApi<PropertyDetail>(`/properties/${id}`);
}

export async function getMapProperties(params?: Record<string, string | string[]>): Promise<import("./types").PropertyMapItem[]> {
  return fetchApi<import("./types").PropertyMapItem[]>("/properties/map", params);
}

export async function getPropertiesByIds(ids: number[]): Promise<PropertyListResponse> {
  if (ids.length === 0) return { items: [], total: 0, page: 1, page_size: 0 };
  return fetchApi<PropertyListResponse>("/properties/by-ids", { ids: ids.join(",") });
}

export async function getSimilarProperties(id: number): Promise<PropertyListItem[]> {
  return fetchApi<PropertyListItem[]>(`/properties/${id}/similar`);
}

export async function getLocations(): Promise<Location[]> {
  return fetchApi<Location[]>("/locations");
}

export async function getStatsOverview(): Promise<StatsOverview> {
  return fetchApi<StatsOverview>("/stats/overview");
}

async function postApi<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = buildUrl(path);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== "") url.searchParams.set(key, value);
    });
  }
  const res = await fetch(url.toString(), { method: "POST", headers: {} });
  if (!res.ok) throw new Error(`API error: ${res.status} ${res.statusText}`);
  return res.json();
}

export async function triggerScrape(source: "zonaprop" | "argenprop" | "mercadolibre") {
  return postApi<{ status: string; spider: string; pid: number }>(`/scrape/trigger/${source}`);
}

export async function triggerGeocode() {
  return postApi<{ status: string; pending: number }>("/scrape/geocode");
}

export async function triggerScore() {
  return postApi<{ status: string }>("/scrape/score");
}

export async function triggerDedup() {
  return postApi<{ status: string }>("/scrape/dedup");
}

export async function triggerAssignLocations() {
  return postApi<{ status: string; assigned: number; total: number }>("/scrape/assign-locations");
}

export async function triggerBackfillAptoCredito() {
  return postApi<{ status: string; updated: number; flagged_properties: number }>("/scrape/backfill-apto-credito");
}

export async function triggerPipeline() {
  return postApi<{ status: string }>("/scrape/run-pipeline");
}

export interface ScheduleStatus {
  enabled: boolean;
  schedule: string;
  last_run_at: string | null;
  last_run_status: "running" | "completed" | "error" | null;
  last_run_steps: Array<{ step: string; status: string; elapsed_s: number; result?: unknown; error?: string }>;
  next_run_at: string | null;
}

export async function getScheduleStatus(): Promise<ScheduleStatus> {
  return fetchApi<ScheduleStatus>("/scrape/schedule");
}
