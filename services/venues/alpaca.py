"""Alpaca US equities venue adapter: the PAPER account only.

Same contract and rules as the Binance and XP adapters:

- **Paper only, pinned:** every trading call goes to ``PAPER_TRADING_URL``
  (``paper-api.alpaca.markets``) and every market-data call to ``DATA_URL``. The live trading host
  is not a setting, not a parameter and not reachable from here: ``_trading_url`` refuses any
  other host, redirects are never followed and LIVE credentials are refused at construction.
  ``real_money_execution`` is always false.
- **Unknown outcomes:** a timeout, a dropped connection or a 5xx raise ``VenueOutcomeUnknown`` so
  the OMS marks the order UNKNOWN and never resends it to resolve doubt.
- **Identity:** the OMS client order id is Alpaca's ``client_order_id``; a resubmit that Alpaca
  refuses as a duplicate returns the order it already holds. Query and cancel address the order by
  that id too.
- **Fail closed on the session:** a new order is refused (REJECTED, nothing sent) unless
  ``/v2/clock`` says the market is open right now; an unreadable clock refuses as well.
- **Orders:** LIMIT only, whole shares, ``ioc`` or ``day`` (GTC would rest overnight: refused).
- **Quotes:** latest quote from the free IEX feed (``feed=iex``), one exchange, not the
  consolidated SIP tape. Each quote carries that label and Alpaca's own timestamp.
- **Secrets:** the key id and secret are sent as headers only; they are never logged, and neither
  the adapter nor its credentials print them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote as urlquote
from urllib.parse import urlsplit

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

from .binance import VenueOutcomeUnknown
from .credentials import VenueCredentials, VenueEnvironment, variable_prefix

logger = logging.getLogger("proto.venues.alpaca")

# Hard-pinned. There is deliberately no mapping from environment to URL: no live host exists here.
PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
_PAPER_HOST = urlsplit(PAPER_TRADING_URL).hostname
_DATA_HOST = urlsplit(DATA_URL).hostname
QUOTE_FEED = "iex"
FEED_LABEL = "IEX"  # one exchange's quotes, not the consolidated (SIP) tape
PREFIX = f"{VenueId.ALPACA_US.value}:"
US_TIMEZONE = "America/New_York"
_ETFS = frozenset({"SPY", "QQQ", "IWM", "DIA", "VTI", "VOO"})
_TIF = {TimeInForce.IOC: "ioc", TimeInForce.DAY: "day"}
_STATUS = {
    "new": OrderStatus.ACKNOWLEDGED,
    "accepted": OrderStatus.ACKNOWLEDGED,
    "pending_new": OrderStatus.ACKNOWLEDGED,
    "accepted_for_bidding": OrderStatus.ACKNOWLEDGED,
    "calculated": OrderStatus.ACKNOWLEDGED,
    "pending_cancel": OrderStatus.ACKNOWLEDGED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELED,
    # IOC remainders expire and DAY orders end with the session: terminal, like a cancel.
    "expired": OrderStatus.CANCELED,
    "done_for_day": OrderStatus.CANCELED,
    "rejected": OrderStatus.REJECTED,
}


class AlpacaRefused(Exception):
    """A definite answer from Alpaca (4xx): the request was refused."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"alpaca {status}: {message}")
        self.status, self.message = status, message


def symbol_of(instrument_id: str) -> str:
    if not instrument_id.startswith(PREFIX) or len(instrument_id) == len(PREFIX):
        raise ValueError(f"{instrument_id} is not an Alpaca US instrument")
    return instrument_id.removeprefix(PREFIX)


def _trading_url(path: str) -> str:
    url = f"{PAPER_TRADING_URL}{path}"
    if urlsplit(url).hostname != _PAPER_HOST:  # defence in depth: a path can never change host
        raise RuntimeError("refused: Alpaca trading calls go to the paper host only")
    return url


def _data_url(path: str) -> str:
    url = f"{DATA_URL}{path}"
    if urlsplit(url).hostname != _DATA_HOST:
        raise RuntimeError("refused: Alpaca market data comes from the data host only")
    return url


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: object) -> datetime | None:
    """Alpaca's RFC 3339 timestamps (nanoseconds, ``Z``), as aware datetimes; None if unusable."""

    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    head, dot, rest = text.partition(".")
    if dot:  # trim nanoseconds to microseconds, keep the offset
        digits = rest[: len(rest) - len(rest.lstrip("0123456789"))]
        offset = rest[len(digits) :]
        text = f"{head}.{digits[:6].ljust(6, '0')}{offset}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


