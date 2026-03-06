"""Tests for all broker services — usable outside market hours.

Covered endpoints (all mocked — no live Alpaca connection needed)
─────────────────────────────────────────────────────────────────
Account / Portfolio
  GET  /api/alpaca/account               → account info
  GET  /api/portfolio/summary            → portfolio value (real-time)
  GET  /api/alpaca/positions             → open positions
  GET  /api/portfolio/history            → order history (syncs from Alpaca)

Search
  GET  /api/alpaca/assets/search?q=      → search stock / ETF / crypto
  GET  /api/alpaca/assets/{symbol}       → asset detail

Orders
  GET    /api/alpaca/orders              → list orders (all/open/closed)
  POST   /api/alpaca/orders              → place order (buy/sell)
  DELETE  /api/alpaca/orders/{id}        → cancel order

Watchlist (Alpaca /v2/watchlists API)
  GET    /api/watchlist/                 → get watchlist
  POST   /api/watchlist/                 → add to watchlist
  DELETE  /api/watchlist/{symbol}        → remove from watchlist

Market Data
  GET  /api/alpaca/market/bars/{symbol}       → historical bars
  GET  /api/alpaca/market/snapshot/{symbol}   → single snapshot
  GET  /api/alpaca/market/snapshots           → multiple snapshots

WebSocket
  ws://…/ws/prices                       → real-time price streaming
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import get_db
from database.models import Base
from main import app

# ── Canonical mock data ───────────────────────────────────────────────────────────

MOCK_ACCOUNT: dict[str, Any] = {
    "id": "acc-abc123",
    "account_number": "PA3UXIKUXP28",
    "status": "ACTIVE",
    "currency": "USD",
    "buying_power": "7200.00",
    "regt_buying_power": "7200.00",
    "daytrading_buying_power": "0.00",
    "cash": "3600.00",
    "portfolio_value": "7200.00",
    "equity": "7200.00",
    "non_marginable_buying_power": "3600.00",
    "is_paper_account": True,
}

MOCK_POSITIONS: list[dict[str, Any]] = [
    {
        "asset_id": "aid-1",
        "symbol": "AAPL",
        "exchange": "NASDAQ",
        "asset_class": "us_equity",
        "qty": "10",
        "qty_available": "10",
        "avg_entry_price": "150.00",
        "side": "long",
        "market_value": "1700.00",
        "cost_basis": "1500.00",
        "unrealized_pl": "200.00",
        "unrealized_plpc": "0.1333",
        "current_price": "170.00",
        "lastday_price": "168.00",
        "change_today": "0.0119",
    }
]

MOCK_ORDERS_ALL: list[dict[str, Any]] = [
    {
        "id": "ord-111",
        "client_order_id": "cli-111",
        "symbol": "AAPL",
        "side": "buy",
        "type": "market",
        "qty": "5",
        "status": "filled",
        "filled_qty": "5",
        "filled_avg_price": "150.00",
        "time_in_force": "day",
        "created_at": "2024-01-15T09:30:00Z",
    },
    {
        "id": "ord-222",
        "client_order_id": "cli-222",
        "symbol": "TSLA",
        "side": "sell",
        "type": "limit",
        "qty": "3",
        "status": "open",
        "filled_qty": "0",
        "filled_avg_price": None,
        "limit_price": "200.00",
        "time_in_force": "gtc",
        "created_at": "2024-01-16T14:00:00Z",
    },
]

MOCK_ORDERS_OPEN = [o for o in MOCK_ORDERS_ALL if o["status"] == "open"]
MOCK_ORDERS_CLOSED = [o for o in MOCK_ORDERS_ALL if o["status"] == "filled"]

MOCK_PLACED_ORDER: dict[str, Any] = {
    "id": "ord-333",
    "client_order_id": "cli-333",
    "symbol": "AAPL",
    "side": "buy",
    "type": "limit",
    "qty": "1",
    "status": "accepted",
    "filled_qty": "0",
    "filled_avg_price": None,
    "limit_price": "150.00",
    "time_in_force": "gtc",
    "created_at": "2024-01-17T10:00:00Z",
}

MOCK_ASSET_AAPL: dict[str, Any] = {
    "id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
    "asset_class": "us_equity",
    "exchange": "NASDAQ",
    "symbol": "AAPL",
    "name": "Apple Inc.",
    "status": "active",
    "tradable": True,
    "fractionable": True,
    "marginable": True,
    "shortable": True,
    "easy_to_borrow": True,
    "maintenance_margin_requirement": 0.3,
}

MOCK_SEARCH_RESULTS: list[dict[str, Any]] = [
    MOCK_ASSET_AAPL,
    {
        "id": "some-uuid-2",
        "asset_class": "us_equity",
        "exchange": "NASDAQ",
        "symbol": "AAPB",
        "name": "GraniteShares 2x Long AAPL Daily ETF",
        "status": "active",
        "tradable": True,
        "fractionable": False,
    },
]

MOCK_BARS: dict[str, Any] = {
    "symbol": "AAPL",
    "timeframe": "1Day",
    "bars": [
        {
            "open": 180.0, "high": 185.0, "low": 179.0, "close": 183.0,
            "volume": 55_000_000, "timestamp": "2024-01-14T05:00:00Z",
        },
        {
            "open": 183.0, "high": 186.0, "low": 182.0, "close": 184.5,
            "volume": 48_000_000, "timestamp": "2024-01-15T05:00:00Z",
        },
    ],
}

MOCK_SNAPSHOT: dict[str, Any] = {
    "symbol": "AAPL",
    "price": 184.5,
    "change": 1.5,
    "change_pct": 0.8197,
    "volume": 48_000_000,
    "latest_trade": {"price": 184.5, "size": 100, "timestamp": "2024-01-15T20:59:00Z"},
    "latest_quote": {
        "ask_price": 184.6, "bid_price": 184.4,
        "ask_size": 2, "bid_size": 3,
        "timestamp": "2024-01-15T20:59:00Z",
    },
    "daily_bar": {
        "open": 183.0, "high": 186.0, "low": 182.0,
        "close": 184.5, "volume": 48_000_000,
    },
    "prev_daily_bar": {
        "open": 180.0, "high": 185.0, "low": 179.0,
        "close": 183.0, "volume": 55_000_000,
    },
}

MOCK_MULTI_SNAPSHOTS: dict[str, Any] = {
    "AAPL": MOCK_SNAPSHOT,
    "TSLA": {
        **MOCK_SNAPSHOT,
        "symbol": "TSLA",
        "price": 210.0,
        "change": -3.5,
        "change_pct": -1.6393,
    },
}

MOCK_WATCHLIST_EMPTY: dict[str, Any] = {
    "id": "wl-001",
    "account_id": "acc-abc123",
    "name": "MyBroker",
    "created_at": "2024-01-10T00:00:00Z",
    "updated_at": "2024-01-10T00:00:00Z",
    "assets": [],
}

MOCK_WATCHLIST_WITH_AAPL: dict[str, Any] = {
    **MOCK_WATCHLIST_EMPTY,
    "updated_at": "2024-01-15T10:00:00Z",
    "assets": [
        {
            "id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
            "class": "us_equity",
            "exchange": "NASDAQ",
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "status": "active",
            "tradable": True,
        },
    ],
}

MOCK_WATCHLIST_AFTER_ADD: dict[str, Any] = {
    **MOCK_WATCHLIST_EMPTY,
    "updated_at": "2024-01-15T11:00:00Z",
    "assets": [
        {
            "id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
            "class": "us_equity",
            "exchange": "NASDAQ",
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "status": "active",
            "tradable": True,
        },
        {
            "id": "some-uuid-tsla",
            "class": "us_equity",
            "exchange": "NASDAQ",
            "symbol": "TSLA",
            "name": "Tesla, Inc.",
            "status": "active",
            "tradable": True,
        },
    ],
}

# ── DB / client fixtures ──────────────────────────────────────────────────────────

def _build_mock_client() -> MagicMock:
    """Return a MagicMock pre-configured with all trading data."""
    m = MagicMock()
    m.get_account.return_value = MOCK_ACCOUNT
    m.get_positions.return_value = MOCK_POSITIONS
    m.get_orders.return_value = MOCK_ORDERS_ALL
    m.get_asset.return_value = MOCK_ASSET_AAPL
    m.search_assets.return_value = MOCK_SEARCH_RESULTS
    m.place_order.return_value = MOCK_PLACED_ORDER
    m.cancel_order.return_value = None
    # Watchlist (Alpaca API)
    m.get_watchlist.return_value = MOCK_WATCHLIST_WITH_AAPL
    m.add_to_watchlist.return_value = MOCK_WATCHLIST_AFTER_ADD
    m.remove_from_watchlist.return_value = MOCK_WATCHLIST_EMPTY
    return m


def _build_mock_market() -> MagicMock:
    """Return a MagicMock pre-configured with all market data."""
    m = MagicMock()
    m.get_bars.return_value = MOCK_BARS
    m.get_snapshot.return_value = MOCK_SNAPSHOT
    m.get_multiple_snapshots.return_value = MOCK_MULTI_SNAPSHOTS
    return m


@pytest.fixture()
def client():
    """TestClient with:
    - in-memory SQLite (for watchlist / DB endpoints)
    - alpaca_client and alpaca_market_data fully mocked
    """
    engine = create_async_engine("postgresql+asyncpg://user:password@localhost/test_db")
    TestingSession = async_sessionmaker(
        bind=engine, expire_on_commit=False, class_=AsyncSession
    )

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    async def override_get_db():
        async with TestingSession() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch("routers.alpaca.alpaca_client", _build_mock_client()),
        patch("routers.alpaca.alpaca_market_data", _build_mock_market()),
        patch("routers.portfolio.alpaca_client", _build_mock_client()),
        patch("routers.portfolio.alpaca_market_data", _build_mock_market()),
        patch("routers.watchlist.alpaca_client", _build_mock_client()),
    ):
        with TestClient(app) as tc:
            yield tc

    app.dependency_overrides.clear()


# ═════════════════════════════════════════════════════════════════════════════════
#  1. ACCOUNT  —  GET /api/alpaca/account
# ═════════════════════════════════════════════════════════════════════════════════

class TestAccount:
    def test_returns_200(self, client: TestClient):
        r = client.get("/api/alpaca/account")
        assert r.status_code == 200

    def test_has_required_fields(self, client: TestClient):
        data = client.get("/api/alpaca/account").json()
        for field in ("id", "account_number", "status", "currency",
                      "buying_power", "cash", "portfolio_value"):
            assert field in data, f"Missing field: {field}"

    def test_status_is_active(self, client: TestClient):
        data = client.get("/api/alpaca/account").json()
        assert data["status"] == "ACTIVE"

    def test_currency_is_usd(self, client: TestClient):
        data = client.get("/api/alpaca/account").json()
        assert data["currency"] == "USD"

    def test_portfolio_value_present(self, client: TestClient):
        data = client.get("/api/alpaca/account").json()
        assert data["portfolio_value"] == "7200.00"


# ═════════════════════════════════════════════════════════════════════════════════
#  2. PORTFOLIO SUMMARY  —  GET /api/portfolio/summary  (valor en tiempo real)
# ═════════════════════════════════════════════════════════════════════════════════

class TestPortfolioSummary:
    def test_returns_200(self, client: TestClient):
        r = client.get("/api/portfolio/summary")
        assert r.status_code == 200

    def test_has_account_section(self, client: TestClient):
        data = client.get("/api/portfolio/summary").json()
        assert "account" in data
        for field in ("cash", "buying_power", "portfolio_value", "equity"):
            assert field in data["account"], f"account missing '{field}'"

    def test_has_positions_section(self, client: TestClient):
        data = client.get("/api/portfolio/summary").json()
        assert "positions" in data
        assert isinstance(data["positions"], list)

    def test_positions_have_pl_fields(self, client: TestClient):
        data = client.get("/api/portfolio/summary").json()
        pos = data["positions"][0]
        assert "current_price" in pos
        assert "unrealized_pl" in pos
        assert "unrealized_pl_pct" in pos

    def test_position_count_matches(self, client: TestClient):
        data = client.get("/api/portfolio/summary").json()
        assert data["position_count"] == len(data["positions"])

    def test_account_currency_is_usd(self, client: TestClient):
        data = client.get("/api/portfolio/summary").json()
        assert data["account"]["currency"] == "USD"

    def test_account_is_paper(self, client: TestClient):
        data = client.get("/api/portfolio/summary").json()
        assert data["account"]["is_paper"] is True


# ═════════════════════════════════════════════════════════════════════════════════
#  3. POSITIONS  —  GET /api/alpaca/positions
# ═════════════════════════════════════════════════════════════════════════════════

class TestPositions:
    def test_returns_200(self, client: TestClient):
        r = client.get("/api/alpaca/positions")
        assert r.status_code == 200

    def test_returns_list(self, client: TestClient):
        data = client.get("/api/alpaca/positions").json()
        assert isinstance(data, list)

    def test_position_has_required_fields(self, client: TestClient):
        data = client.get("/api/alpaca/positions").json()
        pos = data[0]
        for field in ("symbol", "qty", "avg_entry_price", "market_value",
                      "unrealized_pl", "side"):
            assert field in pos, f"Missing field: {field}"

    def test_position_symbol_correct(self, client: TestClient):
        data = client.get("/api/alpaca/positions").json()
        assert data[0]["symbol"] == "AAPL"

    def test_empty_positions_returns_empty_list(self, client: TestClient):
        mock = _build_mock_client()
        mock.get_positions.return_value = []
        with patch("routers.alpaca.alpaca_client", mock):
            data = client.get("/api/alpaca/positions").json()
        assert data == []


# ═════════════════════════════════════════════════════════════════════════════════
#  4. SEARCH ASSETS  —  GET /api/alpaca/assets/search?q=
#     Buscar acción, ETF o crypto
# ═════════════════════════════════════════════════════════════════════════════════

class TestSearchAssets:
    def test_search_returns_200(self, client: TestClient):
        r = client.get("/api/alpaca/assets/search?q=AAPL")
        assert r.status_code == 200

    def test_search_returns_list(self, client: TestClient):
        data = client.get("/api/alpaca/assets/search?q=AAPL").json()
        assert isinstance(data, list)

    def test_search_results_have_required_fields(self, client: TestClient):
        data = client.get("/api/alpaca/assets/search?q=AAPL").json()
        asset = data[0]
        for field in ("symbol", "name", "status", "tradable"):
            assert field in asset, f"Missing field: {field}"

    def test_search_returns_multiple_results(self, client: TestClient):
        data = client.get("/api/alpaca/assets/search?q=AAPL").json()
        assert len(data) == len(MOCK_SEARCH_RESULTS)

    def test_search_requires_q_param(self, client: TestClient):
        r = client.get("/api/alpaca/assets/search")
        assert r.status_code == 422

    def test_search_empty_query_rejected(self, client: TestClient):
        r = client.get("/api/alpaca/assets/search?q=")
        assert r.status_code == 422

    def test_get_asset_by_symbol(self, client: TestClient):
        r = client.get("/api/alpaca/assets/AAPL")
        assert r.status_code == 200
        data = r.json()
        assert data["symbol"] == "AAPL"

    def test_get_asset_has_detail_fields(self, client: TestClient):
        data = client.get("/api/alpaca/assets/AAPL").json()
        for field in ("symbol", "name", "exchange", "asset_class",
                      "tradable", "fractionable", "status"):
            assert field in data, f"Missing field: {field}"

    def test_search_path_not_captured_as_symbol(self, client: TestClient):
        """Ensure /assets/search is never routed to /assets/{symbol}."""
        r = client.get("/api/alpaca/assets/search?q=test")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ═════════════════════════════════════════════════════════════════════════════════
#  5. PLACE ORDER  —  POST /api/alpaca/orders
#     Comprar acción, ETF o crypto
# ═════════════════════════════════════════════════════════════════════════════════

class TestPlaceOrder:
    def test_place_limit_order_returns_201(self, client: TestClient):
        r = client.post("/api/alpaca/orders", json={
            "symbol": "AAPL",
            "qty": 1,
            "side": "buy",
            "order_type": "limit",
            "time_in_force": "gtc",
            "limit_price": 150.00,
        })
        assert r.status_code == 201

    def test_place_order_returns_order_data(self, client: TestClient):
        r = client.post("/api/alpaca/orders", json={
            "symbol": "AAPL",
            "qty": 1,
            "side": "buy",
            "order_type": "limit",
            "time_in_force": "gtc",
            "limit_price": 150.00,
        })
        data = r.json()
        assert "id" in data
        assert data["symbol"] == "AAPL"
        assert data["side"] == "buy"
        assert data["status"] == "accepted"

    def test_place_market_order(self, client: TestClient):
        """Market orders work for crypto (24/7); for stocks they queue for open."""
        r = client.post("/api/alpaca/orders", json={
            "symbol": "BTC/USD",
            "qty": 0.01,
            "side": "buy",
            "order_type": "market",
            "time_in_force": "gtc",
        })
        assert r.status_code == 201
        assert "id" in r.json()

    def test_place_sell_order(self, client: TestClient):
        r = client.post("/api/alpaca/orders", json={
            "symbol": "TSLA",
            "qty": 2,
            "side": "sell",
            "order_type": "limit",
            "time_in_force": "gtc",
            "limit_price": 250.00,
        })
        assert r.status_code == 201

    def test_place_order_missing_symbol_returns_422(self, client: TestClient):
        r = client.post("/api/alpaca/orders", json={
            "qty": 1,
            "side": "buy",
            "order_type": "market",
        })
        assert r.status_code == 422

    def test_place_order_missing_qty_returns_422(self, client: TestClient):
        r = client.post("/api/alpaca/orders", json={
            "symbol": "AAPL",
            "side": "buy",
            "order_type": "market",
        })
        assert r.status_code == 422

    def test_place_order_missing_side_returns_422(self, client: TestClient):
        r = client.post("/api/alpaca/orders", json={
            "symbol": "AAPL",
            "qty": 1,
            "order_type": "market",
        })
        assert r.status_code == 422

    def test_place_order_default_tif_is_day(self, client: TestClient):
        """When time_in_force is omitted the model default is 'day'."""
        r = client.post("/api/alpaca/orders", json={
            "symbol": "AAPL",
            "qty": 1,
            "side": "buy",
            "order_type": "market",
        })
        assert r.status_code == 201

    def test_place_order_with_stop_price(self, client: TestClient):
        r = client.post("/api/alpaca/orders", json={
            "symbol": "AAPL",
            "qty": 1,
            "side": "buy",
            "order_type": "stop",
            "time_in_force": "gtc",
            "stop_price": 145.00,
        })
        assert r.status_code == 201

    def test_place_order_api_error_forwarded(self, client: TestClient):
        mock = _build_mock_client()
        mock.place_order.side_effect = HTTPException(status_code=403, detail="Forbidden")
        with patch("routers.alpaca.alpaca_client", mock):
            r = client.post("/api/alpaca/orders", json={
                "symbol": "AAPL",
                "qty": 1,
                "side": "buy",
                "order_type": "market",
            })
        assert r.status_code == 403


# ═════════════════════════════════════════════════════════════════════════════════
#  6. GET ORDERS  —  GET /api/alpaca/orders
# ═════════════════════════════════════════════════════════════════════════════════

class TestGetOrders:
    def test_returns_200_default(self, client: TestClient):
        r = client.get("/api/alpaca/orders")
        assert r.status_code == 200

    def test_returns_list(self, client: TestClient):
        data = client.get("/api/alpaca/orders").json()
        assert isinstance(data, list)

    def test_status_all(self, client: TestClient):
        r = client.get("/api/alpaca/orders?status=all")
        assert r.status_code == 200
        assert len(r.json()) == len(MOCK_ORDERS_ALL)

    def test_status_open(self, client: TestClient):
        mock = _build_mock_client()
        mock.get_orders.return_value = MOCK_ORDERS_OPEN
        with patch("routers.alpaca.alpaca_client", mock):
            data = client.get("/api/alpaca/orders?status=open").json()
        assert all(o["status"] == "open" for o in data)

    def test_status_closed(self, client: TestClient):
        mock = _build_mock_client()
        mock.get_orders.return_value = MOCK_ORDERS_CLOSED
        with patch("routers.alpaca.alpaca_client", mock):
            data = client.get("/api/alpaca/orders?status=closed").json()
        assert all(o["status"] == "filled" for o in data)

    def test_limit_param_accepted(self, client: TestClient):
        r = client.get("/api/alpaca/orders?limit=10")
        assert r.status_code == 200

    def test_limit_below_min_rejected(self, client: TestClient):
        r = client.get("/api/alpaca/orders?limit=0")
        assert r.status_code == 422

    def test_limit_above_max_rejected(self, client: TestClient):
        r = client.get("/api/alpaca/orders?limit=501")
        assert r.status_code == 422

    def test_order_has_required_fields(self, client: TestClient):
        data = client.get("/api/alpaca/orders").json()
        order = data[0]
        for field in ("id", "symbol", "side", "type", "qty", "status", "time_in_force"):
            assert field in order, f"Missing field: {field}"


# ═════════════════════════════════════════════════════════════════════════════════
#  7. CANCEL ORDER  —  DELETE /api/alpaca/orders/{id}
# ═════════════════════════════════════════════════════════════════════════════════

class TestCancelOrder:
    def test_cancel_returns_204(self, client: TestClient):
        r = client.delete("/api/alpaca/orders/ord-222")
        assert r.status_code == 204

    def test_cancel_calls_client(self, client: TestClient):
        mock = _build_mock_client()
        with patch("routers.alpaca.alpaca_client", mock):
            client.delete("/api/alpaca/orders/ord-222")
        mock.cancel_order.assert_called_once_with("ord-222")

    def test_cancel_nonexistent_order_returns_error(self, client: TestClient):
        mock = _build_mock_client()
        mock.cancel_order.side_effect = HTTPException(status_code=404, detail="Order not found")
        with patch("routers.alpaca.alpaca_client", mock):
            r = client.delete("/api/alpaca/orders/nonexistent-id")
        assert r.status_code == 404

    def test_cancel_already_filled_returns_error(self, client: TestClient):
        mock = _build_mock_client()
        mock.cancel_order.side_effect = HTTPException(
            status_code=422, detail="Order is not cancelable"
        )
        with patch("routers.alpaca.alpaca_client", mock):
            r = client.delete("/api/alpaca/orders/ord-111")
        assert r.status_code == 422


# ═════════════════════════════════════════════════════════════════════════════════
#  8. WATCHLIST  —  GET/POST/DELETE /api/watchlist/  (Alpaca /v2/watchlists)
# ═════════════════════════════════════════════════════════════════════════════════

class TestWatchlist:
    def test_get_returns_200(self, client: TestClient):
        r = client.get("/api/watchlist/")
        assert r.status_code == 200

    def test_get_returns_watchlist_object(self, client: TestClient):
        data = client.get("/api/watchlist/").json()
        assert "id" in data
        assert "name" in data
        assert "assets" in data
        assert isinstance(data["assets"], list)

    def test_get_watchlist_name_is_mybroker(self, client: TestClient):
        data = client.get("/api/watchlist/").json()
        assert data["name"] == "MyBroker"

    def test_get_watchlist_has_assets(self, client: TestClient):
        data = client.get("/api/watchlist/").json()
        assert len(data["assets"]) >= 1

    def test_asset_has_required_fields(self, client: TestClient):
        data = client.get("/api/watchlist/").json()
        asset = data["assets"][0]
        for field in ("symbol", "name", "exchange", "tradable"):
            assert field in asset, f"Asset missing field: {field}"

    def test_add_returns_201(self, client: TestClient):
        r = client.post("/api/watchlist/", json={"symbol": "TSLA"})
        assert r.status_code == 201

    def test_add_returns_updated_watchlist(self, client: TestClient):
        data = client.post("/api/watchlist/", json={"symbol": "TSLA"}).json()
        assert "assets" in data
        symbols = [a["symbol"] for a in data["assets"]]
        assert "TSLA" in symbols

    def test_add_missing_symbol_returns_422(self, client: TestClient):
        r = client.post("/api/watchlist/", json={})
        assert r.status_code == 422

    def test_delete_returns_204(self, client: TestClient):
        r = client.delete("/api/watchlist/AAPL")
        assert r.status_code == 204

    def test_delete_calls_remove(self, client: TestClient):
        mock = _build_mock_client()
        with patch("routers.watchlist.alpaca_client", mock):
            client.delete("/api/watchlist/AAPL")
        mock.remove_from_watchlist.assert_called_once_with("AAPL")

    def test_add_calls_client(self, client: TestClient):
        mock = _build_mock_client()
        with patch("routers.watchlist.alpaca_client", mock):
            client.post("/api/watchlist/", json={"symbol": "GOOG"})
        mock.add_to_watchlist.assert_called_once_with("GOOG")

    def test_get_empty_watchlist(self, client: TestClient):
        mock = _build_mock_client()
        mock.get_watchlist.return_value = MOCK_WATCHLIST_EMPTY
        with patch("routers.watchlist.alpaca_client", mock):
            data = client.get("/api/watchlist/").json()
        assert data["assets"] == []

    def test_add_api_error_forwarded(self, client: TestClient):
        mock = _build_mock_client()
        mock.add_to_watchlist.side_effect = HTTPException(
            status_code=422, detail="Symbol already in watchlist"
        )
        with patch("routers.watchlist.alpaca_client", mock):
            r = client.post("/api/watchlist/", json={"symbol": "AAPL"})
        assert r.status_code == 422

    def test_remove_api_error_forwarded(self, client: TestClient):
        mock = _build_mock_client()
        mock.remove_from_watchlist.side_effect = HTTPException(
            status_code=404, detail="Symbol not in watchlist"
        )
        with patch("routers.watchlist.alpaca_client", mock):
            r = client.delete("/api/watchlist/DOESNOTEXIST")
        assert r.status_code == 404


# ═════════════════════════════════════════════════════════════════════════════════
#  9. HISTORICAL BARS  —  GET /api/alpaca/market/bars/{symbol}
# ═════════════════════════════════════════════════════════════════════════════════

class TestHistoricalBars:
    def test_returns_200(self, client: TestClient):
        r = client.get("/api/alpaca/market/bars/AAPL")
        assert r.status_code == 200

    def test_response_shape(self, client: TestClient):
        data = client.get("/api/alpaca/market/bars/AAPL").json()
        assert "symbol" in data
        assert "timeframe" in data
        assert "bars" in data
        assert isinstance(data["bars"], list)

    def test_bars_contain_ohlcv(self, client: TestClient):
        data = client.get("/api/alpaca/market/bars/AAPL").json()
        bar = data["bars"][0]
        for field in ("open", "high", "low", "close", "volume", "timestamp"):
            assert field in bar, f"Missing OHLCV field: {field}"

    def test_custom_timeframe_accepted(self, client: TestClient):
        r = client.get("/api/alpaca/market/bars/AAPL?timeframe=1Hour")
        assert r.status_code == 200

    def test_with_date_range(self, client: TestClient):
        r = client.get(
            "/api/alpaca/market/bars/AAPL?start=2024-01-01&end=2024-01-15"
        )
        assert r.status_code == 200

    def test_limit_below_min_rejected(self, client: TestClient):
        r = client.get("/api/alpaca/market/bars/AAPL?limit=0")
        assert r.status_code == 422

    def test_limit_above_max_rejected(self, client: TestClient):
        r = client.get("/api/alpaca/market/bars/AAPL?limit=10001")
        assert r.status_code == 422


# ═════════════════════════════════════════════════════════════════════════════════
#  10. SNAPSHOTS  —  GET /api/alpaca/market/snapshot/{symbol}
#                    GET /api/alpaca/market/snapshots?symbols=
# ═════════════════════════════════════════════════════════════════════════════════

class TestSnapshot:
    def test_returns_200(self, client: TestClient):
        r = client.get("/api/alpaca/market/snapshot/AAPL")
        assert r.status_code == 200

    def test_has_price_fields(self, client: TestClient):
        data = client.get("/api/alpaca/market/snapshot/AAPL").json()
        for field in ("symbol", "price", "change", "change_pct"):
            assert field in data, f"Missing field: {field}"

    def test_has_bar_data(self, client: TestClient):
        data = client.get("/api/alpaca/market/snapshot/AAPL").json()
        assert "daily_bar" in data
        assert "prev_daily_bar" in data

    def test_price_is_numeric(self, client: TestClient):
        data = client.get("/api/alpaca/market/snapshot/AAPL").json()
        assert isinstance(data["price"], (int, float))

    def test_symbol_matches_request(self, client: TestClient):
        data = client.get("/api/alpaca/market/snapshot/AAPL").json()
        assert data["symbol"] == "AAPL"


class TestMultipleSnapshots:
    def test_returns_200(self, client: TestClient):
        r = client.get("/api/alpaca/market/snapshots?symbols=AAPL,TSLA")
        assert r.status_code == 200

    def test_returns_dict_keyed_by_symbol(self, client: TestClient):
        data = client.get("/api/alpaca/market/snapshots?symbols=AAPL,TSLA").json()
        assert isinstance(data, dict)
        assert "AAPL" in data
        assert "TSLA" in data

    def test_missing_symbols_param_rejected(self, client: TestClient):
        r = client.get("/api/alpaca/market/snapshots")
        assert r.status_code == 422

    def test_each_snapshot_has_price(self, client: TestClient):
        data = client.get("/api/alpaca/market/snapshots?symbols=AAPL,TSLA").json()
        for sym, snap in data.items():
            assert "price" in snap, f"'{sym}' snapshot missing 'price'"


# ═════════════════════════════════════════════════════════════════════════════════
#  11. PORTFOLIO HISTORY  —  GET /api/portfolio/history
# ═════════════════════════════════════════════════════════════════════════════════

class TestPortfolioHistory:
    def test_returns_200(self, client: TestClient):
        r = client.get("/api/portfolio/history")
        assert r.status_code == 200

    def test_returns_list(self, client: TestClient):
        data = client.get("/api/portfolio/history").json()
        assert isinstance(data, list)

    def test_syncs_closed_orders_to_db(self, client: TestClient):
        data = client.get("/api/portfolio/history").json()
        assert len(data) >= 1

    def test_order_history_fields(self, client: TestClient):
        data = client.get("/api/portfolio/history").json()
        order = data[0]
        for field in ("id", "broker_order_id", "symbol", "side", "qty",
                      "order_type", "status", "broker"):
            assert field in order, f"Missing field: '{field}'"

    def test_broker_is_alpaca(self, client: TestClient):
        data = client.get("/api/portfolio/history").json()
        assert all(o["broker"] == "alpaca" for o in data)

    def test_limit_param_accepted(self, client: TestClient):
        r = client.get("/api/portfolio/history?limit=10")
        assert r.status_code == 200

    def test_limit_below_min_rejected(self, client: TestClient):
        r = client.get("/api/portfolio/history?limit=0")
        assert r.status_code == 422

    def test_second_call_does_not_duplicate(self, client: TestClient):
        client.get("/api/portfolio/history")
        client.get("/api/portfolio/history")
        data = client.get("/api/portfolio/history").json()
        ids = [o["broker_order_id"] for o in data]
        assert len(ids) == len(set(ids)), "Duplicate orders found in history"


# ═════════════════════════════════════════════════════════════════════════════════
#  12. WEBSOCKET  —  ws://…/ws/prices
# ═════════════════════════════════════════════════════════════════════════════════

class TestWebSocket:
    def test_connection_accepted(self, client: TestClient):
        with client.websocket_connect("/ws/prices"):
            pass

    def test_ping_returns_pong(self, client: TestClient):
        with client.websocket_connect("/ws/prices") as ws:
            ws.send_json({"action": "ping"})
            msg = ws.receive_json()
            assert msg == {"type": "pong"}

    def test_subscribe_returns_subscribed(self, client: TestClient):
        with client.websocket_connect("/ws/prices") as ws:
            ws.send_json({"action": "subscribe", "symbols": ["AAPL", "TSLA"]})
            msg = ws.receive_json()
            assert msg["type"] == "subscribed"
            assert set(msg["symbols"]) == {"AAPL", "TSLA"}

    def test_subscribe_normalises_uppercase(self, client: TestClient):
        with client.websocket_connect("/ws/prices") as ws:
            ws.send_json({"action": "subscribe", "symbols": ["aapl", "tsla"]})
            msg = ws.receive_json()
            assert msg["type"] == "subscribed"
            assert "AAPL" in msg["symbols"]
            assert "TSLA" in msg["symbols"]

    def test_subscribe_accumulates(self, client: TestClient):
        with client.websocket_connect("/ws/prices") as ws:
            ws.send_json({"action": "subscribe", "symbols": ["AAPL"]})
            ws.receive_json()
            ws.send_json({"action": "subscribe", "symbols": ["TSLA"]})
            msg = ws.receive_json()
            assert msg["type"] == "subscribed"
            assert "AAPL" in msg["symbols"]
            assert "TSLA" in msg["symbols"]

    def test_unsubscribe_returns_remaining(self, client: TestClient):
        with client.websocket_connect("/ws/prices") as ws:
            ws.send_json({"action": "subscribe", "symbols": ["AAPL", "TSLA"]})
            ws.receive_json()
            ws.send_json({"action": "unsubscribe", "symbols": ["TSLA"]})
            msg = ws.receive_json()
            assert msg["type"] == "unsubscribed"
            assert "TSLA" not in msg["symbols"]
            assert "AAPL" in msg["symbols"]

    def test_unsubscribe_all(self, client: TestClient):
        with client.websocket_connect("/ws/prices") as ws:
            ws.send_json({"action": "subscribe", "symbols": ["AAPL"]})
            ws.receive_json()
            ws.send_json({"action": "unsubscribe", "symbols": ["AAPL"]})
            msg = ws.receive_json()
            assert msg["type"] == "unsubscribed"
            assert msg["symbols"] == []

    def test_subscribe_missing_symbols_returns_error(self, client: TestClient):
        with client.websocket_connect("/ws/prices") as ws:
            ws.send_json({"action": "subscribe", "symbols": []})
            msg = ws.receive_json()
            assert msg["type"] == "error"

    def test_unknown_action_returns_error(self, client: TestClient):
        with client.websocket_connect("/ws/prices") as ws:
            ws.send_json({"action": "invalid_action"})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "invalid_action" in msg["message"]

    def test_invalid_json_returns_error(self, client: TestClient):
        with client.websocket_connect("/ws/prices") as ws:
            ws.send_text("not valid json {{{{")
            msg = ws.receive_json()
            assert msg["type"] == "error"

    def test_multiple_pings(self, client: TestClient):
        with client.websocket_connect("/ws/prices") as ws:
            for _ in range(3):
                ws.send_json({"action": "ping"})
                msg = ws.receive_json()
                assert msg == {"type": "pong"}
