"""Offline replay of the Delta live scanner, sweep and trailing manager."""
import argparse
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

# The live module normally opens its daemon log at import.
with patch("logging.FileHandler", return_value=logging.NullHandler()):
    import mt5_alpha_trader_live as live

live.logger.disabled = True
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "Blueberry_Data"
HOUR = 3600
POINTS = (0, 1200, 2400, 3590)
BASKET = live.OPTIMAL_ALPHA_BASKET
HOLDOUT = ("2026-05-17", "2026-11-17")
BASE_EQUITY = 2500.0
LIVE_RISK_CAP = live.MAX_RISK_CAP_USD
TICK_WINDOW = ("2026-11-10", "2026-11-17")


def seconds(value):
    return int(pd.Timestamp(value, tz="UTC").timestamp())


def read_bars(symbol, tf):
    d = pd.read_parquet(DATA / f"{symbol}_{tf}.parquet")
    d["time"] = pd.to_datetime(d.time, utc=True).dt.as_unit("s").astype("int64")
    d = d.sort_values("time", ignore_index=True)
    if d.time.duplicated().any() or not np.isfinite(d[["open", "high", "low", "close", "spread"]]).all().all():
        raise ValueError(f"Invalid {symbol} {tf} history")
    return d


class Conversions:
    def __init__(self, currencies):
        self.data = {}
        for cur in currencies - {"USD"}:
            d = read_bars(f"USD{cur}.pi", "1H")
            self.data[cur] = d.time.to_numpy() + HOUR, d.close.to_numpy()

    def usd(self, cur, t):
        if cur == "USD":
            return 1.0
        t_series, close = self.data[cur]
        return 1 / close[max(0, t_series.searchsorted(t, side="right") - 1)]


@dataclass
class Tape:
    setup: np.recarray
    candidates: np.ndarray
    h1: np.ndarray
    bids: np.ndarray
    spreads: np.ndarray


def make_tape(cfg, spec, mode, stress):
    symbol, tf = cfg["symbol"], cfg["tf"]
    h1 = read_bars(symbol, "1H")
    setup = (h1 if tf == "1H" else read_bars(symbol, tf))[["time", "open", "high", "low", "close"]].to_records(index=False)
    close = pd.Series(setup.close)
    span = cfg["ema"]
    full = close.ewm(span=span, adjust=False).mean()
    ema = (full + (1 - 2 / (span + 1)) ** 598 * (close.shift(598) - full.shift(598))).fillna(full)
    # Cheap necessary-condition screen. The actual live scanner decides every survivor.
    wick = 0.25 if cfg["primary_model"] == "MODEL_1" else 0.33
    shelf = 0.002 if cfg["primary_model"] == "MODEL_1" else 0.0
    spread_range = np.maximum(1e-6, setup.high - setup.low)
    lower = ((np.minimum(setup.open, setup.close) - setup.low) / spread_range >= wick
             ) & (setup.low <= ema * (1 + shelf) + 1e-12) & (setup.close >= ema - 1e-12)
    upper = ((setup.high - np.maximum(setup.open, setup.close)) / spread_range >= wick
             ) & (setup.high >= ema * (1 - shelf) - 1e-12) & (setup.close <= ema + 1e-12)
    low_first = (h1.close >= h1.open).to_numpy() if mode.startswith("direction") else mode == "low_first"
    first = np.where(low_first, h1.low, h1.high)
    second = np.where(low_first, h1.high, h1.low)
    bids = np.column_stack((h1.open, first, second, h1.close))
    spreads = np.maximum(h1.spread.to_numpy(), 0) * spec.point * (2 if stress else 1)
    return Tape(setup, (lower | upper).to_numpy(), h1.time.to_numpy(), bids, spreads)


