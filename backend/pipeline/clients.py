"""External clients for enrichment, built only when their credentials are configured."""

import logging
import os

import anthropic

from app.config import Settings
from pipeline.geo.onemap import OneMapClient

logger = logging.getLogger(__name__)


def make_llm(settings: Settings) -> anthropic.AsyncAnthropic | None:
    if settings.anthropic_api_key is not None:
        return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return anthropic.AsyncAnthropic()
    logger.warning("ANTHROPIC_API_KEY not set: categories from rules only, no summaries")
    return None


def make_onemap(settings: Settings) -> OneMapClient | None:
    email = settings.onemap_email
    password = settings.onemap_password.get_secret_value() if settings.onemap_password else None
    token = settings.onemap_token.get_secret_value() if settings.onemap_token else None
    if (email and password) or token:
        return OneMapClient(email, password, token=token)
    logger.warning("No OneMap credentials (ONEMAP_TOKEN or ONEMAP_EMAIL/PASSWORD): skipping geocoding")
    return None
