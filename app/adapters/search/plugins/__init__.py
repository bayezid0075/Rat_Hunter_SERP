"""Configurable direct website search plugins."""

from app.adapters.search.plugins.base import WebsiteSearchPlugin
from app.adapters.search.plugins.sites import (
    DarazPlugin,
    GadgetAndGearPlugin,
    PickabooPlugin,
    StarTechPlugin,
)
from app.config import Settings


def build_plugins(settings: Settings) -> list[WebsiteSearchPlugin]:
    plugins: list[WebsiteSearchPlugin] = []
    if settings.direct_daraz_enabled:
        plugins.append(DarazPlugin())
    if settings.direct_pickaboo_enabled:
        plugins.append(PickabooPlugin())
    if settings.direct_startech_enabled:
        plugins.append(StarTechPlugin())
    if settings.direct_gadgetandgear_enabled:
        plugins.append(GadgetAndGearPlugin())
    return plugins


__all__ = [
    "DarazPlugin",
    "GadgetAndGearPlugin",
    "PickabooPlugin",
    "StarTechPlugin",
    "WebsiteSearchPlugin",
    "build_plugins",
]