class PaperBroker:
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_INVALID_STOPS = 10016
    SYMBOL_TRADE_MODE_FULL = 4
    TRADE_ACTION_SLTP = 6
    TIMEFRAME_H1 = 1
    TIMEFRAME_H4 = 4

    def __init__(self, tapes, specs, conversions):
        self.tapes, self.specs, self.conversions = tapes, specs, conversions
        self.ticks, self.setup_indices, self.fresh = {}, {}, set()
        self.now, self.phase = 0, 0

    def symbol_info_tick(self, symbol):
        return self.ticks.get(symbol)

    def symbol_info(self, symbol):
        spec = self.specs[symbol]
        spec.trade_tick_value = spec.trade_contract_size * spec.trade_tick_size * self.conversions.usd(
            spec.currency_profit, self.now,
        )
        return spec

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        i = self.setup_indices[symbol]
        return self.tapes[symbol].setup[max(0, i + 1 - count):i+1]

    def order_send(self, req):
        assert req["action"] == self.TRADE_ACTION_SLTP
        p = self.trader.active_positions_by_ticket[req["position"]]
        symbol = p["symbol"]
        spec, tick = self.specs[symbol], self.ticks[symbol]
        cur = tick.bid if p["side"] == 1 else tick.ask
        minimum = max(spec.trade_stops_level, spec.trade_freeze_level) * spec.point
        ok = p["side"] * (cur - req["sl"]) > minimum
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE if ok else self.TRADE_RETCODE_INVALID_STOPS, comment="paper stop check")

    def account_info(self):
        balance = self.trader.balance
        equity = balance
        for sym, p in self.trader.active_positions.items():
            tick = self.ticks[sym]
            price = tick.bid if p["side"] == 1 else tick.ask
            equity += self.trader.pnl(p, price)
        return SimpleNamespace(balance=balance, equity=equity)


class OfflineGovernor(live.DailyRiskGovernor):
    def _load_state(self):
        pass

    def _save_state(self):
        pass


