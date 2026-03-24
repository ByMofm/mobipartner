import json
import re

import scrapy
from scrapy_playwright.page import PageMethod

from mobipartner_scrapy.items import PropertyItem


class ArgenpropSpider(scrapy.Spider):
    name = "argenprop"
    allowed_domains = ["www.argenprop.com"]

    BASE_URL = "https://www.argenprop.com"

    # (url_slug, property_type, listing_type)
    SEARCHES = [
        ("departamentos/venta/tucuman", "departamento", "venta"),
        ("departamentos/alquiler/tucuman", "departamento", "alquiler"),
        ("casas/venta/tucuman", "casa", "venta"),
        ("casas/alquiler/tucuman", "casa", "alquiler"),
        ("terrenos/venta/tucuman", "terreno", "venta"),
        ("ph/venta/tucuman", "ph", "venta"),
        ("locales-comerciales/venta/tucuman", "local", "venta"),
        ("locales-comerciales/alquiler/tucuman", "local", "alquiler"),
        ("oficinas/venta/tucuman", "oficina", "venta"),
        ("cocheras/venta/tucuman", "cochera", "venta"),
    ]

    MAX_PAGES = 50

    custom_settings = {
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
                        PageMethod("wait_for_selector", ".listing__item", timeout=15000),
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
        cards = response.css(".listing__item")

        if not cards:
            self.logger.info(f"No cards on {response.url}")
            return

        links = []
        for card in cards:
            href = card.css("a.card::attr(href)").get()
            if href:
                links.append(href)

        self.logger.info(
            f"Page {response.meta['page']} — {response.url}: {len(links)} listings"
        )

        known_ids = getattr(self, "known_source_ids", set())

        for link in links:
            # Check if we already have this listing
            id_match = re.search(r"--(\d+)$", link.rstrip("/"))
            source_id = id_match.group(1) if id_match else None
            if source_id and source_id in known_ids:
                continue

            yield response.follow(
                link,
                meta={
                    "playwright": True,
                    "playwright_page_methods": [
                        PageMethod("wait_for_selector", "h1", timeout=15000),
                        # Scroll to trigger lazy-load of images
                        PageMethod("evaluate", "window.scrollBy(0, 300)"),
                        PageMethod("wait_for_timeout", 600),
                        PageMethod("evaluate", "window.scrollBy(0, 300)"),
                        PageMethod("wait_for_timeout", 500),
                        PageMethod("evaluate", "window.scrollBy(0, 300)"),
                        PageMethod("wait_for_timeout", 400),
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

        # Pagination: a[rel=next] → href like /departamentos/venta/tucuman?pagina-2
        page = response.meta["page"]
        if page < self.MAX_PAGES:
            next_href = response.css("a[rel=next]::attr(href)").get()
            if next_href:
                yield response.follow(
                    next_href,
                    meta={
                        "playwright": True,
                        "playwright_page_methods": [
                            PageMethod("wait_for_selector", ".listing__item", timeout=15000),
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
        item["source"] = "argenprop"
        item["source_url"] = response.url

        # ID from URL: ends with --{id}
        match = re.search(r"--(\d+)$", response.url.rstrip("/"))
        item["source_id"] = match.group(1) if match else response.url.rstrip("/").split("/")[-1]

        item["property_type"] = response.meta.get("property_type", "")
        item["listing_type"] = response.meta.get("listing_type", "")

        item["title"] = (
            response.css("h1.titlebar__title::text").get("")
            or response.css("h1::text").get("")
        ).strip()

        item["address"] = (
            response.css("h2.titlebar__address::text").get("")
            or response.css("p.titlebar__address::text").get("")
        ).strip()

        price_text = (
            response.css("p.titlebar__price::text").get("")
            or response.css("span.titlebar__price-value::text").get("")
        )
        item["price"], item["currency"] = self._parse_price(price_text)

        # Features
        features = {}
        for fi in response.css("li.property-features__item, li.detail-item"):
            text = " ".join(t.strip() for t in fi.css("::text").getall() if t.strip())
            if text:
                features[text.lower()] = text
        for row in response.css("div.property-features li, div.property-description li"):
            text = " ".join(row.css("::text").getall()).strip()
            if text:
                features[text.lower()] = text

        item["total_area_m2"] = self._extract_number(features, "m² tot", "sup. total", "total")
        item["covered_area_m2"] = self._extract_number(features, "m² cub", "sup. cub", "cubierta")
        item["rooms"] = self._extract_int(features, "amb")
        item["bedrooms"] = self._extract_int(features, "dorm", "habitac")
        item["bathrooms"] = self._extract_int(features, "baño")
        item["garages"] = self._extract_int(features, "cochera", "garage")
        item["age_years"] = self._extract_int(features, "antigüedad", "años")

        all_text = " ".join(features.keys()) + " " + item.get("title", "") + " " + item.get("description", "")
        item["apto_credito"] = "crédito" in all_text.lower() or "credito" in all_text.lower() or "hipotecario" in all_text.lower()

        expenses_text = response.css("span.titlebar__expenses::text").get("")
        if not expenses_text:
            for k, v in features.items():
                if "expensa" in k:
                    expenses_text = v
                    break
        if expenses_text:
            m = re.search(r"[\d.]+", expenses_text.replace(".", ""))
            if m:
                try:
                    item["expenses_ars"] = float(m.group())
                except ValueError:
                    pass

        # Images — multiple sources with lazy-load support
        images = response.css(
            "img.gallery__image::attr(src), "
            "img.detail-gallery__image::attr(data-src), "
            "img.detail-gallery__image::attr(src), "
            "div.gallery img::attr(src), "
            "div.gallery img::attr(data-src), "
            "img[data-lazy]::attr(data-lazy)"
        ).getall()
        image_list = [img for img in dict.fromkeys(images) if img and "placeholder" not in img]

        # Meta tag fallback
        if not image_list:
            og_image = response.css('meta[property="og:image"]::attr(content)').get("")
            if og_image and "placeholder" not in og_image:
                image_list.append(og_image)

        item["image_urls"] = image_list

        # Description — concatenate all paragraphs
        desc_texts = response.css(
            "div.section-description p::text, "
            "div.property-description p::text, "
            "div.property-description--content p::text"
        ).getall()
        item["description"] = " ".join(t.strip() for t in desc_texts if t.strip()).strip()

        # Coordinates — try Playwright evaluate result first, then JSON-LD
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

        item["raw_data"] = {"url": response.url, "features": features}

        yield item

    def _parse_price(self, text: str) -> tuple:
        if not text:
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

    def _extract_number(self, features: dict, *keywords) -> "float | None":
        for k, v in features.items():
            for kw in keywords:
                if kw in k:
                    m = re.search(r"[\d.,]+", v)
                    if m:
                        try:
                            return float(m.group().replace(",", "."))
                        except ValueError:
                            pass
        return None

    def _extract_int(self, features: dict, *keywords) -> "int | None":
        for k, v in features.items():
            for kw in keywords:
                if kw in k:
                    m = re.search(r"\d+", v)
                    if m:
                        return int(m.group())
        return None
