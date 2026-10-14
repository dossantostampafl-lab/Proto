"""In-process fake of the Binance Spot REST API, shared by adapter and executor tests."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit

import httpx

from services.execution import VenueId
from services.venues.credentials import VenueCredentials, VenueEnvironment

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
BTC_ID = "BINANCE_SPOT:BTCUSDT"
SECRET = "s" * 64


def creds(environment: VenueEnvironment = VenueEnvironment.TESTNET) -> VenueCredentials:
    return VenueCredentials(
        venue=VenueId.BINANCE_SPOT,
        environment=environment,
        account="main",
        api_key="key-123",
        api_secret=SECRET,
    )


EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "status": "TRADING",
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                {"filterType": "LOT_SIZE", "stepSize": "0.00001000", "minQty": "0.00001000"},
                {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
            ],
        },
        {"symbol": "ETHUSDT", "status": "BREAK", "baseAsset": "ETH", "quoteAsset": "USDT"},
    ]
}


class FakeBinance:
    """Enough of the Binance Spot REST API to exercise the adapter."""

    def __init__(self) -> None:
        self.balances = {"USDT": Decimal("10000"), "BTC": Decimal("0")}
        self.orders: dict[str, dict] = {}
        self.trades: dict[int, list[dict]] = {}
        self.bid, self.ask = Decimal("62000.00"), Decimal("62000.20")
        self.fee_asset = "BTC"
        self.submit_error: tuple[int, dict] | None = None
        self.lose_submit_response = False
        self.partial_then_expire = False
        self.restrictions: dict = {}
        self.server_time_ms = int(NOW.timestamp() * 1000) + 1500
        self.time_tolerance_ms = 0
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        params = dict(parse_qsl(urlsplit(str(request.url)).query))
        if path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": self.server_time_ms})
        if path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json=EXCHANGE_INFO)
        self.verify_signature(request)
        if path == "/api/v3/order" and request.method == "POST":
            return self.new_order(params)
        if path == "/api/v3/order" and request.method == "GET":
            order = self.orders.get(params["origClientOrderId"])
            if order is None:
                return httpx.Response(400, json={"code": -2013, "msg": "Order does not exist."})
            return httpx.Response(200, json=order)
        if path == "/api/v3/order" and request.method == "DELETE":
            order = self.orders.get(params["origClientOrderId"])
            if order is None or order["status"] != "NEW":
                return httpx.Response(400, json={"code": -2011, "msg": "Unknown order sent."})
            order["status"] = "CANCELED"
            return httpx.Response(200, json=order)
        if path == "/api/v3/myTrades":
            return httpx.Response(200, json=self.trades.get(int(params["orderId"]), []))
        if path == "/api/v3/account":
            rows = [
                {"asset": a, "free": str(v), "locked": "0"} for a, v in self.balances.items() if v
            ]
            return httpx.Response(200, json={"balances": rows})
        if path == "/sapi/v1/account/apiRestrictions":
            return httpx.Response(200, json=self.restrictions)
        return httpx.Response(404, json={"code": -1, "msg": "no route"})

    def verify_signature(self, request: httpx.Request) -> None:
        assert request.headers["X-MBX-APIKEY"] == "key-123"
        query = urlsplit(str(request.url)).query
        payload, _, signature = query.rpartition("&signature=")
        expected = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        assert hmac.compare_digest(signature, expected), "bad signature"
        stamp = int(dict(parse_qsl(payload))["timestamp"])
        drift = abs(stamp - self.server_time_ms)
        assert drift <= self.time_tolerance_ms, "timestamp not aligned to server time"

    def new_order(self, params: dict[str, str]) -> httpx.Response:
        if self.submit_error is not None:
            status, body = self.submit_error
            return httpx.Response(status, json=body)
        client_id = params["newClientOrderId"]
        if client_id in self.orders:
            return httpx.Response(400, json={"code": -2010, "msg": "Duplicate order sent."})
        quantity, price = Decimal(params["quantity"]), Decimal(params.get("price", self.ask))
        buying = params["side"] == "BUY"
        marketable = (self.ask <= price) if buying else (self.bid >= price)
        order_id = len(self.orders) + 1
        order = {
            "symbol": params["symbol"],
            "orderId": order_id,
            "clientOrderId": client_id,
            "transactTime": self.server_time_ms,
            "price": params.get("price", "0"),
            "origQty": params["quantity"],
            "executedQty": "0",
            "cummulativeQuoteQty": "0",
            "status": "NEW",
            "side": params["side"],
            "type": params["type"],
        }
        fills: list[dict] = []
        if marketable:
            filled = quantity / 2 if self.partial_then_expire else quantity
            touch = self.ask if buying else self.bid
            fee = filled * Decimal("0.001") if self.fee_asset == "BTC" else filled * touch / 1000
            fills = [
                {
                    "price": str(touch),
                    "qty": str(filled),
                    "commission": str(fee),
                    "commissionAsset": self.fee_asset,
                }
            ]
            order["executedQty"] = str(filled)
            order["cummulativeQuoteQty"] = str(filled * touch)
            order["status"] = "EXPIRED" if self.partial_then_expire else "FILLED"
            sign = 1 if buying else -1
            self.balances["BTC"] += sign * filled - (fee if self.fee_asset == "BTC" else 0)
            self.balances["USDT"] -= sign * filled * touch + (
                fee if self.fee_asset == "USDT" else 0
            )
            self.trades[order_id] = fills
        elif params.get("timeInForce") == "IOC":
            order["status"] = "EXPIRED"
        self.orders[client_id] = order
        if self.lose_submit_response:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(200, json={**order, "fills": fills})

    def fill(self, client_id: str) -> None:
        """The market trades through a resting order: fill it completely."""

        order = self.orders[client_id]
        quantity, price = Decimal(order["origQty"]), Decimal(order["price"])
        fee = quantity * Decimal("0.001")
        self.trades[order["orderId"]] = [
            {
                "price": str(price),
                "qty": str(quantity),
                "commission": str(fee),
                "commissionAsset": "BTC",
            }
        ]
        order.update(
            executedQty=str(quantity), cummulativeQuoteQty=str(quantity * price), status="FILLED"
        )
        self.balances["BTC"] += quantity - fee
        self.balances["USDT"] -= quantity * price
