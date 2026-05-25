#!/usr/bin/env python3
"""
Hermes Trading Bot — CLI Entry Point.

Usage:
    python main.py scan                  Scan market, print signals to console
    python main.py scan --telegram       Scan market, send signals via Telegram
    python main.py trade [symbol]        Generate + execute a signal for one symbol
    python main.py portfolio             Show current account/position summary
    python main.py backtest [symbol] --days 365   Backtest momentum strategy
    python main.py congress              Show Pelosi's current trades and recommendations
    python main.py congress --execute    Mirror Pelosi's portfolio
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta

from config import CFG, validate_config
from trader import HermesTrader
from strategies.momentum import MomentumStrategy
from strategies.congress import (
    CongressTracker,
    fetch_congress_trades,
    build_pelosi_portfolio,
    generate_copy_trade_signals,
    PELOSI,
)
from formatter import format_signal, format_portfolio, format_congress

# ---------------------------------------------------------------------------
# Colored output helpers (simple ANSI — no external deps)
# ---------------------------------------------------------------------------

def _green(text: str) -> str:
    return f"\033[92m{text}\033[0m"

def _red(text: str) -> str:
    return f"\033[91m{text}\033[0m"

def _yellow(text: str) -> str:
    return f"\033[93m{text}\033[0m"

def _cyan(text: str) -> str:
    return f"\033[96m{text}\033[0m"

def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m"

def _magenta(text: str) -> str:
    return f"\033[95m{text}\033[0m"

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def _check_keys() -> bool:
    """Return True if Alpaca keys are configured, otherwise print help and return False."""
    if not CFG["ALPACA_API_KEY"] or not CFG["ALPACA_SECRET_KEY"]:
        print()
        print(_red("╔══════════════════════════════════════════════════════════════╗"))
        print(_red("║  Alpaca API keys not found.                                ║"))
        print(_red("║                                                             ║"))
        print(_yellow("║  Copy .env.example → .env and add your credentials:       ║"))
        print(_yellow("║                                                             ║"))
        print(_cyan("║  cp .env.example .env                                       ║"))
        print(_cyan("║  # then edit .env with your ALPACA_API_KEY & ALPACA_SECRET_KEY ║"))
        print(_red("╚══════════════════════════════════════════════════════════════╝"))
        print()
        return False
    return True


# ---------------------------------------------------------------------------
# Telegram sender (optional)
# ---------------------------------------------------------------------------

def _send_telegram(message: str) -> bool:
    """Send a message via Telegram bot token, if configured."""
    token = CFG.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = CFG.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print(_yellow("  ⚠ Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"))
        return False
    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
        }, timeout=10)
        if resp.status_code == 200:
            print(_green("  ✓ Message sent via Telegram"))
            return True
        else:
            print(_red(f"  ✗ Telegram error: {resp.status_code} — {resp.text[:200]}"))
            return False
    except Exception as e:
        print(_red(f"  ✗ Telegram send failed: {e}"))
        return False


def _send_telegram_file(file_path: str) -> bool:
    """Send a document (e.g. backtest report) via Telegram."""
    token = CFG.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = CFG.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/sendDocument"
        with open(file_path, "rb") as f:
            resp = requests.post(url, data={"chat_id": chat_id}, files={"document": f}, timeout=30)
        return resp.status_code == 200
    except Exception as e:
        logger.warning("Telegram file send failed: %s", e)
        return False


# ===================================================================
#  COMMAND HANDLERS
# ===================================================================

def cmd_scan(args: argparse.Namespace) -> None:
    """Scan the watch list and show / send signals."""
    if not _check_keys():
        sys.exit(1)

    trader = HermesTrader()
    print(_bold(f"\n{'═' * 58}"))
    print(_bold(_cyan("  MARKET SCAN")))
    print(_bold(f"{'═' * 58}"))

    signals = trader.scan_market()
    account = trader.get_account_summary()

    # Filter to actionable signals (BUY / SELL)
    actionable = [s for s in signals if s["action"] in ("BUY", "SELL")]

    if not actionable:
        print(_yellow("\n  No actionable signals found. Markets may be closed or indicators flat.\n"))
        return

    for sig in actionable:
        print()
        msg = format_signal(sig, account)
        # Print with minimal ANSI wrapper
        action = sig.get("action", "HOLD")
        if action == "BUY":
            prefix = _green("  🟢 BUY  ")
        elif action == "SELL":
            prefix = _red("  🔴 SELL ")
        else:
            prefix = _yellow("  ⚪ HOLD ")
        print(f"{prefix}{_bold(sig.get('symbol', '???'))}  @ ${sig.get('price', 0):.2f}")
        print(f"       Confidence: {_cyan(sig.get('confidence', 'LOW'))}  "
              f"RSI: {sig.get('rsi', 50):.1f}  "
              f"Score: {sig.get('score', 0):+.3f}")
        if sig.get("reason"):
            print(f"       {sig['reason']}")
        print()

    print(_bold(f"{'═' * 58}"))
    print(f"  Scanned {len(signals)} symbols — {len(actionable)} actionable signals")
    print(_bold(f"{'═' * 58}\n"))

    # Telegram delivery
    if args.telegram:
        for sig in actionable:
            msg = format_signal(sig, account)
            _send_telegram(msg)


def cmd_trade(args: argparse.Namespace) -> None:
    """Generate + execute a signal for a single symbol."""
    if not _check_keys():
        sys.exit(1)

    trader = HermesTrader()
    symbol = args.symbol.upper()

    print(_bold(f"\n{'═' * 58}"))
    print(_bold(_cyan(f"  TRADE — ${symbol}")))
    print(_bold(f"{'═' * 58}"))

    print(f"\n  Generating signal for {symbol}...")
    signal = trader.generate_signal(symbol)

    action = signal.get("action", "HOLD")
    if action == "BUY":
        action_str = _green("BUY")
    elif action == "SELL":
        action_str = _red("SELL")
    else:
        action_str = _yellow("HOLD")

    print(f"\n  Signal: {action_str} @ ${signal.get('price', 0):.2f}")
    print(f"  Confidence: {_cyan(signal.get('confidence', 'LOW'))}")
    print(f"  RSI: {signal.get('rsi', 50):.1f}  |  Score: {signal.get('score', 0):+.3f}")
    if signal.get("reason"):
        print(f"  Reason: {signal['reason']}")

    if signal["action"] == "HOLD":
        print(_yellow("\n  No trade executed — signal is HOLD.\n"))
        return

    # Execute
    print(f"\n  Executing {action_str} order...")
    result = trader.execute_trade(signal)

    if result.get("success"):
        print(_green(f"\n  ✓ Order submitted!"))
        print(f"    ID:     {result.get('order_id', 'N/A')}")
        print(f"    Status: {result.get('status', 'N/A')}")
    else:
        print(_red(f"\n  ✗ Trade failed: {result.get('message', 'Unknown error')}"))

    print()


def cmd_portfolio(args: argparse.Namespace) -> None:
    """Show current account and position summary."""
    if not _check_keys():
        sys.exit(1)

    trader = HermesTrader()
    summary = trader.get_account_summary()
    positions = trader.get_positions()

    print(_bold(f"\n{'═' * 58}"))
    print(_bold(_cyan("  PORTFOLIO OVERVIEW")))
    print(_bold(f"{'═' * 58}"))

    status = summary.get("status", "?")
    if status == "error":
        print(_red("\n  Failed to fetch portfolio data. Check your API keys and connection.\n"))
        return

    print(f"  Status:        {_green('Active') if status == 'ACTIVE' else _yellow(status)}")
    print(f"  Cash:          ${summary.get('cash', 0):>10,.2f}")
    print(f"  Portfolio Val: ${summary.get('portfolio_value', 0):>10,.2f}")
    print(f"  Buying Power:  ${summary.get('buying_power', 0):>10,.2f}")

    pnl = summary.get("pnl", 0)
    pnl_pct = summary.get("day_change_pct", 0)
    pnl_str = _green(f"+${pnl:+,.2f}") if pnl >= 0 else _red(f"-${abs(pnl):,.2f}")
    pnl_pct_str = _green(f"+{pnl_pct:+.2f}%") if pnl_pct >= 0 else _red(f"{pnl_pct:+.2f}%")
    print(f"  Daily P&L:     {pnl_str} ({pnl_pct_str})")

    print()
    if positions:
        print(_bold(f"  Open Positions ({len(positions)}):"))
        print(f"  {'─' * 56}")
        total_mv = sum(p.get("market_value", 0) for p in positions)
        for pos in positions:
            sym = pos.get("symbol", "?")
            qty = pos.get("qty", 0)
            mv = pos.get("market_value", 0)
            pl = pos.get("unrealized_pl", 0)
            pl_pct = pos.get("unrealized_pl_pct", 0)
            alloc = (mv / total_mv * 100) if total_mv > 0 else 0
            pl_disp = _green(f"+${pl:+,.2f}") if pl >= 0 else _red(f"-${abs(pl):,.2f}")
            print(f"    {_bold(sym):6s}  {qty:>5.0f} sh  ${mv:>8,.2f}  "
                  f"{pl_disp} ({pl_pct:+.2f}%)  {alloc:>4.1f}%")
    else:
        print(_yellow("  No open positions\n"))

    print(_bold(f"{'═' * 56}\n"))


def cmd_backtest(args: argparse.Namespace) -> None:
    """Run a backtest of the momentum strategy on historical data."""
    if not _check_keys():
        sys.exit(1)

    from backtest import Backtester

    trader = HermesTrader()
    symbol = args.symbol.upper()
    days = args.days
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    print(_bold(f"\n{'═' * 58}"))
    print(_bold(_cyan(f"  BACKTEST — ${symbol}")))
    print(_bold(f"  {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}"))
    print(_bold(f"{'═' * 58}"))

    def momentum_strategy(df):
        """Strategy function compatible with Backtester signature."""
        ms = MomentumStrategy()
        if df is None or len(df) < 50:
            return []
        # We generate signals for every row where we have sufficient data
        signals = []
        # Use the last row as the current signal
        signal = ms.assemble_signal(symbol, df)
        if signal and signal.get("action") in ("BUY", "SELL"):
            signals.append({
                "date": df.index[-1],
                "symbol": symbol,
                "action": signal["action"],
                "price": float(df["close"].iloc[-1]),
                "confidence": signal.get("confidence", "LOW"),
                "score": signal.get("score", 0),
            })
        return signals

    bt = Backtester(trader)
    equity_curve, trades = bt.run_backtest(symbol, momentum_strategy, start_date, end_date)

    if trades is None or len(equity_curve) == 0:
        print(_red("\n  Backtest returned no data. Check the symbol or date range.\n"))
        return

    metrics = bt.calculate_metrics(equity_curve)

    print()
    print(_bold(f"  {'─' * 56}"))
    print(_bold("  RESULTS"))
    print(_bold(f"  {'─' * 56}"))

    tr = metrics["total_return_pct"]
    cagr = metrics["cagr_pct"]
    sharpe = metrics["sharpe_ratio"]
    dd = metrics["max_drawdown_pct"]
    wr = metrics["win_rate_pct"]
    nt = metrics["num_trades"]

    tr_str = _green(f"+{tr:.2f}%") if tr >= 0 else _red(f"{tr:.2f}%")
    cagr_str = _green(f"+{cagr:.2f}%") if cagr >= 0 else _red(f"{cagr:.2f}%")
    sharpe_str = _green(f"{sharpe:.2f}") if sharpe >= 1 else _yellow(f"{sharpe:.2f}")
    dd_str = _red(f"{dd:.2f}%")
    wr_str = _green(f"{wr:.1f}%") if wr >= 50 else _red(f"{wr:.1f}%")

    print(f"  Total Return:      {tr_str}")
    print(f"  CAGR:              {cagr_str}")
    print(f"  Sharpe Ratio:      {sharpe_str}")
    print(f"  Max Drawdown:      {dd_str}")
    print(f"  Win Rate:          {wr_str}")
    print(f"  Total Trades:      {nt}")

    print()
    bt.output_summary(metrics, trades)

    # Plot
    plot_title = f"{symbol} Momentum Strategy Backtest ({days}d)"
    html_path = f"/Users/ca/workspace/hermes-trader/data/backtest_{symbol.lower()}_{days}d.html"
    bt.plot_equity_curve(equity_curve, plot_title, html_path)
    print(f"  Chart saved: {_cyan(html_path)}")
    print()


def cmd_congress(args: argparse.Namespace) -> None:
    """Show Pelosi's trades and copy recommendations, or execute them."""
    if not _check_keys():
        sys.exit(1)

    trader = HermesTrader()

    if args.execute:
        _cmd_congress_execute(trader)
    else:
        _cmd_congress_show(trader)