class AlpacaPaperAdapter:
    def __init__(
        self,
        credentials: VenueCredentials,
        *,
        instruments: Mapping[str, TradableInstrument],
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if credentials.venue is not VenueId.ALPACA_US:
            raise ValueError("credentials belong to another venue")
        if credentials.environment is not VenueEnvironment.TESTNET:
            # TESTNET here means Alpaca's paper account. There is no live path to fall back to.
            raise ValueError("Alpaca runs on the paper account only (environment TESTNET)")
        # Alpaca key ids say which account they belong to: PK... paper, AK... live. A live key
        # is never sent anywhere (the paper host would answer 401 anyway), and a pair whose
        # secret looks like a key id was typed into the wrong secret: both fail here, by name.
        if credentials.api_key.startswith("AK"):
            raise ValueError("refused: a LIVE Alpaca key id (AK...); the paper key id is PK...")
        if credentials.api_secret.startswith(("PK", "AK")) and not credentials.api_key.startswith(
            "PK"
        ):
            raise ValueError("Alpaca key id and secret look swapped: _API_KEY holds the PK... id")
        self._credentials = credentials
        self._instruments = {k: v for k, v in instruments.items() if v.venue is VenueId.ALPACA_US}
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._clock = clock

    @classmethod
    async def connect(
        cls,
        credentials: VenueCredentials,
        *,
        symbols: Iterable[str],
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> AlpacaPaperAdapter:
        """Check the paper account can trade and load the tradable assets before any order."""

        adapter = cls(credentials, instruments={}, client=client, clock=clock)
        try:
            account = await adapter._send("GET", _trading_url("/v2/account"))
        except AlpacaRefused as exc:
            if exc.status not in (401, 403):
                raise
            # The headers, host and variable names are fixed in code (and tested): a refusal here
            # means the key pair itself. Say what to check, never what the key is.
            prefix = variable_prefix(credentials)
            raise AlpacaRefused(
                exc.status,
                f"{exc.message or 'unauthorized'} - the paper host refused the key pair: "
                f"{prefix}_API_KEY / _API_SECRET must be the current PAPER Key ID and Secret "
                "(regenerating the paper key invalidates the old secret)",
            ) from exc
        if account.get("status") != "ACTIVE" or account.get("trading_blocked"):
            raise RuntimeError("the Alpaca paper account is not active for trading")
        if str(account.get("currency", "USD")) != "USD":
            raise RuntimeError("the Alpaca paper account is not in USD")
        adapter._instruments = await adapter.load_instruments(symbols)
        return adapter

    @property
    def venue(self) -> VenueId:
        return VenueId.ALPACA_US

    @property
    def environment(self) -> VenueEnvironment:
        return VenueEnvironment.TESTNET

    @property
    def real_money_execution(self) -> bool:
        return False  # the paper host is the only host

    @property
    def instruments(self) -> dict[str, TradableInstrument]:
        return dict(self._instruments)

    def __repr__(self) -> str:
        return f"AlpacaPaperAdapter({self._credentials})"

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- transport ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._credentials.api_key,
            "APCA-API-SECRET-KEY": self._credentials.api_secret,
        }

    async def _send(self, method: str, url: str, **kwargs: Any) -> Any:
        host = urlsplit(url).hostname
        if host not in (_PAPER_HOST, _DATA_HOST):
            raise RuntimeError("refused: not an Alpaca paper or data host")
        name = urlsplit(url).path
        try:
            response = await self._client.request(
                method, url, headers=self._headers(), follow_redirects=False, **kwargs
            )
        except httpx.TransportError as exc:  # timeouts, resets, DNS
            raise VenueOutcomeUnknown(f"{method} {name}: {type(exc).__name__}") from exc
        if response.status_code >= 500 or 300 <= response.status_code < 400:
            raise VenueOutcomeUnknown(f"{method} {name}: HTTP {response.status_code}")
        if response.status_code == 204 or not response.content:
            if response.status_code >= 400:
                raise AlpacaRefused(response.status_code, "")
            return {}
        try:
            body = response.json()
        except ValueError as exc:
            raise VenueOutcomeUnknown(f"{method} {name}: unreadable response") from exc
        if response.status_code >= 400:
            message = str(body.get("message", "")) if isinstance(body, dict) else ""
            raise AlpacaRefused(response.status_code, message)
        return body

    # --- venue contract -------------------------------------------------------------

    async def market_open(self) -> bool:
        """Whether Alpaca's clock says the US market is open now. Unreadable: closed."""

        try:
            body = await self._send("GET", _trading_url("/v2/clock"))
        except (VenueOutcomeUnknown, AlpacaRefused):
            logger.warning("Alpaca clock unreadable: treated as closed")
            return False
        return body.get("is_open") is True

    async def submit(self, order: VenueOrder) -> ExecutionReport:
        if order.instrument_id not in self._instruments:
            return self._rejected(order, "instrument not loaded from Alpaca assets")
        if order.order_type is not OrderType.LIMIT or order.limit_price is None:
            return self._rejected(order, "Alpaca paper takes LIMIT orders only")
        tif = _TIF.get(order.time_in_force)
        if tif is None:
            return self._rejected(order, "time in force must be IOC or DAY (no resting GTC)")
        if order.quantity != order.quantity.to_integral_value():
            return self._rejected(order, "whole shares only")
        if not await self.market_open():
            return self._rejected(order, "US market closed (Alpaca clock): order not sent")
        body = {
            "symbol": symbol_of(order.instrument_id),
            "qty": _fmt(order.quantity),
            "side": order.side.lower(),
            "type": "limit",
            "time_in_force": tif,
            "limit_price": _fmt(order.limit_price),
            "client_order_id": order.client_order_id,
        }
        try:
            return self._report(await self._send("POST", _trading_url("/v2/orders"), json=body))
        except AlpacaRefused as exc:
            if "client_order_id" in exc.message.lower() and "unique" in exc.message.lower():
                existing = await self.query(order.client_order_id)
                if existing is not None:
                    return existing
            return self._rejected(order, str(exc))

    async def query(
        self, client_order_id: str, *, instrument_id: str | None = None
    ) -> ExecutionReport | None:
        body = await self._by_client_id(client_order_id)
        return None if body is None else self._report(body)

    async def cancel(
        self, client_order_id: str, *, instrument_id: str | None = None
    ) -> ExecutionReport:
        body = await self._by_client_id(client_order_id)
        if body is None:
            raise LookupError(f"{client_order_id} does not exist at Alpaca")
        try:
            await self._send("DELETE", _trading_url(f"/v2/orders/{urlquote(str(body['id']))}"))
        except AlpacaRefused as exc:
            if exc.status not in (404, 422):
                raise
            # Already terminal (filled, expired) or not cancelable: report what Alpaca holds.
        report = await self.query(client_order_id)
        if report is None:
            raise LookupError(f"{client_order_id} does not exist at Alpaca")
        return report

    async def balances(self) -> dict[str, Decimal]:
        """USD cash plus whole-share positions, for the ledger and reconciliation."""

        account = await self._send("GET", _trading_url("/v2/account"))
        totals: dict[str, Decimal] = {}
        cash = Decimal(str(account.get("cash", "0")))
        if cash:
            totals["USD"] = cash
        for row in await self._send("GET", _trading_url("/v2/positions")) or ():
            quantity = Decimal(str(row.get("qty", "0")))
            if quantity:
                totals[str(row["symbol"])] = quantity
        return totals

    async def quotes(self, symbols: Iterable[str]) -> dict[str, dict[str, str]]:
        """Latest IEX quote per symbol (bid, ask, Alpaca's timestamp), labelled with its feed.

        A symbol without a usable two-sided quote is left out: better none than a made-up price.
        """

        names = ",".join(sorted(set(symbols)))
        body = await self._send(
            "GET",
            _data_url("/v2/stocks/quotes/latest"),
            params={"symbols": names, "feed": QUOTE_FEED},
        )
        out: dict[str, dict[str, str]] = {}
        for symbol, raw in (body.get("quotes") or {}).items():
            parsed = parse_latest_quote(raw)
            if parsed is not None:
                out[str(symbol)] = parsed
        return out

    async def latest_bars(self, symbols: Iterable[str]) -> dict[str, dict[str, str]]:
        """Latest one-minute IEX bar per symbol (read-only market data), labelled with its feed."""

        names = ",".join(sorted(set(symbols)))
        body = await self._send(
            "GET",
            _data_url("/v2/stocks/bars/latest"),
            params={"symbols": names, "feed": QUOTE_FEED},
        )
        out: dict[str, dict[str, str]] = {}
        for symbol, raw in (body.get("bars") or {}).items():
            at = _timestamp(raw.get("t"))
            try:
                values = {k: str(Decimal(str(raw[k]))) for k in ("o", "h", "l", "c", "v")}
            except (KeyError, InvalidOperation):
                continue
            if at is not None:
                out[str(symbol)] = {**values, "observed_at": at.isoformat(), "feed": FEED_LABEL}
        return out

    # --- instruments ----------------------------------------------------------------

    async def load_instruments(self, symbols: Iterable[str]) -> dict[str, TradableInstrument]:
        """Tradable, active US equities from Alpaca's asset list; anything else is left out."""

        instruments: dict[str, TradableInstrument] = {}
        for symbol in sorted(set(symbols)):
            try:
                asset = await self._send("GET", _trading_url(f"/v2/assets/{urlquote(symbol)}"))
            except AlpacaRefused:
                logger.warning("%s is not an Alpaca asset; left out", symbol)
                continue
            if (
                asset.get("tradable") is not True
                or asset.get("status") != "active"
                or asset.get("class") != "us_equity"
            ):
                logger.warning("%s is not tradable on Alpaca; left out", symbol)
                continue
            instrument = TradableInstrument(
                instrument_id=f"{PREFIX}{symbol}",
                venue=VenueId.ALPACA_US,
                asset_class=AssetClass.ETF if symbol in _ETFS else AssetClass.EQUITY,
                base_asset=symbol,
                quote_asset="USD",
                tick_size=Decimal("0.01"),
                lot_size=Decimal("1"),
                min_quantity=Decimal("1"),
                min_notional=Decimal("1"),
                session_type=SessionType.EXCHANGE_SESSION,
                timezone=US_TIMEZONE,
            )
            instruments[instrument.instrument_id] = instrument
        return instruments

    # --- helpers --------------------------------------------------------------------

    async def _by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        try:
            return await self._send(
                "GET",
                _trading_url("/v2/orders:by_client_order_id"),
                params={"client_order_id": client_order_id},
            )
        except AlpacaRefused as exc:
            if exc.status == 404:
                return None
            raise

    def _report(self, body: dict[str, Any]) -> ExecutionReport:
        name = str(body.get("status"))
        status = _STATUS.get(name)
        if status is None:
            raise VenueOutcomeUnknown(f"unmapped Alpaca status {name}")
        filled = Decimal(str(body.get("filled_qty") or "0"))
        quantity = Decimal(str(body["qty"]))
        if status is OrderStatus.ACKNOWLEDGED and filled:
            status = OrderStatus.PARTIALLY_FILLED
        average = body.get("filled_avg_price")
        reported = _timestamp(body.get("updated_at")) or _timestamp(body.get("submitted_at"))
        return ExecutionReport(
            venue=VenueId.ALPACA_US,
            client_order_id=str(body["client_order_id"]),
            venue_order_id=str(body["id"]) if body.get("id") else None,
            instrument_id=f"{PREFIX}{body['symbol']}",
            side="BUY" if str(body["side"]).lower() == "buy" else "SELL",
            status=status,
            quantity=quantity,
            filled_quantity=filled,
            average_price=Decimal(str(average)) if filled and average else None,
            # Alpaca charges no commission on paper equities; regulatory fees are not simulated.
            fee=Decimal("0"),
            fee_asset=None,
            reason=name if status in (OrderStatus.CANCELED, OrderStatus.REJECTED) else None,
            reported_at=reported or self._clock(),
        )

    def _rejected(self, order: VenueOrder, reason: str) -> ExecutionReport:
        return ExecutionReport(
            venue=VenueId.ALPACA_US,
            client_order_id=order.client_order_id,
            instrument_id=order.instrument_id,
            side=order.side,
            status=OrderStatus.REJECTED,
            quantity=order.quantity,
            filled_quantity=Decimal("0"),
            reason=reason[:200],
            reported_at=self._clock(),
        )


def parse_latest_quote(raw: object) -> dict[str, str] | None:
    """One Alpaca latest-quote object (``bp``/``ap``/``t``) as the executor's quote row."""

    if not isinstance(raw, dict):
        return None
    try:
        bid, ask = Decimal(str(raw["bp"])), Decimal(str(raw["ap"]))
    except (KeyError, InvalidOperation):
        return None
    at = _timestamp(raw.get("t"))
    if at is None or not (bid.is_finite() and ask.is_finite()) or not (0 < bid < ask):
        return None
    return {"bid": str(bid), "ask": str(ask), "observed_at": at.isoformat(), "feed": FEED_LABEL}
