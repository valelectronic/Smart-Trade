"""Live CC/USD conversion rate from the Cantex CC-USDC ticker."""

import time

from config import RATE_MARKET_SYMBOL, RATE_STALE_SECONDS
from helpers import log_csv


class LiveCCUsdRate:
    """Track the live CC-USDC rate and detect stale updates."""

    def __init__(self):
        self.rate = None
        self.last_update_monotonic = None
        self.started_monotonic = time.monotonic()
        self.stale_warning_active = False

    def update(self, rate):
        """Store a valid live rate received from the WebSocket."""
        if rate is None or rate <= 0:
            return False

        self.rate = rate
        self.last_update_monotonic = time.monotonic()
        self.stale_warning_active = False

        print(
            f"[RATE] {RATE_MARKET_SYMBOL} updated: "
            f"{rate} USDC per CC"
        )

        log_csv(
            "CC_USD_RATE",
            cc_usd_rate=rate,
            details=(
                f"source=market.{RATE_MARKET_SYMBOL}.ticker"
            ),
        )

        return True

    def get_rate(self, warn_if_stale=True):
        """Return the last rate and warn once per stale period."""
        reference = (
            self.last_update_monotonic
            if self.last_update_monotonic is not None
            else self.started_monotonic
        )

        age = time.monotonic() - reference

        if (
            age > RATE_STALE_SECONDS
            and warn_if_stale
            and not self.stale_warning_active
        ):
            if self.rate is None:
                message = (
                    f"No {RATE_MARKET_SYMBOL} ticker rate received "
                    f"within {RATE_STALE_SECONDS} seconds. "
                    "USD-converted triggers are paused."
                )
            else:
                message = (
                    f"{RATE_MARKET_SYMBOL} ticker is stale "
                    f"({age:.0f}s old). Using the last known rate "
                    f"{self.rate}."
                )

            print(f"[RATE WARNING] {message}")

            log_csv(
                "CC_USD_RATE_STALE",
                cc_usd_rate=self.rate,
                details=message,
            )

            self.stale_warning_active = True

        # Return the last known rate if stale.
        # Return None if a valid rate has never arrived.
        return self.rate