class OfflineTrader(live.MT5AlphaTrader):
    def __init__(self, broker, stress, strict, close_poll):
        self.broker = broker
        self.broker.trader = self
        self.risk_usd = live.DEFAULT_RISK_USD
        self.active_positions, self.trade_cooldowns = {}, {}
        self.active_positions_by_ticket = {}
        self.governor = OfflineGovernor()
        self.balance = BASE_EQUITY
        self.trades = []
        self.rejections = Counter()
        self.entries = 0
        self.stress = stress
        self.strict = strict
        self.close_poll = close_poll
        self.breach = None
        self.peak, self.drawdown = BASE_EQUITY, 0
        self.max_sized, self.max_initial_open, self.over_budget_after_fill = 0.0, 0.0, 0

    def save_state(self):
        pass

    def emit_health_status(self, gov_status="OK", used_loss_today=0):
        pass

    def check_market_session(self, symbol):
        return symbol in self.broker.fresh and super().check_market_session(symbol)

    def scan_asset(self, cfg):
        symbol = cfg["symbol"]
        tape = self.broker.tapes[symbol]
        i = self.broker.setup_indices[symbol]
        if i < 1 or not tape.candidates[i-1]:
            return None
        s = super().scan_asset(cfg)
        if s is None:
            self.rejections["screened_or_evaluated"] += 1
        return s

    def fee(self, symbol, volume):
        commission = 7 if self.broker.specs[symbol].trade_calc_mode == 0 or symbol == "XAUUSD.pi" else 0
        return commission * volume / 2

    def execute_order(self, s):
        symbol, side = s["symbol"], s["side"]
        spec = self.broker.specs[symbol]
        tick = self.broker.ticks[symbol]
        price = tick.ask if side == 1 else tick.bid
        price += side * spec.trade_tick_size * self.stress
        other = tick.bid if side == 1 else tick.ask
        minimum = spec.trade_stops_level * spec.point
        if (side * (other - s["stop"]) <= minimum or side * (s["target"] - other) <= minimum
                or price <= 0 or s["stop"] <= 0 or s["risk_dist"] <= 0):
            self.rejections["broker_order_geometry"] += 1
            return False
        account = self.broker.account_info()
        used = max(0, self.governor.day_anchor_equity - account.equity)
        current = sum(p["actual_risk_usd"] for p in self.active_positions.values())
        projected = used + current + s["actual_risk_usd"]
        if self.strict and projected > 90:
            self.rejections["strict_budget"] += 1
            return False
        self.over_budget_after_fill += int(projected > 90)
        self.max_initial_open = max(self.max_initial_open, current + s["actual_risk_usd"])
        self.max_sized = max(self.max_sized, s["actual_risk_usd"])
        self.entries += 1
        p = {**s, "ticket": self.entries, "entry_price": price, "stop_price": s["stop"],
             "target_price": s["target"], "entry_ts": self.broker.now, "max_fav_r": 0.0}
        self.balance -= self.fee(symbol, p["lot"])
        self.active_positions[symbol] = p
        self.active_positions_by_ticket[p["ticket"]] = p
        return True

    def pnl(self, p, price):
        spec = self.broker.specs[p["symbol"]]
        return (p["side"] * (price - p["entry_price"]) * p["lot"] * spec.trade_contract_size
                * self.broker.conversions.usd(spec.currency_profit, self.broker.now))

    def settle(self, p, price, reason):
        spec = self.broker.specs[p["symbol"]]
        now = self.broker.now
        if self.stress and reason != "TP":
            price -= p["side"] * spec.trade_tick_size
        fee = self.fee(p["symbol"], p["lot"])
        days = now // 86400 - p["entry_ts"] // 86400
        carry = self.stress * 0.02 * p["actual_risk_usd"] * days
        pnl = self.pnl(p, price) - fee - carry
        self.balance += pnl
        self.trades.append({"symbol": p["symbol"], "entry": p["entry_ts"], "exit": now,
                            "cash": pnl - fee, "exit_type": reason, "risk": p["actual_risk_usd"]})
        self.trade_cooldowns[p["symbol"] + "_exit_ts"] = now
        self.active_positions.pop(p["symbol"])
        self.active_positions_by_ticket.pop(p["ticket"])

    def _close_broker_position(self, p, comment="ALGO_CLOSE"):
        tick = self.broker.ticks[p["symbol"]]
        self.settle(p, tick.bid if p["side"] == 1 else tick.ask, comment)
        return True

    def sync_broker_positions(self):
        for sym, p in list(self.active_positions.items()):
            if sym not in self.broker.fresh:
                continue
            tick = self.broker.ticks[sym]
            side = p["side"]
            price = tick.bid if side == 1 else tick.ask
            if side * (price - p["stop_price"]) <= 0:
                fill = price if self.broker.phase == 0 else p["stop_price"]
                self.settle(p, fill, "SL")
            elif side * (price - p["target_price"]) >= 0:
                self.settle(p, p["target_price"], "TP")

    def manage_open_positions(self):
        if not self.close_poll or self.broker.phase in (0, 3):
            super().manage_open_positions()

    def settle_recorded_ticks(self, data, previous, current):
        now = self.broker.now
        for sym, p in list(self.active_positions.items()):
            t, bid, ask = data[sym]
            start, stop = previous[sym] + 1, current[sym] + 1
            price = (bid if p["side"] == 1 else ask)[start:stop]
            if not len(price):
                continue
            hit_stop = p["side"] * (price - p["stop_price"]) <= 0
            hit_target = p["side"] * (price - p["target_price"]) >= 0
            hits = hit_stop | hit_target
            i = hits.argmax()
            if hits[i]:
                self.broker.now = int(t[start + i] // 1000)
                self.settle(p, price[i] if hit_stop[i] else p["target_price"],
                            "SL" if hit_stop[i] else "TP")
        self.broker.now = now

    def sample(self):
        account = self.broker.account_info()
        self.peak = max(self.peak, account.equity)
        self.drawdown = max(self.drawdown, self.peak - account.equity)
        used = max(0, (self.governor.day_anchor_equity or BASE_EQUITY) - account.equity)
        self.max_daily = max(getattr(self, "max_daily", 0), used)
        if self.breach is None and (account.equity <= 2250 or used >= 100):
            self.breach = {"time": self.broker.now, "equity": round(account.equity, 2),
                           "daily_loss": round(used, 2)}


def simulate(window, cfgs, specs, conversions, mode, stress, strict, offset,
             risk_cap=LIVE_RISK_CAP):
    start, end = map(seconds, window)
    tapes = {c["symbol"]: make_tape(c, specs[c["symbol"]], mode, stress) for c in cfgs}
    broker = PaperBroker(tapes, specs, conversions)
    trader = OfflineTrader(broker, stress, strict, close_poll=mode == "direction_close_poll")
    live.mt5 = broker
    live.time = SimpleNamespace(time=lambda: broker.now)
    live.get_server_offset_seconds = lambda: offset * HOUR
    live.OPTIMAL_ALPHA_BASKET = cfgs
    live.MAX_RISK_CAP_USD = risk_cap
    grid = np.arange(start, end, HOUR)
    maps = {}
    for sym, t in tapes.items():
        src = np.searchsorted(t.h1, grid, side="right") - 1
        complete = (src >= 0) & (t.h1[np.maximum(src, 0)] == grid)
        setup = np.searchsorted(t.setup.time, grid, side="right") - 1
        maps[sym] = src, complete, setup
    for gi, hour in enumerate(grid):
        fresh = [(sym, tape, idx[gi], setup[gi]) for sym, tape in tapes.items()
                 for idx, valid, setup in (maps[sym],) if valid[gi]]
        broker.fresh = {x[0] for x in fresh}
        for phase, dt in enumerate(POINTS):
            broker.now, broker.phase = int(hour + dt), phase
            for sym, tape, idx, current in fresh:
                bid = tape.bids[idx, phase]
                broker.ticks[sym] = SimpleNamespace(bid=bid, ask=bid + tape.spreads[idx], time=broker.now)
                broker.setup_indices[sym] = current
            trader.sync_broker_positions()
            trader.run_sweep()
            trader.sample()
            if trader.breach:
                break
        if trader.breach:
            break
    return close_at_end(trader)


def close_at_end(trader):
    for p in list(trader.active_positions.values()):
        trader._close_broker_position(p, "MARK")
    return summarize(trader)


def load_ticks(path, storage, sym, stack):
    columns = ("time_msc", "bid", "ask")
    paths = [storage / f"{sym}_{c}" for c in columns]
    dtypes = ("int64", "float64", "float64")
    with ExitStack() as files:
        outputs = [files.enter_context(p.open("wb")) for p in paths]
        for batch in pq.ParquetFile(path).iter_batches(columns=columns, batch_size=131_072):
            for i, (out, dtype) in enumerate(zip(outputs, dtypes)):
                batch.column(i).to_numpy().astype(dtype, copy=False).tofile(out)
    vectors = tuple(np.memmap(p, mode="r", dtype=d) for p, d in zip(paths, dtypes))
    for x in vectors:
        stack.callback(x._mmap.close)
    return vectors


def tick_study(directory, specs, conversions):
    start, end = map(seconds, TICK_WINDOW)
    files = {c["symbol"]: directory / f"{c['symbol']}_{TICK_WINDOW[0]}_{TICK_WINDOW[1]}.parquet"
             for c in BASKET}
    cfgs = [c for c in BASKET if files[c["symbol"]].exists()
            and pq.read_metadata(files[c["symbol"]]).num_rows > 0]
    if not cfgs:
        raise ValueError(f"No tick archives for {TICK_WINDOW} in {directory}")
    tapes = {c["symbol"]: make_tape(c, specs[c["symbol"]], "direction", 0) for c in cfgs}
    clock = np.arange(start, end, 10)
    coverage = {}
    with TemporaryDirectory(prefix="delta-ticks-") as tmp, ExitStack() as stack:
        data, maps = {}, {}
        for sym, tape in tuple(tapes.items()):
            t, bid, ask = load_ticks(files[sym], Path(tmp), sym, stack)
            h1 = tape.h1[(tape.h1 >= start) & (tape.h1 < end)]
            starts = t.searchsorted(h1 * 1000)
            stops = t.searchsorted((h1 + HOUR) * 1000)
            coverage[sym] = float(np.mean(stops > starts))
            if coverage[sym] < 0.97:
                tapes.pop(sym)
                continue
            data[sym] = t, bid, ask
            maps[sym] = (t.searchsorted(clock * 1000, side="right") - 1,
                         tape.setup.time.searchsorted(clock, side="right") - 1)
        cfgs = [c for c in cfgs if c["symbol"] in tapes]
        if not cfgs:
            raise ValueError("Tick archives failed H1 coverage validation")
        broker = PaperBroker(tapes, specs, conversions)
        trader = OfflineTrader(broker, 0, False, False)
        live.mt5 = broker
        live.time = SimpleNamespace(time=lambda: broker.now)
        live.get_server_offset_seconds = lambda: 0
        live.OPTIMAL_ALPHA_BASKET = cfgs
        live.MAX_RISK_CAP_USD = LIVE_RISK_CAP

        previous = {sym: -1 for sym in data}
        for i, now in enumerate(clock):
            broker.now, broker.phase = int(now), 0
            broker.fresh = set()
            current = {}
            for sym, (t, bid, ask) in data.items():
                indices, setups = maps[sym]
                idx, cr = indices[i], setups[i]
                current[sym] = idx
                if idx < 0:
                    continue
                when = int(t[idx] // 1000)
                broker.ticks[sym] = SimpleNamespace(bid=bid[idx], ask=ask[idx], time=when)
                broker.setup_indices[sym] = cr
                if cr >= 1 and t[idx] >= tapes[sym].setup.time[cr] * 1000 and now - when <= 30:
                    broker.fresh.add(sym)
            trader.settle_recorded_ticks(data, previous, current)
            previous = current
            trader.run_sweep()
            trader.sample()
            if trader.breach:
                break
        tick_stats = close_at_end(trader)
    return {"window": TICK_WINDOW, "symbols": [c["symbol"] for c in cfgs],
            "coverage": coverage, "ticks": tick_stats,
            "matching_bar_subset": simulate(TICK_WINDOW, cfgs, specs, conversions,
                                            "direction", 0, False, 0)}


def summarize(trader):
    p = np.asarray([t["cash"] for t in trader.trades])
    wins, losses = p[p > 0].sum(), -p[p < 0].sum()
    return {
        "trades": len(p), "net_usd": round(float(p.sum()), 4),
        "pf": round(float(wins / losses), 4) if losses else None,
        "max_sampled_equity_drawdown_usd": round(trader.drawdown, 2),
        "max_sampled_daily_loss_usd": round(trader.max_daily, 2),
        "max_trade_risk_usd": trader.max_sized,
        "max_simultaneous_initial_risk_usd": round(trader.max_initial_open, 2),
        "fills_over_90_usd_combined_budget": trader.over_budget_after_fill,
        "first_floor_touch": trader.breach,
        "rejections": dict(trader.rejections),
        "by_symbol": {s: {"trades": len(x), "net_usd": round(sum(t["cash"] for t in x), 4)}
                      for s in sorted({t["symbol"] for t in trader.trades})
                      for x in [[t for t in trader.trades if t["symbol"] == s]]},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specs", type=Path, default=ROOT / "audit/independent_symbol_specs.json")
    parser.add_argument("--output", type=Path, default=ROOT / "audit/independent_runtime_replay.json")
    parser.add_argument("--holdout-only", action="store_true")
    parser.add_argument("--tick-cache", type=Path)
    args = parser.parse_args()
    specs = {s: SimpleNamespace(**v) for s, v in json.loads(args.specs.read_text()).items()}
    conversions = Conversions({i.currency_profit for i in specs.values()})
    cases = [
        ("direction", "direction", 0, False, 0),
        ("low_first", "low_first", 0, False, 0),
        ("high_first", "high_first", 0, False, 0),
        ("slow_trail", "direction_close_poll", 0, False, 0),
        ("cost_stress", "direction", 1, False, 0),
        ("strict_budget", "direction", 0, True, 0),
        ("utc_plus_2", "direction", 0, False, 2),
        ("cap_12_strict", "direction", 0, True, 0, 12.0),
        ("cap_12_stress", "direction", 1, True, 0, 12.0),
        ("cap_12_slow", "direction_close_poll", 0, True, 0, 12.0),
    ]
    windows = {"retrospective_six_months": HOLDOUT}
    if not args.holdout_only:
        windows.update({"2024": ("2024-01-01", "2025-01-01"),
                        "2025": ("2025-01-01", "2026-01-01"),
                        "early_2026": ("2026-01-01", HOLDOUT[0])})
    results = {}
    for label, window in windows.items():
        results[label] = {"window": window}
        selected = cases if label == "retrospective_six_months" else cases[:2]
        if label == "2024":
            selected = selected + [case for case in cases if case[0] == "cap_12_strict"]
        for name, *params in selected:
            result = simulate(window, BASKET, specs, conversions, *params)
            results[label][name] = result
            print(label, name, result["trades"], result["net_usd"], result["pf"],
                  result["max_sampled_equity_drawdown_usd"], result["first_floor_touch"], flush=True)
        args.output.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
    if args.tick_cache:
        results["tick_subset"] = tick_study(args.tick_cache, specs, conversions)
        args.output.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
        print("tick_subset", results["tick_subset"]["symbols"],
              results["tick_subset"]["ticks"]["net_usd"], flush=True)
    print(args.output)


if __name__ == "__main__":
    main()
