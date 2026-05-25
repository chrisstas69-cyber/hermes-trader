"""
Strategic analysis module for Hermes Trading Bot.

Merges all trading signal sources (momentum, congressional copy-trades,
and Adam Sender 13F filings) into a consolidated daily briefing with
ranked recommendations.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def consolidated_briefing() -> dict:
    """
    Run ALL signal sources and merge them into a single consolidated briefing.

    Sources:
        1. Momentum scan (HermesTrader.scan_market)
        2. Congress copy signals (strategies.congress.generate_copy_trade_signals)
        3. Sender 13F signals (strategies.sender)

    Returns:
        Dict with date, sources, consensus_buys, consensus_sells,
        momentum_signals, congress_picks, sender_picks, and recommendations.
    """
    from trader import HermesTrader
    from strategies.congress import generate_copy_trade_signals, fetch_congress_trades, PELOSI
    from strategies.congress import get_politician_portfolio
    from strategies.sender import (
        fetch_latest_holdings,
        fetch_previous_holdings,
        get_portfolio_summary,
        detect_buys,
        detect_sells,
        generate_sender_signals,
    )

    date_str = datetime.now().strftime("%Y-%m-%d")
    sources_used = []
    errors = []

    # ------------------------------------------------------------------ #
    #  1. MOMENTUM SCAN
    # ------------------------------------------------------------------ #
    momentum_signals = []
    try:
        trader = HermesTrader()
        momentum_signals = trader.scan_market()
        sources_used.append("momentum")
        logger.info("Momentum scan: %d signals", len(momentum_signals))
    except Exception as e:
        errors.append(f"Momentum scan failed: {e}")
        logger.warning("Momentum scan failed: %s", e)

    # ------------------------------------------------------------------ #
    #  2. CONGRESS COPY SIGNALS  (Pelosi + Crenshaw)
    # ------------------------------------------------------------------ #
    congress_picks = []
    congress_trades_raw = []
    try:
        congress_signals = generate_copy_trade_signals()
        if congress_signals:
            congress_picks = [
                {
                    "symbol": s["symbol"],
                    "action": s.get("action", "BUY"),
                    "allocation_pct": s.get("allocation_pct", 0),
                    "reason": s.get("reason", ""),
                    "source": s.get("source", "congress_pelosi"),
                }
                for s in congress_signals
            ]
        sources_used.append("congress")

        # Also fetch raw trades for display
        pelosi_trades = fetch_congress_trades(PELOSI, limit=10)
        if pelosi_trades:
            congress_trades_raw = pelosi_trades

        logger.info("Congress picks: %d signals", len(congress_picks))
    except Exception as e:
        errors.append(f"Congress data failed: {e}")
        logger.warning("Congress data failed: %s", e)

    # ------------------------------------------------------------------ #
    #  3. SENDER 13F SIGNALS
    # ------------------------------------------------------------------ #
    sender_picks = {}
    try:
        sender_raw = generate_sender_signals()

        current_holdings = fetch_latest_holdings()
        previous_holdings = fetch_previous_holdings()
        summary = get_portfolio_summary()
        buys = detect_buys(current_holdings, previous_holdings)
        sells = detect_sells(current_holdings, previous_holdings)

        top_holdings_display = []
        top_h = summary.get("top_holdings", [])
        for h in top_h:
            top_holdings_display.append({
                "symbol": h.get("ticker", ""),
                "value": h.get("value", 0),
                "percentage": h.get("percentage", 0),
            })

        buys_display = []
        for b in buys[:5]:
            buys_display.append({
                "symbol": b.get("ticker", ""),
                "value_change": b.get("value_change", 0),
                "pct_change": b.get("pct_change"),
                "type": b.get("type", ""),
            })

        sells_display = []
        for s in sells[:5]:
            sells_display.append({
                "symbol": s.get("ticker", ""),
                "value_change": s.get("value_change", 0),
                "pct_change": s.get("pct_change"),
                "type": s.get("type", ""),
            })

        sender_picks = {
            "quarter": sender_raw.get("quarter", "Q1 2026"),
            "summary": {
                "total_value": summary.get("total_value", 0),
                "num_positions": summary.get("num_positions", 0),
                "top_10_concentration": summary.get("top_10_concentration", 0),
            },
            "top_holdings": top_holdings_display,
            "buys": buys_display,
            "sells": sells_display,
        }
        sources_used.append("sender")
        logger.info("Sender picks: %d buys, %d sells", len(buys), len(sells))
    except Exception as e:
        errors.append(f"Sender data failed: {e}")
        logger.warning("Sender data failed: %s", e)

    # ------------------------------------------------------------------ #
    #  BUILD CONSENSUS SIGNALS
    # ------------------------------------------------------------------ #
    # Index: momentum BUY/SELL symbols
    momentum_buys = {s["symbol"] for s in momentum_signals if s.get("action") == "BUY"}
    momentum_sells = {s["symbol"] for s in momentum_signals if s.get("action") == "SELL"}

    # Congress: all are BUY signals
    congress_buys = {p["symbol"] for p in congress_picks}

    # Sender individual buys/sells
    sender_buy_symbols = {b["symbol"] for b in sender_picks.get("buys", []) if b.get("symbol")}
    sender_sell_symbols = {s["symbol"] for s in sender_picks.get("sells", []) if s.get("symbol")}

    # Build consensus
    consensus_buys = []
    all_buy_candidates = momentum_buys | congress_buys | sender_buy_symbols

    for sym in sorted(all_buy_candidates):
        sym_sources = []
        reasons = []
        if sym in momentum_buys:
            sym_sources.append("momentum")
            # Find the signal for the reason
            ms = next((s for s in momentum_signals if s["symbol"] == sym), None)
            if ms and ms.get("reason"):
                reasons.append(ms["reason"])
        if sym in congress_buys:
            sym_sources.append("congress")
            cp = next((p for p in congress_picks if p["symbol"] == sym), None)
            if cp and cp.get("reason"):
                reasons.append(cp["reason"])
            else:
                reasons.append("Pelosi buying")
        if sym in sender_buy_symbols:
            sym_sources.append("sender")
            sb = next((b for b in sender_picks.get("buys", []) if b["symbol"] == sym), None)
            if sb:
                val = sb.get("value_change", 0)
                if val:
                    reasons.append(f"Sender added ${val:,.0f}")

        if len(sym_sources) >= 1:  # Include all as potential recommendations
            # Determine confidence
            num_sources = len(sym_sources)
            if num_sources >= 2:
                avg_conf = "HIGH"
            elif num_sources == 1:
                # Check individual source confidence
                ms = next((s for s in momentum_signals if s["symbol"] == sym), None)
                if ms and ms.get("confidence") == "HIGH":
                    avg_conf = "HIGH"
                elif ms and ms.get("confidence") == "MEDIUM":
                    avg_conf = "MEDIUM"
                else:
                    avg_conf = "MEDIUM"
            else:
                avg_conf = "LOW"

            entry = {
                "symbol": sym,
                "sources": sym_sources,
                "avg_confidence": avg_conf,
                "reasons": reasons,
            }
            consensus_buys.append(entry)

    consensus_sells = []
    all_sell_candidates = momentum_sells | sender_sell_symbols

    for sym in sorted(all_sell_candidates):
        sym_sources = []
        reasons = []
        if sym in momentum_sells:
            sym_sources.append("momentum")
            ms = next((s for s in momentum_signals if s["symbol"] == sym), None)
            if ms and ms.get("reason"):
                reasons.append(ms["reason"])
        if sym in sender_sell_symbols:
            sym_sources.append("sender")
            ss = next((s for s in sender_picks.get("sells", []) if s["symbol"] == sym), None)
            if ss:
                pct = ss.get("pct_change")
                if pct is not None:
                    reasons.append(f"Sender reduced {pct:.0f}%")
        if sym in congress_buys:  # Congress buying while others selling = conflict
            sym_sources.append("congress")
            reasons.append("Congress buying (conflict)")

        if len(sym_sources) >= 1:
            num_sources = len([s for s in sym_sources if s != "congress"])  # Don't count congress as bearish
            if num_sources >= 2:
                avg_conf = "HIGH"
            else:
                avg_conf = "MEDIUM"

            consensus_sells.append({
                "symbol": sym,
                "sources": sym_sources,
                "avg_confidence": avg_conf,
                "reasons": reasons,
            })

    # ------------------------------------------------------------------ #
    #  BUILD RECOMMENDATIONS (ranked)
    # ------------------------------------------------------------------ #
    recommendations = []
    priority = 1

    # Consensus buys first (appearing in 2+ sources = highest priority)
    for cb in sorted(consensus_buys, key=lambda x: len(x["sources"]), reverse=True):
        if len(cb["sources"]) >= 2:
            reason_parts = cb["reasons"][:3]
            reason_str = "; ".join(reason_parts)
            # Find price
            price = 0
            ms = next((s for s in momentum_signals if s["symbol"] == cb["symbol"]), None)
            if ms:
                price = ms.get("price", 0)

            target_price = round(price * 1.09, 2) if price else 0  # Rough 9% target
            stop_price = round(price * 0.95, 2) if price else 0

            recommendations.append({
                "action": "BUY",
                "symbol": cb["symbol"],
                "priority": priority,
                "price": price,
                "target": target_price,
                "stop_loss": stop_price,
                "reason": f"Top consensus pick — {reason_str}",
                "sources": cb["sources"],
                "confidence": cb["avg_confidence"],
            })
            priority += 1

    # Single-source buys
    for cb in sorted(consensus_buys, key=lambda x: len(x["sources"]), reverse=True):
        if len(cb["sources"]) < 2:
            price = 0
            ms = next((s for s in momentum_signals if s["symbol"] == cb["symbol"]), None)
            if ms:
                price = ms.get("price", 0)

            reason_parts = cb["reasons"][:2]
            reason_str = "; ".join(reason_parts)
            target_price = round(price * 1.086, 2) if price else 0
            stop_price = round(price * 0.95, 2) if price else 0

            recommendations.append({
                "action": "BUY",
                "symbol": cb["symbol"],
                "priority": priority,
                "price": price,
                "target": target_price,
                "stop_loss": stop_price,
                "reason": reason_str,
                "sources": cb["sources"],
                "confidence": cb["avg_confidence"],
            })
            priority += 1

    # Sell recommendations (lower priority)
    for cs in sorted(consensus_sells, key=lambda x: len(x["sources"]), reverse=True):
        reason_parts = cs["reasons"][:2]
        reason_str = "; ".join(reason_parts)
        price = 0
        ms = next((s for s in momentum_signals if s["symbol"] == cs["symbol"]), None)
        if ms:
            price = ms.get("price", 0)

        recommendations.append({
            "action": "SELL",
            "symbol": cs["symbol"],
            "priority": priority,
            "price": price,
            "reason": reason_str,
            "sources": cs["sources"],
            "confidence": cs["avg_confidence"],
        })
        priority += 1

    # ------------------------------------------------------------------ #
    #  BUILD FINAL BRIEFING
    # ------------------------------------------------------------------ #
    briefing = {
        "date": date_str,
        "sources": sources_used,
        "errors": errors if errors else None,
        "consensus_buys": consensus_buys,
        "consensus_sells": consensus_sells,
        "momentum_signals": momentum_signals,
        "congress_picks": congress_picks,
        "congress_trades_raw": congress_trades_raw,
        "sender_picks": sender_picks,
        "recommendations": recommendations,
    }

    logger.info(
        "Consolidated briefing complete — %d recommendations (%d buys, %d sells)",
        len(recommendations),
        sum(1 for r in recommendations if r["action"] == "BUY"),
        sum(1 for r in recommendations if r["action"] == "SELL"),
    )
    return briefing


def format_briefing(briefing: dict) -> str:
    """
    Format the consolidated briefing dict as a Telegram-ready message.

    Args:
        briefing: Output from consolidated_briefing().

    Returns:
        Formatted string with emoji, bold headers, and clean sections.
    """
    lines = []

    # ═══════════════════════════════════════════════
    #   HERMES DAILY BRIEFING
    # ═══════════════════════════════════════════════
    date_str = briefing.get("date", datetime.now().strftime("%Y-%m-%d"))
    lines.append("═══════════════════════════════════════════════")
    lines.append(f"  **HERMES DAILY BRIEFING** — {date_str}")
    lines.append("  $100,000 Paper Account")
    lines.append("═══════════════════════════════════════════════")
    lines.append("")

    # 🏆 TOP RECOMMENDATIONS
    recommendations = briefing.get("recommendations", [])
    if recommendations:
        lines.append("🏆 **TOP RECOMMENDATIONS**")
        lines.append("───────────────────────────────────────────────")
        lines.append("")

        for rec in recommendations:
            action = rec.get("action", "HOLD")
            symbol = rec.get("symbol", "???")
            price = rec.get("price", 0)
            confidence = rec.get("confidence", "MEDIUM")
            sources = rec.get("sources", [])
            reason = rec.get("reason", "")
            target = rec.get("target", 0)
            stop_loss = rec.get("stop_loss", 0)
            priority = rec.get("priority", 0)

            # Medal / emoji
            if priority == 1 and action == "BUY":
                medal = "🥇"
            elif priority == 2 and action == "BUY":
                medal = "🥈"
            elif priority == 3 and action == "BUY":
                medal = "🥉"
            else:
                medal = "🔴" if action == "SELL" else "🟢"

            action_label = f"**{action}**" if action == "SELL" else f"**{action}**"

            price_str = f" @ ${price:.2f}" if price and price > 0 else ""
            lines.append(f"  {medal} {action_label} {symbol}{price_str}")

            # Sources line
            source_labels = []
            for src in sources:
                if src == "momentum":
                    source_labels.append("Momentum")
                elif src == "congress":
                    source_labels.append("Congress")
                elif src == "sender":
                    source_labels.append("Sender 13F")
            if source_labels:
                lines.append(f"     Sources: {' + '.join(source_labels)}")

            if confidence:
                conf_indicator = "⭐⭐⭐" if confidence == "HIGH" else ("⭐⭐" if confidence == "MEDIUM" else "⭐")
                lines.append(f"     Confidence: {conf_indicator}")

            if reason:
                lines.append(f"     {reason}")

            # Target / stop
            if action == "BUY" and price and target:
                gain_pct = ((target - price) / price) * 100
                loss_pct = ((stop_loss - price) / price) * 100
                lines.append(f"     Target: ${target:.2f} ({gain_pct:+.1f}%) | Stop: ${stop_loss:.2f} ({loss_pct:.1f}%)")
            elif action == "SELL":
                lines.append(f"     Signal: Exit position")

            lines.append("")

    if not recommendations:
        lines.append("📭 No recommendations today — markets may be closed.")
        lines.append("")

    # 📡 MOMENTUM SCAN RESULTS
    momentum_signals = briefing.get("momentum_signals", [])
    if momentum_signals:
        lines.append("📡 **MOMENTUM SCAN RESULTS**")
        lines.append("───────────────────────────────────────────────")
        lines.append("")

        for sig in momentum_signals:
            sym = sig.get("symbol", "???")
            action = sig.get("action", "HOLD")
            rsi = sig.get("rsi", 50)
            reason = sig.get("reason", "")

            if action == "BUY":
                indicator = "🟢 BUY"
            elif action == "SELL":
                indicator = "🔴 SELL"
            else:
                indicator = "⏸ HOLD"

            # Truncate reason for compact display
            short_reason = reason[:40] if reason else "No clear setup"
            emoji = "✅" if action == "BUY" else ("❌" if action == "SELL" else "⏸")
            lines.append(f"  {sym:<6s} {indicator}  RSI {rsi:.0f} | {short_reason:<40s} {emoji}")

        lines.append("")

    # 🏛️ CONGRESS WATCH
    congress_picks = briefing.get("congress_picks", [])
    congress_trades_raw = briefing.get("congress_trades_raw", [])
    if congress_picks or congress_trades_raw:
        lines.append("🏛️ **CONGRESS WATCH**")
        lines.append("───────────────────────────────────────────────")
        lines.append("")

        # Show Pelosi and Crenshaw activity
        if congress_picks:
            pelosi_buys = [p for p in congress_picks if p.get("source", "").endswith("pelosi")]
            if pelosi_buys:
                buy_symbols = [p["symbol"] for p in pelosi_buys]
                lines.append(f"  🟢 **PELOSI**: Buying {', '.join(buy_symbols)}")

        # Show raw trade signals if available
        if congress_trades_raw:
            from strategies.congress import _extract_ticker, _extract_transaction_type
            pelosi_recent = []
            for t in congress_trades_raw[:5]:
                ticker = _extract_ticker(t) or "???"
                tx_type = _extract_transaction_type(t)
                pelosi_recent.append(f"{ticker} ({tx_type.upper()})")
            if pelosi_recent:
                lines.append(f"     Recent: {', '.join(pelosi_recent)}")

        lines.append("")

    # 🐋 ADAM SENDER 13F
    sender_picks = briefing.get("sender_picks", {})
    if sender_picks:
        lines.append("🐋 **ADAM SENDER 13F**")
        lines.append("───────────────────────────────────────────────")
        lines.append("")

        summary = sender_picks.get("summary", {})
        total_val = summary.get("total_value", 0) / 1_000_000
        num_pos = summary.get("num_positions", 0)
        quarter = sender_picks.get("quarter", "Q1 2026")
        lines.append(f"  Portfolio: ${total_val:.1f}M | {num_pos} positions")
        lines.append(f"")

        # Top buys
        buys = sender_picks.get("buys", [])
        if buys:
            buy_parts = []
            for b in buys[:3]:
                sym = b.get("symbol", "")
                val = b.get("value_change", 0) / 1_000_000
                pct = b.get("pct_change")
                if pct is not None and pct > 0:
                    buy_parts.append(f"{sym} (+${val:.1f}M, +{pct:.0f}%)")
                else:
                    buy_parts.append(f"{sym} (+${val:.1f}M)")
            if buy_parts:
                lines.append(f"  🟢 **TOP BUYS**: {', '.join(buy_parts)}")

        # Top sells
        sells = sender_picks.get("sells", [])
        if sells:
            sell_parts = []
            for s in sells[:3]:
                sym = s.get("symbol", "")
                val = abs(s.get("value_change", 0)) / 1_000_000
                pct = s.get("pct_change")
                if pct is not None:
                    sell_parts.append(f"{sym} ({pct:.0f}%)")
                else:
                    sell_parts.append(f"{sym} (exited)")
            if sell_parts:
                lines.append(f"  🔴 **TOP SELLS**: {', '.join(sell_parts)}")

        # Top holdings
        top_holdings = sender_picks.get("top_holdings", [])
        if top_holdings:
            top_syms = [h["symbol"] for h in top_holdings[:5] if h.get("symbol")]
            lines.append(f"  Top holdings: {', '.join(top_syms)}")

        lines.append("")

    # ⚡ ANALYSIS
    lines.append("⚡ **MY ANALYSIS**")
    lines.append("───────────────────────────────────────────────")
    lines.append("")

    recommendations = briefing.get("recommendations", [])

    # Find strongest signal
    buy_recs = [r for r in recommendations if r["action"] == "BUY"]
    sell_recs = [r for r in recommendations if r["action"] == "SELL"]

    if buy_recs:
        top = buy_recs[0]
        sym = top.get("symbol", "")
        reason = top.get("reason", "")
        sources = top.get("sources", [])
        lines.append(f"  **Strongest signal**: {sym}")
        lines.append(f"  {reason}")
        if len(sources) >= 2:
            lines.append(f"  **{len(sources)} independent sources** pointing the same direction.")
        lines.append("")

    # Caution signals (mixed signals)
    mixed = []
    momentum_buy_syms = {s["symbol"] for s in momentum_signals if s.get("action") == "BUY"}
    sender_sell_syms = {s["symbol"] for s in sender_picks.get("sells", []) if s.get("symbol")}

    for sym in momentum_buy_syms & sender_sell_syms:
        mixed.append(f"  ⚠️ **{sym}**: Sender cut while momentum says oversold.")

    for sym in sender_sell_syms & {s["symbol"] for s in congress_picks}:
        if sym not in mixed:
            mixed.append(f"  ⚠️ **{sym}**: Congress buying but Sender reducing.")

    if mixed:
        lines.append("  **Mixed signals — wait for confirmation:**")
        lines.append("")
        for m in mixed:
            lines.append(m)
        lines.append("")

    if sell_recs:
        top_sell = sell_recs[0]
        sym = top_sell.get("symbol", "")
        reason = top_sell.get("reason", "")
        sources = top_sell.get("sources", [])
        lines.append(f"  **Watch out**: {sym}")
        lines.append(f"  {reason}")
        if "sender" in sources:
            lines.append("  Smart money exiting — consider reducing exposure.")
        lines.append("")

    if not buy_recs and not sell_recs:
        analysis = briefing.get("errors", [])
        if analysis:
            lines.append("  ⚠️ Could not gather sufficient data for analysis.")
            for err in analysis:
                lines.append(f"  • {err}")
        else:
            lines.append("  No strong signals detected today. Markets may be quiet.")
        lines.append("")

    # Footer
    lines.append("═══════════════════════════════════════════════")
    lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("  ⚡ `/briefing` to regenerate | `/help` for commands")
    lines.append("═══════════════════════════════════════════════")

    return "\n".join(lines)


if __name__ == "__main__":
    # Quick test: run briefing and print to console
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    print("\nGenerating consolidated briefing...\n")
    brief = consolidated_briefing()
    formatted = format_briefing(brief)
    print(formatted)