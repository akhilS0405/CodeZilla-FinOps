import copy
from fleet_data import get_fleet
from logic_agents import UsageDetectiveAgent, RightsizingOptimizerAgent
from ai_agents import compute_risk_score, compute_roi
from risk_config_store import validate_risk_config, DEFAULT_RISK_CONFIG

fleet = get_fleet()
scans = UsageDetectiveAgent.scan_fleet(fleet)

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
        opt = RightsizingOptimizerAgent.optimize(scan)
        risk = compute_risk_score(srv, opt)
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

# validate_risk_config and DEFAULT_RISK_CONFIG imported at top of file

# Test Risk Configuration Validation
is_valid, msg = validate_risk_config(DEFAULT_RISK_CONFIG)
assert is_valid, f"Default risk config must be valid: {msg}"

# Test invalid weight sum detection
bad_cfg = copy.deepcopy(DEFAULT_RISK_CONFIG)
bad_cfg["weights"]["environment_risk"] = 0.50  # now sums to 1.25
is_valid, msg = validate_risk_config(bad_cfg)
assert not is_valid, "Should reject weights not summing to 1.0"

# Test invalid threshold ordering
bad_thresh_cfg = copy.deepcopy(DEFAULT_RISK_CONFIG)
bad_thresh_cfg["verdict_thresholds"] = {"low_max": 70, "medium_max": 50}
is_valid, msg = validate_risk_config(bad_thresh_cfg)
assert not is_valid, "Should reject low_max > medium_max"

# Test dynamic policy scoring with custom reject threshold
custom_cfg = copy.deepcopy(DEFAULT_RISK_CONFIG)
custom_cfg["factor_scores"]["environment_production"] = 80  # User query: company wants 90% to be 80%
assert custom_cfg["factor_scores"]["environment_production"] == 80
custom_risk = compute_risk_score(scans[0]['raw_server'], RightsizingOptimizerAgent.optimize(scans[0]), config=custom_cfg)
assert "config_used" in custom_risk, "compute_risk_score must return config_used"

print("ALL ASSERTIONS PASSED [OK]")
