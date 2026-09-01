use std::collections::HashMap;

use risk_rust::hardening::{ExposureReservation, GateDecision, ReservationAwareRiskGate};
use risk_rust::RiskRequest;
use rust_decimal::Decimal;
use uuid::Uuid;

use crate::{OrderState, SimulatedOrder, TransitionError};

#[derive(Debug, Clone)]
pub struct ExecutionCandidate {
    pub request: RiskRequest,
    pub market_key: String,
    pub asset_key: String,
    pub cluster_key: String,
    pub observed_volatility: Decimal,
    pub max_volatility: Decimal,
    pub price: Decimal,
    pub quantity: Decimal,
}

#[derive(Debug)]
pub enum AdmissionDecision {
    Approved {
        order: SimulatedOrder,
        reservation_id: Uuid,
    },
    Rejected(GateDecision),
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum AdmissionError {
    #[error("execution candidate price and quantity must be positive")]
    InvalidOrderShape,
    #[error("candidate quantity must equal the risk request order size")]
    QuantityMismatch,
    #[error("order validation transition failed: {0}")]
    Transition(#[from] TransitionError),
}

#[derive(Debug)]
pub struct ExecutionAdmissionGate {
    risk_gate: ReservationAwareRiskGate,
    reservations_by_order: HashMap<Uuid, Uuid>,
}

impl ExecutionAdmissionGate {
    pub fn new(risk_gate: ReservationAwareRiskGate) -> Self {
        Self {
            risk_gate,
            reservations_by_order: HashMap::new(),
        }
    }

    pub fn risk_gate(&self) -> &ReservationAwareRiskGate {
        &self.risk_gate
    }

    pub fn risk_gate_mut(&mut self) -> &mut ReservationAwareRiskGate {
        &mut self.risk_gate
    }

    pub fn active_reservations(&self) -> usize {
        self.reservations_by_order.len()
    }

    pub fn admit(
        &mut self,
        candidate: ExecutionCandidate,
    ) -> Result<AdmissionDecision, AdmissionError> {
        if candidate.price <= Decimal::ZERO || candidate.quantity <= Decimal::ZERO {
            return Err(AdmissionError::InvalidOrderShape);
        }
        if candidate.quantity != candidate.request.order_size.abs() {
            return Err(AdmissionError::QuantityMismatch);
        }

        let decision = self.risk_gate.evaluate_with_volatility(
            &candidate.request,
            &candidate.market_key,
            &candidate.asset_key,
            &candidate.cluster_key,
            candidate.observed_volatility,
            candidate.max_volatility,
        );
        if decision != GateDecision::Approved {
            return Ok(AdmissionDecision::Rejected(decision));
        }

        let mut order = SimulatedOrder::new(
            candidate.request.command_id,
            candidate.request.idempotency_key.clone(),
            candidate.price,
            candidate.quantity,
        );
        order.transition(OrderState::Validated)?;

        let reservation_id = self.risk_gate.reserve(ExposureReservation::new(
            candidate.market_key,
            candidate.asset_key,
            candidate.cluster_key,
            candidate.request.order_size,
            candidate.request.order_notional,
        ));
        self.reservations_by_order
            .insert(order.order_id, reservation_id);

        Ok(AdmissionDecision::Approved {
            order,
            reservation_id,
        })
    }

    pub fn release_order(&mut self, order_id: Uuid) -> bool {
        let Some(reservation_id) = self.reservations_by_order.remove(&order_id) else {
            return false;
        };
        self.risk_gate.release(reservation_id).is_some()
    }
}

#[cfg(test)]
mod tests {
    use risk_rust::hardening::{GateRejection, ReservationAwareRiskGate};
    use risk_rust::{KillSwitchState, RejectionReason, RiskLimits, RiskManager};

    use super::*;

    fn manager() -> RiskManager {
        RiskManager {
            kill_switch: KillSwitchState::Armed,
            limits: RiskLimits {
                max_position: Decimal::new(10, 0),
                max_notional: Decimal::new(100_000, 0),
                max_order_size: Decimal::new(3, 0),
                max_market_exposure: Decimal::new(75_000, 0),
                max_asset_exposure: Decimal::new(150_000, 0),
                max_total_exposure: Decimal::new(300_000, 0),
                max_correlated_exposure: Decimal::new(110_000, 0),
                max_open_positions: 8,
                max_concentration: Decimal::ONE,
                max_loss_per_session: Decimal::new(7_500, 0),
                max_daily_drawdown: Decimal::new(5_000, 0),
                minimum_net_edge: Decimal::new(1, 2),
                minimum_confidence: Decimal::new(55, 2),
                minimum_liquidity: Decimal::new(50, 2),
                max_latency_ms: 250,
            },
        }
    }

    fn candidate(notional: i64, volatility: Decimal) -> ExecutionCandidate {
        let command_id = Uuid::new_v4();
        ExecutionCandidate {
            request: RiskRequest {
                command_id,
                idempotency_key: command_id.to_string(),
                current_position: Decimal::ZERO,
                order_size: Decimal::ONE,
                order_notional: Decimal::new(notional, 0),
                current_market_exposure: Decimal::ZERO,
                current_asset_exposure: Decimal::ZERO,
                current_total_exposure: Decimal::ZERO,
                correlated_exposure: Decimal::ZERO,
                open_positions: 0,
                session_loss: Decimal::ZERO,
                session_drawdown: Decimal::ZERO,
                net_edge: Decimal::new(4, 2),
                confidence: Decimal::new(80, 2),
                liquidity_score: Decimal::new(90, 2),
                latency_ms: 10,
            },
            market_key: "btc-usd".into(),
            asset_key: "btc".into(),
            cluster_key: "crypto".into(),
            observed_volatility: volatility,
            max_volatility: Decimal::new(40, 2),
            price: Decimal::new(60_000, 0),
            quantity: Decimal::ONE,
        }
    }

    #[test]
    fn admission_validates_and_reserves_capacity() {
        let risk = ReservationAwareRiskGate::new_presumed_reconciled(manager());
        let mut gate = ExecutionAdmissionGate::new(risk);
        let decision = gate
            .admit(candidate(50_000, Decimal::new(30, 2)))
            .unwrap();

        let AdmissionDecision::Approved { order, .. } = decision else {
            panic!("expected approved execution admission");
        };
        assert_eq!(order.state, OrderState::Validated);
        assert_eq!(gate.active_reservations(), 1);
        assert_eq!(gate.risk_gate().reservation_count(), 1);
        assert!(gate.release_order(order.order_id));
        assert_eq!(gate.active_reservations(), 0);
        assert_eq!(gate.risk_gate().reservation_count(), 0);
    }

    #[test]
    fn high_volatility_cannot_create_validated_order() {
        let risk = ReservationAwareRiskGate::new_presumed_reconciled(manager());
        let mut gate = ExecutionAdmissionGate::new(risk);
        let decision = gate
            .admit(candidate(20_000, Decimal::new(60, 2)))
            .unwrap();

        assert!(matches!(
            decision,
            AdmissionDecision::Rejected(GateDecision::Rejected(reasons))
                if reasons.iter().any(|reason| matches!(
                    reason,
                    GateRejection::Risk(inner)
                        if inner.contains(&RejectionReason::VolatilityTooHigh)
                ))
        ));
        assert_eq!(gate.active_reservations(), 0);
    }

    #[test]
    fn unreconciled_state_cannot_create_validated_order() {
        let risk = ReservationAwareRiskGate::new(manager());
        let mut gate = ExecutionAdmissionGate::new(risk);
        let decision = gate
            .admit(candidate(20_000, Decimal::new(20, 2)))
            .unwrap();
        assert_eq!(
            match decision {
                AdmissionDecision::Rejected(decision) => decision,
                AdmissionDecision::Approved { .. } => panic!("unexpected approval"),
            },
            GateDecision::Rejected(vec![GateRejection::NotReconciled])
        );
    }

    #[test]
    fn existing_reservation_blocks_second_order_before_fill() {
        let risk = ReservationAwareRiskGate::new_presumed_reconciled(manager());
        let mut gate = ExecutionAdmissionGate::new(risk);
        let first = gate
            .admit(candidate(70_000, Decimal::new(20, 2)))
            .unwrap();
        assert!(matches!(first, AdmissionDecision::Approved { .. }));

        let second = gate
            .admit(candidate(10_000, Decimal::new(20, 2)))
            .unwrap();
        assert!(matches!(
            second,
            AdmissionDecision::Rejected(GateDecision::Rejected(reasons))
                if reasons.iter().any(|reason| matches!(
                    reason,
                    GateRejection::Risk(inner)
                        if inner.contains(&RejectionReason::MarketExposureLimit)
                ))
        ));
    }
}
