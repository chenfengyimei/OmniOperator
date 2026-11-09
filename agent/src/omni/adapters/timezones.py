"""Pinned tzdata resolver; avoids OS-dependent timezone database drift."""

from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache
from importlib.resources import files
from zoneinfo import ZoneInfo

import tzdata

from ..domain.contracts import BusinessError


class PinnedTimezones:
    version = tzdata.__version__

    @lru_cache(maxsize=128)
    def resolve(self, key: str) -> dt.tzinfo:
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*", key) or any(
            part in {".", ".."} for part in key.split("/")
        ):
            raise BusinessError("invalid_timezone", "Use an installed IANA timezone", status=422)
        try:
            with files("tzdata.zoneinfo").joinpath(*key.split("/")).open("rb") as source:
                return ZoneInfo.from_file(source, key=key)
        except (OSError, ValueError) as exc:
            raise BusinessError("invalid_timezone", "The requested IANA timezone is unavailable", status=422) from exc
