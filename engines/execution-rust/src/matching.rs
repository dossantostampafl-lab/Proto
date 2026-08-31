use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Side {
    Buy,
    Sell,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TimeInForce {
    Gtc,
    Ioc,
    Fok,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct TopOfBook {
    pub bid: Decimal,
    pub bid_size: Decimal,
    pub ask: Decimal,
    pub ask_size: Decimal,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct LimitIntent {
    pub side: Side,
    pub limit_price: Decimal,
    pub quantity: Decimal,
    pub time_in_force: TimeInForce,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum MatchState {
    Resting,
    PartiallyFilled,
    Filled,
    Canceled,
    Rejected,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct MatchOutcome {
    pub state: MatchState,
    pub filled_quantity: Decimal,
    pub fill_price: Option<Decimal>,
    pub remaining_quantity: Decimal,
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum MatchError {
    #[error("prices and quantities must be positive and book sizes non-negative")]
    InvalidInput,
    #[error("book must not be crossed")]
    CrossedBook,
}

pub fn match_limit(
    intent: LimitIntent,
    book: TopOfBook,
) -> Result<MatchOutcome, MatchError> {
    if intent.limit_price <= Decimal::ZERO
        || intent.quantity <= Decimal::ZERO
        || book.bid <= Decimal::ZERO
        || book.ask <= Decimal::ZERO
        || book.bid_size < Decimal::ZERO
        || book.ask_size < Decimal::ZERO
    {
        return Err(MatchError::InvalidInput);
    }
    if book.bid > book.ask {
        return Err(MatchError::CrossedBook);
    }

    let (marketable, available, execution_price) = match intent.side {
        Side::Buy => (
            intent.limit_price >= book.ask,
            book.ask_size,
            book.ask,
        ),
        Side::Sell => (
            intent.limit_price <= book.bid,
            book.bid_size,
            book.bid,
        ),
    };

    if !marketable {
        return Ok(match intent.time_in_force {
            TimeInForce::Gtc => MatchOutcome {
                state: MatchState::Resting,
                filled_quantity: Decimal::ZERO,
                fill_price: None,
                remaining_quantity: intent.quantity,
            },
            TimeInForce::Ioc | TimeInForce::Fok => MatchOutcome {
                state: MatchState::Canceled,
                filled_quantity: Decimal::ZERO,
                fill_price: None,
                remaining_quantity: Decimal::ZERO,
            },
        });
    }

    if intent.time_in_force == TimeInForce::Fok && available < intent.quantity {
        return Ok(MatchOutcome {
            state: MatchState::Canceled,
            filled_quantity: Decimal::ZERO,
            fill_price: None,
            remaining_quantity: Decimal::ZERO,
        });
    }

    let filled = intent.quantity.min(available);
    let remaining = intent.quantity - filled;
    let state = if filled == intent.quantity {
        MatchState::Filled
    } else if intent.time_in_force == TimeInForce::Gtc {
        MatchState::PartiallyFilled
    } else {
        MatchState::PartiallyFilled
    };
    let remaining_quantity = if intent.time_in_force == TimeInForce::Ioc {
        Decimal::ZERO
    } else {
        remaining
    };

    Ok(MatchOutcome {
        state,
        filled_quantity: filled,
        fill_price: (filled > Decimal::ZERO).then_some(execution_price),
        remaining_quantity,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn book() -> TopOfBook {
        TopOfBook {
            bid: Decimal::new(99, 2),
            bid_size: Decimal::new(4, 0),
            ask: Decimal::new(101, 2),
            ask_size: Decimal::new(3, 0),
        }
    }

    #[test]
    fn gtc_non_marketable_limit_rests() {
        let outcome = match_limit(
            LimitIntent {
                side: Side::Buy,
                limit_price: Decimal::ONE,
                quantity: Decimal::new(2, 0),
                time_in_force: TimeInForce::Gtc,
            },
            book(),
        )
        .unwrap();
        assert_eq!(outcome.state, MatchState::Resting);
        assert_eq!(outcome.filled_quantity, Decimal::ZERO);
    }

    #[test]
    fn ioc_partially_fills_and_cancels_remainder() {
        let outcome = match_limit(
            LimitIntent {
                side: Side::Buy,
                limit_price: Decimal::new(102, 2),
                quantity: Decimal::new(5, 0),
                time_in_force: TimeInForce::Ioc,
            },
            book(),
        )
        .unwrap();
        assert_eq!(outcome.state, MatchState::PartiallyFilled);
        assert_eq!(outcome.filled_quantity, Decimal::new(3, 0));
        assert_eq!(outcome.remaining_quantity, Decimal::ZERO);
        assert_eq!(outcome.fill_price, Some(Decimal::new(101, 2)));
    }

    #[test]
    fn fok_cancels_when_full_quantity_is_unavailable() {
        let outcome = match_limit(
            LimitIntent {
                side: Side::Buy,
                limit_price: Decimal::new(102, 2),
                quantity: Decimal::new(5, 0),
                time_in_force: TimeInForce::Fok,
            },
            book(),
        )
        .unwrap();
        assert_eq!(outcome.state, MatchState::Canceled);
        assert_eq!(outcome.filled_quantity, Decimal::ZERO);
    }

    #[test]
    fn marketable_sell_executes_at_resting_bid() {
        let outcome = match_limit(
            LimitIntent {
                side: Side::Sell,
                limit_price: Decimal::new(98, 2),
                quantity: Decimal::new(2, 0),
                time_in_force: TimeInForce::Gtc,
            },
            book(),
        )
        .unwrap();
        assert_eq!(outcome.state, MatchState::Filled);
        assert_eq!(outcome.fill_price, Some(Decimal::new(99, 2)));
    }
}