def _cmd_congress_show(trader) -> None:
    """Display Pelosi's current trades and recommendations."""
    print(_bold(f"\n{'═' * 58}"))
    print(_bold(_magenta("  🏛️  CONGRESS TRACKER — NANCY PELOSI")))
    print(_bold(f"{'═' * 58}"))

    print("\n  Fetching recent congressional trade filings...")
    trades = fetch_congress_trades(PELOSI, limit=20)

    if not trades:
        print(_yellow("\n  ⚠ Could not fetch recent trades. Capitol Trades API may be unreachable.\n"))
        trades = []

    print("\n  Building copy-trade recommendations...")
    recommendations = generate_copy_trade_signals()

    if recommendations:
        print(_green(f"  Found {len(recommendations)} copy-trade recommendations\n"))
        print(_bold(f"  {'─' * 56}"))
        print(_bold("  COPY-TRADE RECOMMENDATIONS"))
        print(_bold(f"  {'─' * 56}"))
        for rec in recommendations:
            sym = rec.get("symbol", "???")
            alloc = rec.get("allocation_pct", 0)
            reason = rec.get("reason", "")
            print(f"    {_green('🟢 BUY')}  {_bold(sym):6s}  target {alloc:.1f}%")
            if reason:
                print(f"         {reason}")
        print()
    else:
        print(_yellow("\n  No copy-trade recommendations available.\n"))

    # Show raw trades
    if trades:
        print(_bold(f"  Recent Filings ({len(trades)}):"))
        print(f"  {'─' * 56}")
        for t in trades[:10]:
            ticker = _extract_ticker_friendly(t)
            tx_type = _extract_type_friendly(t)
            amount = _extract_amount_friendly(t)
            if tx_type in ("buy", "purchase"):
                emoji = _green("🟢")
            else:
                emoji = _red("🔴")
            print(f"    {emoji} {_bold(ticker) if ticker else '???'}  {tx_type.upper():>6s}  ~${amount:>8,.0f}")
        print()

    print(_cyan("  To execute: python main.py congress --execute"))
    print()


