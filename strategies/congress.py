"""
Congress copy-trading module — THE PELOSI TRACKER.

Fetches recent congressional trade filings from the Capitol Trades API
and generates copy-trading signals to mirror politician portfolios.
"""

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Common politician IDs
PELOSI = "P000197"
CRENSHAW = "C001120"
GREEN = "G000553"
GAIETZ = "G000578"
OCASIO_CORTEZ = "O000172"
WARNOCK = "W000790"
BURCHETT = "B001309"
MACE = "M000194"

# Cache for API results to reduce requests
_trade_cache = {}
_cache_times = {}
CACHE_TTL = 3600  # 1 hour

# API base URL
CAPITOL_TRADES_API = "https://api.capitoltrades.com"


def _rate_limited_request(url: str, params: dict = None, max_retries: int = 2) -> Optional[dict]:
    """
    Make an HTTP GET request with rate limiting and error handling.

    Args:
        url: Full URL to request.
        params: Query parameters dict.
        max_retries: Number of retries on failure.

    Returns:
        Parsed JSON response dict, or None on failure.
    """
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 429:
                wait = min(2 ** (attempt + 1), 30)
                logger.warning("Rate limited by Capitol Trades API, waiting %ds", wait)
                time.sleep(wait)
                continue
            logger.warning("HTTP error from Capitol Trades API: %s", e)
            return None
        except requests.exceptions.ConnectionError as e:
            logger.warning("Connection error to Capitol Trades API: %s", e)
            if attempt < max_retries:
                time.sleep(2)
                continue
            return None
        except requests.exceptions.Timeout:
            logger.warning("Timeout connecting to Capitol Trades API")
            if attempt < max_retries:
                time.sleep(2)
                continue
            return None
        except requests.exceptions.RequestException as e:
            logger.warning("Request failed: %s", e)
            return None

    return None


def fetch_congress_trades(politician: str = PELOSI, limit: int = 20) -> list:
    """
    Fetch recent congressional trade filings for a given politician.

    Uses the Capitol Trades API at https://api.capitoltrades.com.

    Args:
        politician: Politician ID (default: Nancy Pelosi).
        limit: Maximum number of trades to return.

    Returns:
        List of trade dicts, or empty list on failure.
    """
    cache_key = f"trades_{politician}_{limit}"
    now = time.time()

    # Check cache
    if cache_key in _trade_cache and now - _cache_times.get(cache_key, 0) < CACHE_TTL:
        logger.debug("Using cached trades for %s", politician)
        return _trade_cache[cache_key]

    try:
        # First try the trades endpoint
        data = _rate_limited_request(
            f"{CAPITOL_TRADES_API}/trades",
            params={"politicianId": politician, "limit": limit},
        )

        if data and "data" in data:
            trades = data["data"]
            _trade_cache[cache_key] = trades
            _cache_times[cache_key] = now
            logger.info("Fetched %d trades for politician %s", len(trades), politician)
            return trades
        elif data and "results" in data:
            trades = data["results"]
            _trade_cache[cache_key] = trades
            _cache_times[cache_key] = now
            logger.info("Fetched %d trades for politician %s", len(trades), politician)
            return trades

        # Try alternative endpoint pattern
        data = _rate_limited_request(
            f"{CAPITOL_TRADES_API}/politician/{politician}/trades",
            params={"limit": limit},
        )
        if data and "data" in data:
            trades = data["data"]
        elif data and "results" in data:
            trades = data["results"]
        else:
            trades = []

        _trade_cache[cache_key] = trades
        _cache_times[cache_key] = now
        logger.info("Fetched %d trades for politician %s", len(trades), politician)
        return trades

    except Exception as e:
        logger.error("Failed to fetch congressional trades: %s", e)
        return []


def get_politician_portfolio(politician_id: str) -> list:
    """
    Aggregate current known holdings for a politician based on recent filings.

    Args:
        politician_id: Politician ID string.

    Returns:
        List of dicts with ticker, allocation_pct, and details.
    """
    trades = fetch_congress_trades(politician_id, limit=50)
    if not trades:
        return []

    # Aggregate holdings by ticker
    holdings = {}
    total_value = 0

    for trade in trades:
        ticker = _extract_ticker(trade)
        if not ticker:
            continue

        amount = _extract_amount(trade)
        transaction = _extract_transaction_type(trade)

        if ticker not in holdings:
            holdings[ticker] = {"shares": 0, "total_value": 0, "buys": 0, "sells": 0}

        if transaction == "purchase" or transaction == "buy":
            holdings[ticker]["buys"] += 1
            holdings[ticker]["total_value"] += amount if amount else 100000
        elif transaction == "sale" or transaction == "sell" or transaction == "full_sale":
            holdings[ticker]["sells"] += 1
            holdings[ticker]["total_value"] -= amount if amount else 100000

        total_value += amount if amount else 100000

    # Remove fully-sold positions and calculate allocations
    portfolio = []
    for ticker, data in holdings.items():
        if data["total_value"] <= 0:
            continue
        allocation = data["total_value"] / total_value if total_value > 0 else 0
        portfolio.append({
            "ticker": ticker,
            "allocation_pct": round(allocation * 100, 2),
            "total_value": round(data["total_value"], 2),
            "recent_activity": "buying" if data["buys"] >= data["sells"] else "selling",
        })

    # Sort by allocation descending
    portfolio.sort(key=lambda x: x["allocation_pct"], reverse=True)
    return portfolio


