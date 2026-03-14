import re

import scrapy
from scrapy_playwright.page import PageMethod

from mobipartner_scrapy.items import PropertyItem


class TucumanPropiedadesSpider(scrapy.Spider):
    """Spider for tucumanpropiedades.com — 'Pixel Inmobiliario' (Laravel) platform.

    Uses Playwright for CSRF-protected Laravel pages.
    """

    name = "tucumanpropiedades"
    allowed_domains = ["tucumanpropiedades.com"]

    BASE_URL = "https://tucumanpropiedades.com"
    MAX_PAGES = 30

    SEARCHES = [
        ("sale", "venta"),
        ("rent", "alquiler"),
    ]

    custom_settings = {
        "PLAYWRIGHT_BROWSER_TYPE": "firefox",
        "DOWNLOAD_DELAY": 3,
        "CONCURRENT_REQUESTS": 1,
        "ROBOTSTXT_OBEY": False,
        "COOKIES_ENABLED": True,  # Laravel needs cookies for CSRF
        "USER_AGENT": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    }

    def start_requests(self):
        for purpose, listing_type in self.SEARCHES:
            url = f"{self.BASE_URL}/ads?purpose={purpose}&page=1"
            yield scrapy.Request(
                url,
                meta={
                    "playwright": True,
                    "playwright_context": f"tucprop-{purpose}-p1",
                    "playwright_page_methods": [
                        PageMethod("wait_for_load_state", "networkidle"),
                    ],
                    "listing_type": listing_type,
                    "purpose": purpose,
                    "page": 1,
                },
                callback=self.parse_listing_page,
                errback=self.handle_error,
            )

    def handle_error(self, failure):
        self.logger.error(f"Request failed: {failure.request.url} — {failure.value}")

    def parse_listing_page(self, response):
        """Parse property listing cards."""
        cards = response.css(
            ".property_item, .ad-box-grid-view, .listing-item, "
            ".property-card, .card-property"
        )

        self.logger.info(
            f"Page {response.meta['page']} — {response.url}: {len(cards)} cards"
        )

        if not cards:
            return

        known_ids = getattr(self, "known_source_ids", set())

        for card in cards:
            # Detail link
            link = card.css("a::attr(href)").get("")
            if not link:
                continue

            detail_url = self._abs_url(link)

            # Extract source_id from URL
            slug = link.rstrip("/").split("/")[-1]
            source_id = slug

            # Basic data from card
            title = card.css(
                ".property-title::text, h3::text, h4::text, .title::text"
            ).get("").strip()

            price_text = card.css(
                ".property-price::text, .price::text, .ad-price::text"
            ).get("").strip()
            price, currency = self._parse_price(price_text)

            address = card.css(
                ".property-address::text, .location::text, .address::text"
            ).get("").strip()

            # Card image
            images = card.css(
                "img::attr(src), img::attr(data-src)"
            ).getall()
            image_urls = [
                img for img in dict.fromkeys(images)
                if img and "logo" not in img and "placeholder" not in img
            ]

            item = PropertyItem()
            item["source"] = "tucumanpropiedades"
            item["source_id"] = source_id
            item["source_url"] = detail_url
            item["title"] = title if title else slug
            item["price"] = price
            item["currency"] = currency
            item["address"] = address
            item["property_type"] = self._guess_type(title + " " + slug)
            item["listing_type"] = response.meta["listing_type"]
            item["image_urls"] = image_urls
            item["latitude"] = None
            item["longitude"] = None
            item["total_area_m2"] = None
            item["covered_area_m2"] = None
            item["rooms"] = None
            item["bedrooms"] = None
            item["bathrooms"] = None
            item["garages"] = None
            item["age_years"] = None
            item["description"] = ""
            item["apto_credito"] = False
            item["raw_data"] = {"url": detail_url}

            if source_id not in known_ids:
                yield scrapy.Request(
                    detail_url,
                    meta={
                        "playwright": True,
                        "playwright_context": f"tucprop-detail-{source_id[:20]}",
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

        # Pagination
        page = response.meta["page"]
        if page < self.MAX_PAGES:
            next_link = response.css(
                "a[rel=next]::attr(href), .pagination a.next::attr(href), "
                ".pagination li:last-child a::attr(href)"
            ).get()
            if next_link:
                yield scrapy.Request(
                    self._abs_url(next_link),
                    meta={
                        "playwright": True,
                        "playwright_context": f"tucprop-{response.meta['purpose']}-p{page+1}",
                        "playwright_page_methods": [
                            PageMethod("wait_for_load_state", "networkidle"),
                        ],
                        "listing_type": response.meta["listing_type"],
                        "purpose": response.meta["purpose"],
                        "page": page + 1,
                    },
                    callback=self.parse_listing_page,
                    errback=self.handle_error,
                )

    def _detail_error(self, failure):
        self.logger.warning(f"Detail failed, using card data: {failure.value}")
        item_data = failure.request.meta.get("item_data")
        if item_data:
            item = PropertyItem()
            for k, v in item_data.items():
                item[k] = v
            return item

    def parse_detail(self, response):
        """Parse detail page for complete property data."""
        item_data = response.meta["item_data"]
        item = PropertyItem()
        for k, v in item_data.items():
            item[k] = v

        # Title
        title = response.css(
            "h1::text, .property-title::text, .ad-title::text"
        ).get("").strip()
        if title:
            item["title"] = title

        # Price
        price_text = response.css(
            ".property-price::text, .price::text, .ad-price::text, "
            "h3.price::text"
        ).get("").strip()
        if price_text:
            price, currency = self._parse_price(price_text)
            if price:
                item["price"] = price
                item["currency"] = currency

        # Address
        address = response.css(
            ".property-address::text, .location::text, .address::text, "
            ".ad-location::text"
        ).get("").strip()
        if address:
            item["address"] = address

        # Description
        desc_parts = response.css(
            ".property-description *::text, .description *::text, "
            ".ad-description *::text, .detail-description *::text"
        ).getall()
        desc = " ".join(t.strip() for t in desc_parts if t.strip())
        if desc:
            item["description"] = desc

        # Features / specs
        for feat in response.css(
            ".property-features li, .features li, "
            ".property-features__item, .ad-features li"
        ):
            text = feat.css("::text").get("").strip().lower()
            num_match = re.search(r"[\d.,]+", text)
            if not num_match:
                continue
            val_str = num_match.group().replace(".", "").replace(",", ".")
            try:
                val = float(val_str)
            except ValueError:
                continue

            if "m²" in text or "m2" in text or "sup" in text:
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

        # Images
        images = response.css(
            ".property-gallery img::attr(src), .gallery img::attr(src), "
            ".carousel img::attr(src), .slider img::attr(src), "
            ".ad-gallery img::attr(src), "
            "img[src*='storage']::attr(src), "
            "img[src*='uploads']::attr(src)"
        ).getall()
        detail_images = [
            img for img in dict.fromkeys(images)
            if img and "logo" not in img and "placeholder" not in img
        ]
        if len(detail_images) > len(item.get("image_urls", [])):
            item["image_urls"] = detail_images

        # Coordinates from page (Google Maps embed or JS)
        lat_match = re.search(r"lat[itude]*['\"]?\s*[:=]\s*(-?[\d.]+)", response.text)
        lng_match = re.search(r"lng|lon[gitude]*['\"]?\s*[:=]\s*(-?[\d.]+)", response.text)
        if lat_match and lng_match:
            try:
                item["latitude"] = float(lat_match.group(1))
                item["longitude"] = float(lng_match.group(1))
            except ValueError:
                pass

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

    def _guess_type(self, text):
        """Guess property type from text."""
        text = text.lower()
        if "depto" in text or "departamento" in text:
            return "departamento"
        if "casa" in text:
            return "casa"
        if "terreno" in text or "lote" in text:
            return "terreno"
        if "local" in text or "comercial" in text:
            return "local"
        if "oficina" in text:
            return "oficina"
        if "ph" in text:
            return "ph"
        if "cochera" in text:
            return "cochera"
        return "departamento"