def _cmd_congress_execute(trader) -> None:
    """Mirror Pelosi's portfolio — buy recommended positions."""
    print(_bold(f"\n{'═' * 58}"))
    print(_bold(_magenta("  🏛️  CONGRESS EXECUTE — MIRROR PELOSI")))
    print(_bold(f"{'═' * 58}"))

    recommendations = generate_copy_trade_signals()
    if not recommendations:
        print(_yellow("\n  No recommendations to execute.\n"))
        return

    print(_green(f"\n  Executing {len(recommendations)} copy-trade signal(s)...\n"))

    results = []
    for rec in recommendations:
        sym = rec.get("symbol", "???")
        alloc = rec.get("allocation_pct", 0)
        print(f"  Processing {_bold(sym)} (target {alloc:.1f}%)...")

        # Get current price
        signal = trader.generate_signal(sym)
        if signal.get("price", 0) <= 0:
            print(_yellow(f"    ⚠ Skipping {sym} — could not get price\n"))
            continue

        rec["price"] = signal["price"]
        rec["stop_loss"] = signal.get("stop_loss", 0)
        rec["target"] = signal.get("target", 0)

        result = trader.execute_trade(rec)
        results.append(result)

        if result.get("success"):
            print(_green(f"    ✓ Order submitted: {result.get('order_id', 'N/A')}"))
        else:
            msg = result.get("message", "Unknown error")
            print(_yellow(f"    ⚠ {msg}"))
        print()

    successes = sum(1 for r in results if r.get("success"))
    failures = len(results) - successes
    print(_bold(f"{'═' * 58}"))
    if successes:
        print(_green(f"  Orders submitted: {successes}"))
    if failures:
        print(_yellow(f"  Skipped / failed: {failures}"))
    print(_bold(f"{'═' * 58}\n"))


