"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useProperty } from "@/hooks/useProperties";
import { useFavorites } from "@/contexts/FavoritesContext";
import { getSimilarProperties } from "@/lib/api";
import Container from "@mui/material/Container";
import Paper from "@mui/material/Paper";
import Typography from "@mui/material/Typography";
import Box from "@mui/material/Box";
import Grid from "@mui/material/Grid2";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Button from "@mui/material/Button";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TableCell from "@mui/material/TableCell";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Fade from "@mui/material/Fade";
import LinearProgress from "@mui/material/LinearProgress";
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import PoolIcon from "@mui/icons-material/Pool";
import FitnessCenterIcon from "@mui/icons-material/FitnessCenter";
import SecurityIcon from "@mui/icons-material/Security";
import BalconyIcon from "@mui/icons-material/Balcony";
import LocalLaundryServiceIcon from "@mui/icons-material/LocalLaundryService";
import FavoriteIcon from "@mui/icons-material/Favorite";
import FavoriteBorderIcon from "@mui/icons-material/FavoriteBorder";
import PropertyCard from "@/components/PropertyCard";
import PriceHistoryChart from "@/components/PriceHistoryChart";
import PriceGauge from "@/components/PriceGauge";

const TYPE_LABELS: Record<string, string> = {
  apartment: "Departamento",
  house: "Casa",
  ph: "PH",
  land: "Terreno",
  commercial: "Local",
  office: "Oficina",
  garage: "Cochera",
  warehouse: "Galpon",
};

const LISTING_LABELS: Record<string, string> = {
  sale: "Venta",
  rent: "Alquiler",
  temporary_rent: "Alquiler Temporario",
};

const SOURCE_LABELS: Record<string, string> = {
  zonaprop: "ZonaProp",
  argenprop: "Argenprop",
  mercadolibre: "MercadoLibre",
};

function formatPrice(price: number | null, currency: string | null): string {
  if (!price) return "Consultar";
  const formatted = price.toLocaleString("es-AR");
  return currency === "USD" ? `USD ${formatted}` : `$ ${formatted}`;
}

