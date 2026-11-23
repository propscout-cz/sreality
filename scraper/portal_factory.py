"""One place that turns a source name into a Portal (or client) instance.

Every caller that turns a source into a Portal needs the same table and the same
two constructor exceptions: the realtime worker, to run a lane, and the sreality
entrypoint, to build its portal from config. `portal_runner` reads the client
table to make a portal's client. A second copy of either table is a second thing
to get wrong.

Imports are lazy and inside the functions on purpose: every portal module imports
`scraper.portal_runner`, so a module-level import here would close a cycle.
"""

from __future__ import annotations

import functools
import importlib
from typing import Any

from scraper.portal import PortalConfig

# sreality and bazos predate the config-taking constructor (see build_portal), so
# their entries are unused for construction; they are kept so the two tables stay
# symmetric and a future refactor toward the uniform constructor has one place to
# update.
PORTAL_CLASSES: dict[str, tuple[str, str]] = {
    "bazos": ("scraper.bazos_main", "BazosPortal"),
    "bezrealitky": ("scraper.bezrealitky_main", "BezrealitkyPortal"),
    "ceskereality": ("scraper.ceskereality_main", "CeskerealityPortal"),
    "idnes": ("scraper.idnes_main", "IdnesPortal"),
    "maxima": ("scraper.maxima_main", "MaximaPortal"),
    "mmreality": ("scraper.mmreality_main", "MmRealityPortal"),
    "realitymix": ("scraper.realitymix_main", "RealitymixPortal"),
    "remax": ("scraper.remax_main", "RemaxPortal"),
    "sreality": ("scraper.main", "SrealityPortal"),
    "web": ("scraper.web_main", "WebPortal"),
}

CLIENT_CLASSES: dict[str, tuple[str, str]] = {
    "bazos": ("scraper.bazos_client", "BazosClient"),
    "bezrealitky": ("scraper.bezrealitky_client", "BezrealitkyClient"),
    "ceskereality": ("scraper.ceskereality_client", "CeskerealityClient"),
    "idnes": ("scraper.idnes_client", "IdnesClient"),
    "maxima": ("scraper.maxima_client", "MaximaClient"),
    "mmreality": ("scraper.mmreality_client", "MmRealityClient"),
    "realitymix": ("scraper.realitymix_client", "RealitymixClient"),
    "remax": ("scraper.remax_client", "RemaxClient"),
    "sreality": ("scraper.sreality_client", "SrealityClient"),
    "web": ("scraper.web_client", "WebClient"),
}


def build_portal(source: str, config: PortalConfig) -> Any:
    """A Portal instance for `source`, configured from `config`."""
    if source == "bazos":
        # Bazos predates the config-taking constructor: it takes scopes and
        # reads its limits off attributes (the bazos_main.main wiring,
        # reproduced here).
        from scraper import bazos_main

        scopes = [
            c for c in config.categories
            if bazos_main.SALE_TYPE.get(c.get("sale_type"))
            and bazos_main.CATEGORY_MAIN.get(c.get("category"))
        ]
        portal = bazos_main.BazosPortal(categories=scopes)
        portal.index_rate = config.limits.index_rate
        portal.shared_rate_limiter = config.limits.shared_rate_limiter
        portal.price_change_min_pct = config.limits.price_change_min_pct
        return portal
    if source == "sreality":
        # Also predates the config-taking constructor (main.SrealityPortal takes
        # index_rate, not a PortalConfig, and builds its own category list).
        from scraper import main as sreality_main

        portal = sreality_main.SrealityPortal(index_rate=config.limits.index_rate)
        portal.shared_rate_limiter = config.limits.shared_rate_limiter
        portal.price_change_min_pct = config.limits.price_change_min_pct
        return portal
    mod_name, cls_name = PORTAL_CLASSES[source]
    cls = getattr(importlib.import_module(mod_name), cls_name)
    return cls(config)


def build_client_class(source: str) -> Any:
    """The client CLASS (not an instance) for `source` — callers read class
    attributes off it (`USE_PROXY`, `PROXY_REQUIRED`) rather than making one."""
    mod_name, cls_name = CLIENT_CLASSES[source]
    return getattr(importlib.import_module(mod_name), cls_name)


@functools.cache
def indexless_sources() -> tuple[str, ...]:
    """The sources whose Portal has no index to walk (the `indexless` seam: `web`): their walk observes no ad's end."""
    return tuple(sorted(s for s, (mod_name, cls_name) in PORTAL_CLASSES.items()
                        if getattr(getattr(importlib.import_module(mod_name), cls_name), "indexless", False)))
