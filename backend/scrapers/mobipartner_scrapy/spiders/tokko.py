import re

import scrapy
from scrapy_playwright.page import PageMethod

from mobipartner_scrapy.items import PropertyItem


class TokkoSpider(scrapy.Spider):
    """Base spider for sites built on Tokko Broker CRM.

    Subclasses must define: name, BASE_URL, allowed_domains.
    Tokko sites use infinite scroll and share the same DOM structure.
    """

    BASE_URL = ""
    MAX_PAGES = 20

    SEARCHES = [
        ("Departamentos", "Venta", "departamento", "venta"),
        ("Departamentos", "Alquiler", "departamento", "alquiler"),
        ("Casas", "Venta", "casa", "venta"),
        ("Casas", "Alquiler", "casa", "alquiler"),
        ("Terrenos", "Venta", "terreno", "venta"),
        ("Locales", "Venta", "local", "venta"),
        ("Locales", "Alquiler", "local", "alquiler"),
        ("Oficinas", "Venta", "oficina", "venta"),
        ("Oficinas", "Alquiler", "oficina", "alquiler"),
    ]

    custom_settings = {
        "PLAYWRIGHT_BROWSER_TYPE": "firefox",
        "DOWNLOAD_DELAY": 3,
        "CONCURRENT_REQUESTS": 1,
        "ROBOTSTXT_OBEY": False,
        "COOKIES_ENABLED": False,
        "USER_AGENT": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    }

    def start_requests(self):
        for tipo, operacion, prop_type, listing_type in self.SEARCHES:
            url = f"{self.BASE_URL}/Buscar-{tipo}-en-{operacion}"
            yield scrapy.Request(
                url,
                meta={
                    "playwright": True,
                    "playwright_context": f"tokko-{self.name}-{tipo}-{operacion}",
                    "playwright_page_methods": [
                        PageMethod("wait_for_load_state", "networkidle"),
                        PageMethod("wait_for_timeout", 2000),
                    ],
                    "property_type": prop_type,
                    "listing_type": listing_type,
                    "page": 1,
                },
                callback=self.parse_listing_page,
                errback=self.handle_error,
            )

    def handle_error(self, failure):
        self.logger.error(f"Request failed: {failure.request.url} — {failure.value}")

    def parse_listing_page(self, response):
        """Parse Tokko listing page with property cards."""
        cards = response.css(".resultados-list li, .property-list li, .prop-list li")

        self.logger.info(
            f"Page {response.meta['page']} — {response.url}: {len(cards)} cards"
        )

        # Check for "no more properties" marker
        if "--NoMoreProperties--" in response.text or not cards:
            self.logger.info("No more properties found, stopping pagination")
            return

        # Extract coordinates from JS markers
        geo_map = self._extract_markers(response)

        known_ids = getattr(self, "known_source_ids", set())

        for card in cards:
            item = self._parse_card(card, response, geo_map)
            if not item:
                continue

            source_id = item["source_id"]
            detail_href = card.css("a::attr(href)").get("")
            detail_url = self._abs_url(detail_href) if detail_href else None

            if source_id not in known_ids and detail_url:
                yield scrapy.Request(
                    detail_url,
                    meta={
                        "playwright": True,
                        "playwright_context": f"tokko-detail-{source_id}",
                        "playwright_page_methods": [
                            PageMethod("wait_for_load_state", "networkidle"),
                        ],
                        "item_data": dict(item),
                    },
                    callback=self.parse_detail,
                    errback=self._detail_error,
                    dont_filter=True,
                )
            else:
                yield item

        # Pagination — try next page link or construct it
        page = response.meta["page"]
        if page < self.MAX_PAGES:
            next_link = response.css(
                "a.next::attr(href), a[rel=next]::attr(href), "
                ".paginacion a.next::attr(href)"
            ).get()

            if next_link:
                yield scrapy.Request(
                    self._abs_url(next_link),
                    meta={
                        "playwright": True,
                        "playwright_context": f"tokko-{self.name}-p{page+1}",
                        "playwright_page_methods": [
                            PageMethod("wait_for_load_state", "networkidle"),
                            PageMethod("wait_for_timeout", 2000),
                        ],
                        "property_type": response.meta["property_type"],
                        "listing_type": response.meta["listing_type"],
                        "page": page + 1,
                    },
                    callback=self.parse_listing_page,
                    errback=self.handle_error,
                )
            else:
                # Try Tokko's get_next_page() URL pattern
                next_url = f"{response.url}?page={page + 1}"
                yield scrapy.Request(
                    next_url,
                    meta={
                        "playwright": True,
                        "playwright_context": f"tokko-{self.name}-p{page+1}",
                        "playwright_page_methods": [
                            PageMethod("wait_for_load_state", "networkidle"),
                            PageMethod("wait_for_timeout", 2000),
                        ],
                        "property_type": response.meta["property_type"],
                        "listing_type": response.meta["listing_type"],
                        "page": page + 1,
                    },
                    callback=self.parse_listing_page,
                    errback=self.handle_error,
                )

    def _parse_card(self, card, response, geo_map):
        """Extract property data from a listing card."""
        # Find detail link and source ID
        link = card.css("a::attr(href)").get("")
        id_match = re.search(r"/p/(\d+)", link) or re.search(r"/(\d+)-", link)
        source_id = id_match.group(1) if id_match else ""
        if not source_id:
            # Try data attribute
            source_id = card.attrib.get("data-id", "")
        if not source_id:
            return None

        item = PropertyItem()
        item["source"] = self.name
        item["source_id"] = source_id
        item["source_url"] = self._abs_url(link) if link else response.url

        item["property_type"] = response.meta["property_type"]
        item["listing_type"] = response.meta["listing_type"]

        # Price
        price_text = card.css(
            ".precio::text, .price::text, .prop-price::text"
        ).get("").strip()
        item["price"], item["currency"] = self._parse_price(price_text)

        # Address
        address = card.css(
            ".ubicacion::text, .address::text, .prop-address::text, "
            ".prop-location::text"
        ).get("").strip()
        item["address"] = address

        # Title
        title = card.css(
            ".prop-title::text, .title::text, h3::text, h4::text"
        ).get("").strip()
        item["title"] = title if title else address

        # Coordinates from JS markers
        geo = geo_map.get(source_id, {})
        item["latitude"] = geo.get("lat")
        item["longitude"] = geo.get("lng")

        # Images from card
        images = card.css(
            "img[src*='static.tokkobroker.com']::attr(src), "
            "img[src*='tokkobroker']::attr(src), "
            "img::attr(src)"
        ).getall()
        item["image_urls"] = [
            img for img in dict.fromkeys(images)
            if img and "logo" not in img and "placeholder" not in img
        ]

        # Specs from card text
        specs_text = card.css(
            ".prop-features::text, .features::text, .datos::text"
        ).getall()
        specs = " ".join(specs_text)
        item["total_area_m2"] = self._extract_number(specs, r"([\d.,]+)\s*m[²2]")
        item["covered_area_m2"] = None
        item["rooms"] = self._extract_int(specs, r"(\d+)\s*amb")
        item["bedrooms"] = self._extract_int(specs, r"(\d+)\s*dorm")
        item["bathrooms"] = self._extract_int(specs, r"(\d+)\s*ba[ñn]")
        item["garages"] = self._extract_int(specs, r"(\d+)\s*coch")
        item["age_years"] = None
        item["description"] = ""
        item["apto_credito"] = False
        item["raw_data"] = {"url": item["source_url"]}

        return item

    def _extract_markers(self, response):
        """Extract {id: {lat, lng}} from add_new_marker() calls in page JS."""
        geo_map = {}
        for match in re.finditer(
            r"add_new_marker\s*\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,.*?/p/(\d+)",
            response.text,
            re.DOTALL,
        ):
            lat, lng, pid = match.groups()
            try:
                geo_map[pid] = {"lat": float(lat), "lng": float(lng)}
            except ValueError:
                pass
        return geo_map

    def _detail_error(self, failure):
        self.logger.warning(f"Detail failed, using card data: {failure.value}")
        item_data = failure.request.meta.get("item_data")
        if item_data:
            item = PropertyItem()
            for k, v in item_data.items():
                item[k] = v
            return item

    def parse_detail(self, response):
        """Parse Tokko detail page for full property data."""
        item_data = response.meta["item_data"]
        item = PropertyItem()
        for k, v in item_data.items():
            item[k] = v

        # Title
        title = response.css("h1::text, h2.title::text").get("").strip()
        if title:
            item["title"] = title

        # Description
        desc_parts = response.css(
            ".description *::text, .prop-description *::text, "
            "#description *::text, .detail-description *::text"
        ).getall()
        desc = " ".join(t.strip() for t in desc_parts if t.strip())
        if desc:
            item["description"] = desc

        # Price from detail (may be more accurate)
        price_text = response.css(
            ".precio::text, .price::text, .prop-price::text, "
            "h3.price::text"
        ).get("").strip()
        if price_text:
            price, currency = self._parse_price(price_text)
            if price:
                item["price"] = price
                item["currency"] = currency

        # Specs from detail
        for spec_el in response.css(
            ".prop-detail li, .property-features li, .datos li, .features li"
        ):
            text = spec_el.css("::text").get("").strip().lower()
            num_match = re.search(r"[\d.,]+", text)
            if not num_match:
                continue
            val_str = num_match.group().replace(".", "").replace(",", ".")
            try:
                val = float(val_str)
            except ValueError:
                continue

            if "sup" in text and "tot" in text or "m²" in text or "m2" in text:
                if "cub" in text:
                    item["covered_area_m2"] = val
                else:
                    item["total_area_m2"] = val
            elif "amb" in text:
                item["rooms"] = int(val)
            elif "dorm" in text or "hab" in text:
                item["bedrooms"] = int(val)
            elif "baño" in text or "bano" in text:
                item["bathrooms"] = int(val)
            elif "coch" in text or "gar" in text:
                item["garages"] = int(val)
            elif "antig" in text:
                item["age_years"] = int(val)
            elif "expensa" in text:
                item["expenses_ars"] = val

        # Images from detail gallery
        images = response.css(
            "img[src*='static.tokkobroker.com/pictures']::attr(src), "
            "img[src*='tokkobroker']::attr(src), "
            ".gallery img::attr(src), "
            ".carousel img::attr(src)"
        ).getall()
        detail_images = [
            img for img in dict.fromkeys(images)
            if img and "logo" not in img and "placeholder" not in img
        ]
        if len(detail_images) > len(item.get("image_urls", [])):
            item["image_urls"] = detail_images

        # Coordinates from detail JS
        if not item.get("latitude"):
            geo_match = re.search(
                r"add_new_marker\s*\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)",
                response.text,
            )
            if geo_match:
                try:
                    item["latitude"] = float(geo_match.group(1))
                    item["longitude"] = float(geo_match.group(2))
                except ValueError:
                    pass

        # Address from detail
        address = response.css(
            ".ubicacion::text, .address::text, .prop-address::text"
        ).get("").strip()
        if address and len(address) > len(item.get("address", "")):
            item["address"] = address

        # Apto crédito
        all_text = (
            item.get("source_url", "") + " " +
            item.get("description", "") + " " +
            item.get("title", "")
        ).lower()
        item["apto_credito"] = (
            "crédito" in all_text or "credito" in all_text or "hipotecario" in all_text
        )

        yield item

    def _abs_url(self, path):
        if path.startswith("http"):
            return path
        return self.BASE_URL + (path if path.startswith("/") else "/" + path)

    def _parse_price(self, text):
        if not text or not text.strip():
            return None, None
        text = text.strip()
        currency = "USD" if any(s in text for s in ("USD", "U$S", "US$")) else "ARS"
        numbers = re.findall(r"[\d.,]+", text)
        if numbers:
            try:
                return float(numbers[0].replace(".", "").replace(",", ".")), currency
            except ValueError:
                pass
        return None, currency

    @staticmethod
    def _extract_number(text, pattern):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1).replace(".", "").replace(",", "."))
            except ValueError:
                pass
        return None

    @staticmethod
    def _extract_int(text, pattern):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        return None


class GuzmanGuzmanSpider(TokkoSpider):
    """Guzmán & Guzmán Propiedades — Tokko Broker site."""

    name = "guzman_guzman"
    BASE_URL = "https://www.guzmanyguzman.com.ar"
    allowed_domains = ["www.guzmanyguzman.com.ar"]
