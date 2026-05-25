"""
Momentum strategy module for Hermes Trading Bot.

Provides technical indicator checks and signal assembly using RSI, MACD,
volume analysis, and SMA crossovers.
"""

import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class MomentumStrategy:
    """Momentum-based trading strategy using technical indicators."""

    @staticmethod
    def check_rsi_oversold(df: pd.DataFrame, period: int = 14, threshold: float = 35) -> tuple:
        """
        Check if RSI indicates oversold conditions.

        Args:
            df: DataFrame with 'close' column (must have at least `period` rows).
            period: RSI lookback period.
            threshold: RSI value below which is considered oversold.

        Returns:
            Tuple of (bool indicating oversold, float RSI value, float score [0-1]).
        """
        if df is None or len(df) < period + 1:
            return False, 50.0, 0.0

        try:
            close = df["close"].astype(float)
            delta = close.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = (-delta.where(delta < 0, 0.0))

            avg_gain = gain.rolling(window=period, min_periods=period).mean()
            avg_loss = loss.rolling(window=period, min_periods=period).mean()

            for i in range(period, len(avg_gain)):
                avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
                avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period

            rs = avg_gain / avg_loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))

            current_rsi = rsi.iloc[-1]
            if pd.isna(current_rsi):
                return False, 50.0, 0.0

            is_oversold = current_rsi < threshold
            # Score: 0 at threshold, 1 at RSI=20 (linearly mapped)
            score = max(0.0, min(1.0, (threshold - current_rsi) / (threshold - 20.0)))
            if not is_oversold:
                # Check for overbought reverse signal
                is_overbought = current_rsi > (100 - threshold)
                if is_overbought:
                    score = -max(0.0, min(1.0, (current_rsi - (100 - threshold)) / ((100 - threshold) - 80.0)))
                else:
                    score = 0.0

            return is_oversold, round(current_rsi, 2), round(score, 3)

        except (KeyError, IndexError, ValueError) as e:
            logger.warning("RSI calculation failed: %s", e)
            return False, 50.0, 0.0

    @staticmethod
    def check_macd_cross(df: pd.DataFrame) -> dict:
        """
        Detect MACD line / signal line crossovers.

        Args:
            df: DataFrame with 'close' column.

        Returns:
            Dict with cross_detected, direction ('bullish'|'bearish'|None),
            and histogram value.
        """
        result = {
            "cross_detected": False,
            "direction": None,
            "histogram": 0.0,
        }

        if df is None or len(df) < 26:
            return result

        try:
            close = df["close"].astype(float)
            ema_12 = close.ewm(span=12, adjust=False).mean()
            ema_26 = close.ewm(span=26, adjust=False).mean()
            macd_line = ema_12 - ema_26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            histogram = macd_line - signal_line

            if len(histogram) < 2:
                return result

            prev_hist = histogram.iloc[-2]
            curr_hist = histogram.iloc[-1]

            result["histogram"] = round(curr_hist, 4)

            # Bullish cross: histogram crosses from negative to positive
            if prev_hist <= 0 and curr_hist > 0:
                result["cross_detected"] = True
                result["direction"] = "bullish"
            # Bearish cross: histogram crosses from positive to negative
            elif prev_hist >= 0 and curr_hist < 0:
                result["cross_detected"] = True
                result["direction"] = "bearish"

            return result

        except (KeyError, IndexError, ValueError) as e:
            logger.warning("MACD calculation failed: %s", e)
            return result

    @staticmethod
    def check_volume_spike(df: pd.DataFrame, multiplier: float = 1.5) -> tuple:
        """
        Check if current volume is significantly above average.

        Args:
            df: DataFrame with 'volume' column.
            multiplier: Threshold multiple of average volume.

        Returns:
            Tuple of (bool indicating spike, float volume ratio).
        """
        if df is None or len(df) < 20:
            return False, 1.0

        try:
            volume = df["volume"].astype(float)
            avg_volume = volume.iloc[-20:].mean()
            current_volume = volume.iloc[-1]

            if pd.isna(avg_volume) or avg_volume == 0:
                return False, 1.0

            ratio = current_volume / avg_volume
            return bool(ratio > multiplier), round(ratio, 2)

        except (KeyError, IndexError, ValueError) as e:
            logger.warning("Volume spike check failed: %s", e)
            return False, 1.0

    @staticmethod
    def check_sma_cross(df: pd.DataFrame) -> dict:
        """
        Detect golden cross (SMA 20 crosses above SMA 50) or death cross.

        Args:
            df: DataFrame with 'close' column.

        Returns:
            Dict with cross_detected, cross_type ('golden'|'death'|None),
            and prices for SMA20 and SMA50.
        """
        result = {
            "cross_detected": False,
            "cross_type": None,
            "sma_20": None,
            "sma_50": None,
        }

        if df is None or len(df) < 50:
            return result

        try:
            close = df["close"].astype(float)
            sma_20 = close.rolling(window=20).mean()
            sma_50 = close.rolling(window=50).mean()

            result["sma_20"] = round(sma_20.iloc[-1], 2) if not pd.isna(sma_20.iloc[-1]) else None
            result["sma_50"] = round(sma_50.iloc[-1], 2) if not pd.isna(sma_50.iloc[-1]) else None

            if len(sma_20) < 2 or len(sma_50) < 2:
                return result

            prev_sma_20 = sma_20.iloc[-2]
            prev_sma_50 = sma_50.iloc[-2]
            curr_sma_20 = sma_20.iloc[-1]
            curr_sma_50 = sma_50.iloc[-1]

            if pd.isna(prev_sma_20) or pd.isna(prev_sma_50) or pd.isna(curr_sma_20) or pd.isna(curr_sma_50):
                return result

            # Golden cross: SMA20 crosses above SMA50
            if prev_sma_20 <= prev_sma_50 and curr_sma_20 > curr_sma_50:
                result["cross_detected"] = True
                result["cross_type"] = "golden"
            # Death cross: SMA20 crosses below SMA50
            elif prev_sma_20 >= prev_sma_50 and curr_sma_20 < curr_sma_50:
                result["cross_detected"] = True
                result["cross_type"] = "death"

            return result

        except (KeyError, IndexError, ValueError) as e:
            logger.warning("SMA cross check failed: %s", e)
            return result

    @staticmethod
    def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """Calculate RSI for a price series."""
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta.where(delta < 0, 0.0))

        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()

        for i in range(period, len(avg_gain)):
            avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
            avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period

        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def calculate_macd(series: pd.Series) -> tuple:
        """Calculate MACD line, signal line, and histogram."""
        ema_12 = series.ewm(span=12, adjust=False).mean()
        ema_26 = series.ewm(span=26, adjust=False).mean()
        macd_line = ema_12 - ema_26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    def assemble_signal(self, symbol: str, df: pd.DataFrame) -> dict:
        """
        Combine all indicator checks into a weighted signal.

        Args:
            symbol: Ticker symbol.
            df: DataFrame with OHLCV data (must have 'close' and 'volume').

        Returns:
            Signal dict with action, confidence, score components, and reasoning.
        """
        if df is None or len(df) < 20:
            return {
                "symbol": symbol,
                "action": "HOLD",
                "confidence": "LOW",
                "score": 0,
                "reason": "Insufficient historical data",
            }

        close = df["close"].astype(float)
        current_price = float(close.iloc[-1])

        # Run all indicator checks
        rsi_oversold, rsi_value, rsi_score = self.check_rsi_oversold(df)
        macd_result = self.check_macd_cross(df)
        volume_spike, volume_ratio = self.check_volume_spike(df)
        sma_result = self.check_sma_cross(df)

        # Build weighted score (range: -1 to 1)
        # RSI: oversold = bullish, overbought = bearish
        # MACD: bullish cross = +, bearish cross = -
        # Volume: spike confirms direction
        # SMA: golden cross = +, death cross = -
        score = 0.0
        reasons = []

        # RSI contribution (weight: 0.30)
        score += rsi_score * 0.30
        if rsi_oversold:
            reasons.append(f"RSI({rsi_value}) oversold bounce opportunity")
        elif rsi_value > 70:
            reasons.append(f"RSI({rsi_value}) overbought — caution")

        # MACD contribution (weight: 0.25)
        if macd_result["direction"] == "bullish":
            score += 0.25
            reasons.append("Bullish MACD cross detected")
        elif macd_result["direction"] == "bearish":
            score -= 0.25
            reasons.append("Bearish MACD cross detected")

        # Volume contribution (weight: 0.20)
        if volume_spike:
            if score > 0:
                score += 0.20
                reasons.append(f"Volume {volume_ratio}x avg — confirming uptick")
            elif score < 0:
                score -= 0.20
                reasons.append(f"Volume {volume_ratio}x avg — selling pressure")
            else:
                reasons.append(f"Volume {volume_ratio}x avg — elevated activity")
        elif volume_ratio > 1.2:
            reasons.append(f"Volume {volume_ratio}x avg — slightly elevated")

        # SMA contribution (weight: 0.25)
        if sma_result["cross_type"] == "golden":
            score += 0.25
            reasons.append("Golden cross (20 SMA above 50 SMA)")
        elif sma_result["cross_type"] == "death":
            score -= 0.25
            reasons.append("Death cross (20 SMA below 50 SMA)")

        # Price vs SMA50 trend check
        sma_50 = sma_result.get("sma_50")
        if sma_50 is not None and current_price > sma_50:
            score += 0.05
        elif sma_50 is not None and current_price < sma_50:
            score -= 0.05

        # Determine action
        if score >= 0.35:
            action = "BUY"
        elif score <= -0.35:
            action = "SELL"
        else:
            action = "HOLD"

        # Determine confidence
        abs_score = abs(score)
        if abs_score >= 0.55:
            confidence = "HIGH"
        elif abs_score >= 0.25:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # Build reason string
        reason = "; ".join(reasons) if reasons else "No clear signals detected"

        # Calculate stop-loss and target
        atr = self._calculate_atr(df)
        if action == "BUY":
            stop_loss = round(current_price - (atr * 1.5), 2)
            target = round(current_price + (atr * 2.5), 2)
        elif action == "SELL":
            stop_loss = round(current_price + (atr * 1.5), 2)
            target = round(current_price - (atr * 2.5), 2)
        else:
            stop_loss = round(current_price - (atr * 1.5), 2)
            target = round(current_price + (atr * 2.5), 2)

        return {
            "symbol": symbol,
            "action": action,
            "confidence": confidence,
            "price": current_price,
            "rsi": rsi_value,
            "macd_bullish": macd_result["direction"] == "bullish",
            "volume_ratio": volume_ratio,
            "reason": reason,
            "stop_loss": stop_loss,
            "target": target,
            "score": round(score, 3),
        }

    @staticmethod
    def _calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
        """Calculate Average True Range for volatility measurement."""
        try:
            high = df["high"].astype(float)
            low = df["low"].astype(float)
            close = df["close"].astype(float)

            prev_close = close.shift(1)
            tr = pd.concat([
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ], axis=1).max(axis=1)

            atr = tr.rolling(window=period).mean().iloc[-1]
            return float(atr) if not pd.isna(atr) else current_price * 0.02
        except Exception:
            # Fallback ATR: 2% of price
            return float(df["close"].iloc[-1]) * 0.02 if len(df) > 0 else 1.0