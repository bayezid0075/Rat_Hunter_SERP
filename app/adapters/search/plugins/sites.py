"""Built-in direct website plugin declarations.

Each entry points at a real public search endpoint.  Network access is never
assumed: blocked/unavailable sources surface standardized adapter errors and
fixture-based tests exercise the parsers without live access.
"""

from __future__ import annotations

from app.adapters.search.plugins.base import ConfigurablePlugin


class DarazPlugin(ConfigurablePlugin):
    name = "daraz"
    display_name = "Daraz"
    base_url = "https://www.daraz.com.bd"
    search_path = "/catalog/?q={query}"
    currency_hint = "BDT"
    card_selectors = (
        "div[data-qa-locator='product-item']",
        ".gridItem--Yd0sa",
        ".product-card",
        ".c2prKC",
    )
    title_selectors = (".RfADt a", ".title", "[class*='title'] a", "a")
    price_selectors = (".aBrP0", ".price", "[class*='price']")
    image_selectors = ("img",)


class PickabooPlugin(ConfigurablePlugin):
    name = "pickaboo"
    display_name = "Pickaboo"
    base_url = "https://www.pickaboo.com"
    search_path = "/search?q={query}"
    currency_hint = "BDT"
    card_selectors = (".product-item", ".product-card", "li.product", ".grid-product")
    title_selectors = (".product-name", ".product-title", ".title", "h3", "h2")
    price_selectors = (".price", ".product-price", "[class*='price']")
    image_selectors = ("img",)


class StarTechPlugin(ConfigurablePlugin):
    name = "startech"
    display_name = "Star Tech"
    base_url = "https://www.startech.com.bd"
    search_path = "/product/search?search={query}"
    currency_hint = "BDT"
    card_selectors = (".product-thumb", ".p-item", ".product-layout", ".product-item")
    title_selectors = (".p-item-name a", ".product-name a", "h4 a", "h3 a", "a")
    price_selectors = (".p-item-price", ".price", "[class*='price']")
    image_selectors = ("img",)


class GadgetAndGearPlugin(ConfigurablePlugin):
    name = "gadgetandgear"
    display_name = "Gadget & Gear"
    base_url = "https://gadgetandgear.com"
    search_path = "/search?q={query}"
    currency_hint = "BDT"
    card_selectors = (".product-item", ".product-card", ".single-product", ".product-grid-item")
    title_selectors = (".product-title a", ".product-name", "h4 a", "h3 a", "a")
    price_selectors = (".price", ".product-price", "[class*='price']")
    image_selectors = ("img",)
