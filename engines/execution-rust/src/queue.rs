use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct QueueConfig {
    /// Fraction of observed cancellations ahead credited toward queue advancement.
    /// Zero is maximally conservative; one assumes all reported cancellations were ahead.
    pub cancellation_credit: Decimal,
}

impl Default for QueueConfig {
    fn default() -> Self {
        Self {
            cancellation_credit: Decimal::new(5, 1),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct QueueEvent {
    /// Aggressive traded quantity executed at this exact resting price level.
    pub traded_quantity: Decimal,
    /// Displayed quantity removed from this price level without trading.
    pub canceled_quantity: Decimal,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct QueueOrder {
    pub quantity: Decimal,
    pub filled_quantity: Decimal,
    pub queue_ahead: Decimal,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct QueueFillOutcome {
    pub queue_ahead_before: Decimal,
    pub queue_ahead_after: Decimal,
    pub newly_filled_quantity: Decimal,
    pub total_filled_quantity: Decimal,
    pub remaining_quantity: Decimal,
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum QueueModelError {
    #[error("quantity must be positive and queue ahead must be non-negative")]
    InvalidOrder,
    #[error("queue event quantities must be non-negative")]
    InvalidEvent,
    #[error("cancellation credit must be between zero and one")]
    InvalidConfig,
}

impl QueueOrder {
    pub fn new(quantity: Decimal, queue_ahead: Decimal) -> Result<Self, QueueModelError> {
        if quantity <= Decimal::ZERO || queue_ahead < Decimal::ZERO {
            return Err(QueueModelError::InvalidOrder);
        }
        Ok(Self {
            quantity,
            filled_quantity: Decimal::ZERO,
            queue_ahead,
        })
    }

    pub fn remaining_quantity(&self) -> Decimal {
        self.quantity - self.filled_quantity
    }

    /// Applies a single event at the order's exact resting price level.
    ///
    /// Cancellations advance queue position according to `cancellation_credit` but never fill
    /// the order. Aggressive traded quantity first consumes queue ahead; only residual traded
    /// quantity can fill the resting order. This keeps fill sequencing deterministic and
    /// prevents top-of-book size from being treated as instantly available to a resting order.
    pub fn apply_event(
        &mut self,
        event: QueueEvent,
        config: QueueConfig,
    ) -> Result<QueueFillOutcome, QueueModelError> {
        if event.traded_quantity < Decimal::ZERO || event.canceled_quantity < Decimal::ZERO {
            return Err(QueueModelError::InvalidEvent);
        }
        if config.cancellation_credit < Decimal::ZERO
            || config.cancellation_credit > Decimal::ONE
        {
            return Err(QueueModelError::InvalidConfig);
        }

        let queue_ahead_before = self.queue_ahead;
        let credited_cancellations = event.canceled_quantity * config.cancellation_credit;
        self.queue_ahead = (self.queue_ahead - credited_cancellations).max(Decimal::ZERO);

        let traded_into_queue = event.traded_quantity.min(self.queue_ahead);
        self.queue_ahead -= traded_into_queue;
        let residual_traded = event.traded_quantity - traded_into_queue;

        let newly_filled = residual_traded.min(self.remaining_quantity());
        self.filled_quantity += newly_filled;

        Ok(QueueFillOutcome {
            queue_ahead_before,
            queue_ahead_after: self.queue_ahead,
            newly_filled_quantity: newly_filled,
            total_filled_quantity: self.filled_quantity,
            remaining_quantity: self.remaining_quantity(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(value: i64) -> Decimal {
        Decimal::new(value, 0)
    }

    #[test]
    fn traded_volume_must_consume_queue_ahead_before_filling() {
        let mut order = QueueOrder::new(d(3), d(5)).unwrap();
        let first = order
            .apply_event(
                QueueEvent {
                    traded_quantity: d(4),
                    canceled_quantity: Decimal::ZERO,
                },
                QueueConfig::default(),
            )
            .unwrap();

        assert_eq!(first.queue_ahead_after, d(1));
        assert_eq!(first.newly_filled_quantity, Decimal::ZERO);

        let second = order
            .apply_event(
                QueueEvent {
                    traded_quantity: d(3),
                    canceled_quantity: Decimal::ZERO,
                },
                QueueConfig::default(),
            )
            .unwrap();

        assert_eq!(second.queue_ahead_after, Decimal::ZERO);
        assert_eq!(second.newly_filled_quantity, d(2));
        assert_eq!(second.remaining_quantity, d(1));
    }

    #[test]
    fn cancellations_advance_queue_but_never_create_fill() {
        let mut order = QueueOrder::new(d(2), d(10)).unwrap();
        let outcome = order
            .apply_event(
                QueueEvent {
                    traded_quantity: Decimal::ZERO,
                    canceled_quantity: d(8),
                },
                QueueConfig {
                    cancellation_credit: Decimal::new(5, 1),
                },
            )
            .unwrap();

        assert_eq!(outcome.queue_ahead_after, d(6));
        assert_eq!(outcome.newly_filled_quantity, Decimal::ZERO);
    }

    #[test]
    fn fill_is_capped_at_remaining_order_quantity() {
        let mut order = QueueOrder::new(d(2), Decimal::ZERO).unwrap();
        let outcome = order
            .apply_event(
                QueueEvent {
                    traded_quantity: d(10),
                    canceled_quantity: Decimal::ZERO,
                },
                QueueConfig::default(),
            )
            .unwrap();

        assert_eq!(outcome.newly_filled_quantity, d(2));
        assert_eq!(outcome.remaining_quantity, Decimal::ZERO);
    }

    #[test]
    fn zero_cancellation_credit_is_conservative() {
        let mut order = QueueOrder::new(d(1), d(5)).unwrap();
        let outcome = order
            .apply_event(
                QueueEvent {
                    traded_quantity: Decimal::ZERO,
                    canceled_quantity: d(5),
                },
                QueueConfig {
                    cancellation_credit: Decimal::ZERO,
                },
            )
            .unwrap();

        assert_eq!(outcome.queue_ahead_after, d(5));
    }

    #[test]
    fn invalid_inputs_fail_closed() {
        assert_eq!(
            QueueOrder::new(Decimal::ZERO, Decimal::ZERO),
            Err(QueueModelError::InvalidOrder)
        );

        let mut order = QueueOrder::new(d(1), Decimal::ZERO).unwrap();
        assert_eq!(
            order.apply_event(
                QueueEvent {
                    traded_quantity: Decimal::new(-1, 0),
                    canceled_quantity: Decimal::ZERO,
                },
                QueueConfig::default(),
            ),
            Err(QueueModelError::InvalidEvent)
        );
    }
}
