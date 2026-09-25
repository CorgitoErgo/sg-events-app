"""PDPA: never log a user's precise location (geo-proximity-sg skill).

Coordinates arrive as query parameters, so the access log would record them. This filter
rounds lat/lng in logged request paths to 2 decimal places (about 1 km).
"""

import logging
import re

_COORD = re.compile(r"(?<=[?&])(lat|lng)=(-?\d+(?:\.\d+)?)")


def round_coords(path: str) -> str:
    return _COORD.sub(lambda m: f"{m.group(1)}={float(m.group(2)):.2f}", path)


class CoordinateRoundingFilter(logging.Filter):
    """For uvicorn.access, whose record args are (client, method, path, http_version, status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3 and isinstance(record.args[2], str):
            args = list(record.args)
            args[2] = round_coords(args[2])
            record.args = tuple(args)
        return True


def install() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, CoordinateRoundingFilter) for f in access.filters):
        access.addFilter(CoordinateRoundingFilter())
