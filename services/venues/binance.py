"""Binance Spot venue adapter (REST, HMAC-SHA256 signed).

It translates OMS orders and reports, and never decides. The rules:

- **Unknown outcomes:** a 5xx, a timeout, a dropped connection or the codes
  -1006/-1007 mean the order may or may not exist, so the call raises and the
  OMS marks the order UNKNOWN. Any other 4xx is a definite rejection.
- **Order identity:** the OMS client order id is sent as `newClientOrderId` and
  every query or cancel uses it, so an order is never resent to resolve doubt.
- **Fees** come from the fills (`newOrderRespType=FULL`, or `myTrades` on
  query) in the asset Binance charged; the ledger posts them in that asset.
- **Environments:** TESTNET holds no real money. LIVE reports
  `real_money_execution=true`, so the OMS refuses it until a live scope is
  certified (Phase 6).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from services.execution import (
    ExecutionReport,
    OrderStatus,
    OrderType,
    TimeInForce,
    TradableInstrument,
    VenueId,
)
from services.execution.venue import VenueOrder
from services.market_data.instruments import AssetClass, SessionType

from .credentials import VenueCredentials, VenueEnvironment

logger = logging.getLogger("proto.venues.binance")

BASE_URLS = {
    VenueEnvironment.TESTNET: "https://testnet.binance.vision",
    VenueEnvironment.LIVE: "https://api.binance.com",
}
_CLIENT_ID = re.compile(r"^[.A-Z:/a-z0-9_-]{1,36}$")
_UNKNOWN_CODES = frozenset({-1006, -1007})
_ORDER_DOES_NOT_EXIST = -2013
_UNKNOWN_ORDER_ON_CANCEL = -2011
_STATUS = {
    "PENDING_NEW": OrderStatus.ACKNOWLEDGED,
    "NEW": OrderStatus.ACKNOWLEDGED,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELED,
    "REJECTED": OrderStatus.REJECTED,
    # IOC remainders and self-trade prevention expire: terminal, like a cancel.
    "EXPIRED": OrderStatus.CANCELED,
    "EXPIRED_IN_MATCH": OrderStatus.CANCELED,
}


class BinanceError(Exception):
    """A definite answer from Binance: the request was refused."""

    def __init__(self, status: int, code: int | None, message: str) -> None:
        super().__init__(f"binance {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class VenueOutcomeUnknown(Exception):
    """The request may or may not have taken effect at the venue."""


class KeyNotTradeOnly(Exception):
    """The API key can do more than trade spot from a fixed IP."""


def symbol_of(instrument_id: str) -> str:
    venue, _, symbol = instrument_id.partition(":")
    if venue != VenueId.BINANCE_SPOT.value or not symbol:
        raise ValueError(f"not a Binance spot instrument: {instrument_id}")
    return symbol


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _ms(value: object, fallback: datetime) -> datetime:
    if isinstance(value, int) and value > 0:
        return datetime.fromtimestamp(value / 1000, UTC)
    return fallback


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BinanceSpotAdapter:
    def __init__(
        self,
        credentials: VenueCredentials,
        *,
        instruments: Mapping[str, TradableInstrument],
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] = _utc_now,
        recv_window_ms: int = 5000,
    ) -> None:
        if credentials.venue is not VenueId.BINANCE_SPOT:
            raise ValueError("credentials belong to another venue")
        self._credentials = credentials
        self._base = BASE_URLS[credentials.environment]
        self._instruments = {
            k: v for k, v in instruments.items() if v.venue is VenueId.BINANCE_SPOT
        }
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._clock = clock
        self._recv_window = recv_window_ms
        self._offset_ms: int | None = None

    @property
    def venue(self) -> VenueId:
        return VenueId.BINANCE_SPOT

    @property
    def environment(self) -> VenueEnvironment:
        return self._credentials.environment

    @property
    def real_money_execution(self) -> bool:
        return self._credentials.environment is VenueEnvironment.LIVE

    @property
    def instruments(self) -> dict[str, TradableInstrument]:
        """Grids loaded from exchangeInfo; the OMS must use exactly these."""

        return dict(self._instruments)

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- transport ------------------------------------------------------------

    async def _send(self, method: str, path: str, *, params=None, headers=None) -> Any:
        # Messages name the endpoint only: a signed path carries the query and its signature,
        # which must never reach logs, order reasons or the journal.
        name = path.split("?", 1)[0]
        try:
            response = await self._client.request(
                method,
                f"{self._base}{path}",
                params=params,
                headers=headers,
                follow_redirects=False,
            )
        except httpx.TransportError as exc:  # timeouts, resets, DNS
            raise VenueOutcomeUnknown(f"{method} {name}: {type(exc).__name__}") from exc
        if response.status_code >= 500 or 300 <= response.status_code < 400:
            raise VenueOutcomeUnknown(f"{method} {name}: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise VenueOutcomeUnknown(f"{method} {name}: unreadable response") from exc
        if response.status_code >= 400:
            code = body.get("code") if isinstance(body, dict) else None
            message = str(body.get("msg", "")) if isinstance(body, dict) else ""
            if code in _UNKNOWN_CODES:
                raise VenueOutcomeUnknown(f"{method} {name}: {code} {message}")
            raise BinanceError(response.status_code, code, message)
        return body

    async def _sync_clock(self) -> None:
        body = await self._send("GET", "/api/v3/time")
        local = int(self._clock().timestamp() * 1000)
        self._offset_ms = int(body["serverTime"]) - local

    async def _signed(self, method: str, path: str, params: dict[str, str]) -> Any:
        if self._offset_ms is None:
            await self._sync_clock()
        assert self._offset_ms is not None
        timestamp = int(self._clock().timestamp() * 1000) + self._offset_ms
        query = urlencode(
            {**params, "recvWindow": str(self._recv_window), "timestamp": str(timestamp)}
        )
        signature = hmac.new(
            self._credentials.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        try:
            return await self._send(
                method,
                f"{path}?{query}&signature={signature}",
                headers={"X-MBX-APIKEY": self._credentials.api_key},
            )
        except BinanceError as exc:
            if exc.code == -1021:  # timestamp outside recvWindow: resync next time
                self._offset_ms = None
            raise

    # --- venue contract ---------------------------------------------------------

    async def submit(self, order: VenueOrder) -> ExecutionReport:
        if not _CLIENT_ID.match(order.client_order_id):
            raise ValueError("client_order_id is not valid for Binance")
        if order.instrument_id not in self._instruments:
            return self._rejected(order, "instrument not loaded from exchangeInfo")
        if order.time_in_force is TimeInForce.DAY:
            return self._rejected(order, "DAY time in force is not offered by Binance spot")
        params = {
            "symbol": symbol_of(order.instrument_id),
            "side": order.side,
            "type": order.order_type.value,
            "quantity": _fmt(order.quantity),
            "newClientOrderId": order.client_order_id,
            "newOrderRespType": "FULL",
        }
        if order.order_type is OrderType.LIMIT:
            assert order.limit_price is not None
            params["price"] = _fmt(order.limit_price)
            params["timeInForce"] = order.time_in_force.value
        try:
            body = await self._signed("POST", "/api/v3/order", params)
        except BinanceError as exc:
            if "duplicate" in exc.message.lower():
                # The id already exists at the venue: report what it holds.
                existing = await self.query(
                    order.client_order_id, instrument_id=order.instrument_id
                )
                if existing is not None:
                    return existing
            return self._rejected(order, str(exc))
        return self._report(body, self._fees(body.get("fills", ())))

    async def query(
        self, client_order_id: str, *, instrument_id: str | None = None
    ) -> ExecutionReport | None:
        symbol = symbol_of(self._require(instrument_id))
        try:
            body = await self._signed(
                "GET", "/api/v3/order", {"symbol": symbol, "origClientOrderId": client_order_id}
            )
        except BinanceError as exc:
            if exc.code == _ORDER_DOES_NOT_EXIST:
                return None
            raise
        fills: list[dict] = []
        if Decimal(str(body.get("executedQty", "0"))) > 0:
            fills = await self._signed(
                "GET", "/api/v3/myTrades", {"symbol": symbol, "orderId": str(body["orderId"])}
            )
        return self._report(body, self._fees(fills))

    async def cancel(
        self, client_order_id: str, *, instrument_id: str | None = None
    ) -> ExecutionReport:
        symbol = symbol_of(self._require(instrument_id))
        try:
            await self._signed(
                "DELETE",
                "/api/v3/order",
                {"symbol": symbol, "origClientOrderId": client_order_id},
            )
        except BinanceError as exc:
            if exc.code != _UNKNOWN_ORDER_ON_CANCEL:
                raise
            # Already terminal (filled, expired) or never placed: report the truth.
        report = await self.query(client_order_id, instrument_id=instrument_id)
        if report is None:
            raise LookupError(f"{client_order_id} does not exist at Binance")
        return report

    async def balances(self) -> dict[str, Decimal]:
        body = await self._signed("GET", "/api/v3/account", {"omitZeroBalances": "true"})
        totals: dict[str, Decimal] = {}
        for row in body.get("balances", ()):
            total = Decimal(str(row["free"])) + Decimal(str(row["locked"]))
            if total:
                totals[row["asset"]] = total
        return totals

    # --- account safety -----------------------------------------------------------

    async def verify_trade_only(self) -> dict[str, bool]:
        """Refuse keys that can withdraw, transfer, use margin/futures or any IP.

        Required before a LIVE key is used. The testnet has no such endpoint
        and its keys cannot move real funds.
        """

        if self.environment is not VenueEnvironment.LIVE:
            raise RuntimeError("API restrictions exist only on the live venue")
        body = await self._signed("GET", "/sapi/v1/account/apiRestrictions", {})
        must_be_false = (
            "enableWithdrawals",
            "enableInternalTransfer",
            "permitsUniversalTransfer",
            "enableMargin",
            "enableFutures",
        )
        problems = [flag for flag in must_be_false if body.get(flag)]
        if not body.get("ipRestrict"):
            problems.append("ipRestrict disabled")
        if not body.get("enableSpotAndMarginTrading"):
            problems.append("spot trading disabled")
        if problems:
            raise KeyNotTradeOnly(", ".join(problems))
        return {flag: bool(body.get(flag)) for flag in (*must_be_false, "ipRestrict")}

    # --- helpers --------------------------------------------------------------------

    def _require(self, instrument_id: str | None) -> str:
        if instrument_id is None:
            raise ValueError("Binance needs the instrument to address an order")
        return instrument_id

    def _fees(self, fills: Iterable[dict]) -> dict[str, Decimal]:
        fees: dict[str, Decimal] = defaultdict(Decimal)
        for fill in fills:
            commission = Decimal(str(fill.get("commission", "0")))
            if commission:
                fees[str(fill["commissionAsset"])] += commission
        return dict(fees)

    def _report(self, body: dict, fees: dict[str, Decimal]) -> ExecutionReport:
        if len(fees) > 1:
            # One order, one fee asset: anything else needs a ledger change first.
            raise VenueOutcomeUnknown(f"mixed fee assets {sorted(fees)}")
        status_name = str(body["status"])
        filled = Decimal(str(body.get("executedQty", "0")))
        quote = Decimal(str(body.get("cummulativeQuoteQty", "0")))
        status = _STATUS.get(status_name)
        if status_name == "PENDING_CANCEL":
            status = OrderStatus.PARTIALLY_FILLED if filled else OrderStatus.ACKNOWLEDGED
        if status is None:
            raise VenueOutcomeUnknown(f"unmapped Binance status {status_name}")
        if status is OrderStatus.ACKNOWLEDGED and filled:
            status = OrderStatus.PARTIALLY_FILLED
        [(fee_asset, fee)] = fees.items() if fees else [(None, Decimal("0"))]
        symbol = str(body["symbol"])
        return ExecutionReport(
            venue=VenueId.BINANCE_SPOT,
            client_order_id=str(body["clientOrderId"]),
            venue_order_id=str(body["orderId"]),
            instrument_id=f"{VenueId.BINANCE_SPOT.value}:{symbol}",
            side=body["side"],
            status=status,
            quantity=Decimal(str(body["origQty"])),
            filled_quantity=filled,
            average_price=(quote / filled) if filled else None,
            fee=fee,
            fee_asset=fee_asset,
            reason=status_name if status is OrderStatus.CANCELED else None,
            reported_at=_ms(body.get("updateTime") or body.get("transactTime"), self._clock()),
        )

    def _rejected(self, order: VenueOrder, reason: str) -> ExecutionReport:
        return ExecutionReport(
            venue=VenueId.BINANCE_SPOT,
            client_order_id=order.client_order_id,
            instrument_id=order.instrument_id,
            side=order.side,
            status=OrderStatus.REJECTED,
            quantity=order.quantity,
            filled_quantity=Decimal("0"),
            reason=reason[:200],
            reported_at=self._clock(),
        )


async def load_instruments(
    symbols: Iterable[str],
    *,
    environment: VenueEnvironment,
    client: httpx.AsyncClient | None = None,
) -> dict[str, TradableInstrument]:
    """Order grids straight from Binance `exchangeInfo` (public, unsigned).

    Symbols that are missing or not TRADING are left out, so the OMS rejects
    their orders instead of trusting an approximation.
    """

    owned = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    url = f"{BASE_URLS[environment]}/api/v3/exchangeInfo"
    try:
        response = await http.get(
            url, params={"symbols": json.dumps(sorted(set(symbols)), separators=(",", ":"))}
        )
        if response.status_code == 400:
            # Binance refuses the whole list when one symbol does not exist there (-1121): ask
            # for each symbol alone and leave out only the ones it does not list.
            rows: list[dict] = []
            for symbol in sorted(set(symbols)):
                single = await http.get(url, params={"symbol": symbol})
                if single.status_code == 400:
                    logger.warning("%s is not listed on Binance; left out", symbol)
                    continue
                single.raise_for_status()
                rows.extend(single.json().get("symbols", ()))
            body = {"symbols": rows}
        else:
            response.raise_for_status()
            body = response.json()
    finally:
        if owned:
            await http.aclose()

    instruments: dict[str, TradableInstrument] = {}
    for row in body.get("symbols", ()):
        if row.get("status") != "TRADING":
            logger.warning("%s is not trading on Binance; left out", row.get("symbol"))
            continue
        filters = {f["filterType"]: f for f in row.get("filters", ())}
        notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
        try:
            instrument = TradableInstrument(
                instrument_id=f"{VenueId.BINANCE_SPOT.value}:{row['symbol']}",
                venue=VenueId.BINANCE_SPOT,
                asset_class=AssetClass.CRYPTO,
                base_asset=row["baseAsset"],
                quote_asset=row["quoteAsset"],
                tick_size=Decimal(filters["PRICE_FILTER"]["tickSize"]).normalize(),
                lot_size=Decimal(filters["LOT_SIZE"]["stepSize"]).normalize(),
                min_quantity=Decimal(filters["LOT_SIZE"]["minQty"]).normalize(),
                min_notional=Decimal(notional["minNotional"]).normalize(),
                session_type=SessionType.CONTINUOUS_24_7,
                timezone="UTC",
            )
        except (KeyError, ValueError) as exc:
            logger.warning("%s has an unusable grid (%s); left out", row.get("symbol"), exc)
            continue
        instruments[instrument.instrument_id] = instrument
    return instruments
