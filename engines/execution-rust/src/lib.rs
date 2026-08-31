pub mod matching;

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
        use OrderState::*;
        let valid = matches!(
            (self.state, next),
            (Created, Validated)
                | (Created, Rejected)
                | (Validated, Queued)
                | (Validated, Rejected)
                | (Queued, Resting)
                | (Queued, Canceled)
                | (Resting, PartiallyFilled)
                | (Resting, Filled)
                | (Resting, Canceled)
                | (Resting, Expired)
                | (PartiallyFilled, PartiallyFilled)
                | (PartiallyFilled, Filled)
                | (PartiallyFilled, Canceled)
                | (PartiallyFilled, Expired)
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
        let next = if self.filled_quantity + quantity == self.quantity {
            OrderState::Filled
        } else {
            OrderState::PartiallyFilled
        };
        self.transition(next)?;
        self.filled_quantity += quantity;
        Ok(())
    }
}

pub fn estimate_fill(input: &FillModelInput) -> Result<FillEstimate, FillModelError> {
    if input.spread_bps < Decimal::ZERO
        || input.queue_position < Decimal::ZERO
        || input.market_volume < Decimal::ZERO
        || input.trade_intensity < Decimal::ZERO
        || input.hawkes_intensity < Decimal::ZERO
        || input.order_size <= Decimal::ZERO
    {
        return Err(FillModelError::InvalidInput);
    }

    let spread = input.spread_bps.to_f64().unwrap_or(0.0);
    let queue = input.queue_position.to_f64().unwrap_or(0.0);
    let volume = input.market_volume.to_f64().unwrap_or(0.0);
    let intensity = input.trade_intensity.to_f64().unwrap_or(0.0);
    let hawkes = input.hawkes_intensity.to_f64().unwrap_or(0.0);
    let size = input.order_size.to_f64().unwrap_or(0.0);
    let latency = input.latency_ms as f64;

    let flow_support = (volume / (volume + size.max(1e-9))).clamp(0.0, 1.0);
    let intensity_support = (1.0 - (-0.15 * (intensity + hawkes)).exp()).clamp(0.0, 1.0);
    let queue_penalty = 1.0 / (1.0 + queue.max(0.0));
    let latency_penalty = (-latency / 1_000.0).exp();
    let spread_penalty = 1.0 / (1.0 + spread / 25.0);

    let probability = (flow_support
        * (0.35 + 0.65 * intensity_support)
        * queue_penalty
        * latency_penalty
        * spread_penalty)
        .clamp(0.0, 1.0);

    let expected_quantity = size * probability;
    let slippage_bps = (spread * 0.25
        + latency / 100.0
        + (size / (volume + 1.0)) * 10.0
        + (1.0 - queue_penalty) * 2.0)
        .max(0.0);

    Ok(FillEstimate {
        fill_probability: Decimal::from_f64_retain(probability).unwrap_or(Decimal::ZERO),
        expected_fill_quantity: Decimal::from_f64_retain(expected_quantity)
            .unwrap_or(Decimal::ZERO),
        expected_slippage_bps: Decimal::from_f64_retain(slippage_bps).unwrap_or(Decimal::ZERO),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn order_does_not_instant_fill() {
        let mut order = SimulatedOrder::new(
            Uuid::new_v4(),
            "cmd-1".into(),
            Decimal::new(53, 2),
            Decimal::new(2, 0),
        );
        assert_eq!(order.state, OrderState::Created);
        order.transition(OrderState::Validated).unwrap();
        order.transition(OrderState::Queued).unwrap();
        order.transition(OrderState::Resting).unwrap();
        order.apply_fill(Decimal::ONE).unwrap();
        assert_eq!(order.state, OrderState::PartiallyFilled);
        assert_eq!(order.filled_quantity, Decimal::ONE);
    }

    #[test]
    fn rejects_overfill() {
        let mut order =
            SimulatedOrder::new(Uuid::new_v4(), "cmd-2".into(), Decimal::ONE, Decimal::ONE);
        order.transition(OrderState::Validated).unwrap();
        order.transition(OrderState::Queued).unwrap();
        order.transition(OrderState::Resting).unwrap();
        assert_eq!(
            order.apply_fill(Decimal::new(2, 0)),
            Err(TransitionError::InvalidFill)
        );
    }

    #[test]
    fn invalid_state_fill_does_not_mutate_accounting() {
        let mut order = SimulatedOrder::new(
            Uuid::new_v4(),
            "cmd-atomic".into(),
            Decimal::ONE,
            Decimal::new(2, 0),
        );

        assert_eq!(
            order.apply_fill(Decimal::ONE),
            Err(TransitionError::Invalid(
                OrderState::Created,
                OrderState::PartiallyFilled
            ))
        );
        assert_eq!(order.state, OrderState::Created);
        assert_eq!(order.filled_quantity, Decimal::ZERO);
    }

    #[test]
    fn fill_probability_improves_with_flow_and_queue_priority() {
        let favorable = FillModelInput {
            spread_bps: Decimal::new(5, 0),
            queue_position: Decimal::new(1, 1),
            market_volume: Decimal::new(1_000, 0),
            trade_intensity: Decimal::new(8, 0),
            latency_ms: 10,
            order_size: Decimal::new(10, 0),
            hawkes_intensity: Decimal::new(4, 0),
        };
        let adverse = FillModelInput {
            spread_bps: Decimal::new(25, 0),
            queue_position: Decimal::new(5, 0),
            market_volume: Decimal::new(100, 0),
            trade_intensity: Decimal::new(1, 1),
            latency_ms: 400,
            order_size: Decimal::new(50, 0),
            hawkes_intensity: Decimal::new(1, 1),
        };

        let good = estimate_fill(&favorable).unwrap();
        let bad = estimate_fill(&adverse).unwrap();
        assert!(good.fill_probability > bad.fill_probability);
        assert!(good.expected_slippage_bps < bad.expected_slippage_bps);
    }

    #[test]
    fn fill_model_never_assumes_instant_full_fill() {
        let estimate = estimate_fill(&FillModelInput {
            spread_bps: Decimal::ZERO,
            queue_position: Decimal::ZERO,
            market_volume: Decimal::new(10_000, 0),
            trade_intensity: Decimal::new(10, 0),
            latency_ms: 0,
            order_size: Decimal::ONE,
            hawkes_intensity: Decimal::new(10, 0),
        })
        .unwrap();

        assert!(estimate.fill_probability < Decimal::ONE);
        assert!(estimate.expected_fill_quantity < Decimal::ONE);
    }
}
