pub mod matching;
pub mod queue;

use rust_decimal::Decimal;
use rust_decimal::prelude::ToPrimitive;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrderState {
    Created,
    Validated,
    Queued,
    Resting,
    PartiallyFilled,
    Filled,
    Canceled,
    Rejected,
    Expired,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SimulatedOrder {
    pub order_id: Uuid,
    pub command_id: Uuid,
    pub idempotency_key: String,
    pub price: Decimal,
    pub quantity: Decimal,
    pub filled_quantity: Decimal,
    pub state: OrderState,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FillModelInput {
    pub spread_bps: Decimal,
    pub queue_position: Decimal,
    pub market_volume: Decimal,
    pub trade_intensity: Decimal,
    pub latency_ms: u64,
    pub order_size: Decimal,
    pub hawkes_intensity: Decimal,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct FillEstimate {
    pub fill_probability: Decimal,
    pub expected_fill_quantity: Decimal,
    pub expected_slippage_bps: Decimal,
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum TransitionError {
    #[error("invalid order state transition: {0:?} -> {1:?}")]
    Invalid(OrderState, OrderState),
    #[error("fill quantity must be positive and not exceed remaining quantity")]
    InvalidFill,
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum FillModelError {
    #[error("fill model inputs must be non-negative and order size must be positive")]
    InvalidInput,
}

impl SimulatedOrder {
    pub fn new(
        command_id: Uuid,
        idempotency_key: String,
        price: Decimal,
        quantity: Decimal,
    ) -> Self {
        Self {
            order_id: Uuid::new_v4(),
            command_id,
            idempotency_key,
            price,
            quantity,
            filled_quantity: Decimal::ZERO,
            state: OrderState::Created,
        }
    }

    pub fn transition(&mut self, next: OrderState) -> Result<(), TransitionError> {
        let valid = matches!(
            (self.state, next),
            (OrderState::Created, OrderState::Validated)
                | (OrderState::Created, OrderState::Rejected)
                | (OrderState::Validated, OrderState::Queued)
                | (OrderState::Validated, OrderState::Rejected)
                | (OrderState::Queued, OrderState::Resting)
                | (OrderState::Queued, OrderState::Canceled)
                | (OrderState::Resting, OrderState::PartiallyFilled)
                | (OrderState::Resting, OrderState::Filled)
                | (OrderState::Resting, OrderState::Canceled)
                | (OrderState::Resting, OrderState::Expired)
                | (OrderState::PartiallyFilled, OrderState::PartiallyFilled)
                | (OrderState::PartiallyFilled, OrderState::Filled)
                | (OrderState::PartiallyFilled, OrderState::Canceled)
                | (OrderState::PartiallyFilled, OrderState::Expired)
        );
        if !valid {
            return Err(TransitionError::Invalid(self.state, next));
        }
        self.state = next;
        Ok(())
    }

    pub fn apply_fill(&mut self, quantity: Decimal) -> Result<(), TransitionError> {
        let remaining = self.quantity - self.filled_quantity;
        if quantity <= Decimal::ZERO || quantity > remaining {
            return Err(TransitionError::InvalidFill);
        }
        self.filled_quantity += quantity;
        let next = if self.filled_quantity == self.quantity {
            OrderState::Filled
        } else {
            OrderState::PartiallyFilled
        };
        self.transition(next)
    }
}

pub fn estimate_fill(input: &FillModelInput) -> Result<FillEstimate, FillModelError> {
    if input.spread_bps < Decimal::ZERO
        || input.queue_position < Decimal::ZERO
        || input.market_volume < Decimal::ZERO
        || input.trade_intensity < Decimal::ZERO
        || input.order_size <= Decimal::ZERO
        || input.hawkes_intensity < Decimal::ZERO
    {
        return Err(FillModelError::InvalidInput);
    }

    let one = Decimal::ONE;
    let hundred = Decimal::new(100, 0);
    let thousand = Decimal::new(1000, 0);

    let queue_penalty = one / (one + input.queue_position);
    let volume_ratio = (input.market_volume / input.order_size).min(one);
    let intensity_score = (input.trade_intensity / (input.trade_intensity + one)).min(one);
    let hawkes_score = (input.hawkes_intensity / (input.hawkes_intensity + one)).min(one);
    let latency_penalty = one / (one + Decimal::from(input.latency_ms) / thousand);
    let spread_penalty = one / (one + input.spread_bps / hundred);

    let probability = (queue_penalty
        * volume_ratio
        * intensity_score
        * latency_penalty
        * spread_penalty
        * (Decimal::new(7, 1) + Decimal::new(3, 1) * hawkes_score))
        .clamp(Decimal::ZERO, one);

    let expected_fill_quantity = input.order_size * probability;
    let expected_slippage_bps = input.spread_bps * (one - probability)
        + Decimal::from(input.latency_ms) / Decimal::new(100, 0);

    Ok(FillEstimate {
        fill_probability: probability,
        expected_fill_quantity,
        expected_slippage_bps,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lifecycle_rejects_invalid_transition() {
        let mut order = SimulatedOrder::new(
            Uuid::new_v4(),
            "idempotency-1".to_string(),
            Decimal::new(101, 2),
            Decimal::new(2, 0),
        );
        assert_eq!(
            order.transition(OrderState::Filled),
            Err(TransitionError::Invalid(
                OrderState::Created,
                OrderState::Filled
            ))
        );
    }

    #[test]
    fn lifecycle_tracks_partial_and_full_fill() {
        let mut order = SimulatedOrder::new(
            Uuid::new_v4(),
            "idempotency-2".to_string(),
            Decimal::new(101, 2),
            Decimal::new(2, 0),
        );
        order.transition(OrderState::Validated).unwrap();
        order.transition(OrderState::Queued).unwrap();
        order.transition(OrderState::Resting).unwrap();
        order.apply_fill(Decimal::ONE).unwrap();
        assert_eq!(order.state, OrderState::PartiallyFilled);
        order.apply_fill(Decimal::ONE).unwrap();
        assert_eq!(order.state, OrderState::Filled);
    }

    #[test]
    fn estimate_fill_is_bounded() {
        let estimate = estimate_fill(&FillModelInput {
            spread_bps: Decimal::new(5, 0),
            queue_position: Decimal::new(1, 0),
            market_volume: Decimal::new(100, 0),
            trade_intensity: Decimal::new(5, 0),
            latency_ms: 10,
            order_size: Decimal::new(1, 0),
            hawkes_intensity: Decimal::new(2, 0),
        })
        .unwrap();
        let probability = estimate.fill_probability.to_f64().unwrap();
        assert!((0.0..=1.0).contains(&probability));
        assert!(estimate.expected_fill_quantity <= Decimal::ONE);
    }

    #[test]
    fn estimate_fill_rejects_invalid_input() {
        let result = estimate_fill(&FillModelInput {
            spread_bps: Decimal::new(-1, 0),
            queue_position: Decimal::ZERO,
            market_volume: Decimal::ZERO,
            trade_intensity: Decimal::ZERO,
            latency_ms: 0,
            order_size: Decimal::ONE,
            hawkes_intensity: Decimal::ZERO,
        });
        assert_eq!(result, Err(FillModelError::InvalidInput));
    }
}
