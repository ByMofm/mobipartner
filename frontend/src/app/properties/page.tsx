"use client";

import { Suspense, useState, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import dynamic from "next/dynamic";
import { useQuery } from "@tanstack/react-query";
import { getProperties } from "@/lib/api";
import PropertyCard from "@/components/PropertyCard";
import PropertyFiltersComponent from "@/components/PropertyFilters";
import { PropertyFilters, PropertyType, ListingType, OrderBy } from "@/lib/types";
import Container from "@mui/material/Container";
import Typography from "@mui/material/Typography";
import Box from "@mui/material/Box";
import Grid from "@mui/material/Grid2";
import Alert from "@mui/material/Alert";
import Skeleton from "@mui/material/Skeleton";
import Pagination from "@mui/material/Pagination";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import ToggleButton from "@mui/material/ToggleButton";
import ViewListIcon from "@mui/icons-material/ViewList";
import MapIcon from "@mui/icons-material/Map";
import ViewSidebarIcon from "@mui/icons-material/ViewSidebar";

const PropertyMap = dynamic(() => import("@/components/Map/PropertyMap"), {
  ssr: false,
  loading: () => <Skeleton variant="rectangular" height={500} sx={{ borderRadius: 2 }} />,
});

export default function PropertiesPage() {
  return (
    <Suspense fallback={<Container maxWidth="xl" sx={{ py: 3 }}><Skeleton variant="rectangular" height={400} /></Container>}>
      <PropertiesContent />
    </Suspense>
  );
}

function PropertiesContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const [viewMode, setViewMode] = useState<"list" | "map" | "split">("split");

  const [filters, setFilters] = useState<PropertyFilters>({
    property_type: searchParams.getAll("property_type").length ? (searchParams.getAll("property_type") as PropertyType[]) : undefined,
    listing_type: (searchParams.get("listing_type") as ListingType) || undefined,
    min_price: searchParams.get("min_price") ? Number(searchParams.get("min_price")) : undefined,
    max_price: searchParams.get("max_price") ? Number(searchParams.get("max_price")) : undefined,
    location_id: searchParams.get("location_id") ? Number(searchParams.get("location_id")) : undefined,
    bedrooms: searchParams.get("bedrooms") ? Number(searchParams.get("bedrooms")) : undefined,
    apto_credito: searchParams.get("apto_credito") === "true" || undefined,
    order_by: (searchParams.get("order_by") as OrderBy) || "score_desc",
    page: searchParams.get("page") ? Number(searchParams.get("page")) : 1,
    page_size: 20,
  });

  const { data, status, error } = useQuery({
    queryKey: ["properties", filters],
    queryFn: () => getProperties(filters),
  });

  const handleFiltersChange = useCallback(
    (newFilters: PropertyFilters) => {
      setFilters(newFilters);
      const params = new URLSearchParams();
      newFilters.property_type?.forEach((t) => params.append("property_type", t));
      if (newFilters.listing_type) params.set("listing_type", newFilters.listing_type);
      if (newFilters.min_price) params.set("min_price", String(newFilters.min_price));
      if (newFilters.max_price) params.set("max_price", String(newFilters.max_price));
      if (newFilters.location_id) params.set("location_id", String(newFilters.location_id));
      if (newFilters.bedrooms) params.set("bedrooms", String(newFilters.bedrooms));
      if (newFilters.apto_credito) params.set("apto_credito", "true");
      if (newFilters.order_by && newFilters.order_by !== "score_desc") params.set("order_by", newFilters.order_by);
      if (newFilters.page && newFilters.page > 1) params.set("page", String(newFilters.page));
      const qs = params.toString();
      router.push(qs ? `?${qs}` : "/properties", { scroll: false });
    },
    [router]
  );

  const handleMarkerClick = useCallback(
    (id: number) => router.push(`/properties/${id}`),
    [router]
  );

  const totalPages = data ? Math.ceil(data.total / data.page_size) : 0;

  const listContent = (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {status === "pending" && (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
          {[1, 2, 3, 4, 5].map((i) => (
            <Skeleton key={i} variant="rectangular" height={130} sx={{ borderRadius: 2 }} />
          ))}
        </Box>
      )}

      {data && (
        <>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            {data.total.toLocaleString("es-AR")} propiedades encontradas
          </Typography>

          <Box sx={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 1.5 }}>
            {data.items.map((property) => (
              <PropertyCard key={property.id} property={property} />
            ))}
          </Box>

          {totalPages > 1 && (
            <Box sx={{ display: "flex", justifyContent: "center", pt: 2 }}>
              <Pagination
                count={totalPages}
                page={filters.page || 1}
                onChange={(_, page) => handleFiltersChange({ ...filters, page })}
                color="secondary"
                size="small"
              />
            </Box>
          )}
        </>
      )}
    </Box>
  );

  return (
    <Container maxWidth="xl" sx={{ py: 3 }}>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 2 }}>
        <Typography variant="h5" fontWeight="bold">
          Propiedades
        </Typography>
        <ToggleButtonGroup
          value={viewMode}
          exclusive
          onChange={(_, v) => v && setViewMode(v)}
          size="small"
        >
          <ToggleButton value="list"><ViewListIcon fontSize="small" /></ToggleButton>
          <ToggleButton value="map"><MapIcon fontSize="small" /></ToggleButton>
          <ToggleButton value="split"><ViewSidebarIcon fontSize="small" /></ToggleButton>
        </ToggleButtonGroup>
      </Box>

      <PropertyFiltersComponent filters={filters} onChange={handleFiltersChange} />

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Error al cargar propiedades. Verifica que el backend este corriendo.
        </Alert>
      )}

      {viewMode === "list" && (
        <Box>
          {status === "pending" && (
            <Grid container spacing={2}>
              {[1, 2, 3, 4, 5, 6].map((i) => (
                <Grid key={i} size={{ xs: 12, md: 6, lg: 4 }}>
                  <Skeleton variant="rectangular" height={150} sx={{ borderRadius: 2 }} />
                </Grid>
              ))}
            </Grid>
          )}
          {data && (
            <>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                {data.total.toLocaleString("es-AR")} propiedades encontradas
              </Typography>
              <Grid container spacing={2}>
                {data.items.map((property) => (
                  <Grid key={property.id} size={{ xs: 12, md: 6, lg: 4 }}>
                    <PropertyCard property={property} />
                  </Grid>
                ))}
              </Grid>
              {totalPages > 1 && (
                <Box sx={{ display: "flex", justifyContent: "center", mt: 3 }}>
                  <Pagination
                    count={totalPages}
                    page={filters.page || 1}
                    onChange={(_, page) => handleFiltersChange({ ...filters, page })}
                    color="secondary"
                  />
                </Box>
              )}
            </>
          )}
        </Box>
      )}

      {viewMode === "map" && (
        <PropertyMap filters={filters} onMarkerClick={handleMarkerClick} />
      )}

      {viewMode === "split" && (
        <Grid container spacing={2} sx={{ height: 560 }}>
          <Grid size={{ xs: 12, lg: 6 }} sx={{ height: "100%" }}>
            <PropertyMap filters={filters} onMarkerClick={handleMarkerClick} />
          </Grid>
          <Grid size={{ xs: 12, lg: 6 }} sx={{ height: "100%", overflow: "hidden" }}>
            {listContent}
          </Grid>
        </Grid>
      )}
    </Container>
  );
}
