"""Domain rules for pages found on the open web (admin console and discovery agent)."""

from urllib.parse import urlsplit

# News pages: keep facts and a link only, never the article text (CLAUDE.md).
NEWS_DOMAINS = (
    "straitstimes.com", "channelnewsasia.com", "todayonline.com", "mothership.sg", "zaobao.com.sg",
    "businesstimes.com.sg", "asiaone.com", "beritaharian.sg", "tamilmurasu.com.sg", "8world.com",
)  # fmt: skip

# Never fetched automatically, with the reason logged for each skip.
BLOCKED_DOMAINS: dict[str, str] = {
    "facebook.com": "prohibits automated access",
    "fb.com": "prohibits automated access",
    "instagram.com": "prohibits automated access",
    "linkedin.com": "prohibits automated access",
    "x.com": "prohibits automated access",
    "twitter.com": "prohibits automated access",
    "tiktok.com": "prohibits automated access",
    "threads.net": "prohibits automated access",
    "youtube.com": "video, not event listings",
    "reddit.com": "prohibits automated access",
    "t.me": "chat, not event listings",
    "telegram.me": "chat, not event listings",
    "whatsapp.com": "chat, not event listings",
    "quora.com": "prohibits automated access",
    "meetup.com": "API-only under its terms (needs Meetup Pro)",
    "luma.com": "covered by the Luma ICS adapter; Luma's terms allow only its public feeds",
    "lu.ma": "covered by the Luma ICS adapter; Luma's terms allow only its public feeds",
    "google.com": "search engine",
    "bing.com": "search engine",
}


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def _matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_news(url: str) -> bool:
    host = host_of(url)
    return any(_matches(host, d) for d in NEWS_DOMAINS)


def blocked_reason(url: str) -> str | None:
    host = host_of(url)
    return next((reason for domain, reason in BLOCKED_DOMAINS.items() if _matches(host, domain)), None)
