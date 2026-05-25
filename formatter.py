"""
Telegram message formatter for Hermes Trading Bot.

Provides clean, emoji-rich formatting for trading signals, portfolio summaries,
and congressional trade information.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def format_signal(signal: dict, account_summary: dict = None) -> str:
    """
    Format a trading signal as a clean Telegram message.

    Args:
        signal: Signal dict from generate_signal().
        account_summary: Optional account summary dict for position sizing context.

    Returns:
        Formatted Telegram message string.
    """
    symbol = signal.get("symbol", "???")
    action = signal.get("action", "HOLD")
    confidence = signal.get("confidence", "LOW")
    price = signal.get("price", 0.0)
    rsi = signal.get("rsi", 50)
    macd_bullish = signal.get("macd_bullish", False)
    volume_ratio = signal.get("volume_ratio", 1.0)
    reason = signal.get("reason", "")
    stop_loss = signal.get("stop_loss", 0.0)
    target = signal.get("target", 0.0)

    # Action emoji and text
    if action == "BUY":
        action_emoji = "🟢 **BUY**"
    elif action == "SELL":
        action_emoji = "🔴 **SELL**"
    else:
        action_emoji = "⚪ **HOLD**"

    # Confidence stars
    if confidence == "HIGH":
        conf_stars = "⭐⭐⭐"
    elif confidence == "MEDIUM":
        conf_stars = "⭐⭐"
    else:
        conf_stars = "⭐"

    # MACD indicator
    macd_text = "Bullish cross ✅" if macd_bullish else "Bearish ❌"

    # Volume indicator
    if volume_ratio >= 2.0:
        vol_text = f"{volume_ratio}x avg — 📈 Spike"
    elif volume_ratio >= 1.5:
        vol_text = f"{volume_ratio}x avg — Elevated"
    else:
        vol_text = f"{volume_ratio}x avg — Normal"

    # RSI text
    if rsi < 35:
        rsi_text = f"{rsi} — Oversold 📉"
    elif rsi > 70:
        rsi_text = f"{rsi} — Overbought 📈"
    elif rsi < 45:
        rsi_text = f"{rsi} — Below neutral"
    elif rsi > 55:
        rsi_text = f"{rsi} — Above neutral"
    else:
        rsi_text = f"{rsi} — Neutral"

    # Price targets
    target_pct = ((target - price) / price * 100) if price > 0 else 0
    stop_pct = ((stop_loss - price) / price * 100) if price > 0 else 0

    target_line = f"🎯 Target: ${target:.2f} ({target_pct:+.1f}%)" if target > 0 else ""
    stop_line = f"🛑 Stop: ${stop_loss:.2f} ({stop_pct:+.1f}%)" if stop_loss > 0 else ""

    # Position size
    position_line = ""
    if account_summary and price > 0:
        from config import CFG
        pos_value = account_summary.get("portfolio_value", 100000) * CFG["MAX_POSITION_SIZE"]
        qty = max(1, int(pos_value / price))
        position_line = f"📏 Position: ~{qty} shares ({CFG['MAX_POSITION_SIZE']*100}% of account)"

    # Build message
    lines = [
        f"📈 **SIGNAL — ${symbol}**",
        "",
        f"{action_emoji} @ ${price:.2f}",
        f"Confidence: **{confidence}** {conf_stars}",
        "",
        "**📊 Indicators:**",
        f"• RSI(14): {rsi_text}",
        f"• MACD: {macd_text}",
        f"• Volume: {vol_text}",
        "",
    ]

    if target_line:
        lines.append(target_line)
    if stop_line:
        lines.append(stop_line)
    if position_line:
        lines.append(position_line)

    lines.append("")
    lines.append(f"_{reason}_")
    lines.append("")
    lines.append(f"⏰ {datetime.now().strftime('%H:%M:%S UTC')}")
    lines.append(f"⚡ `/approve_{symbol.lower()}` to execute")

    return "\n".join(lines)


def format_portfolio(positions: list, summary: dict) -> str:
    """
    Format portfolio overview as a Telegram message.

    Args:
        positions: List of position dicts from get_positions().
        summary: Account summary dict from get_account_summary().

    Returns:
        Formatted Telegram message string.
    """
    lines = [
        "🏦 **Portfolio Overview**",
        "",
        f"💰 Cash: **${summary.get('cash', 0):,.2f}**",
        f"📊 Portfolio Value: **${summary.get('portfolio_value', 0):,.2f}**",
        f"💪 Buying Power: **${summary.get('buying_power', 0):,.2f}**",
        "",
    ]

    pnl = summary.get("pnl", 0)
    pnl_pct = summary.get("day_change_pct", 0)
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    lines.append(f"{pnl_emoji} Daily P&L: **${pnl:+,.2f}** ({pnl_pct:+.2f}%)")
    lines.append("")

    if positions:
        lines.append(f"**Open Positions ({len(positions)}):**")
        lines.append("")
        total_mv = sum(p.get("market_value", 0) for p in positions)
        for pos in positions:
            symbol = pos.get("symbol", "?")
            qty = pos.get("qty", 0)
            mv = pos.get("market_value", 0)
            pl = pos.get("unrealized_pl", 0)
            pl_pct = pos.get("unrealized_pl_pct", 0)
            alloc = (mv / total_mv * 100) if total_mv > 0 else 0
            pl_emoji = "🟢" if pl >= 0 else "🔴"

            lines.append(
                f"{pl_emoji} **{symbol}** — {qty:.0f} shares @ ${mv/qty:.2f} avg"
                if qty > 0 else
                f"{pl_emoji} **{symbol}**"
            )
            lines.append(f"   P&L: ${pl:+,.2f} ({pl_pct:+.2f}%) | {alloc:.1f}% of portfolio")
            lines.append("")
    else:
        lines.append("📭 **No open positions**")
        lines.append("")

    lines.append(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")

    return "\n".join(lines)


def format_congress(politician_name: str, trades: list, recommendations: list) -> str:
    """
    Format congressional trading information as a Telegram message.

    Args:
        politician_name: Display name of the politician (e.g., "Nancy Pelosi").
        trades: List of recent trade dicts.
        recommendations: List of trade recommendation dicts.

    Returns:
        Formatted Telegram message string.
    """
    lines = [
        f"🏛️ **Congress Tracker — {politician_name}**",
        "",
    ]

    # Recent trades section
    if trades:
        lines.append(f"**Recent Filings:**")
        lines.append("")
        for trade in trades[:10]:
            from strategies.congress import _extract_ticker, _extract_transaction_type, _extract_amount
            ticker = _extract_ticker(trade) or "???"
            tx_type = _extract_transaction_type(trade)
            amount = _extract_amount(trade) or 0

            if tx_type == "buy" or tx_type == "purchase":
                emoji = "🟢"
            elif tx_type == "sell" or tx_type == "sale":
                emoji = "🔴"
            else:
                emoji = "⚪"

            lines.append(f"{emoji} **{ticker}** — {tx_type.upper()} ~${amount:,.0f}")
        lines.append("")
    else:
        lines.append("⚠️ Could not fetch recent trades. API may be unavailable.")
        lines.append("")

    # Recommendations section
    if recommendations:
        lines.append("**💡 Copy-Trade Recommendations:**")
        lines.append("")
        for rec in recommendations:
            action = rec.get("action", "HOLD")
            symbol = rec.get("symbol", "???")
            reason = rec.get("reason", "")
            alloc = rec.get("allocation_pct", 0)

            emoji = "🟢" if action == "BUY" else ("🔴" if action == "SELL" else "⚪")
            lines.append(f"{emoji} **{action} {symbol}**")
            if alloc:
                lines.append(f"   Target: {alloc:.1f}% of portfolio")
            if reason:
                lines.append(f"   _{reason}_")
            lines.append("")
    else:
        lines.append("No copy-trade recommendations available.")
        lines.append("")

    lines.append(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("⚡ `/congress --execute` to mirror portfolio")

    return "\n".join(lines)


def format_sender_report(signal_data: dict) -> str:
    """
    Format Adam Sender's 13F portfolio report as a Telegram message.

    Args:
        signal_data: Output from strategies.sender.generate_sender_signals().

    Returns:
        Formatted Telegram message string.
    """
    from strategies.sender import format_sender_report as _raw_format
    return _raw_format(signal_data)