def build_pelosi_portfolio() -> list:
    """Get Nancy Pelosi's current stock portfolio with allocations."""
    logger.info("Building Pelosi portfolio...")
    return get_politician_portfolio(PELOSI)


def _extract_ticker(trade: dict) -> Optional[str]:
    """Extract ticker symbol from a trade dict, handling various API shapes."""
    for key in ("ticker", "symbol", "issuer", "issuerTicker", "assetTicker"):
        val = trade.get(key)
        if val and isinstance(val, str) and len(val) <= 5:
            return val.upper()
    # Try nested structures
    asset = trade.get("asset", {}) or {}
    for key in ("ticker", "symbol"):
        val = asset.get(key)
        if val and isinstance(val, str):
            return val.upper()
    return None


def _extract_amount(trade: dict) -> Optional[float]:
    """Extract trade amount from a trade dict."""
    for key in ("amount", "value", "transactionValue", "estimatedValue"):
        val = trade.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
    # Parse amount range
    for key in ("amountRange", "valueRange", "range"):
        val = trade.get(key)
        if val and isinstance(val, (list, tuple)) and len(val) >= 2:
            try:
                return (float(val[0]) + float(val[1])) / 2
            except (ValueError, TypeError):
                pass
    return 100000  # default estimate


def _extract_transaction_type(trade: dict) -> str:
    """Extract transaction type (buy/sell) from trade dict."""
    tx_type = trade.get("transactionType", trade.get("type", ""))
    if isinstance(tx_type, str):
        tx_lower = tx_type.lower()
        if "sell" in tx_lower or "sale" in tx_lower:
            return "sell"
        if "buy" in tx_lower or "purchase" in tx_lower or "exchange" in tx_lower:
            return "buy"
    return "unknown"


def compare_to_current_holdings(strategy_holdings: list, current_positions: list) -> dict:
    """
    Compare strategy target holdings to current positions and identify trades needed.

    Args:
        strategy_holdings: List of dicts with ticker, allocation_pct from strategy.
        current_positions: List of dicts with symbol, market_value from actual positions.

    Returns:
        Dict with buy_list, sell_list, and summary.
    """
    # Build lookup of current positions
    current = {}
    for pos in current_positions:
        symbol = pos.get("symbol", "").upper()
        current[symbol] = pos

    buy_list = []
    sell_list = []
    keep_list = []

    # Check strategy holdings against current
    strategy_tickers = {h["ticker"] for h in strategy_holdings}
    current_tickers = set(current.keys())

    # Stocks to buy (in strategy but not in portfolio)
    to_buy = strategy_tickers - current_tickers
    for holding in strategy_holdings:
        if holding["ticker"] in to_buy:
            buy_list.append({
                "symbol": holding["ticker"],
                "action": "BUY",
                "allocation_pct": holding["allocation_pct"],
                "reason": f"Pelosi holds ~{holding['allocation_pct']}% — not in portfolio",
            })

    # Stocks to consider selling (in portfolio but heavily reduced in strategy)
    for ticker in current_tickers:
        if ticker not in strategy_tickers:
            sell_list.append({
                "symbol": ticker,
                "action": "SELL",
                "reason": "Not in Pelosi's recent portfolio",
            })

    # Stocks to keep or adjust
    for holding in strategy_holdings:
        if holding["ticker"] in current:
            keep_list.append(holding["ticker"])

    return {
        "buy_list": buy_list,
        "sell_list": sell_list,
        "keep_list": keep_list,
        "num_buys": len(buy_list),
        "num_sells": len(sell_list),
        "num_keeps": len(keep_list),
    }


def generate_copy_trade_signals() -> list:
    """
    Generate BUY/SELL signals to match Nancy Pelosi's portfolio.

    Returns:
        List of signal dicts.
    """
    from config import CFG

    portfolio = build_pelosi_portfolio()
    if not portfolio:
        logger.warning("Could not build Pelosi portfolio — API may be unreachable.")
        return []

    logger.info("Pelosi portfolio built: %d holdings", len(portfolio))

    signals = []
    for holding in portfolio:
        signal = {
            "symbol": holding["ticker"],
            "action": "BUY",
            "confidence": "MEDIUM",
            "price": 0.0,  # Will be filled by trader if needed
            "reason": f"Pelosi copy-trade: {holding['allocation_pct']}% allocation target",
            "stop_loss": 0.0,
            "target": 0.0,
            "allocation_pct": holding["allocation_pct"],
            "source": "congress_pelosi",
        }
        signals.append(signal)

    return signals