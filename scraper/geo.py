import json
import logging
import re

from .client import LinkedInClient

logger = logging.getLogger("scraper.geo")

TYPEAHEAD_URL = "https://www.linkedin.com/jobs-guest/jobs/api/typeahead/hitsV2"

# Verified-by-use fallbacks for the most common countries.
FALLBACK_GEO_IDS: dict[str, str] = {
    "united states": "103644278",
    "usa": "103644278",
    "us": "103644278",
    "united kingdom": "101165590",
    "uk": "101165590",
    "great britain": "101165590",
    "canada": "101174742",
    "germany": "101282230",
    "france": "105015875",
    "spain": "105646813",
    "netherlands": "102890719",
    "poland": "105072130",
    "portugal": "100364837",
    "ukraine": "102264497",
    "india": "102713980",
    "brazil": "106057199",
    "mexico": "103323778",
    "australia": "101452733",
    "singapore": "102454443",
    "united arab emirates": "104305776",
    "uae": "104305776",
    "israel": "101620260",
    "turkey": "102127542",
    "japan": "101355337",
    "worldwide": "92000000",
}

GEO_URN_RE = re.compile(r"urn:li:(?:fsd_geo|geo):(\d+)")


def fallback_geo_id(country: str) -> str | None:
    return FALLBACK_GEO_IDS.get((country or "").strip().lower())


def resolve_geo_id(country: str, client: LinkedInClient | None = None) -> str:
    """Return the geoId for a country, or "" when it cannot be resolved."""
    country = (country or "").strip()
    if not country:
        return ""

    # 1) Curated table first: no request, no 404 noise, stable ids.
    geo_id = fallback_geo_id(country)
    if geo_id:
        logger.info("geoId for %r taken from the curated table: %s", country, geo_id)
        return geo_id

    # 2) Unknown country: ask LinkedIn's geo typeahead.
    if client is not None:
        try:
            raw = client.get(
                TYPEAHEAD_URL,
                params={"q": "type", "type": "GEO", "query": country},
            )
            geo_id = _extract_from_typeahead(raw)
            if geo_id:
                logger.info("geoId for %r resolved via typeahead: %s", country, geo_id)
                return geo_id
        except Exception as exc:  # noqa: BLE001 - never fail the run on this
            logger.warning("typeahead lookup failed for %r: %s", country, exc)

    logger.warning(
        "no geoId found for %r; searching by free-text location instead "
        "(set geo_id manually in the admin for exact results)", country
    )
    return ""


def _extract_from_typeahead(raw: str) -> str:
    if not raw:
        return ""
    # The response is JSON, but tolerate an HTML wrapper.
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        payload = None

    if isinstance(payload, dict):
        for hit in payload.get("hits", []) or []:
            blob = json.dumps(hit)
            match = GEO_URN_RE.search(blob)
            if match:
                return match.group(1)

    match = GEO_URN_RE.search(raw)
    return match.group(1) if match else ""
