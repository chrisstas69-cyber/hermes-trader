"""
Configuration module for Hermes Trading Bot.

Loads all settings from environment variables with .env file support.
All API keys are loaded from environment — never hardcoded.
"""

import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env file if it exists
load_dotenv()


def get_config() -> dict:
    """Load and return all configuration values as a dictionary."""
    config = {
        # Alpaca API credentials
        "ALPACA_API_KEY": os.getenv("ALPACA_API_KEY", ""),
        "ALPACA_SECRET_KEY": os.getenv("ALPACA_SECRET_KEY", ""),
        "ALPACA_PAPER": os.getenv("ALPACA_PAPER", "true").lower() in ("true", "1", "yes"),

        # Trading parameters
        "WATCH_LIST": [
            "NVDA", "AAPL", "MSFT", "TSLA", "AMZN",
            "GOOGL", "META", "QQQ", "SPY",
        ],
        "MAX_POSITION_SIZE": 0.05,  # 5% of account per position
        "MAX_OPEN_POSITIONS": 5,
        "DAILY_LOSS_LIMIT": -0.02,  # -2% daily loss limit

        # Telegram (optional — falls back to print)
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", ""),

        # API endpoints
        "ALPACA_BASE_URL": "https://paper-api.alpaca.markets"
        if os.getenv("ALPACA_PAPER", "true").lower() in ("true", "1", "yes")
        else "https://api.alpaca.markets",
        "ALPACA_DATA_URL": "https://data.alpaca.markets",
    }

    return config


def validate_config() -> bool:
    """Check that required config values are present. Log warnings if missing."""
    config = get_config()
    missing = []

    if not config["ALPACA_API_KEY"]:
        missing.append("ALPACA_API_KEY")
    if not config["ALPACA_SECRET_KEY"]:
        missing.append("ALPACA_SECRET_KEY")

    if missing:
        logger.warning(
            "Missing required configuration: %s. "
            "Create a .env file from .env.example with your Alpaca API keys.",
            ", ".join(missing),
        )
        return False

    logger.info(
        "Configuration validated — trading on %s",
        "paper" if config["ALPACA_PAPER"] else "LIVE",
    )
    return True


# Pre-built config singleton for simple imports
CFG = get_config()