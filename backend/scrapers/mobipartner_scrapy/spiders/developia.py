import re

import scrapy

from mobipartner_scrapy.items import PropertyItem


class DevelopiaSpider(scrapy.Spider):
    """Base spider for sites built on Developia platform.

    Simple HTML sites, no Playwright needed. Subclasses define:
    name, BASE_URL, allowed_domains.
    """

    BASE_URL = ""
    MAX_PAGES = 15

    SEARCHES = [
        ("venta", "venta"),
        ("alquiler", "alquiler"),
    ]

    custom_settings = {
        # No Playwright — simple HTML
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy.core.downloader.handlers.http.HTTPDownloadHandler",
            "https": "scrapy.core.downloader.handlers.http.HTTPDownloadHandler",
        },
        "DOWNLOAD_DELAY": 2,
        "CONCURRENT_REQUESTS": 2,
        "ROBOTSTXT_OBEY": False,
        "USER_AGENT": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    }

    def start_requests(self):
        for operacion, listing_type in self.SEARCHES:
            url = f"{self.BASE_URL}/propiedades?operacion={operacion}&page=1"
            yield scrapy.Request(
                url,
                meta={"listing_type": listing_type, "operacion": operacion, "page": 1},
                callback=self.parse_listing_page,
                errback=self.handle_error,
            )

    def closed(self, reason):
        stats = self.crawler.stats.get_stats()
        self.logger.info(
            f"Spider closed ({reason}): "
            f"items={stats.get('item_scraped_count', 0)}, "
            f"errors={stats.get('item_dropped_count', 0)}"
        )

    def handle_error(self, failure):
        self.logger.error(f"Request failed: {failure.request.url} — {failure.value}")

    def parse_listing_page(self, response):
        """Parse listing page with property cards.

        Developia sites use minimal CSS classes. We find property links
        matching /propiedades/{slug} and extract data from surrounding elements.
        """
        # Find all unique property detail links
        links = response.css('a[href*="/propiedades/"]::attr(href)').getall()
        # Deduplicate while preserving order, filter out pagination/nav links
        seen = set()
        property_links = []
        for link in links:
            slug = link.rstrip("/").split("/")[-1]
            if slug and slug not in seen and not slug.startswith("?"):
                seen.add(slug)
                property_links.append(link)

        self.logger.info(
            f"Page {response.meta['page']} — {response.url}: {len(property_links)} cards"
        )

        if not property_links:
            return

        known_ids = getattr(self, "known_source_ids", set())

        for link in property_links:
            detail_url = self._abs_url(link)
            slug = link.rstrip("/").split("/")[-1]
            source_id = slug

            # Extract title from the link text or nearby h3
            title_el = response.css(f'a[href$="{slug}"]::text').get("").strip()
            # Also try h3 containing a link to this property
            if not title_el:
                title_el = response.css(
                    f'h3 a[href*="{slug}"]::text'
                ).get("").strip()

            item = PropertyItem()
            item["source"] = self.name
            item["source_id"] = source_id
            item["source_url"] = detail_url
            item["title"] = title_el if title_el else slug.replace("-", " ").title()
            item["price"] = None
            item["currency"] = None
            item["address"] = ""
            item["property_type"] = self._guess_type(item["title"])
            item["listing_type"] = response.meta["listing_type"]
            item["image_urls"] = []
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
                # Always visit detail page — card data is too sparse
                yield scrapy.Request(
                    detail_url,
                    meta={"item_data": dict(item)},
                    callback=self.parse_detail,
                    errback=self._detail_error,
                    dont_filter=True,
                )
            else:
                yield item

        # Pagination — try next link, then increment page number
        page = response.meta["page"]
        if page < self.MAX_PAGES and property_links:
            next_link = response.css(
                "a[rel=next]::attr(href), .pagination a.next::attr(href), "
                ".pagination li:last-child a::attr(href), "
                "a:contains('Siguiente')::attr(href), a:contains('»')::attr(href)"
            ).get()

            if next_link:
                yield scrapy.Request(
                    self._abs_url(next_link),
                    meta={
                        "listing_type": response.meta["listing_type"],
                        "operacion": response.meta["operacion"],
                        "page": page + 1,
                    },
                    callback=self.parse_listing_page,
                    errback=self.handle_error,
                )
            else:
                # Increment page number in URL
                next_url = re.sub(
                    r"page=\d+",
                    f"page={page + 1}",
                    response.url,
                )
                if next_url != response.url:
                    yield scrapy.Request(
                        next_url,
                        meta={
                            "listing_type": response.meta["listing_type"],
                            "operacion": response.meta["operacion"],
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
        """Parse detail page for complete property data.

        Developia sites use basic HTML: h2 for title, h3 for price,
        ul>li for features with values in <strong>, and images in <a><img>.
        """
        item_data = response.meta["item_data"]
        item = PropertyItem()
        for k, v in item_data.items():
            item[k] = v

        # Title — h1 or h2
        title = response.css("h1::text, h2::text").get("").strip()
        if title:
            item["title"] = title

        # Price — look in h3, h4, strong, or any text matching price pattern
        for selector in ["h3::text", "h4::text", "strong::text", ".precio::text", ".price::text"]:
            for text in response.css(selector).getall():
                text = text.strip()
                if re.search(r"(?:USD|U\$S|\$)\s*[\d.,]+", text) or "precio" in text.lower():
                    price, currency = self._parse_price(text)
                    if price:
                        item["price"] = price
                        item["currency"] = currency
                        break
            if item.get("price"):
                break

        # Description — paragraphs in main content
        desc_parts = response.css("p::text").getall()
        desc = " ".join(t.strip() for t in desc_parts if t.strip() and len(t.strip()) > 20)
        if desc:
            item["description"] = desc

        # Features — ul > li with values in <strong> or plain text
        for feat in response.css("ul li"):
            all_text = " ".join(feat.css("::text").getall()).strip().lower()
            # Try to get numeric value from <strong> or from text
            val_text = feat.css("strong::text").get("")
            if not val_text:
                val_text = all_text
            num_match = re.search(r"[\d.,]+", val_text)
            if not num_match:
                continue
            val_str = num_match.group().replace(".", "").replace(",", ".")
            try:
                val = float(val_str)
            except ValueError:
                continue

            if "m²" in all_text or "m2" in all_text or "mts" in all_text or "cuadrado" in all_text or "sup" in all_text:
                if "cub" in all_text:
                    item["covered_area_m2"] = val
                else:
                    item["total_area_m2"] = val
            elif "amb" in all_text:
                item["rooms"] = int(val)
            elif "dorm" in all_text or "hab" in all_text:
                item["bedrooms"] = int(val)
            elif "baño" in all_text or "bano" in all_text:
                item["bathrooms"] = int(val)
            elif "coch" in all_text or "gar" in all_text:
                item["garages"] = int(val)
            elif "antig" in all_text:
                item["age_years"] = int(val)
            elif "plant" in all_text:
                pass  # floors, not tracked

        # Images — look for property photos (inmuebles/fotos paths or gallery)
        images = response.css(
            "a[href*='fotos'] img::attr(src), "
            "a[href*='inmuebles'] img::attr(src), "
            "img[src*='fotos']::attr(src), "
            "img[src*='inmuebles']::attr(src), "
            "img[src*='storage']::attr(src), "
            "img[src*='uploads']::attr(src), "
            ".gallery img::attr(src), "
            ".carousel img::attr(src)"
        ).getall()
        # Also get full-size images from links
        full_images = response.css(
            "a[href*='fotos']::attr(href), "
            "a[href*='inmuebles']::attr(href)"
        ).getall()
        all_imgs = full_images + images
        detail_images = [
            img for img in dict.fromkeys(all_imgs)
            if img and "logo" not in img and "placeholder" not in img
            and ("fotos" in img or "inmuebles" in img or "storage" in img or "uploads" in img)
        ]
        if detail_images:
            item["image_urls"] = detail_images

        # Coordinates — from JS (Google Maps locationpicker or similar)
        lat_match = re.search(r"lat(?:itude)?['\"]?\s*[:=]\s*(-?\d+\.\d+)", response.text)
        lng_match = re.search(r"(?:lng|lon(?:gitude)?)['\"]?\s*[:=]\s*(-?\d+\.\d+)", response.text)
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


class GarciaPintoSpider(DevelopiaSpider):
    """García Pinto Propiedades — Developia platform."""

    name = "garcia_pinto"
    BASE_URL = "https://garciapintopropiedades.com.ar"
    allowed_domains = ["garciapintopropiedades.com.ar"]


class LimaInmobiliariaSpider(DevelopiaSpider):
    """Lima Inmobiliaria — Developia platform."""

    name = "lima_inmobiliaria"
    BASE_URL = "https://www.limainmobiliaria.com.ar"
    allowed_domains = ["www.limainmobiliaria.com.ar"]
