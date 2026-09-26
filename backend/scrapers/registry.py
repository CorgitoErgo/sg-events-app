"""Every source adapter, by source_id. The CLI and the scheduler worker both read this."""

from scrapers.base import SourceAdapter
from scrapers.luma import LumaAdapter

ADAPTERS: dict[str, type[SourceAdapter]] = {"luma": LumaAdapter}
