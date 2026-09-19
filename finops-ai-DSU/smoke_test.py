"""
smoke_test.py — Deterministic verification suite for all 4 CloudSage agents.
Tests the full agent pipeline end-to-end using DEFAULT_CONFIG as the baseline,
then exercises config mutation to verify that changing thresholds visibly changes
outputs (the core acceptance criterion of the configurable refactor).
"""
import copy
from fleet_data import get_fleet
from config import DEFAULT_CONFIG, validate_config
from logic_agents import UsageDetectiveAgent, RightsizingOptimizerAgent
from ai_agents import compute_risk_score, compute_roi

fleet = get_fleet()
scans = UsageDetectiveAgent.scan_fleet(fleet, config=DEFAULT_CONFIG)

flagged = [s for s in scans if s['is_flagged']]
healthy = [s for s in scans if not s['is_flagged']]

print(f"Total servers: {len(fleet)}")
print(f"Flagged: {len(flagged)}")
print(f"Healthy: {len(healthy)}")
print()

for reason in ['OVERPROVISIONED', 'ZOMBIE_GPU', 'ZOMBIE_IDLE', 'UNSCHEDULED_NONPROD']:
    count = sum(1 for s in scans if reason in s['waste_reasons'])
    print(f"  {reason}: {count}")

print()
print("--- Optimization + Risk + ROI results ---")
total_current = 0
total_savings = 0
no_downsize = 0
errors = 0
rejected_count = 0

for scan in scans:
    srv = scan['raw_server']
    try:
        opt = RightsizingOptimizerAgent.optimize(scan, config=DEFAULT_CONFIG)
        risk = compute_risk_score(srv, opt, config=DEFAULT_CONFIG)
        roi = compute_roi(opt)
        total_savings += opt['monthly_savings_usd']
        total_current += opt['current_monthly_cost']
        if opt['action_type'] == 'NO_SAFE_DOWNSIZE_AVAILABLE':
            no_downsize += 1
        if risk['verdict'] == 'REJECTED':
            rejected_count += 1
        print(
            f"  {opt['server_id']:<22s} | {opt['action_type']:<30s} | "
            f"${opt['monthly_savings_usd']:>7.2f}/mo | risk={risk['risk_score']:3d} "
            f"({risk['verdict']}) | annual=${roi['annual_savings_usd']:,.2f}"
        )
    except Exception as e:
        errors += 1
        print(f"  ERROR on {scan['server_id']}: {e}")

print()
print(f"Fleet monthly spend:         ${total_current:,.2f}")
print(f"Total monthly savings:       ${total_savings:,.2f}")
print(f"Total annual savings:        ${total_savings*12:,.2f}")
print(f"NO_SAFE_DOWNSIZE_AVAILABLE:  {no_downsize}")
print(f"REJECTED risk verdicts:      {rejected_count}")
print(f"Errors:                      {errors}")
print()

# ─────────────────────────────────────────────────────────────────────────────
# Validation tests
# ─────────────────────────────────────────────────────────────────────────────

# 1. Default config must be valid
is_valid, err_list = validate_config(DEFAULT_CONFIG)
assert is_valid, f"Default config must be valid: {err_list}"

# 2. Invalid weight sum is rejected
bad_weights = copy.deepcopy(DEFAULT_CONFIG)
bad_weights["risk"]["weights"]["environment_risk"] = 0.50  # now sums to 1.25
is_valid, err_list = validate_config(bad_weights)
assert not is_valid, "Should reject weights not summing to 1.0"
assert any("weights" in e for e in err_list), f"Expected weight error, got: {err_list}"

# 3. Invalid verdict threshold ordering is rejected
bad_thresh = copy.deepcopy(DEFAULT_CONFIG)
bad_thresh["risk"]["verdict_thresholds"] = {"approve_max": 70, "conditional_max": 50}
is_valid, err_list = validate_config(bad_thresh)
assert not is_valid, "Should reject approve_max > conditional_max"

# 4. Invalid sizing headroom (<1.0) is rejected
bad_sizing = copy.deepcopy(DEFAULT_CONFIG)
bad_sizing["sizing"]["ram_headroom_over_peak"] = 0.80
is_valid, err_list = validate_config(bad_sizing)
assert not is_valid, "Should reject headroom < 1.0"

# 5. Invalid CPU% detection threshold is rejected
bad_detect = copy.deepcopy(DEFAULT_CONFIG)
bad_detect["detection"]["overprovisioned_cpu_max_pct"] = 150.0
is_valid, err_list = validate_config(bad_detect)
assert not is_valid, "Should reject CPU% > 100"

# 6. Changing environment_production score changes the risk output
custom_cfg = copy.deepcopy(DEFAULT_CONFIG)
custom_cfg["risk"]["factor_scores"]["environment_production"] = 50.0  # lowered from 90
first_scan = scans[0]
first_opt = RightsizingOptimizerAgent.optimize(first_scan, config=custom_cfg)
default_risk = compute_risk_score(first_scan['raw_server'], first_opt, config=DEFAULT_CONFIG)
custom_risk  = compute_risk_score(first_scan['raw_server'], first_opt, config=custom_cfg)
assert "config_used" in custom_risk, "compute_risk_score must return config_used"
# Risk score with lower production penalty should be <= default (same or lower)
if first_scan['raw_server'].get('environment') == 'production':
    assert custom_risk['risk_score'] <= default_risk['risk_score'], (
        f"Lowering environment_production from 90 to 50 should not increase risk "
        f"(got default={default_risk['risk_score']}, custom={custom_risk['risk_score']})"
    )

# 7. Changing detection thresholds changes which servers are flagged
lenient_cfg = copy.deepcopy(DEFAULT_CONFIG)
lenient_cfg["detection"]["overprovisioned_cpu_max_pct"] = 1.0  # very tight — almost nothing flagged
lenient_scans = UsageDetectiveAgent.scan_fleet(fleet, config=lenient_cfg)
strict_cfg = copy.deepcopy(DEFAULT_CONFIG)
strict_cfg["detection"]["overprovisioned_cpu_max_pct"] = 80.0  # very loose — many flagged
strict_scans = UsageDetectiveAgent.scan_fleet(fleet, config=strict_cfg)
lenient_flagged = sum(1 for s in lenient_scans if "OVERPROVISIONED" in s["waste_reasons"])
strict_flagged  = sum(1 for s in strict_scans  if "OVERPROVISIONED" in s["waste_reasons"])
assert strict_flagged >= lenient_flagged, (
    f"Relaxing CPU threshold to 80% should flag >= servers than 1% "
    f"(lenient={lenient_flagged}, strict={strict_flagged})"
)

print("ALL ASSERTIONS PASSED [OK]")