function ImageGallery({ images }: { images: string[] }) {
  const [current, setCurrent] = useState(0);

  if (images.length === 0) return null;

  return (
    <Box sx={{ mb: 3 }}>
      <Box sx={{ position: "relative", borderRadius: 2, overflow: "hidden", bgcolor: "grey.200" }}>
        <Box
          component="img"
          src={images[current]}
          alt={`Imagen ${current + 1}`}
          sx={{ width: "100%", height: 320, objectFit: "cover", display: "block" }}
        />
        {images.length > 1 && (
          <>
            <IconButton
              onClick={() => setCurrent((c) => (c > 0 ? c - 1 : images.length - 1))}
              sx={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", bgcolor: "rgba(0,0,0,0.5)", color: "white", "&:hover": { bgcolor: "rgba(0,0,0,0.7)" } }}
              size="small"
            >
              <ChevronLeftIcon />
            </IconButton>
            <IconButton
              onClick={() => setCurrent((c) => (c < images.length - 1 ? c + 1 : 0))}
              sx={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", bgcolor: "rgba(0,0,0,0.5)", color: "white", "&:hover": { bgcolor: "rgba(0,0,0,0.7)" } }}
              size="small"
            >
              <ChevronRightIcon />
            </IconButton>
            <Chip
              label={`${current + 1} / ${images.length}`}
              size="small"
              sx={{ position: "absolute", bottom: 8, right: 8, bgcolor: "rgba(0,0,0,0.5)", color: "white" }}
            />
          </>
        )}
      </Box>
      {images.length > 1 && (
        <Stack direction="row" spacing={0.5} sx={{ mt: 1, overflowX: "auto" }}>
          {images.slice(0, 8).map((img, i) => (
            <Box
              key={i}
              component="img"
              src={img}
              alt={`Miniatura ${i + 1}`}
              onClick={() => setCurrent(i)}
              sx={{
                width: 64,
                height: 48,
                objectFit: "cover",
                borderRadius: 1,
                cursor: "pointer",
                border: 2,
                borderColor: i === current ? "secondary.main" : "transparent",
              }}
            />
          ))}
          {images.length > 8 && (
            <Typography variant="caption" color="text.secondary" sx={{ display: "flex", alignItems: "center", px: 1 }}>
              +{images.length - 8}
            </Typography>
          )}
        </Stack>
      )}
    </Box>
  );
}

export default function PropertyDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const router = useRouter();
  const propertyId = Number(params.id);
  const { data: property, isLoading, error } = useProperty(propertyId);
  const { isFavorite, addFavorite, removeFavorite } = useFavorites();
  const fav = isFavorite(propertyId);

  const { data: similar } = useQuery({
    queryKey: ["similar", propertyId],
    queryFn: () => getSimilarProperties(propertyId),
    enabled: !!property,
  });

  if (isLoading) {
    return (
      <Container maxWidth="md" sx={{ py: 4 }}>
        <Skeleton variant="rectangular" height={320} sx={{ borderRadius: 2, mb: 2 }} animation="wave" />
        <Skeleton variant="text" width="60%" height={40} animation="wave" />
        <Skeleton variant="text" width="40%" height={30} animation="wave" />
      </Container>
    );
  }

  if (error || !property) {
    return (
      <Container maxWidth="md" sx={{ py: 6, textAlign: "center" }}>
        <Typography color="error">Propiedad no encontrada</Typography>
      </Container>
    );
  }

  const allImages = property.listings.flatMap((l) => l.image_urls);
  const uniqueImages = Array.from(new Set(allImages));

  const mainScore = property.overall_score ?? property.price_score;
  const scoreLabel =
    mainScore != null
      ? property.overall_score != null
        ? mainScore >= 70 ? "Excelente" : mainScore >= 40 ? "Bueno" : "Regular"
        : mainScore >= 70 ? "Buen precio" : mainScore >= 40 ? "Precio justo" : "Sobreprecio"
      : null;

  const scoreColor: "success" | "warning" | "error" =
    mainScore != null
      ? mainScore >= 70 ? "success" : mainScore >= 40 ? "warning" : "error"
      : "success";

  const hasScoreBreakdown = property.price_score != null || property.zone_score != null || property.condition_score != null;

  return (
    <Container maxWidth="md" sx={{ py: 4 }}>
      <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
        <Button
          startIcon={<ChevronLeftIcon />}
          onClick={() => router.back()}
        >
          Volver
        </Button>
        <IconButton
          onClick={() => (fav ? removeFavorite(propertyId) : addFavorite(propertyId))}
          color={fav ? "error" : "default"}
        >
          {fav ? <FavoriteIcon /> : <FavoriteBorderIcon />}
        </IconButton>
      </Stack>

      <Fade in timeout={300}>
        <Paper sx={{ p: 3 }}>
          <ImageGallery images={uniqueImages} />

          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", mb: 3, gap: 2 }}>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 0.5 }}>
                <Typography variant="body2" color="text.secondary">
                  {TYPE_LABELS[property.property_type]}
                </Typography>
                <Chip label={LISTING_LABELS[property.listing_type]} size="small" variant="outlined" />
                {property.listings.length > 1 && (
                  <Chip label={`${property.listings.length} fuentes`} size="small" color="secondary" variant="outlined" />
                )}
              </Stack>
              <Typography variant="h4" fontWeight="bold">
                {formatPrice(property.current_price, property.current_currency)}
              </Typography>
              {property.address && (
                <Typography variant="body1" color="text.secondary" sx={{ mt: 0.5 }}>
                  {property.address}
                </Typography>
              )}
            </Box>
            {scoreLabel && (
              <Paper
                elevation={0}
                sx={{
                  flexShrink: 0,
                  textAlign: "center",
                  px: 3,
                  py: 2,
                  borderRadius: 3,
                  bgcolor: `${scoreColor}.light`,
                  color: `${scoreColor}.dark`,
                  border: 1,
                  borderColor: `${scoreColor}.main`,
                }}
              >
                <Typography variant="subtitle1" fontWeight="bold">
                  {scoreLabel}
                </Typography>
                {property.price_context && (
                  <Typography variant="caption" sx={{ opacity: 0.85 }}>
                    {property.price_context.pct_vs_median != null ? (
                      <>
                        {property.price_context.pct_vs_median < 0
                          ? `${Math.abs(property.price_context.pct_vs_median)}% debajo`
                          : property.price_context.pct_vs_median > 0
                          ? `${property.price_context.pct_vs_median}% encima`
                          : "En"} de la mediana
                        <br />
                        {property.price_context.median_per_m2_usd != null && (
                          <>USD {property.price_context.median_per_m2_usd.toLocaleString("es-AR")}/m2<br /></>
                        )}
                      </>
                    ) : (
                      <>
                        USD {property.price_context.price_usd?.toLocaleString("es-AR")}
                        <br />
                        mediana USD {property.price_context.median_usd?.toLocaleString("es-AR")}
                        <br />
                      </>
                    )}
                    ({property.price_context.num_comparables ?? property.price_context.comparables_count} similares
                    {property.price_context.comparison_scope === "barrio" && " en tu zona"}
                    {property.price_context.comparison_scope === "ciudad" && " en la ciudad"}
                    {property.price_context.comparison_scope === "departamento" && " en el departamento"}
                    {property.price_context.comparison_scope === "tipo_global" && " en la provincia"})
                  </Typography>
                )}
              </Paper>
            )}
          </Box>

          {/* Price Gauge */}
          {property.price_context && <PriceGauge context={property.price_context} />}

          {/* Score Breakdown */}
          {hasScoreBreakdown && (
            <Box sx={{ my: 3, p: 2, bgcolor: "grey.50", borderRadius: 2 }}>
              <Typography variant="subtitle2" fontWeight="bold" sx={{ mb: 1.5 }}>
                Desglose de puntaje
              </Typography>
              <Stack spacing={1.5}>
                {property.price_score != null && (
                  <Box>
                    <Box sx={{ display: "flex", justifyContent: "space-between", mb: 0.5 }}>
                      <Typography variant="caption">
                        Precio
                        {property.price_context?.comparison_scope === "barrio" && " (vs zona)"}
                        {property.price_context?.comparison_scope === "ciudad" && " (vs ciudad)"}
                        {property.price_context?.comparison_scope === "departamento" && " (vs depto)"}
                        {property.price_context?.comparison_scope === "tipo_global" && " (vs provincia)"}
                      </Typography>
                      <Typography variant="caption" fontWeight="bold">{property.price_score}/100</Typography>
                    </Box>
                    <LinearProgress
                      variant="determinate"
                      value={property.price_score}
                      color={property.price_score >= 70 ? "success" : property.price_score >= 40 ? "warning" : "error"}
                      sx={{ height: 8, borderRadius: 4 }}
                    />
                  </Box>
                )}
                {property.zone_score != null && (
                  <Box>
                    <Box sx={{ display: "flex", justifyContent: "space-between", mb: 0.5 }}>
                      <Typography variant="caption">Zona</Typography>
                      <Typography variant="caption" fontWeight="bold">{property.zone_score}/100</Typography>
                    </Box>
                    <LinearProgress
                      variant="determinate"
                      value={property.zone_score}
                      color={property.zone_score >= 60 ? "success" : property.zone_score >= 40 ? "warning" : "error"}
                      sx={{ height: 8, borderRadius: 4 }}
                    />
                  </Box>
                )}
                {property.condition_score != null && (
                  <Box>
                    <Box sx={{ display: "flex", justifyContent: "space-between", mb: 0.5 }}>
                      <Typography variant="caption">
                        Estado del inmueble
                        {property.image_analysis?.condition_label && ` — ${property.image_analysis.condition_label}`}
                      </Typography>
                      <Typography variant="caption" fontWeight="bold">{property.condition_score}/100</Typography>
                    </Box>
                    <LinearProgress
                      variant="determinate"
                      value={property.condition_score}
                      color={property.condition_score >= 60 ? "success" : property.condition_score >= 40 ? "warning" : "error"}
                      sx={{ height: 8, borderRadius: 4 }}
                    />
                    {property.image_analysis?.renovation_state && (
                      <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
                        Renovación: {property.image_analysis.renovation_state.replace("_", " ")}
                      </Typography>
                    )}
                  </Box>
                )}
              </Stack>
            </Box>
          )}

          {/* Features */}
          <Grid container spacing={2} sx={{ py: 2, borderTop: 1, borderBottom: 1, borderColor: "divider" }}>
            {property.total_area_m2 && (
              <Grid size={{ xs: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">Superficie Total</Typography>
                <Typography variant="subtitle1" fontWeight="bold">{property.total_area_m2} m2</Typography>
              </Grid>
            )}
            {property.covered_area_m2 && (
              <Grid size={{ xs: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">Superficie Cubierta</Typography>
                <Typography variant="subtitle1" fontWeight="bold">{property.covered_area_m2} m2</Typography>
              </Grid>
            )}
            {property.rooms != null && (
              <Grid size={{ xs: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">Ambientes</Typography>
                <Typography variant="subtitle1" fontWeight="bold">{property.rooms}</Typography>
              </Grid>
            )}
            {property.bedrooms != null && (
              <Grid size={{ xs: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">Dormitorios</Typography>
                <Typography variant="subtitle1" fontWeight="bold">{property.bedrooms}</Typography>
              </Grid>
            )}
            {property.bathrooms != null && (
              <Grid size={{ xs: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">Banos</Typography>
                <Typography variant="subtitle1" fontWeight="bold">{property.bathrooms}</Typography>
              </Grid>
            )}
            {property.garages != null && (
              <Grid size={{ xs: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">Cocheras</Typography>
                <Typography variant="subtitle1" fontWeight="bold">{property.garages}</Typography>
              </Grid>
            )}
            {property.expenses_ars != null && (
              <Grid size={{ xs: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">Expensas</Typography>
                <Typography variant="subtitle1" fontWeight="bold">$ {property.expenses_ars?.toLocaleString("es-AR")}</Typography>
              </Grid>
            )}
            {property.price_per_m2_usd != null && (
              <Grid size={{ xs: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">USD/m2</Typography>
                <Typography variant="subtitle1" fontWeight="bold">USD {property.price_per_m2_usd}</Typography>
              </Grid>
            )}
          </Grid>

          {/* Amenities */}
          {(property.has_pool || property.has_gym || property.has_laundry || property.has_security || property.has_balcony) && (
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mt: 2 }}>
              {property.has_pool && <Chip icon={<PoolIcon />} label="Pileta" variant="outlined" size="small" />}
              {property.has_gym && <Chip icon={<FitnessCenterIcon />} label="Gimnasio" variant="outlined" size="small" />}
              {property.has_laundry && <Chip icon={<LocalLaundryServiceIcon />} label="Lavadero" variant="outlined" size="small" />}
              {property.has_security && <Chip icon={<SecurityIcon />} label="Seguridad" variant="outlined" size="small" />}
              {property.has_balcony && <Chip icon={<BalconyIcon />} label="Balcon" variant="outlined" size="small" />}
            </Stack>
          )}

          {/* Listings */}
          {property.listings.length > 0 && (
            <Box sx={{ mt: 4 }}>
              <Typography variant="h6" sx={{ mb: 2 }}>
                Publicaciones ({property.listings.length})
              </Typography>
              <Table size="small">
                <TableBody>
                  {property.listings.map((listing) => (
                    <TableRow key={listing.id}>
                      <TableCell>
                        <Typography variant="body2" fontWeight="bold">
                          {SOURCE_LABELS[listing.source] || listing.source}
                        </Typography>
                        {listing.original_title && (
                          <Typography variant="caption" color="text.secondary" noWrap sx={{ display: "block", maxWidth: 300 }}>
                            {listing.original_title}
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        {listing.original_price && (
                          <Typography variant="body2" color="text.secondary">
                            {formatPrice(listing.original_price, listing.original_currency)}
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        <Chip
                          label={listing.is_active ? "Activo" : "Inactivo"}
                          color={listing.is_active ? "success" : "error"}
                          size="small"
                          variant="outlined"
                        />
                      </TableCell>
                      <TableCell align="right">
                        <Button
                          href={listing.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          size="small"
                          color="secondary"
                        >
                          Ver en sitio
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          )}

          {/* Price History Chart + Table */}
          {property.price_history.length > 0 && (
            <Box sx={{ mt: 4 }}>
              <Typography variant="h6" sx={{ mb: 2 }}>
                Historial de Precios
              </Typography>
              <PriceHistoryChart
                priceHistory={property.price_history}
                medianUsd={property.price_context?.median_usd}
              />
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Fecha</TableCell>
                    <TableCell>Precio</TableCell>
                    <TableCell>USD</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {property.price_history.map((ph) => (
                    <TableRow key={ph.id}>
                      <TableCell>{new Date(ph.scraped_at).toLocaleDateString("es-AR")}</TableCell>
                      <TableCell>{formatPrice(ph.price, ph.currency)}</TableCell>
                      <TableCell>{ph.price_usd ? `USD ${ph.price_usd.toLocaleString("es-AR")}` : "-"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          )}
        </Paper>
      </Fade>

      {/* Similar Properties */}
      {similar && similar.length > 0 && (
        <Box sx={{ mt: 4 }}>
          <Typography variant="h6" fontWeight="bold" sx={{ mb: 2 }}>
            Propiedades similares
          </Typography>
          <Box
            sx={{
              display: "flex",
              gap: 2,
              overflowX: "auto",
              pb: 1,
              "& > *": { minWidth: 320, flexShrink: 0 },
            }}
          >
            {similar.map((p) => (
              <PropertyCard key={p.id} property={p} />
            ))}
          </Box>
        </Box>
      )}
    </Container>
  );
}
