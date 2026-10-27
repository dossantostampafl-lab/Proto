"""XP (B3) venue adapter: the executor's client of the MT5 bridge on the Windows VM.

Same contract and rules as the Binance adapter:

- **Unknown outcomes:** a timeout, a dropped connection or a 5xx from the bridge raise, so the
  OMS marks the order UNKNOWN and never resends it to resolve doubt.
- **Identity:** the OMS client order id addresses every submit, query and cancel; the bridge
  maps it to the MT5 ticket and returns the existing order on a resubmit.
- **Environment:** the bridge reports the logged-in account. DEMO maps to TESTNET (no real
  money). Anything else is LIVE, so `real_money_execution` is true and the OMS refuses it until
  a live scope is certified. The bridge itself refuses orders on a non-demo account as well.
- **Grids** come from the terminal's symbol specs (`load_instruments`), never from guesses.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from services.execution import ExecutionReport, OrderStatus, TradableInstrument, VenueId
from services.execution.venue import VenueOrder
from services.market_data.instruments import AssetClass, SessionType

from .binance import VenueOutcomeUnknown
from .credentials import VenueEnvironment

PREFIX = f"{VenueId.XP_B3.value}:"
B3_TIMEZONE = "America/Sao_Paulo"
_STATUS = {
    "ACKNOWLEDGED": OrderStatus.ACKNOWLEDGED,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELED,
    "REJECTED": OrderStatus.REJECTED,
}


class BridgeRefused(Exception):
    """A definite answer from the bridge (4xx): the request was refused."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


