import json
import re

import scrapy
from scrapy_playwright.page import PageMethod

from mobipartner_scrapy.items import PropertyItem


class ZonaPropSpider(scrapy.Spider):
    """Spider for ZonaProp using Playwright (Firefox).

    Extracts data from listing cards and visits detail pages for full data
    (description, all images, age, floor, expensas).
    """

    name = "zonaprop"
    allowed_domains = ["www.zonaprop.com.ar"]

    BASE_URL = "https://www.zonaprop.com.ar"
    MAX_PAGES = 50

    SEARCHES = [
        ("departamentos-venta-tucuman", "departamento", "venta"),
        ("departamentos-alquiler-tucuman", "departamento", "alquiler"),
        ("casas-venta-tucuman", "casa", "venta"),
        ("casas-alquiler-tucuman", "casa", "alquiler"),
        ("terrenos-venta-tucuman", "terreno", "venta"),
        ("ph-venta-tucuman", "ph", "venta"),
        ("locales-comerciales-venta-tucuman", "local", "venta"),
        ("locales-comerciales-alquiler-tucuman", "local", "alquiler"),
        ("oficinas-venta-tucuman", "oficina", "venta"),
        ("cocheras-venta-tucuman", "cochera", "venta"),
    ]

    custom_settings = {
        "PLAYWRIGHT_BROWSER_TYPE": "firefox",
        "DOWNLOAD_DELAY": 3,
        "CONCURRENT_REQUESTS": 2,
        "ROBOTSTXT_OBEY": False,
        "COOKIES_ENABLED": False,
        "DOWNLOADER_MIDDLEWARES": {
            "mobipartner_scrapy.middlewares.RotateUserAgentMiddleware": None,
            "mobipartner_scrapy.middlewares.PlaywrightRetryMiddleware": 610,
            "mobipartner_scrapy.middlewares.CircuitBreakerMiddleware": 620,
        },
        "USER_AGENT": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    }

    def start_requests(self):
        for slug, prop_type, listing_type in self.SEARCHES:
            url = f"{self.BASE_URL}/{slug}.html"
            yield scrapy.Request(
                url,
                meta={
                    "playwright": True,
                    "playwright_context": f"zp-{slug}-p1",
                    "playwright_page_methods": [
                        PageMethod("wait_for_load_state", "networkidle"),
                    ],
                    "property_type": prop_type,
                    "listing_type": listing_type,
                    "page": 1,
                    "slug": slug,
                },
                callback=self.parse_listing_page,
                errback=self.handle_error,
            )

    def closed(self, reason):
        stats = self.crawler.stats.get_stats()
        self.logger.info(
            f"Spider closed ({reason}): "
            f"items={stats.get('item_scraped_count', 0)}, "
            f"errors={stats.get('item_dropped_count', 0)}, "
            f"retries={stats.get('playwright_retry/count', 0)}"
        )

    def handle_error(self, failure):
        self.logger.error(f"Request failed: {failure.request.url} — {failure.value}")

    def _extract_geo_from_page(self, response) -> dict:
        """Extract {posting_id: {lat, lng}} from ZonaProp's embedded __PRELOADED_STATE__."""
        geo_map = {}

        text = response.text
        marker = "__PRELOADED_STATE__ = {"
        idx = text.find(marker)
        if idx == -1:
            return geo_map

        try:
            start = idx + len(marker) - 1  # position of the opening {
            state, _ = json.JSONDecoder().raw_decode(text, start)
            postings = state.get("listStore", {}).get("listPostings", [])
            for p in postings:
                pid = str(p.get("postingId", ""))
                geo = (
                    p.get("postingLocation", {})
                    .get("postingGeolocation", {})
                    .get("geolocation", {})
                )
                lat = geo.get("latitude")
                lng = geo.get("longitude")
                if pid and lat and lng:
                    geo_map[pid] = {"lat": float(lat), "lng": float(lng)}
        except Exception as e:
            self.logger.debug(f"Could not parse __PRELOADED_STATE__: {e}")

        return geo_map

    def parse_listing_page(self, response):
        cards = response.css("div[data-posting-type=PROPERTY]")

        self.logger.info(
            f"Page {response.meta['page']} — {response.url}: {len(cards)} listings"
        )

        geo_map = self._extract_geo_from_page(response)
        self.logger.info(f"Geo data found for {len(geo_map)} postings on this page")

        known_ids = getattr(self, "known_source_ids", set())

        for card in cards:
            source_id = card.attrib.get("data-id", "")
            detail_path = card.attrib.get("data-to-posting", "")
            if not source_id:
                continue

            # Extract card data (used as meta for detail or as fallback item)
            card_data = self._extract_card_data(card, source_id, detail_path, response, geo_map)

            detail_url = (
                self.BASE_URL + detail_path.split("?")[0]
                if detail_path else None
            )

            # If we already have this listing in DB, yield card data only (price update)
            if source_id in known_ids or not detail_url:
                item = self._card_data_to_item(card_data)
                yield item
            else:
                # Visit detail page for full data
                yield scrapy.Request(
                    detail_url,
                    meta={
                        "playwright": True,
                        "playwright_context": f"zp-detail-{source_id}",
                        "playwright_page_methods": [
                            PageMethod("wait_for_selector", "h1, h2.title-type-sup-property", timeout=15000),
                            PageMethod("evaluate", "window.scrollBy(0, 400)"),
                            PageMethod("wait_for_timeout", 800),
                            PageMethod("evaluate", "window.scrollBy(0, 400)"),
                            PageMethod("wait_for_timeout", 500),
                        ],
                        "card_data": card_data,
                    },
                    callback=self.parse_detail,
                    errback=self._detail_error,
                    dont_filter=True,
                )

        # Pagination
        page = response.meta["page"]
        if page < self.MAX_PAGES:
            next_href = response.css("a[data-qa=PAGING_NEXT]::attr(href)").get()
            if next_href:
                yield scrapy.Request(
                    self.BASE_URL + next_href if next_href.startswith("/") else next_href,
                    meta={
                        "playwright": True,
                        "playwright_context": f"zp-{response.meta['slug']}-p{page + 1}",
                        "playwright_page_methods": [
                            PageMethod("wait_for_selector", "div[data-posting-type]", timeout=30000),
                        ],
                        "property_type": response.meta["property_type"],
                        "listing_type": response.meta["listing_type"],
                        "page": page + 1,
                        "slug": response.meta["slug"],
                    },
                    callback=self.parse_listing_page,
                    errback=self.handle_error,
                )

    def _extract_card_data(self, card, source_id, detail_path, response, geo_map):
        """Extract all available data from a listing card."""
        price_text = card.css("[data-qa=POSTING_CARD_PRICE]::text").get("").strip()
        price, currency = self._parse_price(price_text)

        location_parts = [
            t.strip() for t in card.css("[data-qa=POSTING_CARD_LOCATION]::text, [data-qa=POSTING_CARD_LOCATION] *::text").getall()
            if t.strip()
        ]
        address = ", ".join(dict.fromkeys(location_parts)) if location_parts else ""

        geo = geo_map.get(source_id, {})

        feat_text = card.css("[data-qa=POSTING_CARD_FEATURES]::text").get("").strip()
        feats = self._parse_features(feat_text)

        desc = card.css("[data-qa=POSTING_CARD_DESCRIPTION]::text").get("").strip()

        images = card.css(
            "img[src*='zonapropcdn.com']::attr(src), "
            "img[data-flickity-lazyload*='zonapropcdn']::attr(data-flickity-lazyload)"
        ).getall()
        image_urls = [
            img for img in dict.fromkeys(images)
            if img and "placeholder" not in img and "empresas" not in img
        ]

        source_url = (
            self.BASE_URL + detail_path.split("?")[0]
            if detail_path else response.url
        )

        return {
            "source_id": source_id,
            "source_url": source_url,
            "property_type": response.meta["property_type"],
            "listing_type": response.meta["listing_type"],
            "price": price,
            "currency": currency,
            "address": address,
            "latitude": geo.get("lat"),
            "longitude": geo.get("lng"),
            "feats": feats,
            "feat_text": feat_text,
            "description": desc,
            "title": desc[:120] if desc else address,
            "image_urls": image_urls,
        }

    def _card_data_to_item(self, cd):
        """Convert card_data dict to a PropertyItem (fallback when detail not visited)."""
        item = PropertyItem()
        item["source"] = "zonaprop"
        item["source_id"] = cd["source_id"]
        item["source_url"] = cd["source_url"]
        item["property_type"] = cd["property_type"]
        item["listing_type"] = cd["listing_type"]
        item["price"] = cd["price"]
        item["currency"] = cd["currency"]
        item["address"] = cd["address"]
        item["latitude"] = cd["latitude"]
        item["longitude"] = cd["longitude"]
        item["total_area_m2"] = cd["feats"].get("total_area_m2")
        item["covered_area_m2"] = cd["feats"].get("covered_area_m2")
        item["rooms"] = cd["feats"].get("rooms")
        item["bedrooms"] = cd["feats"].get("bedrooms")
        item["bathrooms"] = cd["feats"].get("bathrooms")
        item["garages"] = cd["feats"].get("garages")
        item["age_years"] = None
        item["title"] = cd["title"]
        item["description"] = cd["description"]
        all_text = (cd["source_url"] + " " + cd["description"] + " " + cd["address"]).lower()
        item["apto_credito"] = "crédito" in all_text or "credito" in all_text or "hipotecario" in all_text
        item["image_urls"] = cd["image_urls"]
        item["raw_data"] = {"url": cd["source_url"], "features_text": cd["feat_text"]}
        return item

    def _detail_error(self, failure):
        """If detail page fails, yield item from card data."""
        self.logger.warning(f"Detail page failed, using card data: {failure.value}")
        card_data = failure.request.meta.get("card_data")
        if card_data:
            return self._card_data_to_item(card_data)

    def parse_detail(self, response):
        """Parse ZonaProp detail page for full property data."""
        cd = response.meta["card_data"]

        item = PropertyItem()
        item["source"] = "zonaprop"
        item["source_id"] = cd["source_id"]
        item["source_url"] = cd["source_url"]
        item["property_type"] = cd["property_type"]
        item["listing_type"] = cd["listing_type"]

        # Price from card (reliable) — override with detail if found
        item["price"] = cd["price"]
        item["currency"] = cd["currency"]

        # Try to get price from detail page JS variable
        price_match = re.search(r"'precioVenta'\s*:\s*\"(\w+)\s+([\d.]+)\"", response.text)
        if price_match:
            cur_str = price_match.group(1)
            price_str = price_match.group(2)
            try:
                item["price"] = float(price_str.replace(".", "").replace(",", "."))
                item["currency"] = "USD" if "USD" in cur_str or "U$S" in cur_str else "ARS"
            except ValueError:
                pass

        # Address and coordinates from card (geo_map is more reliable)
        item["address"] = cd["address"]
        item["latitude"] = cd["latitude"]
        item["longitude"] = cd["longitude"]

        # Title from detail page
        title = (
            response.css("h1::text").get("")
            or response.css("h2.title-type-sup-property::text").get("")
        ).strip()
        item["title"] = title if title else cd["title"]

        # Description from detail page
        desc = ""
        for sel in [
            "div#description-text *::text",
            "div.section-description--content *::text",
            "div.section-description p::text",
        ]:
            texts = response.css(sel).getall()
            if texts:
                desc = " ".join(t.strip() for t in texts if t.strip())
                break
        item["description"] = desc if desc else cd["description"]

        # Features from detail page — icon-based selectors
        feats = dict(cd["feats"])  # start with card features
        for feat_el in response.css("li.icon-feature"):
            classes = feat_el.attrib.get("class", "")
            text = feat_el.css("::text").get("").strip()
            num_match = re.search(r"[\d.,]+", text)
            if not num_match:
                continue
            val_str = num_match.group().replace(".", "").replace(",", ".")
            try:
                val = float(val_str)
            except ValueError:
                continue

            if "icon-stotal" in classes:
                feats["total_area_m2"] = val
            elif "icon-scubierta" in classes:
                feats["covered_area_m2"] = val
            elif "icon-ambiente" in classes:
                feats["rooms"] = int(val)
            elif "icon-dormitorio" in classes:
                feats["bedrooms"] = int(val)
            elif "icon-bano" in classes:
                feats["bathrooms"] = int(val)
            elif "icon-cochera" in classes:
                feats["garages"] = int(val)
            elif "icon-antiguedad" in classes:
                feats["age_years"] = int(val)

        item["total_area_m2"] = feats.get("total_area_m2")
        item["covered_area_m2"] = feats.get("covered_area_m2")
        item["rooms"] = feats.get("rooms")
        item["bedrooms"] = feats.get("bedrooms")
        item["bathrooms"] = feats.get("bathrooms")
        item["garages"] = feats.get("garages")
        item["age_years"] = feats.get("age_years")

        # Expensas from detail
        for feat_el in response.css("li.icon-feature"):
            text = feat_el.css("::text").get("").strip().lower()
            if "expensa" in text:
                m = re.search(r"[\d.]+", text.replace(".", ""))
                if m:
                    try:
                        item["expenses_ars"] = float(m.group())
                    except ValueError:
                        pass
                break

        # Images from detail gallery — should have 15-20+ vs 6 from card
        images = response.css(
            "img[src*='zonapropcdn.com']::attr(src), "
            "img[data-flickity-lazyload*='zonapropcdn']::attr(data-flickity-lazyload), "
            "img[data-src*='zonapropcdn']::attr(data-src)"
        ).getall()
        detail_images = [
            img for img in dict.fromkeys(images)
            if img and "placeholder" not in img and "empresas" not in img
        ]
        # Use detail images if we got more, otherwise fall back to card images
        item["image_urls"] = detail_images if len(detail_images) >= len(cd["image_urls"]) else cd["image_urls"]

        # Apto crédito
        all_text = (
            cd["source_url"] + " " + item.get("description", "") + " " +
            cd["address"] + " " + item.get("title", "")
        ).lower()
        item["apto_credito"] = "crédito" in all_text or "credito" in all_text or "hipotecario" in all_text

        item["raw_data"] = {"url": cd["source_url"], "features_text": cd["feat_text"]}

        yield item

    def _parse_features(self, text: str) -> dict:
        """Parse card feature line: '152 m² tot.\n6 amb.\n3 dorm.\n2 baños\n1 coch.'"""
        result = {}
        if not text:
            return result
        for part in re.split(r"[\n·|]", text):
            part = part.strip()
            num = re.search(r"[\d.,]+", part)
            if not num:
                continue
            val_str = num.group().replace(".", "").replace(",", ".")
            try:
                val = float(val_str)
            except ValueError:
                continue
            low = part.lower()
            if "m²" in low and "cub" in low:
                result["covered_area_m2"] = val
            elif "m²" in low:
                result["total_area_m2"] = val
            elif "amb" in low:
                result["rooms"] = int(val)
            elif "dorm" in low or "hab" in low:
                result["bedrooms"] = int(val)
            elif "baño" in low or "bano" in low:
                result["bathrooms"] = int(val)
            elif "coch" in low or "gar" in low:
                result["garages"] = int(val)
        return result

    def _parse_price(self, text: str) -> tuple:
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
