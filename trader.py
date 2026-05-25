"""
Core trading engine for Hermes Trading Bot.

Connects to Alpaca (paper by default), fetches account data, market data,
generates signals using the momentum strategy, and submits orders.
"""

import logging
from typing import Optional
from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from config import CFG, validate_config
from strategies.momentum import MomentumStrategy

logger = logging.getLogger(__name__)


class HermesTrader:
    """Core trading engine that connects to Alpaca and manages trading operations."""

    def __init__(self):
        """Initialize the trading engine with Alpaca connection."""
        self.config = CFG
        self._validate_or_warn()

        self.api_key = self.config["ALPACA_API_KEY"]
        self.secret_key = self.config["ALPACA_SECRET_KEY"]
        self.paper = self.config["ALPACA_PAPER"]
        self.base_url = self.config["ALPACA_BASE_URL"]

        # Initialize Alpaca clients
        self.trading_client = TradingClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            paper=self.paper,
        )
        self.data_client = StockHistoricalDataClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
        )

        # Strategy engine
        self.strategy = MomentumStrategy()

        # Track daily P&L
        self._daily_pnl = 0.0

        logger.info(
            "HermesTrader initialized — %s trading on %s",
            "LIVE" if not self.paper else "Paper",
            self.base_url,
        )

    def _validate_or_warn(self):
        """Warn if API keys are missing but don't crash."""
        if not validate_config():
            logger.warning(
                "Alpaca API keys are not configured. "
                "Copy .env.example to .env and fill in your credentials."
            )

    # ------------------------------------------------------------------ #
    #  Account & Positions
    # ------------------------------------------------------------------ #

    def get_account_summary(self) -> dict:
        """
        Retrieve account summary from Alpaca.

        Returns:
            Dict with cash, portfolio_value, buying_power, pnl, day_change_pct.
        """
        try:
            account = self.trading_client.get_account()

            portfolio_value = float(account.portfolio_value)
            cash = float(account.cash)
            buying_power = float(account.buying_power)
            equity = float(account.equity)
            last_equity = float(account.last_equity) if hasattr(account, 'last_equity') and account.last_equity else equity

            day_pnl = equity - last_equity
            day_change_pct = (day_pnl / last_equity * 100) if last_equity > 0 else 0.0

            self._daily_pnl = day_pnl

            return {
                "cash": round(cash, 2),
                "portfolio_value": round(portfolio_value, 2),
                "equity": round(equity, 2),
                "buying_power": round(buying_power, 2),
                "pnl": round(day_pnl, 2),
                "day_change_pct": round(day_change_pct, 3),
                "status": account.status,
                "currency": account.currency,
            }

        except Exception as e:
            logger.error("Failed to fetch account summary: %s", e)
            return {
                "cash": 0.0,
                "portfolio_value": 0.0,
                "equity": 0.0,
                "buying_power": 0.0,
                "pnl": 0.0,
                "day_change_pct": 0.0,
                "status": "error",
                "currency": "USD",
            }

    def get_positions(self) -> list:
        """
        Retrieve current open positions from Alpaca.

        Returns:
            List of position dicts with symbol, qty, market_value, cost_basis, pnl.
        """
        try:
            positions = self.trading_client.get_all_positions()
            result = []
            for pos in positions:
                result.append({
                    "symbol": pos.symbol,
                    "qty": float(pos.qty),
                    "market_value": float(pos.market_value),
                    "cost_basis": float(pos.cost_basis),
                    "avg_entry_price": float(pos.avg_entry_price),
                    "current_price": float(pos.current_price),
                    "unrealized_pl": float(pos.unrealized_pl),
                    "unrealized_pl_pct": float(pos.unrealized_plpc) * 100,
                    "day_pl": float(pos.unrealized_intraday_pl) if hasattr(pos, 'unrealized_intraday_pl') else 0.0,
                })
            return result

        except Exception as e:
            logger.error("Failed to fetch positions: %s", e)
            return []

    # ------------------------------------------------------------------ #
    #  Market Data
    # ------------------------------------------------------------------ #

    def get_historical_data(
        self, symbol: str, days: int = 30, timeframe: str = "1Day"
    ) -> Optional[pd.DataFrame]:
        """
        Fetch historical OHLCV data for a symbol from Alpaca.

        Args:
            symbol: Ticker symbol.
            days: Number of days of history to fetch.
            timeframe: Alpaca timeframe string ('1Day', '1Hour', '15Min', etc.).

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume.
            Returns None on failure.
        """
        try:
            # Map timeframe string to TimeFrame
            tf_map = {
                "1Day": TimeFrame.Day,
                "1Hour": TimeFrame.Hour,
                "15Min": TimeFrame.Minute,
                "5Min": TimeFrame.Minute,
                "1Min": TimeFrame.Minute,
            }
            tf = tf_map.get(timeframe, TimeFrame.Day)

            # For custom minute/hour amounts, construct manually
            if timeframe == "15Min":
                import alpaca.data.timeframe as tfm
                tf = tfm.TimeFrame(15, tfm.TimeFrameUnit.Minute)
            elif timeframe == "5Min":
                import alpaca.data.timeframe as tfm
                tf = tfm.TimeFrame(5, tfm.TimeFrameUnit.Minute)
            elif timeframe == "1Min":
                import alpaca.data.timeframe as tfm
                tf = tfm.TimeFrame(1, tfm.TimeFrameUnit.Minute)

            end = datetime.now()
            start = end - timedelta(days=days)

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                start=start,
                end=end,
            )

            bars = self.data_client.get_stock_bars(request)

            if symbol.upper() not in bars.data:
                logger.warning("No data returned for %s", symbol)
                return None

            records = []
            for bar in bars.data[symbol.upper()]:
                records.append({
                    "timestamp": bar.timestamp,
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "volume": float(bar.volume),
                    "trade_count": bar.trade_count if hasattr(bar, 'trade_count') else 0,
                    "vwap": float(bar.vwap) if hasattr(bar, 'vwap') and bar.vwap else 0.0,
                })

            if not records:
                logger.warning("Empty data returned for %s", symbol)
                return None

            df = pd.DataFrame(records)
            df.set_index("timestamp", inplace=True)
            df.sort_index(inplace=True)

            logger.debug("Fetched %d bars for %s", len(df), symbol)
            return df

        except Exception as e:
            logger.error("Failed to fetch historical data for %s: %s", symbol, e)
            return None

    @staticmethod
    def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """
        Add technical indicators to a DataFrame.

        Adds columns: rsi_14, macd, macd_signal, macd_histogram,
        sma_20, sma_50, volume_ratio.

        Args:
            df: DataFrame with 'close' and 'volume' columns.

        Returns:
            DataFrame with added indicator columns.
        """
        if df is None or df.empty:
            return df

        result = df.copy()
        close = result["close"].astype(float)

        # RSI(14)
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta.where(delta < 0, 0.0))
        avg_gain = gain.ewm(span=14, adjust=False).mean()
        avg_loss = loss.ewm(span=14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        result["rsi_14"] = 100 - (100 / (1 + rs))

        # MACD (12/26/9)
        ema_12 = close.ewm(span=12, adjust=False).mean()
        ema_26 = close.ewm(span=26, adjust=False).mean()
        result["macd"] = ema_12 - ema_26
        result["macd_signal"] = result["macd"].ewm(span=9, adjust=False).mean()
        result["macd_histogram"] = result["macd"] - result["macd_signal"]

        # SMA(20, 50)
        result["sma_20"] = close.rolling(window=20).mean()
        result["sma_50"] = close.rolling(window=50).mean()

        # Volume ratio (current / avg of last 20)
        volume = result["volume"].astype(float)
        avg_volume = volume.rolling(window=20).mean()
        result["volume_ratio"] = volume / avg_volume.replace(0, 1)

        return result

    # ------------------------------------------------------------------ #
    #  Signal Generation
    # ------------------------------------------------------------------ #

    def generate_signal(self, symbol: str) -> dict:
        """
        Analyze a single ticker and return a trading signal.

        Args:
            symbol: Ticker symbol.

        Returns:
            Signal dict with action, confidence, price, indicators, and reasoning.
        """
        df = self.get_historical_data(symbol, days=90)
        if df is None or len(df) < 20:
            return {
                "symbol": symbol,
                "action": "HOLD",
                "confidence": "LOW",
                "price": 0.0,
                "rsi": 50,
                "macd_bullish": False,
                "volume_ratio": 1.0,
                "reason": f"Insufficient data available for {symbol}",
                "stop_loss": 0.0,
                "target": 0.0,
            }

        # Add indicators
        df = self.calculate_indicators(df)

        # Use momentum strategy to assemble signal
        signal = self.strategy.assemble_signal(symbol, df)

        return signal

    def scan_market(self) -> list:
        """
        Scan the full watch list and return sorted signals.

        Returns:
            List of signal dicts sorted by confidence (HIGH first).
        """
        watch_list = self.config["WATCH_LIST"]
        logger.info("Scanning market for %d symbols...", len(watch_list))

        signals = []
        for symbol in watch_list:
            try:
                signal = self.generate_signal(symbol)
                signals.append(signal)
                logger.debug(
                    "Signal for %s: %s (%s confidence, score=%.2f)",
                    symbol, signal["action"], signal["confidence"], signal.get("score", 0),
                )
            except Exception as e:
                logger.error("Error scanning %s: %s", symbol, e)
                signals.append({
                    "symbol": symbol,
                    "action": "HOLD",
                    "confidence": "LOW",
                    "price": 0.0,
                    "reason": f"Error: {e}",
                })

        # Sort: HIGH confidence BUY first, then MEDIUM BUY, etc.
        priority = {"BUY": 0, "SELL": 1, "HOLD": 2}
        conf_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

        signals.sort(key=lambda s: (
            priority.get(s["action"], 3),
            conf_rank.get(s["confidence"], 3),
        ))

        logger.info("Market scan complete — %d signals generated", len(signals))
        return signals

    # ------------------------------------------------------------------ #
    #  Order Execution
    # ------------------------------------------------------------------ #

    def execute_trade(self, signal: dict) -> dict:
        """
        Validate and execute a trade based on a signal.

        Checks position limits, risk rules, and daily loss limit before submitting.

        Args:
            signal: Signal dict from generate_signal().

        Returns:
            Dict with success status, order details, and any error message.
        """
        if signal["action"] == "HOLD":
            return {"success": False, "message": f"HOLD signal for {signal['symbol']} — no action taken"}

        # Check daily loss limit
        account = self.get_account_summary()
        if account["day_change_pct"] <= CFG["DAILY_LOSS_LIMIT"] * 100:
            logger.warning("Daily loss limit reached (%.2f%%). Skipping trades.", account["day_change_pct"])
            return {
                "success": False,
                "message": f"Daily loss limit of {CFG['DAILY_LOSS_LIMIT']*100}% reached",
            }

        # Check max open positions
        positions = self.get_positions()
        if signal["action"] == "BUY" and len(positions) >= CFG["MAX_OPEN_POSITIONS"]:
            logger.warning("Max positions (%d) reached. Cannot open new trade.", CFG["MAX_OPEN_POSITIONS"])
            return {
                "success": False,
                "message": f"Maximum {CFG['MAX_OPEN_POSITIONS']} open positions reached",
            }

        # Check if we already have this position for BUY signals
        for pos in positions:
            if pos["symbol"] == signal["symbol"] and signal["action"] == "BUY":
                logger.info("Already holding %s — skipping duplicate BUY", signal["symbol"])
                return {
                    "success": False,
                    "message": f"Already holding {signal['symbol']}",
                }

        # Calculate position size
        price = signal["price"]
        if price <= 0:
            return {"success": False, "message": f"Invalid price for {signal['symbol']}"}

        portfolio_value = account["portfolio_value"]
        position_value = portfolio_value * CFG["MAX_POSITION_SIZE"]
        qty = max(1, int(position_value / price))

        if qty < 1:
            return {
                "success": False,
                "message": f"Position size too small for {signal['symbol']} at ${price:.2f}",
            }

        # Submit the order
        side = OrderSide.BUY if signal["action"] == "BUY" else OrderSide.SELL

        return self.submit_order(signal["symbol"], qty, side)

    def submit_order(
        self, symbol: str, qty: int, side: OrderSide, order_type: str = "market"
    ) -> dict:
        """
        Submit a raw order to Alpaca.

        Args:
            symbol: Ticker symbol.
            qty: Number of shares.
            side: OrderSide.BUY or OrderSide.SELL.
            order_type: 'market' or 'limit' (default: market).

        Returns:
            Dict with success status and order details.
        """
        try:
            if order_type == "market":
                order_request = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                )
            else:
                order_request = LimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=0.0,  # Should be set properly in production
                )

            order = self.trading_client.submit_order(order_request)

            logger.info(
                "Order submitted: %s %d shares of %s (%s)",
                side.name, qty, symbol, order_type,
            )

            return {
                "success": True,
                "order_id": order.id,
                "symbol": symbol,
                "qty": qty,
                "side": side.name,
                "type": order_type,
                "status": order.status,
                "submitted_at": str(order.submitted_at) if hasattr(order, 'submitted_at') else "",
            }

        except Exception as e:
            logger.error("Order submission failed for %s: %s", symbol, e)
            return {
                "success": False,
                "message": f"Order failed: {e}",
                "symbol": symbol,
            }

    def close_position(self, symbol: str) -> dict:
        """Close an open position entirely."""
        try:
            self.trading_client.close_position(symbol)
            logger.info("Position %s closed", symbol)
            return {"success": True, "symbol": symbol}
        except Exception as e:
            logger.error("Failed to close position %s: %s", symbol, e)
            return {"success": False, "message": str(e)}