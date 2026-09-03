from __future__ import annotations

from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AssetClass(StrEnum):
    CRYPTO = "CRYPTO"
    EQUITY = "EQUITY"
    ETF = "ETF"
    INDEX = "INDEX"
    PREDICTION_MARKET = "PREDICTION_MARKET"


class SessionType(StrEnum):
    CONTINUOUS_24_7 = "CONTINUOUS_24_7"
    EXCHANGE_SESSION = "EXCHANGE_SESSION"
    CONTRACT_DEFINED = "CONTRACT_DEFINED"


class Instrument(BaseModel):
    """Provider-neutral identity and trading-calendar metadata.

    Registration describes an instrument; it does not imply that PROTO has a
    live data subscription or execution connectivity for the instrument.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    instrument_id: str = Field(min_length=3, max_length=160)
    symbol: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    asset_class: AssetClass
    venue: str = Field(min_length=1, max_length=80)
    currency: str = Field(min_length=3, max_length=3)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    timezone: str = Field(min_length=1, max_length=80)
    market_calendar: str = Field(min_length=1, max_length=80)
    session_type: SessionType
    tick_size: float | None = Field(default=None, gt=0)
    lot_size: float | None = Field(default=None, gt=0)
    benchmark_instrument_id: str | None = Field(default=None, min_length=3, max_length=160)
    sector: str | None = Field(default=None, max_length=120)
    industry: str | None = Field(default=None, max_length=160)
    enabled: bool = True

    @field_validator("symbol", "venue", "currency", mode="before")
    @classmethod
    def normalize_uppercase(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("country", mode="before")
    @classmethod
    def normalize_country(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_identity_and_timezone(self) -> Instrument:
        if ":" not in self.instrument_id:
            raise ValueError("instrument_id must be namespaced, for example VENUE:SYMBOL")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        if self.benchmark_instrument_id == self.instrument_id:
            raise ValueError("instrument cannot benchmark itself")
        return self


class InstrumentRegistry:
    """In-memory registry with explicit, conflict-safe registration semantics."""

    def __init__(self, instruments: tuple[Instrument, ...] = ()) -> None:
        self._instruments: dict[str, Instrument] = {}
        for instrument in instruments:
            self.register(instrument)

    def register(self, instrument: Instrument) -> Instrument:
        existing = self._instruments.get(instrument.instrument_id)
        if existing is not None and existing != instrument:
            raise ValueError("instrument_id already belongs to a different contract")
        self._instruments[instrument.instrument_id] = instrument
        return instrument

    def get(self, instrument_id: str) -> Instrument | None:
        return self._instruments.get(instrument_id)

    def require(self, instrument_id: str) -> Instrument:
        instrument = self.get(instrument_id)
        if instrument is None:
            raise KeyError(f"unknown instrument_id: {instrument_id}")
        return instrument

    def list(
        self,
        *,
        asset_class: AssetClass | None = None,
        venue: str | None = None,
        enabled_only: bool = True,
    ) -> tuple[Instrument, ...]:
        normalized_venue = venue.strip().upper() if venue is not None else None
        values = (
            instrument
            for instrument in self._instruments.values()
            if (not enabled_only or instrument.enabled)
            and (asset_class is None or instrument.asset_class is asset_class)
            and (normalized_venue is None or instrument.venue == normalized_venue)
        )
        return tuple(sorted(values, key=lambda item: item.instrument_id))

    def snapshot(self) -> dict[str, object]:
        return {
            "count": len(self._instruments),
            "instruments": [
                instrument.model_dump(mode="json")
                for instrument in sorted(
                    self._instruments.values(), key=lambda item: item.instrument_id
                )
            ],
            "financial_connectivity": False,
            "real_money_execution": False,
        }
