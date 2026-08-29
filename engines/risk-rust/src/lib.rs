use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum KillSwitchState {
    Armed,
    Triggered,
    Locked,
    ResetPending,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RiskLimits {
    pub max_position: Decimal,
    pub max_notional: Decimal,
    pub max_order_size: Decimal,
    pub max_daily_drawdown: Decimal,
    pub minimum_net_edge: Decimal,
    pub minimum_confidence: Decimal,
    pub minimum_liquidity: Decimal,
    pub max_latency_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RiskRequest {
    pub command_id: Uuid,
    pub idempotency_key: String,
    pub current_position: Decimal,
    pub order_size: Decimal,
    pub order_notional: Decimal,
    pub session_drawdown: Decimal,
    pub net_edge: Decimal,
    pub confidence: Decimal,
    pub liquidity_score: Decimal,
    pub latency_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum RejectionReason {
    KillSwitch,
    PositionLimit,
    NotionalLimit,
    OrderSizeLimit,
    DrawdownLimit,
    EdgeTooSmall,
    ConfidenceTooLow,
    LiquidityTooLow,
    LatencyTooHigh,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum RiskDecision {
    Approved,
    Rejected(Vec<RejectionReason>),
}

#[derive(Debug, Clone)]
pub struct RiskManager {
    pub limits: RiskLimits,
    pub kill_switch: KillSwitchState,
}

impl RiskManager {
    pub fn evaluate(&self, request: &RiskRequest) -> RiskDecision {
        let mut reasons = Vec::new();

        if self.kill_switch != KillSwitchState::Armed {
            reasons.push(RejectionReason::KillSwitch);
        }
        if (request.current_position + request.order_size).abs() > self.limits.max_position {
            reasons.push(RejectionReason::PositionLimit);
        }
        if request.order_notional.abs() > self.limits.max_notional {
            reasons.push(RejectionReason::NotionalLimit);
        }
        if request.order_size.abs() > self.limits.max_order_size {
            reasons.push(RejectionReason::OrderSizeLimit);
        }
        if request.session_drawdown.abs() > self.limits.max_daily_drawdown {
            reasons.push(RejectionReason::DrawdownLimit);
        }
        if request.net_edge <= self.limits.minimum_net_edge {
            reasons.push(RejectionReason::EdgeTooSmall);
        }
        if request.confidence < self.limits.minimum_confidence {
            reasons.push(RejectionReason::ConfidenceTooLow);
        }
        if request.liquidity_score < self.limits.minimum_liquidity {
            reasons.push(RejectionReason::LiquidityTooLow);
        }
        if request.latency_ms > self.limits.max_latency_ms {
            reasons.push(RejectionReason::LatencyTooHigh);
        }

        if reasons.is_empty() {
            RiskDecision::Approved
        } else {
            RiskDecision::Rejected(reasons)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rust_decimal::Decimal;

    fn manager() -> RiskManager {
        RiskManager {
            kill_switch: KillSwitchState::Armed,
            limits: RiskLimits {
                max_position: Decimal::new(10, 0),
                max_notional: Decimal::new(100_000, 0),
                max_order_size: Decimal::new(2, 0),
                max_daily_drawdown: Decimal::new(5_000, 0),
                minimum_net_edge: Decimal::new(1, 2),
                minimum_confidence: Decimal::new(55, 2),
                minimum_liquidity: Decimal::new(50, 2),
                max_latency_ms: 250,
            },
        }
    }

    #[test]
    fn approves_request_inside_all_limits() {
        let request = RiskRequest {
            command_id: Uuid::new_v4(),
            idempotency_key: "a".into(),
            current_position: Decimal::ZERO,
            order_size: Decimal::ONE,
            order_notional: Decimal::new(50_000, 0),
            session_drawdown: Decimal::ZERO,
            net_edge: Decimal::new(4, 2),
            confidence: Decimal::new(70, 2),
            liquidity_score: Decimal::new(80, 2),
            latency_ms: 20,
        };
        assert_eq!(manager().evaluate(&request), RiskDecision::Approved);
    }

    #[test]
    fn kill_switch_blocks_everything() {
        let mut rm = manager();
        rm.kill_switch = KillSwitchState::Triggered;
        let request = RiskRequest {
            command_id: Uuid::new_v4(),
            idempotency_key: "b".into(),
            current_position: Decimal::ZERO,
            order_size: Decimal::ONE,
            order_notional: Decimal::ONE,
            session_drawdown: Decimal::ZERO,
            net_edge: Decimal::ONE,
            confidence: Decimal::ONE,
            liquidity_score: Decimal::ONE,
            latency_ms: 1,
        };
        assert!(matches!(rm.evaluate(&request), RiskDecision::Rejected(_)));
    }
}
