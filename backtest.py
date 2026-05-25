#!/usr/bin/env python3
"""
Backtesting engine for Hermes Trading Bot.

Provides a `Backtester` class that runs a strategy function over historical
OHLCV data, produces an equity curve, calculates performance metrics, and
generates an HTML equity-curve chart using inline SVG (no external deps).
"""

import logging
import math
from datetime import datetime
from typing import Callable, Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class Backtester:
    """Backtesting engine that uses a HermesTrader instance for data access."""

    def __init__(self, trader):
        """
        Args:
            trader: HermesTrader instance (used for get_historical_data).
        """
        self.trader = trader

    # ------------------------------------------------------------------
    #  Core backtest runner
    # ------------------------------------------------------------------

    def run_backtest(
        self,
        symbol: str,
        strategy_func: Callable,
        start_date: datetime,
        end_date: Optional[datetime] = None,
    ) -> tuple:
        """
        Run a strategy over historical price data.

        The strategy function signature must be::

            def strategy(df: pd.DataFrame) -> list[dict]:
                ...

        It receives a DataFrame with columns [open, high, low, close, volume]
        and must return a list of signal dicts.  Each signal must have at
        least ``date``, ``action`` ('BUY' / 'SELL'), and ``price``.

        The backtest simulates simple long-only trading:
            - BUY  → enter at close price (if not already in position)
            - SELL → exit at close price (if in position)

        Args:
            symbol: Ticker symbol.
            strategy_func: Callable (df -> list[signal]).
            start_date: Start of backtest period.
            end_date: End of backtest period (default: now).

        Returns:
            Tuple (equity_curve, trades).
            equity_curve: pd.DataFrame with columns [date, equity].
            trades: List of dicts with keys
                [date, action, price, shares, portfolio_value, pnl].
        """
        if end_date is None:
            end_date = datetime.now()

        days = (end_date - start_date).days
        if days < 1:
            days = 365

        logger.info(
            "Backtest %s: %s → %s (%d days)",
            symbol,
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
            days,
        )

        # Fetch data (add 60 buffer days for indicator calculation)
        buffer_days = days + 60
        df = self.trader.get_historical_data(symbol, days=buffer_days, timeframe="1Day")
        if df is None or df.empty:
            logger.warning("No historical data returned for %s", symbol)
            return pd.DataFrame(), []

        # Filter to backtest date range
        df = df.copy()
        df = df[df.index <= pd.Timestamp(end_date).tz_localize('UTC')]
        df = df[df.index >= pd.Timestamp(start_date).tz_localize('UTC')]
        if len(df) < 20:
            logger.warning("Insufficient data in date range for %s", symbol)
            return pd.DataFrame(), []

        df.sort_index(inplace=True)

        # Add indicators for the full dataset (including buffer for lookback)
        df_full = self.trader.get_historical_data(symbol, days=buffer_days, timeframe="1Day")
        if df_full is not None:
            df_full = self.trader.calculate_indicators(df_full)
        else:
            df_full = df

        # Simulate trading
        trades = []
        equity_curve = []
        cash = 100_000.0  # Starting capital
        shares = 0
        in_position = False
        entry_price = 0.0

        # We walk through each day
        for idx, (date, row) in enumerate(df.iterrows()):
            price = float(row["close"])
            date_ts = date

            # Get the window up to this point for the strategy
            lookback = df_full.loc[:date_ts].copy() if date_ts in df_full.index else df.loc[:date_ts].copy()
            if len(lookback) < 20:
                continue

            signals = strategy_func(lookback)

            # Find the most recent signal
            latest_signal = None
            for sig in signals:
                if sig.get("action") in ("BUY", "SELL"):
                    latest_signal = sig

            if latest_signal is None:
                # HOLD — mark equity
                port_value = cash + shares * price
                equity_curve.append({"date": date_ts, "equity": round(port_value, 2)})
                continue

            action = latest_signal["action"]

            if action == "BUY" and not in_position:
                # Enter with all cash
                shares = int(cash / price)
                if shares < 1:
                    continue
                cost = shares * price
                cash -= cost
                in_position = True
                entry_price = price
                trades.append({
                    "date": date_ts,
                    "action": "BUY",
                    "price": round(price, 2),
                    "shares": shares,
                    "portfolio_value": round(cash + shares * price, 2),
                    "pnl": 0.0,
                })
                logger.debug("BUY  %s  %d shares @ %.2f", date_ts.date(), shares, price)

            elif action == "SELL" and in_position:
                # Exit position
                proceeds = shares * price
                cash += proceeds
                pnl = proceeds - (shares * entry_price)
                trades.append({
                    "date": date_ts,
                    "action": "SELL",
                    "price": round(price, 2),
                    "shares": shares,
                    "portfolio_value": round(cash, 2),
                    "pnl": round(pnl, 2),
                })
                logger.debug("SELL %s  %d shares @ %.2f  PnL: %.2f", date_ts.date(), shares, price, pnl)
                shares = 0
                in_position = False
                entry_price = 0.0

            # Record equity
            port_value = cash + shares * price
            equity_curve.append({"date": date_ts, "equity": round(port_value, 2)})

        # Close any open position at the last price
        if in_position and len(df) > 0:
            last_price = float(df["close"].iloc[-1])
            proceeds = shares * last_price
            cash += proceeds
            pnl = proceeds - (shares * entry_price)
            trades.append({
                "date": df.index[-1],
                "action": "SELL (close)",
                "price": round(last_price, 2),
                "shares": shares,
                "portfolio_value": round(cash, 2),
                "pnl": round(pnl, 2),
            })
            shares = 0
            equity_curve[-1]["equity"] = round(cash, 2)

        equity_df = pd.DataFrame(equity_curve)
        if not equity_df.empty:
            equity_df.set_index("date", inplace=True)

        logger.info(
            "Backtest complete: %d trades, final equity $%.2f",
            len([t for t in trades if t["action"] in ("BUY", "SELL")]),
            cash,
        )
        return equity_df, trades

    # ------------------------------------------------------------------
    #  Metrics calculation
    # ------------------------------------------------------------------

    def calculate_metrics(self, equity_curve: pd.DataFrame) -> dict:
        """
        Calculate performance metrics from the equity curve.

        Args:
            equity_curve: DataFrame with 'equity' column (indexed by date).

        Returns:
            Dict with keys:
                sharpe_ratio, max_drawdown_pct, win_rate_pct,
                total_return_pct, cagr_pct, num_trades.
        """
        if equity_curve is None or equity_curve.empty:
            return {
                "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "win_rate_pct": 0.0,
                "total_return_pct": 0.0,
                "cagr_pct": 0.0,
                "num_trades": 0,
            }

        equity = equity_curve["equity"].values
        initial = float(equity[0]) if len(equity) > 0 else 100_000
        final = float(equity[-1]) if len(equity) > 0 else initial

        # Total return
        total_return_pct = ((final - initial) / initial) * 100 if initial > 0 else 0.0

        # CAGR
        if len(equity) > 1:
            days = (equity_curve.index[-1] - equity_curve.index[0]).days
            years = days / 365.25
            if years > 0 and initial > 0:
                cagr_pct = ((final / initial) ** (1 / years) - 1) * 100
            else:
                cagr_pct = 0.0
        else:
            cagr_pct = 0.0

        # Max drawdown
        peak = equity[0]
        max_dd = 0.0
        for val in equity:
            if val > peak:
                peak = val
            dd = (peak - val) / peak * 100
            if dd > max_dd:
                max_dd = dd

        # Sharpe ratio (annualized, assuming daily returns, risk-free ~0)
        if len(equity) > 1:
            returns = pd.Series(equity).pct_change().dropna()
            if len(returns) > 0 and returns.std() > 0:
                sharpe_ratio = (returns.mean() / returns.std()) * math.sqrt(252)
            else:
                sharpe_ratio = 0.0
        else:
            sharpe_ratio = 0.0

        # Win rate is not directly calculable from equity curve alone;
        # it will be filled in from the trades list in output_summary.
        return {
            "sharpe_ratio": round(sharpe_ratio, 3),
            "max_drawdown_pct": round(max_dd, 2),
            "win_rate_pct": 0.0,  # placeholder — filled externally
            "total_return_pct": round(total_return_pct, 2),
            "cagr_pct": round(cagr_pct, 2),
            "num_trades": 0,  # placeholder — filled externally
        }

    # ------------------------------------------------------------------
    #  Summary output
    # ------------------------------------------------------------------

    def output_summary(self, metrics: dict, trades: list) -> None:
        """
        Print a clean backtest summary table.

        Args:
            metrics: Dict from calculate_metrics().
            trades: List of trade dicts from run_backtest().
        """
        # Count wins/losses from trades
        buy_sell_trades = [t for t in trades if t["action"] in ("BUY", "SELL", "SELL (close)")]
        sells = [t for t in buy_sell_trades if "SELL" in t["action"]]
        wins = sum(1 for t in sells if t.get("pnl", 0) > 0)
        losses = sum(1 for t in sells if t.get("pnl", 0) <= 0)
        total_trades = wins + losses
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

        metrics["win_rate_pct"] = round(win_rate, 1)
        metrics["num_trades"] = total_trades

        print()
        print("  Summary")
        print(f"  {'─' * 56}")
        print(f"    Total Return:      {metrics['total_return_pct']:>8.2f}%")
        print(f"    CAGR:              {metrics['cagr_pct']:>8.2f}%")
        print(f"    Sharpe Ratio:      {metrics['sharpe_ratio']:>8.3f}")
        print(f"    Max Drawdown:      {metrics['max_drawdown_pct']:>8.2f}%")
        print(f"    Win Rate:          {win_rate:>7.1f}%  ({wins}W / {losses}L)")
        print(f"    Total Trades:      {total_trades:>8d}")
        print()
        if sells:
            total_pnl = sum(t.get("pnl", 0) for t in sells)
            avg_pnl = total_pnl / len(sells) if sells else 0
            print(f"    Total PnL:         ${total_pnl:>8,.2f}")
            print(f"    Avg PnL / Trade:   ${avg_pnl:>8,.2f}")
        print(f"  {'─' * 56}")
        print()

        # Print trade log
        if trades:
            print("  Trade Log")
            print(f"  {'─' * 56}")
            print(f"    {'Date':<14s} {'Action':<14s} {'Price':>8s}  {'PnL':>10s}  {'PortValue':>12s}")
            print(f"    {'─' * 56}")
            for t in trades:
                d = t.get("date", "")
                if hasattr(d, "strftime"):
                    d = d.strftime("%Y-%m-%d")
                act = t.get("action", "")
                pr = t.get("price", 0)
                pnl = t.get("pnl", 0)
                pv = t.get("portfolio_value", 0)
                pnl_s = f"${pnl:+,.2f}" if pnl != 0 else "     -"
                print(f"    {str(d):<14s} {act:<14s} {pr:>8.2f}  {pnl_s:>10s}  ${pv:>9,.2f}")
            print(f"  {'─' * 56}")
            print()

    # ------------------------------------------------------------------
    #  Charting (SVG inline — no external deps)
    # ------------------------------------------------------------------

    def plot_equity_curve(
        self,
        equity_curve: pd.DataFrame,
        title: str = "Equity Curve",
        output_path: str = "equity_curve.html",
    ) -> str:
        """
        Write an HTML file with an inline SVG equity curve chart.

        The chart is a clean polyline drawn on a simple grid.  No JavaScript
        or external charting libraries are used.

        Args:
            equity_curve: DataFrame with 'equity' column indexed by date.
            title: Chart title.
            output_path: Path for the output HTML file.

        Returns:
            The output path.
        """
        if equity_curve is None or equity_curve.empty:
            logger.warning("Empty equity curve — nothing to plot")
            return output_path

        equity = equity_curve["equity"].values
        n = len(equity)

        if n < 2:
            logger.warning("Equity curve has fewer than 2 points — nothing to plot")
            return output_path

        # Chart dimensions
        W, H = 900, 450
        PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 70, 30, 30, 50
        plot_w = W - PAD_LEFT - PAD_RIGHT
        plot_h = H - PAD_TOP - PAD_BOTTOM

        min_val = float(np.min(equity))
        max_val = float(np.max(equity))
        val_range = max_val - min_val
        if val_range == 0:
            val_range = max_val * 0.1 or 100
        # Add 5% padding
        min_val -= val_range * 0.05
        max_val += val_range * 0.05
        val_range = max_val - min_val

        # Build polyline points
        points = []
        for i, val in enumerate(equity):
            x = PAD_LEFT + (i / (n - 1)) * plot_w
            y = PAD_TOP + (1 - (val - min_val) / val_range) * plot_h
            points.append(f"{x:.1f},{y:.1f}")

        polyline = " ".join(points)

        # Y-axis grid lines (5 lines)
        y_lines = []
        for i in range(6):
            frac = i / 5
            y = PAD_TOP + (1 - frac) * plot_h
            val = min_val + frac * val_range
            y_lines.append((y, val))

        # X-axis labels (5 labels)
        x_labels = []
        for i in range(5):
            frac = i / 4
            x = PAD_LEFT + frac * plot_w
            idx = int(frac * (n - 1))
            if hasattr(equity_curve.index[idx], "strftime"):
                label = pd.Timestamp(equity_curve.index[idx]).strftime("%Y-%m-%d")
            else:
                label = str(equity_curve.index[idx])[:10]
            x_labels.append((x, label))

        # Starting equity value
        start_equity = equity[0]
        end_equity = equity[-1]
        direction = "up" if end_equity >= start_equity else "down"
        color = "#22c55e" if direction == "up" else "#ef4444"

        # Format helpers
        def fmt_val(v):
            if abs(v) >= 1_000_000:
                return f"${v/1_000_000:.2f}M"
            if abs(v) >= 1_000:
                return f"${v/1_000:.1f}K"
            return f"${v:.0f}"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #0f172a; color: #e2e8f0; display: flex; justify-content: center;
         align-items: center; min-height: 100vh; margin: 0; padding: 20px; }}
  .card {{ background: #1e293b; border-radius: 12px; padding: 24px;
          box-shadow: 0 4px 24px rgba(0,0,0,0.4); }}
  h1 {{ font-size: 18px; font-weight: 600; margin: 0 0 8px 0; color: #f1f5f9; }}
  .subtitle {{ font-size: 13px; color: #94a3b8; margin-bottom: 16px; }}
  svg {{ display: block; }}
  .stats {{ display: flex; gap: 24px; margin-top: 16px; flex-wrap: wrap; }}
  .stat {{ text-align: center; }}
  .stat-value {{ font-size: 20px; font-weight: 700; }}
  .stat-label {{ font-size: 11px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }}
</style>
</head>
<body>
<div class="card">
  <h1>{title}</h1>
  <div class="subtitle">{n} trading days</div>
  <svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg">
    <!-- Grid lines -->
    <g stroke="#334155" stroke-width="1">
"""
        for y, val in y_lines:
            html += f'      <line x1="{PAD_LEFT}" y1="{y:.1f}" x2="{W - PAD_RIGHT}" y2="{y:.1f}" />\n'

        html += '    </g>\n'

        # Y-axis labels
        html += '    <g fill="#94a3b8" font-size="12" font-family="monospace">\n'
        for y, val in y_lines:
            html += f'      <text x="{PAD_LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">{fmt_val(val)}</text>\n'
        html += '    </g>\n'

        # X-axis labels
        html += '    <g fill="#94a3b8" font-size="10" text-anchor="middle">\n'
        for x, label in x_labels:
            html += f'      <text x="{x:.1f}" y="{H - 8}">{label}</text>\n'
        html += '    </g>\n'

        # Zero/starting equity line
        html += f'    <line x1="{PAD_LEFT}" y1="{PAD_TOP + (1 - (start_equity - min_val) / val_range) * plot_h:.1f}" '
        html += f'x2="{W - PAD_RIGHT}" y2="{PAD_TOP + (1 - (start_equity - min_val) / val_range) * plot_h:.1f}" '
        html += 'stroke="#475569" stroke-width="1" stroke-dasharray="4,3" />\n'

        # Area under curve (gradient-like fill — a polygon)
        poly_fill = f"{PAD_LEFT},{PAD_TOP + plot_h} "
        poly_fill += " ".join(points)
        poly_fill += f" {W - PAD_RIGHT},{PAD_TOP + plot_h}"
        html += f'    <polygon points="{poly_fill}" fill="{color}" fill-opacity="0.08" />\n'

        # Equity curve polyline
        html += f'    <polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="2" '
        html += 'stroke-linejoin="round" stroke-linecap="round" />\n'

        # Start / end markers
        html += f'    <circle cx="{PAD_LEFT}" cy="{PAD_TOP + (1 - (start_equity - min_val) / val_range) * plot_h:.1f}" '
        html += f'r="4" fill="{color}" />\n'
        html += f'    <circle cx="{W - PAD_RIGHT}" cy="{PAD_TOP + (1 - (end_equity - min_val) / val_range) * plot_h:.1f}" '
        html += f'r="4" fill="{color}" />\n'

        html += """  </svg>
  <div class="stats">
    <div class="stat">
      <div class="stat-value" style="color: #e2e8f0;">""" + fmt_val(start_equity) + """</div>
      <div class="stat-label">Start</div>
    </div>
    <div class="stat">
      <div class="stat-value" style="color: """ + color + """;">""" + fmt_val(end_equity) + """</div>
      <div class="stat-label">End</div>
    </div>
    <div class="stat">
      <div class="stat-value" style="color: """ + color + """;">"""
        change_pct = ((end_equity - start_equity) / start_equity) * 100 if start_equity > 0 else 0
        sign = "+" if change_pct >= 0 else ""
        html += f'{sign}{change_pct:.2f}%'
        html += """</div>
      <div class="stat-label">Return</div>
    </div>
  </div>
</div>
</body>
</html>"""

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

        logger.info("Equity curve chart saved to %s", output_path)
        return output_path