def symbol_of(instrument_id: str) -> str:
    if not instrument_id.startswith(PREFIX):
        raise ValueError(f"{instrument_id} is not an XP/B3 instrument")
    return instrument_id.removeprefix(PREFIX)


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _call(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> Any:
    try:
        response = await client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise VenueOutcomeUnknown(f"{method} {path}: {type(exc).__name__}") from exc
    if response.status_code >= 500 or 300 <= response.status_code < 400:
        raise VenueOutcomeUnknown(f"{method} {path}: HTTP {response.status_code}")
    try:
        body = response.json() if response.content else {}
    except ValueError as exc:
        if response.status_code >= 400:
            raise BridgeRefused(response.status_code, "") from exc
        # A 2xx we cannot read may still have placed the order: unknown, never a rejection.
        raise VenueOutcomeUnknown(f"{method} {path}: unreadable response") from exc
    if response.status_code >= 400:
        detail = body.get("detail", "") if isinstance(body, dict) else ""
        raise BridgeRefused(response.status_code, str(detail))
    return body


def bridge_client(base_url: str, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(10.0),
    )


class XpMt5Adapter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        environment: VenueEnvironment,
        instruments: Mapping[str, TradableInstrument],
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._client = client
        self._environment = environment
        self._instruments = {k: v for k, v in instruments.items() if v.venue is VenueId.XP_B3}
        self._clock = clock
        self._left_demo: str | None = None

    @classmethod
    async def connect(cls, client: httpx.AsyncClient, *, symbols: Iterable[str]) -> XpMt5Adapter:
        """Read the account mode and the grids from the bridge before any order."""

        health = await _call(client, "GET", "/health")
        if not health.get("connected"):
            raise VenueOutcomeUnknown("the MT5 terminal is not connected")
        environment = (
            VenueEnvironment.TESTNET
            if health.get("account_mode") == "DEMO"
            else VenueEnvironment.LIVE
        )
        instruments = await load_instruments(client, symbols)
        return cls(client, environment=environment, instruments=instruments)

    @property
    def venue(self) -> VenueId:
        return VenueId.XP_B3

    @property
    def environment(self) -> VenueEnvironment:
        return self._environment

    @property
    def real_money_execution(self) -> bool:
        return self._environment is VenueEnvironment.LIVE

    @property
    def instruments(self) -> dict[str, TradableInstrument]:
        return dict(self._instruments)

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- venue contract -------------------------------------------------------------

    async def submit(self, order: VenueOrder) -> ExecutionReport:
        if self._left_demo is not None:
            # Latched by `balances`: the terminal reported a non-DEMO login after connect.
            return self._rejected(order, f"terminal on a {self._left_demo} account: not sent")
        if order.instrument_id not in self._instruments:
            return self._rejected(order, "instrument not loaded from the terminal")
        body = {
            "client_order_id": order.client_order_id,
            "symbol": symbol_of(order.instrument_id),
            "side": order.side,
            "quantity": str(order.quantity),
            "order_type": order.order_type.value,
            "limit_price": str(order.limit_price) if order.limit_price is not None else None,
            "time_in_force": order.time_in_force.value,
        }
        try:
            return self._report(await _call(self._client, "POST", "/v1/orders", json=body))
        except BridgeRefused as exc:
            return self._rejected(order, exc.detail or str(exc))

    async def query(
        self, client_order_id: str, *, instrument_id: str | None = None
    ) -> ExecutionReport | None:
        try:
            return self._report(await _call(self._client, "GET", f"/v1/orders/{client_order_id}"))
        except BridgeRefused as exc:
            if exc.status == 404:
                return None
            raise

    async def cancel(
        self, client_order_id: str, *, instrument_id: str | None = None
    ) -> ExecutionReport:
        try:
            body = await _call(self._client, "POST", f"/v1/orders/{client_order_id}/cancel")
        except BridgeRefused as exc:
            if exc.status == 404:
                raise LookupError(f"{client_order_id} does not exist at XP") from exc
            raise
        return self._report(body)

    async def balances(self) -> dict[str, Decimal]:
        body = await _call(self._client, "GET", "/v1/balances")
        mode = body.get("account_mode")
        if mode is not None and mode != "DEMO":
            # The login changed under us (the bridge refuses such orders too). Latch LIVE, never
            # back: every later submit is refused here and reconciliation fails until a restart.
            self._environment, self._left_demo = VenueEnvironment.LIVE, str(mode)[:20]
            raise VenueOutcomeUnknown(f"the MT5 terminal reports a {mode} account, not DEMO")
        return {
            asset: Decimal(value) for asset, value in body["balances"].items() if Decimal(value)
        }

    async def quotes(self, symbols: Iterable[str]) -> dict[str, dict[str, str]]:
        names = ",".join(sorted(set(symbols)))
        return (await _call(self._client, "GET", "/v1/quotes", params={"names": names}))["quotes"]

    # --- helpers --------------------------------------------------------------------

    def _report(self, body: dict[str, Any]) -> ExecutionReport:
        status = _STATUS.get(str(body.get("status")))
        if status is None:
            raise VenueOutcomeUnknown(f"unmapped bridge status {body.get('status')}")
        filled = Decimal(str(body.get("filled_quantity", "0")))
        average = body.get("average_price")
        return ExecutionReport(
            venue=VenueId.XP_B3,
            client_order_id=str(body["client_order_id"]),
            venue_order_id=str(body["ticket"]) if body.get("ticket") else None,
            instrument_id=f"{PREFIX}{body['symbol']}",
            side=body["side"],
            status=status,
            quantity=Decimal(str(body["quantity"])),
            filled_quantity=filled,
            average_price=Decimal(str(average)) if filled and average else None,
            fee=Decimal(str(body.get("fee", "0"))),
            fee_asset=body.get("fee_asset") if Decimal(str(body.get("fee", "0"))) else None,
            reason=body.get("reason"),
            reported_at=datetime.fromisoformat(body["reported_at"])
            if body.get("reported_at")
            else self._clock(),
        )

    def _rejected(self, order: VenueOrder, reason: str) -> ExecutionReport:
        return ExecutionReport(
            venue=VenueId.XP_B3,
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
    client: httpx.AsyncClient, symbols: Iterable[str]
) -> dict[str, TradableInstrument]:
    """Order grids from the terminal's symbol specs; unknown symbols are left out."""

    names = ",".join(sorted(set(symbols)))
    body = await _call(client, "GET", "/v1/symbols", params={"names": names})
    instruments: dict[str, TradableInstrument] = {}
    for name, spec in body.get("symbols", {}).items():
        step = Decimal(spec["volume_step"]).normalize()
        instrument = TradableInstrument(
            instrument_id=f"{PREFIX}{name}",
            venue=VenueId.XP_B3,
            asset_class=AssetClass.ETF if name.endswith(("11", "11F")) else AssetClass.EQUITY,
            base_asset=name,
            quote_asset=str(spec.get("currency") or "BRL"),
            tick_size=Decimal(spec["tick_size"]).normalize(),
            lot_size=step,
            min_quantity=Decimal(spec["volume_min"]).normalize(),
            min_notional=Decimal("0.01"),
            session_type=SessionType.EXCHANGE_SESSION,
            timezone=B3_TIMEZONE,
        )
        instruments[instrument.instrument_id] = instrument
    return instruments
