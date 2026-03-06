"""Real-time price streaming via WebSockets.

Public exports
--------------
ConnectionManager  – tracks clients and their symbol subscriptions
PriceFeed          – background task that fetches prices and broadcasts them
"""
from streaming.manager import ConnectionManager
from streaming.price_feed import PriceFeed

__all__ = ["ConnectionManager", "PriceFeed"]
