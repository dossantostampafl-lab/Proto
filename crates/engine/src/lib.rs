use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum Side {
    Buy,
    Sell,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct Order {
    pub quantity: f64,
    pub limit_price: f64,
    pub side: Side,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct RiskLimits {
    pub max_order_notional: f64,
    pub max_position_notional: f64,
}

#[derive(Debug, Error, PartialEq)]
pub enum RiskError {
    #[error("order notional exceeds configured limit")]
    OrderNotionalExceeded,
    #[error("position notional exceeds configured limit")]
    PositionNotionalExceeded,
}

pub fn validate_order(
    order: Order,
    current_position_notional: f64,
    limits: RiskLimits,
) -> Result<(), RiskError> {
    let notional = order.quantity * order.limit_price;
    if notional > limits.max_order_notional {
        return Err(RiskError::OrderNotionalExceeded);
    }
    if current_position_notional + notional > limits.max_position_notional {
        return Err(RiskError::PositionNotionalExceeded);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_order_within_limits() {
        let order = Order { quantity: 0.1, limit_price: 60_000.0, side: Side::Buy };
        let limits = RiskLimits { max_order_notional: 10_000.0, max_position_notional: 25_000.0 };
        assert_eq!(validate_order(order, 0.0, limits), Ok(()));
    }

    #[test]
    fn rejects_order_over_limit() {
        let order = Order { quantity: 1.0, limit_price: 60_000.0, side: Side::Buy };
        let limits = RiskLimits { max_order_notional: 10_000.0, max_position_notional: 25_000.0 };
        assert_eq!(validate_order(order, 0.0, limits), Err(RiskError::OrderNotionalExceeded));
    }
}
