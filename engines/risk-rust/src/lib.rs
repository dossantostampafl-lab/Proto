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
    pub max_asset_exposure: Decimal,
    pub max_total_exposure: Decimal,
    pub max_correlated_exposure: Decimal,
    pub max_open_positions: u32,
    pub max_concentration: Decimal,
    pub max_loss_per_session: Decimal,
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
    pub current_asset_exposure: Decimal,
    pub current_total_exposure: Decimal,
    pub correlated_exposure: Decimal,
    pub open_positions: u32,
    pub session_loss: Decimal,
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
    AssetExposureLimit,
    TotalExposureLimit,
    CorrelatedExposureLimit,
    OpenPositionsLimit,
    ConcentrationLimit,
    SessionLossLimit,
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
        let projected_position = request.current_position + request.order_size;
        let risk_reducing = projected_position.abs() < request.current_position.abs();
        let projected_asset_exposure = if risk_reducing {
            (request.current_asset_exposure - request.order_notional.abs()).max(Decimal::ZERO)
        } else {
            request.current_asset_exposure + request.order_notional.abs()
        };
        let projected_total_exposure = if risk_reducing {
            (request.current_total_exposure - request.order_notional.abs()).max(Decimal::ZERO)
        } else {
            request.current_total_exposure + request.order_notional.abs()
        };

        if self.kill_switch != KillSwitchState::Armed {
            reasons.push(RejectionReason::KillSwitch);
        }
        if projected_position.abs() > self.limits.max_position {
            reasons.push(RejectionReason::PositionLimit);
        }
        if request.order_notional.abs() > self.limits.max_notional {
            reasons.push(RejectionReason::NotionalLimit);
        }
        if request.order_size.abs() > self.limits.max_order_size {
            reasons.push(RejectionReason::OrderSizeLimit);
        }
        if projected_asset_exposure > self.limits.max_asset_exposure {
            reasons.push(RejectionReason::AssetExposureLimit);
        }
        if projected_total_exposure > self.limits.max_total_exposure {
            reasons.push(RejectionReason::TotalExposureLimit);
        }
        if request.correlated_exposure.abs() > self.limits.max_correlated_exposure {
            reasons.push(RejectionReason::CorrelatedExposureLimit);
        }
        if !risk_reducing && request.open_positions >= self.limits.max_open_positions {
            reasons.push(RejectionReason::OpenPositionsLimit);
        }
        if projected_total_exposure > Decimal::ZERO {
            let concentration = projected_asset_exposure / projected_total_exposure;
            if concentration > self.limits.max_concentration {
                reasons.push(RejectionReason::ConcentrationLimit);
            }
        }
        if request.session_loss.abs() > self.limits.max_loss_per_session {
            reasons.push(RejectionReason::SessionLossLimit);
        }
        if request.session_drawdown.abs() > self.limits.max_daily_drawdown {
            reasons.push(RejectionReason::DrawdownLimit);
        }

        // A closing order that strictly reduces absolute exposure may proceed without
        // alpha-quality gates. Hard safety gates above still apply.
        if !risk_reducing {
            if request.net_edge <= self.limits.minimum_net_edge {
                reasons.push(RejectionReason::EdgeTooSmall);
            }
            if request.confidence < self.limits.minimum_confidence {
                reasons.push(RejectionReason::ConfidenceTooLow);
            }
            if request.liquidity_score < self.limits.minimum_liquidity {
                reasons.push(RejectionReason::LiquidityTooLow);
            }
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

    fn manager() -> RiskManager {
        RiskManager {
            kill_switch: KillSwitchState::Armed,
            limits: RiskLimits {
                max_position: Decimal::new(10, 0),
                max_notional: Decimal::new(100_000, 0),
                max_order_size: Decimal::new(2, 0),
                max_asset_exposure: Decimal::new(150_000, 0),
                max_total_exposure: Decimal::new(300_000, 0),
                max_correlated_exposure: Decimal::new(200_000, 0),
                max_open_positions: 8,
                max_concentration: Decimal::new(75, 2),
                max_loss_per_session: Decimal::new(7_500, 0),
                max_daily_drawdown: Decimal::new(5_000, 0),
                minimum_net_edge: Decimal::new(1, 2),
                minimum_confidence: Decimal::new(55, 2),
                minimum_liquidity: Decimal::new(50, 2),
                max_latency_ms: 250,
            },
        }
    }

    fn request() -> RiskRequest {
        RiskRequest {
            command_id: Uuid::new_v4(),
            idempotency_key: "risk-test".into(),
            current_position: Decimal::ZERO,
            order_size: Decimal::ONE,
            order_notional: Decimal::new(50_000, 0),
            current_asset_exposure: Decimal::ZERO,
            current_total_exposure: Decimal::new(100_000, 0),
            correlated_exposure: Decimal::new(50_000, 0),
            open_positions: 2,
            session_loss: Decimal::ZERO,
            session_drawdown: Decimal::ZERO,
            net_edge: Decimal::new(4, 2),
            confidence: Decimal::new(70, 2),
            liquidity_score: Decimal::new(80, 2),
            latency_ms: 20,
        }
    }

    #[test]
    fn approves_request_inside_all_limits() {
        assert_eq!(manager().evaluate(&request()), RiskDecision::Approved);
    }

    #[test]
    fn kill_switch_blocks_everything() {
        let mut rm = manager();
        rm.kill_switch = KillSwitchState::Triggered;
        assert!(matches!(
            rm.evaluate(&request()),
            RiskDecision::Rejected(reasons) if reasons.contains(&RejectionReason::KillSwitch)
        ));
    }

    #[test]
    fn rejects_aggregate_exposure_and_concentration_breaches() {
        let mut candidate = request();
        candidate.current_asset_exposure = Decimal::new(140_000, 0);
        candidate.current_total_exposure = Decimal::new(260_000, 0);

        let decision = manager().evaluate(&candidate);
        assert!(matches!(
            decision,
            RiskDecision::Rejected(reasons)
                if reasons.contains(&RejectionReason::AssetExposureLimit)
                    && reasons.contains(&RejectionReason::TotalExposureLimit)
        ));
    }

    #[test]
    fn risk_reducing_close_bypasses_alpha_quality_gates() {
        let mut candidate = request();
        candidate.current_position = Decimal::new(2, 0);
        candidate.order_size = Decimal::new(-1, 0);
        candidate.current_asset_exposure = Decimal::new(80_000, 0);
        candidate.current_total_exposure = Decimal::new(120_000, 0);
        candidate.net_edge = Decimal::ZERO;
        candidate.confidence = Decimal::ZERO;
        candidate.liquidity_score = Decimal::ZERO;

        assert_eq!(manager().evaluate(&candidate), RiskDecision::Approved);
    }

    #[test]
    fn risk_reducing_close_still_respects_hard_safety_gates() {
        let mut candidate = request();
        candidate.current_position = Decimal::new(2, 0);
        candidate.order_size = Decimal::new(-1, 0);
        candidate.current_asset_exposure = Decimal::new(80_000, 0);
        candidate.current_total_exposure = Decimal::new(120_000, 0);
        candidate.latency_ms = 500;

        assert!(matches!(
            manager().evaluate(&candidate),
            RiskDecision::Rejected(reasons) if reasons.contains(&RejectionReason::LatencyTooHigh)
        ));
    }
}
