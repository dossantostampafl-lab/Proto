use rust_decimal::Decimal;
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

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum TransitionError {
    #[error("invalid order state transition: {0:?} -> {1:?}")]
    Invalid(OrderState, OrderState),
    #[error("fill quantity must be positive and not exceed remaining quantity")]
    InvalidFill,
}

impl SimulatedOrder {
    pub fn new(command_id: Uuid, idempotency_key: String, price: Decimal, quantity: Decimal) -> Self {
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
        self.filled_quantity += quantity;
        let next = if self.filled_quantity == self.quantity {
            OrderState::Filled
        } else {
            OrderState::PartiallyFilled
        };
        self.transition(next)
    }
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
        let mut order = SimulatedOrder::new(
            Uuid::new_v4(),
            "cmd-2".into(),
            Decimal::ONE,
            Decimal::ONE,
        );
        order.transition(OrderState::Validated).unwrap();
        order.transition(OrderState::Queued).unwrap();
        order.transition(OrderState::Resting).unwrap();
        assert_eq!(order.apply_fill(Decimal::new(2, 0)), Err(TransitionError::InvalidFill));
    }
}
