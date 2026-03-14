import json
import re

import scrapy

from mobipartner_scrapy.items import PropertyItem


class InmoClickSpider(scrapy.Spider):
    """Spider for InmoClick — aggregator with embedded JSON data.

    No Playwright needed: property data is in a JS variable `var propiedades = [...]`
    rendered server-side.
    """

    name = "inmoclick"
    allowed_domains = ["inmoclick.com"]

    MAX_PAGES = 35

    SEARCHES = [
        ("venta", "venta"),
        ("alquiler", "alquiler"),
    ]

    custom_settings = {
        # Override Playwright handlers — this spider uses plain HTTP
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy.core.downloader.handlers.http.HTTPDownloadHandler",
            "https": "scrapy.core.downloader.handlers.http.HTTPDownloadHandler",
        },
        "DOWNLOAD_DELAY": 2,
        "CONCURRENT_REQUESTS": 2,
        "ROBOTSTXT_OBEY": False,
        "USER_AGENT": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    }

    PROPERTY_TYPE_MAP = {
        "departamento": "departamento",
        "departamentos": "departamento",
        "casa": "casa",
        "casas": "casa",
        "terreno": "terreno",
        "terrenos": "terreno",
        "local": "local",
        "locales": "local",
        "oficina": "oficina",
        "ph": "ph",
        "cochera": "cochera",
        "galpón": "galpon",
        "galpon": "galpon",
    }

    def start_requests(self):
        for operacion, listing_type in self.SEARCHES:
            for page in range(1, self.MAX_PAGES + 1):
                url = f"https://inmoclick.com/inmuebles/tucuman?operacion={operacion}&page={page}"
                yield scrapy.Request(
                    url,
                    meta={"listing_type": listing_type, "page": page},
                    callback=self.parse_listing_page,
                    errback=self.handle_error,
                )

    def handle_error(self, failure):
        self.logger.error(f"Request failed: {failure.request.url} — {failure.value}")

    def parse_listing_page(self, response):
        """Extract property data from embedded JSON in page source."""
        # Look for the JS variable containing property data
        match = re.search(
            r"var\s+propiedades\s*=\s*(\[.*?\])\s*;",
            response.text,
            re.DOTALL,
        )
        if not match:
            self.logger.info(
                f"No propiedades found on page {response.meta['page']} — "
                f"likely last page"
            )
            return

        try:
            properties = json.loads(match.group(1))
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse JSON on {response.url}: {e}")
            return

        self.logger.info(
            f"Page {response.meta['page']}: {len(properties)} properties found"
        )

        if not properties:
            return

        listing_type = response.meta["listing_type"]
        known_ids = getattr(self, "known_source_ids", set())

        for prop in properties:
            source_id = str(prop.get("prp_id", ""))
            if not source_id:
                continue

            item = self._build_item_from_json(prop, listing_type, source_id)

            # Visit detail page only for new listings
            if source_id not in known_ids:
                detail_url = self._build_detail_url(prop)
                if detail_url:
                    yield scrapy.Request(
                        detail_url,
                        meta={"item_data": dict(item), "source_id": source_id},
                        callback=self.parse_detail,
                        errback=self._detail_error,
                        dont_filter=True,
                    )
                    continue

            yield item

    def _build_item_from_json(self, prop, listing_type, source_id):
        """Build a PropertyItem from the embedded JSON object."""
        item = PropertyItem()
        item["source"] = "inmoclick"
        item["source_id"] = source_id

        # Price
        price_usd = prop.get("prp_pre_dol")
        price_ars = prop.get("prp_pre_pes")
        if price_usd and float(price_usd) > 0:
            item["price"] = float(price_usd)
            item["currency"] = "USD"
        elif price_ars and float(price_ars) > 0:
            item["price"] = float(price_ars)
            item["currency"] = "ARS"
        else:
            item["price"] = None
            item["currency"] = None

        # Coordinates
        lat = prop.get("prp_lat")
        lng = prop.get("prp_lng")
        item["latitude"] = float(lat) if lat else None
        item["longitude"] = float(lng) if lng else None

        # Property type
        tipo = (prop.get("tipo_propiedad") or prop.get("nombre") or "").lower()
        item["property_type"] = self.PROPERTY_TYPE_MAP.get(
            tipo.split()[0] if tipo else "", "departamento"
        )
        item["listing_type"] = listing_type

        # Specs
        item["total_area_m2"] = self._float_or_none(prop.get("superficie_total"))
        item["covered_area_m2"] = self._float_or_none(prop.get("superficie_cubierta"))
        item["bedrooms"] = self._int_or_none(prop.get("dormitorios"))
        item["bathrooms"] = self._int_or_none(prop.get("banos"))
        item["rooms"] = self._int_or_none(prop.get("ambientes"))
        item["garages"] = self._int_or_none(prop.get("cocheras"))
        item["age_years"] = self._int_or_none(prop.get("antiguedad"))

        # Address
        address_parts = [
            prop.get("calle", ""),
            prop.get("numero", ""),
            prop.get("barrio", ""),
            prop.get("localidad", ""),
        ]
        item["address"] = ", ".join(p.strip() for p in address_parts if p and p.strip())
        if not item["address"]:
            item["address"] = prop.get("nombre", "")

        item["title"] = prop.get("nombre", "") or item["address"]

        # Build source URL
        detail_url = self._build_detail_url(prop)
        item["source_url"] = detail_url or f"https://inmoclick.com/inmuebles/{source_id}"

        item["description"] = ""
        item["image_urls"] = []
        item["apto_credito"] = False
        item["raw_data"] = {"inmoclick_json": prop}

        return item

    def _build_detail_url(self, prop):
        """Build detail page URL from JSON data."""
        usr_id = prop.get("usr_id", "")
        prp_id = prop.get("prp_id", "")
        nombre = prop.get("nombre", "")
        if usr_id and prp_id:
            slug = re.sub(r"[^a-z0-9]+", "-", nombre.lower().strip()).strip("-")
            return f"https://inmoclick.com/{usr_id}-/inmuebles/{prp_id}/ficha/{slug}"
        return None

    def _detail_error(self, failure):
        """If detail fails, yield the item from JSON data alone."""
        self.logger.warning(f"Detail failed, using JSON data: {failure.value}")
        item_data = failure.request.meta.get("item_data")
        if item_data:
            item = PropertyItem()
            for k, v in item_data.items():
                item[k] = v
            return item

    def parse_detail(self, response):
        """Parse detail page for description, images, and additional specs."""
        item_data = response.meta["item_data"]
        item = PropertyItem()
        for k, v in item_data.items():
            item[k] = v

        # Description
        desc_parts = response.css(
            ".descripcion-propiedad *::text, "
            ".description *::text, "
            "#descripcion *::text"
        ).getall()
        desc = " ".join(t.strip() for t in desc_parts if t.strip())
        if desc:
            item["description"] = desc

        # Images
        images = response.css(
            "img[src*='inmoclick']::attr(src), "
            "img[src*='inmueble']::attr(src), "
            "img[data-src*='inmoclick']::attr(data-src), "
            ".gallery img::attr(src), "
            ".carousel img::attr(src), "
            ".slider img::attr(src)"
        ).getall()
        image_urls = [
            img for img in dict.fromkeys(images)
            if img and "logo" not in img and "placeholder" not in img
        ]
        if image_urls:
            item["image_urls"] = image_urls

        # Title from page
        title = response.css("h1::text").get("").strip()
        if title:
            item["title"] = title

        # Address from page (may be more complete)
        address = response.css(
            ".ubicacion::text, .direccion::text, .address::text"
        ).get("").strip()
        if address and len(address) > len(item.get("address", "")):
            item["address"] = address

        # Apto crédito check
        all_text = (
            item.get("source_url", "") + " " +
            item.get("description", "") + " " +
            item.get("title", "")
        ).lower()
        item["apto_credito"] = (
            "crédito" in all_text or "credito" in all_text or "hipotecario" in all_text
        )

        yield item

    @staticmethod
    def _float_or_none(val):
        if val is None:
            return None
        try:
            v = float(val)
            return v if v > 0 else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _int_or_none(val):
        if val is None:
            return None
        try:
            v = int(float(val))
            return v if v > 0 else None
        except (ValueError, TypeError):
            return None
