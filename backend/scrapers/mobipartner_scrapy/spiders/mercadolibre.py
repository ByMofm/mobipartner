import json
import re

import scrapy
from scrapy_playwright.page import PageMethod

from mobipartner_scrapy.items import PropertyItem


class MercadoLibreSpider(scrapy.Spider):
    """Spider for MercadoLibre Inmuebles using Playwright (Firefox).

    Scrapes inmuebles.mercadolibre.com.ar for properties in Tucumán.
    Uses Firefox because Chromium headless shell gets detected as a bot.
    """

    name = "mercadolibre"
    # ML uses multiple subdomains (departamento., casa., etc.) — no domain restriction
    allowed_domains = []

    BASE_URL = "https://inmuebles.mercadolibre.com.ar"
    MAX_PAGES = 30

    SEARCHES = [
        ("departamentos/tucuman-venta", "departamento", "venta"),
        ("departamentos/tucuman-alquiler", "departamento", "alquiler"),
        ("casas/tucuman-venta", "casa", "venta"),
        ("casas/tucuman-alquiler", "casa", "alquiler"),
        ("terrenos/tucuman-venta", "terreno", "venta"),
        ("ph/tucuman-venta", "ph", "venta"),
        ("locales-comerciales/tucuman-venta", "local", "venta"),
        ("locales-comerciales/tucuman-alquiler", "local", "alquiler"),
        ("oficinas/tucuman-venta", "oficina", "venta"),
        ("cocheras/tucuman-venta", "cochera", "venta"),
    ]

    custom_settings = {
        "PLAYWRIGHT_BROWSER_TYPE": "firefox",
        "DOWNLOAD_DELAY": 2,
        "CONCURRENT_REQUESTS": 2,
    }

    def closed(self, reason):
        stats = self.crawler.stats.get_stats()
        self.logger.info(
            f"Spider closed ({reason}): "
            f"items={stats.get('item_scraped_count', 0)}, "
            f"errors={stats.get('item_dropped_count', 0)}, "
            f"retries={stats.get('playwright_retry/count', 0)}"
        )

    def start_requests(self):
        for slug, prop_type, listing_type in self.SEARCHES:
            url = f"{self.BASE_URL}/{slug}"
            yield scrapy.Request(
                url,
                meta={
                    "playwright": True,
                    "playwright_page_methods": [
                        PageMethod("wait_for_selector", ".poly-card", timeout=20000),
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
        links = response.css("a.poly-component__title::attr(href)").getall()
        links = list(dict.fromkeys(links))  # deduplicate preserving order

        self.logger.info(
            f"Page {response.meta['page']} — {response.url}: {len(links)} listings"
        )

        known_ids = getattr(self, "known_source_ids", set())

        for link in links:
            # Check if we already have this listing
            match = re.search(r"MLA-?(\d+)", link)
            source_id = f"MLA{match.group(1)}" if match else None
            if source_id and source_id in known_ids:
                continue

            yield scrapy.Request(
                link,
                meta={
                    "playwright": True,
                    "playwright_page_methods": [
                        PageMethod("wait_for_load_state", "networkidle"),
                        # Scroll gallery into view first
                        PageMethod("evaluate", """() => {
                            const gallery = document.querySelector('.ui-pdp-gallery');
                            if (gallery) gallery.scrollIntoView();
                        }"""),
                        PageMethod("wait_for_timeout", 800),
                        # Progressive scrolls to trigger lazy-load
                        PageMethod("evaluate", "window.scrollBy(0, 400)"),
                        PageMethod("wait_for_timeout", 600),
                        PageMethod("evaluate", "window.scrollBy(0, 400)"),
                        PageMethod("wait_for_timeout", 600),
                        PageMethod("evaluate", "window.scrollBy(0, 400)"),
                        PageMethod("wait_for_timeout", 600),
                        PageMethod("evaluate", "window.scrollBy(0, 400)"),
                        PageMethod("wait_for_timeout", 500),
                        # Extract coordinates from scripts via JS evaluate
                        PageMethod("evaluate", """() => {
                            try {
                                const scripts = Array.from(document.querySelectorAll('script'));
                                for (const s of scripts) {
                                    const t = s.textContent || '';
                                    const la = t.match(/"latitude"\\s*:\\s*(-?\\d+\\.\\d+)/);
                                    const ln = t.match(/"longitude"\\s*:\\s*(-?\\d+\\.\\d+)/);
                                    if (la && ln) return {lat: parseFloat(la[1]), lng: parseFloat(ln[1])};
                                }
                            } catch(e) {}
                            return null;
                        }"""),
                    ],
                    "property_type": response.meta["property_type"],
                    "listing_type": response.meta["listing_type"],
                },
                callback=self.parse_detail,
                errback=self.handle_error,
            )

        # Pagination: ML uses _Desde_{offset} in the URL, 48 items per page
        page = response.meta["page"]
        if page < self.MAX_PAGES and links:
            offset = page * 48
            # Build next URL: base_url_without_query + _Desde_{offset+1}
            base = re.sub(r"_Desde_\d+", "", response.url).rstrip("/")
            next_url = f"{base}_Desde_{offset + 1}"
            yield scrapy.Request(
                next_url,
                meta={
                    "playwright": True,
                    "playwright_page_methods": [
                        PageMethod("wait_for_selector", ".poly-card", timeout=20000),
                    ],
                    "property_type": response.meta["property_type"],
                    "listing_type": response.meta["listing_type"],
                    "page": page + 1,
                },
                callback=self.parse_listing_page,
                errback=self.handle_error,
            )

    def parse_detail(self, response):
        item = PropertyItem()
        item["source"] = "mercadolibre"
        item["source_url"] = response.url

        # MLA ID from URL
        match = re.search(r"MLA-?(\d+)", response.url)
        item["source_id"] = f"MLA{match.group(1)}" if match else response.url.split("/")[-1]

        item["property_type"] = response.meta.get("property_type", "departamento")
        item["listing_type"] = response.meta.get("listing_type", "venta")

        # Title
        item["title"] = response.css("h1.ui-pdp-title::text").get("").strip()

        # Price — first fraction+symbol on the page (main price)
        price_fraction = response.css("span.andes-money-amount__fraction::text").get("").strip()
        currency_sym = response.css("span.andes-money-amount__currency-symbol::text").get("").strip()
        item["price"], item["currency"] = self._parse_price(price_fraction, currency_sym)

        # Address — join all location text parts for a fuller address
        location_texts = [
            t.strip()
            for t in response.css("div.ui-vip-location__subtitle *::text").getall()
            if t.strip() and "ver información" not in t.strip().lower()
        ]
        item["address"] = ", ".join(dict.fromkeys(location_texts)) if location_texts else ""

        # Coordinates — try Playwright JS evaluate result first
        item["latitude"] = None
        item["longitude"] = None
        for pm in response.meta.get("playwright_page_methods", []):
            if hasattr(pm, "result") and isinstance(pm.result, dict):
                lat = pm.result.get("lat")
                lng = pm.result.get("lng")
                if lat and lng:
                    item["latitude"] = float(lat)
                    item["longitude"] = float(lng)
                    break

        # Coordinates fallback — JSON-LD
        if not item["latitude"]:
            for script in response.css('script[type="application/ld+json"]::text').getall():
                try:
                    ld = json.loads(script)
                    if isinstance(ld, list):
                        ld = ld[0]
                    geo = ld.get("geo", {})
                    lat = geo.get("latitude")
                    lng = geo.get("longitude")
                    if lat and lng:
                        item["latitude"] = float(lat)
                        item["longitude"] = float(lng)
                        break
                except Exception:
                    pass

        # Specs table — th text is in nested div, td text in nested span
        specs = {}
        for row in response.css("tr.andes-table__row"):
            key = row.css("th div::text").get("").strip().lower()
            val = " ".join(t.strip() for t in row.css("td *::text").getall() if t.strip())
            if key and val:
                specs[key] = val

        item["total_area_m2"] = self._extract_m2(specs, "superficie total", "sup. total")
        item["covered_area_m2"] = self._extract_m2(specs, "superficie cubierta", "sup. cubierta")
        item["rooms"] = self._extract_int(specs, "ambientes", "amb.")
        item["bedrooms"] = self._extract_int(specs, "dormitorios", "habitaciones")
        item["bathrooms"] = self._extract_int(specs, "baños")
        item["garages"] = self._extract_int(specs, "cocheras", "garages")
        item["age_years"] = self._extract_int(specs, "antigüedad")
        item["floor_number"] = self._extract_int(specs, "piso")

        # Expenses
        for k, v in specs.items():
            if "expensa" in k:
                m = re.search(r"[\d.]+", v.replace(".", "").replace(",", ""))
                if m:
                    try:
                        item["expenses_ars"] = float(m.group())
                    except ValueError:
                        pass
                break

        # Description
        item["description"] = " ".join(
            response.css("p.ui-pdp-description__content::text").getall()
        ).strip()

        # Images — multiple strategies with fallbacks
        image_urls = []

        # Strategy 1: Parse __NEXT_DATA__ — search recursively for pictures arrays
        next_data_text = response.css("script#__NEXT_DATA__::text").get("")
        if next_data_text:
            try:
                next_data = json.loads(next_data_text)
                # Try known path first
                pictures = (
                    next_data.get("props", {})
                    .get("pageProps", {})
                    .get("initialState", {})
                    .get("components", {})
                    .get("gallery", {})
                    .get("pictures", [])
                )
                if not pictures:
                    # Recursive search for any "pictures" key
                    pictures = self._find_pictures_recursive(next_data)
                for pic in pictures:
                    url = pic.get("url", "") or pic.get("secure_url", "")
                    if url and not url.startswith("data:"):
                        image_urls.append(url)
            except (json.JSONDecodeError, AttributeError):
                pass

        # Strategy 2: data-zoom attributes (high-res)
        if not image_urls:
            image_urls = [
                u for u in response.css("img.ui-pdp-gallery__figure__image::attr(data-zoom)").getall()
                if u and not u.startswith("data:") and "placeholder" not in u
            ]

        # Strategy 3: src attributes
        if not image_urls:
            image_urls = [
                u for u in (
                    response.css("img.ui-pdp-gallery__figure__image::attr(src)").getall()
                    or response.css("figure.ui-pdp-gallery__figure img::attr(src)").getall()
                    or response.css("img.ui-pdp-image::attr(src)").getall()
                )
                if u and not u.startswith("data:") and "placeholder" not in u
            ]

        # Strategy 4: meta tags fallback (at least 1 image)
        if not image_urls:
            og_image = response.css('meta[property="og:image"]::attr(content)').get("")
            if og_image and not og_image.startswith("data:"):
                image_urls.append(og_image)
            twitter_image = response.css('meta[name="twitter:image"]::attr(content)').get("")
            if twitter_image and not twitter_image.startswith("data:") and twitter_image != og_image:
                image_urls.append(twitter_image)

        item["image_urls"] = list(dict.fromkeys(image_urls))  # deduplicate

        all_text = " ".join(specs.keys()) + " " + " ".join(specs.values()) + " " + item.get("description", "") + " " + item.get("title", "")
        item["apto_credito"] = "crédito" in all_text.lower() or "credito" in all_text.lower() or "hipotecario" in all_text.lower()

        item["raw_data"] = {"url": response.url, "specs": specs}

        yield item

    def _find_pictures_recursive(self, obj, max_depth=8):
        """Recursively search a JSON object for arrays of picture objects."""
        if max_depth <= 0:
            return []
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key == "pictures" and isinstance(val, list) and val:
                    # Check if items look like picture objects (have url field)
                    if isinstance(val[0], dict) and ("url" in val[0] or "secure_url" in val[0]):
                        return val
                result = self._find_pictures_recursive(val, max_depth - 1)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = self._find_pictures_recursive(item, max_depth - 1)
                if result:
                    return result
        return []

    def _parse_price(self, fraction: str, symbol: str) -> tuple:
        if not fraction:
            return None, None
        currency = "USD" if any(c in symbol for c in ("U", "D", "$")) and "US" in symbol else "ARS"
        try:
            return float(fraction.replace(".", "").replace(",", ".")), currency
        except ValueError:
            return None, currency

    def _extract_m2(self, specs: dict, *keywords) -> "float | None":
        for k, v in specs.items():
            for kw in keywords:
                if kw in k:
                    # Take first number (handles ranges like "45 m² a 57 m²")
                    m = re.search(r"[\d.,]+", v)
                    if m:
                        try:
                            return float(m.group().replace(".", "").replace(",", "."))
                        except ValueError:
                            pass
        return None

    def _extract_int(self, specs: dict, *keywords) -> "int | None":
        for k, v in specs.items():
            for kw in keywords:
                if kw in k:
                    m = re.search(r"\d+", v)
                    if m:
                        return int(m.group())
        return None
