"""Alpaca broker integration.

Uses the official alpaca-py SDK (v0.43+).
Docs: https://docs.alpaca.markets/
"""
from alpaca.trading.client import TradingClient
from alpaca.trading.stream import TradingStream
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.live import StockDataStream, CryptoDataStream

from config import settings


def get_trading_client() -> TradingClient:
    """REST client for orders, positions, account."""
    return TradingClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
        paper=settings.alpaca_paper,
    )


def get_trading_stream() -> TradingStream:
    """WebSocket stream for order/trade updates."""
    return TradingStream(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
        paper=settings.alpaca_paper,
    )


def get_stock_data_client() -> StockHistoricalDataClient:
    """REST client for historical stock/ETF bars, quotes, trades."""
    return StockHistoricalDataClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
    )


def get_crypto_data_client() -> CryptoHistoricalDataClient:
    """REST client for historical crypto bars, quotes, trades."""
    return CryptoHistoricalDataClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
    )


def get_stock_stream() -> StockDataStream:
    """WebSocket stream for real-time stock/ETF market data."""
    return StockDataStream(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
    )


def get_crypto_stream() -> CryptoDataStream:
    """WebSocket stream for real-time crypto market data."""
    return CryptoDataStream(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
    )
