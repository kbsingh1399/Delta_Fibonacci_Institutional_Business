"""
Daily Risk Governor for FundedNext MT5 Accounts.
Enforces FundedNext's exact prop-firm rulebook:
1. Daily Loss Limit: 5% of initial balance ($235.08 on $4,701.64 account).
   - Anchored to 00:00 server time.
   - Includes floating PnL + closed PnL today.
2. Overall Loss Limit: 10% static floor ($4,231.48 on $4,701.64 account).
3. Throttle Threshold: At 80% of daily limit ($188.06), throttle new entries to max 1 concurrent.
"""

from typing import Tuple, Optional
import logging

logger = logging.getLogger("DailyRiskGovernor")


class DailyRiskGovernor:
    def __init__(
        self,
        initial_balance: float = 4701.64,
        daily_pct: float = 0.05,
        overall_pct: float = 0.10,
    ):
        self.initial = initial_balance
        self.daily_limit = round(initial_balance * daily_pct, 2)         # $235.08
        self.overall_floor = round(initial_balance * (1.0 - overall_pct), 2)  # $4,231.48
        self.day_anchor_equity: Optional[float] = None
        self.last_server_day: Optional[int] = None

    def check(self, current_equity: float, closed_pnl_today: float, server_ts: int) -> Tuple[str, str, float]:
        """Evaluates live equity and closed PnL against FundedNext rules.
        Returns: (status, message, used_loss_today)
        status:
          - 'OK'
          - 'THROTTLE_NEW_ENTRIES'
          - 'HALT_DAILY_LIMIT'
          - 'HALT_OVERALL_FLOOR'
        """
        day = int(server_ts) // 86400

        # Server midnight reset
        if self.last_server_day is None or day != self.last_server_day:
            self.last_server_day = day
            self.day_anchor_equity = current_equity
            logger.info(
                f"🌅 [GOVERNOR 00:00 SERVER RESET] New server day {day}. "
                f"Day anchor equity set to ${current_equity:,.2f}"
            )

        if self.day_anchor_equity is None:
            self.day_anchor_equity = current_equity

        # Loss used today = equity drop from midnight + any closed losses today
        floating_drop = max(0.0, self.day_anchor_equity - current_equity)
        closed_loss = max(0.0, -closed_pnl_today)
        used_loss_today = round(floating_drop + closed_loss, 2)

        # 1. Check Lifetime 10% Static Floor
        if current_equity <= self.overall_floor:
            msg = (
                f"CRITICAL: Equity ${current_equity:,.2f} breached Lifetime Floor "
                f"${self.overall_floor:,.2f} (-10.0%)!"
            )
            return "HALT_OVERALL_FLOOR", msg, used_loss_today

        # 2. Check Daily 5% Limit
        if used_loss_today >= self.daily_limit:
            msg = (
                f"HALT: Today's Loss ${used_loss_today:,.2f} breached Daily 5% Limit "
                f"${self.daily_limit:,.2f}!"
            )
            return "HALT_DAILY_LIMIT", msg, used_loss_today

        # 3. Check 80% Daily Limit Throttle
        throttle_limit = round(0.80 * self.daily_limit, 2)
        if used_loss_today >= throttle_limit:
            msg = (
                f"THROTTLE: Today's Loss ${used_loss_today:,.2f} >= 80% of Daily Limit "
                f"(${throttle_limit:,.2f}). New entries throttled."
            )
            return "THROTTLE_NEW_ENTRIES", msg, used_loss_today

        msg = (
            f"OK (Used Today: ${used_loss_today:,.2f} / ${self.daily_limit:,.2f} | "
            f"Equity: ${current_equity:,.2f} / Floor: ${self.overall_floor:,.2f})"
        )
        return "OK", msg, used_loss_today
