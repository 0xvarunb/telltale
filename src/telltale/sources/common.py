"""What every publisher's lister shares: its error, the record of a report it left out, and how
that record is logged and handed on."""

import logging
from collections.abc import Callable
from dataclasses import dataclass

# Why a report is out of scope when its vessels belong only to one of these groups.
GROUP_REASONS = {
    "fishing": "fishing vessel",
    "recreational": "recreational vessel",
    "naval": "naval vessel",
}


class ListingError(Exception):
    """A publisher's listing could not be read or paged through; the message names where."""


@dataclass
class SkippedReport:
    """A listed report left out of the listing.

    `dropped` is True when it may be a report in scope that could not be read (no report type,
    or its page request failed); False for an expected filter, such as another report type or a
    vessel type out of scope.
    """

    slug: str
    title: str
    page_url: str
    reason: str
    dropped: bool


def skip(
    on_skip: Callable[[SkippedReport], None] | None,
    skipped: SkippedReport,
    logger: logging.Logger,
) -> None:
    """Log a report left out (a warning when it is dropped, else debug) on the lister's own
    `logger`, then pass it to `on_skip`."""
    if skipped.dropped:
        logger.warning(
            "left out %s (%s): %s",
            skipped.slug or skipped.title,
            skipped.reason,
            skipped.page_url or "no page link",
        )
    else:
        logger.debug("left out %s (%s)", skipped.slug, skipped.reason)
    if on_skip is not None:
        on_skip(skipped)
