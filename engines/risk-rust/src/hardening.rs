use std::collections::HashMap;

use rust_decimal::Decimal;
use uuid::Uuid;

use crate::{RejectionReason, RiskDecision, RiskManager, RiskRequest};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExposureReservation {
    pub reservation_id: Uuid,
    pub market_key: String,
    pub asset_key: String,
    pub cluster_key: String,
    pub position_delta: Decimal,
    pub notional: Decimal,
}

impl ExposureReservation {
    pub fn new(
        market_key: impl Into<String>,
        asset_key: impl Into<String>,
        cluster_key: impl Into<String>,
        position_delta: Decimal,
        notional: Decimal,
    ) -> Self {
        Self {
            reservation_id: Uuid::new_v4(),
            market_key: market_key.into(),
            asset_key: asset_key.into(),
            cluster_key: cluster_key.into(),
            position_delta,
            notional: notional.abs(),
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ReservedExposure {
    pub position: Decimal,
    pub market_notional: Decimal,
    pub asset_notional: Decimal,
    pub cluster_notional: Decimal,
    pub total_notional: Decimal,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GateRejection {
    NotReconciled,
    Risk(Vec<RejectionReason>),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GateDecision {
    Approved,
    Rejected(Vec<GateRejection>),
}

#[derive(Debug, Clone, Default)]
struct BatchExposure {
    position_by_market: HashMap<String, Decimal>,
    notional_by_market: HashMap<String, Decimal>,
    notional_by_asset: HashMap<String, Decimal>,
    notional_by_cluster: HashMap<String, Decimal>,
    total_notional: Decimal,
}

impl BatchExposure {
    fn apply(&mut self, item: &BatchRiskRequest<'_>) {
        let notional = item.request.order_notional.abs();
        *self
            .position_by_market
            .entry(item.market_key.to_owned())
            .or_default() += item.request.order_size;
        *self
            .notional_by_market
            .entry(item.market_key.to_owned())
            .or_default() += notional;
        *self
            .notional_by_asset
            .entry(item.asset_key.to_owned())
            .or_default() += notional;
        *self
            .notional_by_cluster
            .entry(item.cluster_key.to_owned())
            .or_default() += notional;
        self.total_notional += notional;
    }

    fn for_keys(&self, market_key: &str, asset_key: &str, cluster_key: &str) -> ReservedExposure {
        ReservedExposure {
            position: self
                .position_by_market
                .get(market_key)
                .copied()
                .unwrap_or(Decimal::ZERO),
            market_notional: self
                .notional_by_market
                .get(market_key)
                .copied()
                .unwrap_or(Decimal::ZERO),
            asset_notional: self
                .notional_by_asset
                .get(asset_key)
                .copied()
                .unwrap_or(Decimal::ZERO),
            cluster_notional: self
                .notional_by_cluster
                .get(cluster_key)
                .copied()
                .unwrap_or(Decimal::ZERO),
            total_notional: self.total_notional,
        }
    }
}

/// Stateful fail-closed wrapper around the deterministic `RiskManager`.
///
/// This layer closes three production-risk gaps that a single stateless
/// evaluation cannot cover by itself:
/// - startup/recovery remains fail-closed until explicit reconciliation;
/// - accepted-but-not-yet-filled orders reserve capacity immediately;
/// - simultaneous candidates are evaluated cumulatively as one atomic batch.
#[derive(Debug, Clone)]
pub struct ReservationAwareRiskGate {
    manager: RiskManager,
    reconciled: bool,
    reservations: HashMap<Uuid, ExposureReservation>,
}

impl ReservationAwareRiskGate {
    pub fn new(manager: RiskManager) -> Self {
        Self {
            manager,
            reconciled: false,
            reservations: HashMap::new(),
        }
    }

    /// Simulation/replay constructor where a flat external state is part of
    /// the deterministic fixture rather than an unknown live state.
    pub fn new_presumed_reconciled(manager: RiskManager) -> Self {
        Self {
            manager,
            reconciled: true,
            reservations: HashMap::new(),
        }
    }

    pub fn is_reconciled(&self) -> bool {
        self.reconciled
    }

    pub fn mark_unreconciled(&mut self) {
        self.reconciled = false;
    }

    /// Call only after positions and all working orders have been reconciled
    /// into the state used to construct incoming `RiskRequest`s.
    pub fn mark_reconciled(&mut self) {
        self.reconciled = true;
    }

    pub fn manager(&self) -> &RiskManager {
        &self.manager
    }

    pub fn manager_mut(&mut self) -> &mut RiskManager {
        &mut self.manager
    }

    pub fn reservation_count(&self) -> usize {
        self.reservations.len()
    }

    pub fn reserve(&mut self, reservation: ExposureReservation) -> Uuid {
        let id = reservation.reservation_id;
        self.reservations.insert(id, reservation);
        id
    }

    pub fn release(&mut self, reservation_id: Uuid) -> Option<ExposureReservation> {
        self.reservations.remove(&reservation_id)
    }

    pub fn reserved_exposure(
        &self,
        market_key: &str,
        asset_key: &str,
        cluster_key: &str,
    ) -> ReservedExposure {
        let mut exposure = ReservedExposure::default();
        for reservation in self.reservations.values() {
            exposure.total_notional += reservation.notional;
            if reservation.market_key == market_key {
                exposure.market_notional += reservation.notional;
                exposure.position += reservation.position_delta;
            }
            if reservation.asset_key == asset_key {
                exposure.asset_notional += reservation.notional;
            }
            if reservation.cluster_key == cluster_key {
                exposure.cluster_notional += reservation.notional;
            }
        }
        exposure
    }

    pub fn evaluate(
        &self,
        request: &RiskRequest,
        market_key: &str,
        asset_key: &str,
        cluster_key: &str,
    ) -> GateDecision {
        if !self.reconciled {
            return GateDecision::Rejected(vec![GateRejection::NotReconciled]);
        }

        let adjusted = self.with_reserved_exposure(
            request,
            market_key,
            asset_key,
            cluster_key,
            &ReservedExposure::default(),
        );
        Self::decision_from_risk(self.manager.evaluate(&adjusted))
    }

    pub fn evaluate_with_volatility(
        &self,
        request: &RiskRequest,
        market_key: &str,
        asset_key: &str,
        cluster_key: &str,
        observed_volatility: Decimal,
        max_volatility: Decimal,
    ) -> GateDecision {
        if !self.reconciled {
            return GateDecision::Rejected(vec![GateRejection::NotReconciled]);
        }

        let adjusted = self.with_reserved_exposure(
            request,
            market_key,
            asset_key,
            cluster_key,
            &ReservedExposure::default(),
        );
        Self::decision_from_risk(self.manager.evaluate_with_volatility(
            &adjusted,
            observed_volatility,
            max_volatility,
        ))
    }

    /// Evaluate all candidates against one cumulative hypothetical exposure
    /// state. No reservation is committed here; callers reserve only after
    /// the entire batch clears.
    pub fn evaluate_batch<'a, I>(&self, requests: I) -> GateDecision
    where
        I: IntoIterator<Item = BatchRiskRequest<'a>>,
    {
        if !self.reconciled {
            return GateDecision::Rejected(vec![GateRejection::NotReconciled]);
        }

        let mut cumulative = BatchExposure::default();
        let mut failures = Vec::new();

        for item in requests {
            let batch_for_keys =
                cumulative.for_keys(item.market_key, item.asset_key, item.cluster_key);
            let adjusted = self.with_reserved_exposure(
                item.request,
                item.market_key,
                item.asset_key,
                item.cluster_key,
                &batch_for_keys,
            );

            if let RiskDecision::Rejected(reasons) = self.manager.evaluate(&adjusted) {
                failures.push(GateRejection::Risk(reasons));
                continue;
            }

            cumulative.apply(&item);
        }

        if failures.is_empty() {
            GateDecision::Approved
        } else {
            GateDecision::Rejected(failures)
        }
    }

    /// Evaluate simultaneous candidates cumulatively while applying each
    /// candidate's market-specific volatility ceiling.
    pub fn evaluate_batch_with_volatility<'a, I>(&self, requests: I) -> GateDecision
    where
        I: IntoIterator<Item = VolatilityBatchRiskRequest<'a>>,
    {
        if !self.reconciled {
            return GateDecision::Rejected(vec![GateRejection::NotReconciled]);
        }

        let mut cumulative = BatchExposure::default();
        let mut failures = Vec::new();

        for item in requests {
            let batch_for_keys =
                cumulative.for_keys(item.market_key, item.asset_key, item.cluster_key);
            let adjusted = self.with_reserved_exposure(
                item.request,
                item.market_key,
                item.asset_key,
                item.cluster_key,
                &batch_for_keys,
            );

            if let RiskDecision::Rejected(reasons) = self.manager.evaluate_with_volatility(
                &adjusted,
                item.observed_volatility,
                item.max_volatility,
            ) {
                failures.push(GateRejection::Risk(reasons));
                continue;
            }

            cumulative.apply(&BatchRiskRequest {
                request: item.request,
                market_key: item.market_key,
                asset_key: item.asset_key,
                cluster_key: item.cluster_key,
            });
        }

        if failures.is_empty() {
            GateDecision::Approved
        } else {
            GateDecision::Rejected(failures)
        }
    }

    fn with_reserved_exposure(
        &self,
        request: &RiskRequest,
        market_key: &str,
        asset_key: &str,
        cluster_key: &str,
        cumulative: &ReservedExposure,
    ) -> RiskRequest {
        let reserved = self.reserved_exposure(market_key, asset_key, cluster_key);
        let mut adjusted = request.clone();
        adjusted.current_position += reserved.position + cumulative.position;
        adjusted.current_market_exposure += reserved.market_notional + cumulative.market_notional;
        adjusted.current_asset_exposure += reserved.asset_notional + cumulative.asset_notional;
        adjusted.current_total_exposure += reserved.total_notional + cumulative.total_notional;
        adjusted.correlated_exposure += reserved.cluster_notional + cumulative.cluster_notional;
        adjusted
    }

    fn decision_from_risk(decision: RiskDecision) -> GateDecision {
        match decision {
            RiskDecision::Approved => GateDecision::Approved,
            RiskDecision::Rejected(reasons) => {
                GateDecision::Rejected(vec![GateRejection::Risk(reasons)])
            }
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct BatchRiskRequest<'a> {
    pub request: &'a RiskRequest,
    pub market_key: &'a str,
    pub asset_key: &'a str,
    pub cluster_key: &'a str,
}

#[derive(Debug, Clone, Copy)]
pub struct VolatilityBatchRiskRequest<'a> {
    pub request: &'a RiskRequest,
    pub market_key: &'a str,
    pub asset_key: &'a str,
    pub cluster_key: &'a str,
    pub observed_volatility: Decimal,
    pub max_volatility: Decimal,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{KillSwitchState, RiskLimits};

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

    fn request(notional: i64) -> RiskRequest {
        RiskRequest {
            command_id: Uuid::new_v4(),
            idempotency_key: Uuid::new_v4().to_string(),
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
        }
    }

    #[test]
    fn starts_fail_closed_until_reconciled() {
        let gate = ReservationAwareRiskGate::new(manager());
        assert_eq!(
            gate.evaluate(&request(10_000), "btc-usd", "btc", "crypto"),
            GateDecision::Rejected(vec![GateRejection::NotReconciled])
        );
    }

    #[test]
    fn working_order_reservation_consumes_capacity_before_fill() {
        let mut gate = ReservationAwareRiskGate::new_presumed_reconciled(manager());
        gate.reserve(ExposureReservation::new(
            "btc-usd",
            "btc",
            "crypto",
            Decimal::ONE,
            Decimal::new(70_000, 0),
        ));

        let decision = gate.evaluate(&request(10_000), "btc-usd", "btc", "crypto");
        assert!(matches!(
            decision,
            GateDecision::Rejected(reasons)
                if reasons.iter().any(|reason| matches!(
                    reason,
                    GateRejection::Risk(inner)
                        if inner.contains(&RejectionReason::MarketExposureLimit)
                ))
        ));
    }

    #[test]
    fn release_returns_reserved_capacity() {
        let mut gate = ReservationAwareRiskGate::new_presumed_reconciled(manager());
        let id = gate.reserve(ExposureReservation::new(
            "btc-usd",
            "btc",
            "crypto",
            Decimal::ONE,
            Decimal::new(70_000, 0),
        ));
        assert_eq!(gate.reservation_count(), 1);
        assert!(gate.release(id).is_some());
        assert_eq!(gate.reservation_count(), 0);
        assert_eq!(
            gate.evaluate(&request(10_000), "btc-usd", "btc", "crypto"),
            GateDecision::Approved
        );
    }

    #[test]
    fn batch_evaluation_catches_joint_cluster_breach() {
        let gate = ReservationAwareRiskGate::new_presumed_reconciled(manager());
        let first = request(60_000);
        let second = request(60_000);

        let decision = gate.evaluate_batch([
            BatchRiskRequest {
                request: &first,
                market_key: "btc-usd",
                asset_key: "btc",
                cluster_key: "crypto",
            },
            BatchRiskRequest {
                request: &second,
                market_key: "eth-usd",
                asset_key: "eth",
                cluster_key: "crypto",
            },
        ]);

        assert!(matches!(
            decision,
            GateDecision::Rejected(reasons)
                if reasons.iter().any(|reason| matches!(
                    reason,
                    GateRejection::Risk(inner)
                        if inner.contains(&RejectionReason::CorrelatedExposureLimit)
                ))
        ));
    }

    #[test]
    fn batch_does_not_mix_independent_market_limits() {
        let gate = ReservationAwareRiskGate::new_presumed_reconciled(manager());
        let first = request(60_000);
        let second = request(60_000);
        let decision = gate.evaluate_batch([
            BatchRiskRequest {
                request: &first,
                market_key: "btc-usd",
                asset_key: "btc",
                cluster_key: "crypto-a",
            },
            BatchRiskRequest {
                request: &second,
                market_key: "gold-usd",
                asset_key: "gold",
                cluster_key: "metals",
            },
        ]);
        assert_eq!(decision, GateDecision::Approved);
    }

    #[test]
    fn reservation_aware_gate_rejects_volatility_breach() {
        let gate = ReservationAwareRiskGate::new_presumed_reconciled(manager());
        let decision = gate.evaluate_with_volatility(
            &request(10_000),
            "btc-usd",
            "btc",
            "crypto",
            Decimal::new(90, 2),
            Decimal::new(60, 2),
        );

        assert!(matches!(
            decision,
            GateDecision::Rejected(reasons)
                if reasons.iter().any(|reason| matches!(
                    reason,
                    GateRejection::Risk(inner)
                        if inner.contains(&RejectionReason::VolatilityTooHigh)
                ))
        ));
    }

    #[test]
    fn reservation_aware_volatility_gate_remains_fail_closed_before_reconciliation() {
        let gate = ReservationAwareRiskGate::new(manager());
        assert_eq!(
            gate.evaluate_with_volatility(
                &request(10_000),
                "btc-usd",
                "btc",
                "crypto",
                Decimal::new(20, 2),
                Decimal::new(60, 2),
            ),
            GateDecision::Rejected(vec![GateRejection::NotReconciled])
        );
    }

    #[test]
    fn volatility_batch_rejects_only_breaching_candidate() {
        let gate = ReservationAwareRiskGate::new_presumed_reconciled(manager());
        let first = request(20_000);
        let second = request(20_000);
        let decision = gate.evaluate_batch_with_volatility([
            VolatilityBatchRiskRequest {
                request: &first,
                market_key: "btc-usd",
                asset_key: "btc",
                cluster_key: "crypto-a",
                observed_volatility: Decimal::new(40, 2),
                max_volatility: Decimal::new(60, 2),
            },
            VolatilityBatchRiskRequest {
                request: &second,
                market_key: "eth-usd",
                asset_key: "eth",
                cluster_key: "crypto-b",
                observed_volatility: Decimal::new(95, 2),
                max_volatility: Decimal::new(60, 2),
            },
        ]);

        assert!(matches!(
            decision,
            GateDecision::Rejected(reasons)
                if reasons.len() == 1
                    && reasons.iter().any(|reason| matches!(
                        reason,
                        GateRejection::Risk(inner)
                            if inner.contains(&RejectionReason::VolatilityTooHigh)
                    ))
        ));
    }
}