# ---------------------------------------------------------------------------
# Friendly extractors for display (don't require congress imports)
# ---------------------------------------------------------------------------

def _extract_ticker_friendly(trade: dict) -> str:
    for key in ("ticker", "symbol", "issuer", "issuerTicker", "assetTicker"):
        val = trade.get(key)
        if val and isinstance(val, str) and len(val) <= 5:
            return val.upper()
    asset = trade.get("asset", {}) or {}
    for key in ("ticker", "symbol"):
        val = asset.get(key)
        if val and isinstance(val, str):
            return val.upper()
    return "???"


def _extract_type_friendly(trade: dict) -> str:
    tx = trade.get("transactionType", trade.get("type", ""))
    if isinstance(tx, str):
        t = tx.lower()
        if "sell" in t or "sale" in t:
            return "sell"
        if "buy" in t or "purchase" in t:
            return "buy"
    return "unknown"


def _extract_amount_friendly(trade: dict) -> float:
    for key in ("amount", "value", "transactionValue", "estimatedValue"):
        val = trade.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
    return 100000


# ===================================================================
#  ENTRY POINT
# ===================================================================

def main():
    parser = argparse.ArgumentParser(
        prog="hermes-trader",
        description="Hermes Trading Bot — automated trading with Alpaca",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py scan                    Scan watch list for signals
  python main.py scan --telegram         Scan + send to Telegram
  python main.py trade NVDA              Analyze + trade NVDA
  python main.py portfolio               Show portfolio summary
  python main.py backtest AAPL --days 365 Backtest AAPL for 1 year
  python main.py congress                Show Pelosi trades
  python main.py congress --execute      Mirror Pelosi portfolio
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # scan
    scan_parser = subparsers.add_parser("scan", help="Scan the watch list for trading signals")
    scan_parser.add_argument("--telegram", action="store_true", help="Send signals via Telegram")

    # trade
    trade_parser = subparsers.add_parser("trade", help="Generate + execute a signal for one symbol")
    trade_parser.add_argument("symbol", type=str, help="Ticker symbol (e.g. AAPL)")

    # portfolio
    subparsers.add_parser("portfolio", help="Show account and position summary")

    # backtest
    backtest_parser = subparsers.add_parser("backtest", help="Backtest strategy on historical data")
    backtest_parser.add_argument("symbol", type=str, help="Ticker symbol (e.g. AAPL)")
    backtest_parser.add_argument("--days", type=int, default=365, help="Days of historical data (default: 365)")

    # congress
    congress_parser = subparsers.add_parser("congress", help="Track congressional trades (Pelosi)")
    congress_parser.add_argument("--execute", action="store_true", help="Execute copy trades to mirror Pelosi's portfolio")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    # Dispatch
    dispatch = {
        "scan": cmd_scan,
        "trade": cmd_trade,
        "portfolio": cmd_portfolio,
        "backtest": cmd_backtest,
        "congress": cmd_congress,
    }

    try:
        dispatch[args.command](args)
    except KeyboardInterrupt:
        print(_yellow("\n\n  Interrupted by user.\n"))
        sys.exit(1)
    except Exception as e:
        print(_red(f"\n  ✗ Error: {e}\n"))
        logger.exception("Unhandled exception in %s command", args.command)
        sys.exit(1)


if __name__ == "__main__":
    main()