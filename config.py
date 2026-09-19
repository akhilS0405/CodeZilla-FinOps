"""
config.py — CloudSage Unified Configuration Store
Single source of truth for every tunable number across all 4 agents.

Sections
--------
detection  : Agent 1 (UsageDetectiveAgent) — waste-detection thresholds
sizing     : Agent 2 (RightsizingOptimizerAgent) — headroom multipliers
scheduling : Agent 2 — business-hour schedule constants
risk       : Agent 3 (SRERiskOfficerAgent) — weights, factor scores, verdict cutoffs
"""

import copy
import json
import os

# ─────────────────────────────────────────────────────────────────────────────
# Default configuration — enterprise-grade, FinOps Foundation aligned
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG: dict = {
    # ── Agent 1: Usage Detective — detection thresholds ─────────────────────
    "detection": {
        "overprovisioned_cpu_max_pct": 15.0,        # below this CPU% → candidate
        "overprovisioned_ram_max_ratio": 0.35,      # below this RAM utilization ratio → candidate
        "zombie_gpu_util_max_pct": 2.0,             # GPU util% below this = idle
        "zombie_gpu_idle_hours_min": 6.0,           # hours of continuous GPU inactivity before flag
        "zombie_network_max_mb_day": 5.0,           # network MB/day below this = dead server
        "unscheduled_hours_per_week_threshold": 168.0,  # 24x7 — non-prod running continuously
    },

    # ── Agent 2: Rightsizing Optimizer — sizing safety margins ───────────────
    "sizing": {
        "ram_headroom_over_peak": 1.30,              # target_ram = peak_ram * this
        "ram_headroom_over_p95": 1.40,              # target_ram = max(above, p95_ram * this)
        "vcpu_headroom_over_peak": 1.25,            # target_vcpu = ceil(peak_vcpu * this)
        "zombie_gpu_hibernate_cost_fraction": 0.10, # retained cost when GPU instance is hibernated
    },

    # ── Agent 2: Business-hours schedule (inputs only — derived values computed) ──
    "scheduling": {
        "full_month_hours": 730.0,       # 24x7 baseline (approx 30.4 days x 24h)
        "business_hours_per_day": 9.0,   # e.g., 9 am to 6 pm
        "business_days_per_week": 5.0,   # Mon-Fri
        "weeks_per_month": 4.33,         # ISO average
        # active_hours_per_month is DERIVED — see compute_active_hours_per_month()
    },

    # ── Agent 3: SRE Risk Officer — weights, factor scores, thresholds ───────
    "risk": {
        "weights": {
            "environment_risk": 0.25,
            "criticality_risk": 0.25,
            "capacity_risk": 0.20,
            "uncertainty_risk": 0.15,
            "dependency_risk": 0.15,
        },
        "factor_scores": {
            "environment_production": 90.0,
            "environment_nonprod": 20.0,
            "criticality_high": 90.0,
            "criticality_low": 30.0,
            "dependency_database": 70.0,
            "dependency_other": 25.0,
            "uncertainty_high": 60.0,
            "uncertainty_low": 20.0,
        },
        # RAM peak-vs-p95 gap (GB) that triggers "high" volatility uncertainty score
        "uncertainty_ram_gap_threshold_gb": 5.0,
        # Headroom % cutoffs that drive capacity risk score
        "capacity_thresholds_pct": {
            "tight": 15.0,       # < 15% headroom -> tight capacity
            "moderate": 30.0,    # 15-30% headroom -> moderate capacity
        },
        "capacity_scores": {
            "tight": 85.0,
            "moderate": 50.0,
            "safe": 15.0,
        },
        # Score cutoffs for three-tier verdict
        "verdict_thresholds": {
            "approve_max": 30.0,      # score <= approve_max -> APPROVED
            "conditional_max": 60.0,  # score <= conditional_max -> APPROVED_WITH_CONDITIONS, else REJECTED
        },
    },
}

CONFIG_FILE_PATH = os.path.join(os.path.dirname(__file__), "config.json")


# ─────────────────────────────────────────────────────────────────────────────
# Derived helper — compute, never store
# ─────────────────────────────────────────────────────────────────────────────
def compute_active_hours_per_month(scheduling_cfg: dict) -> float:
    """
    Returns the number of active billing hours per month for a business-hours
    schedule. Never store this directly; always derive it from the three base
    inputs so the UI only needs sliders for real inputs, not a redundant fourth.
    """
    return (
        scheduling_cfg["business_hours_per_day"]
        * scheduling_cfg["business_days_per_week"]
        * scheduling_cfg["weeks_per_month"]
    )


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────
def validate_config(config: dict) -> tuple[bool, list[str]]:
    """
    Returns (is_valid, list_of_error_messages).
    Never silently accepts a broken config — every constraint is explicit.
    """
    errors: list[str] = []

    # Detection section
    d = config.get("detection", {})
    if not (0 < d.get("overprovisioned_cpu_max_pct", -1) <= 100):
        errors.append("detection.overprovisioned_cpu_max_pct must be between 0 and 100")
    if not (0 < d.get("overprovisioned_ram_max_ratio", -1) <= 1):
        errors.append("detection.overprovisioned_ram_max_ratio must be between 0 and 1")
    if not (0 <= d.get("zombie_gpu_util_max_pct", -1) <= 100):
        errors.append("detection.zombie_gpu_util_max_pct must be between 0 and 100")
    if d.get("zombie_gpu_idle_hours_min", -1) < 0:
        errors.append("detection.zombie_gpu_idle_hours_min must be >= 0")
    if d.get("zombie_network_max_mb_day", -1) < 0:
        errors.append("detection.zombie_network_max_mb_day must be >= 0")
    if not (0 < d.get("unscheduled_hours_per_week_threshold", -1) <= 168):
        errors.append("detection.unscheduled_hours_per_week_threshold must be between 0 and 168")

    # Sizing section
    s = config.get("sizing", {})
    if s.get("ram_headroom_over_peak", 0) < 1.0:
        errors.append("sizing.ram_headroom_over_peak must be >= 1.0 (headroom can't shrink capacity)")
    if s.get("ram_headroom_over_p95", 0) < 1.0:
        errors.append("sizing.ram_headroom_over_p95 must be >= 1.0")
    if s.get("vcpu_headroom_over_peak", 0) < 1.0:
        errors.append("sizing.vcpu_headroom_over_peak must be >= 1.0")
    if not (0 <= s.get("zombie_gpu_hibernate_cost_fraction", -1) <= 1):
        errors.append("sizing.zombie_gpu_hibernate_cost_fraction must be between 0 and 1")

    # Scheduling section
    sch = config.get("scheduling", {})
    if not (0 < sch.get("business_hours_per_day", -1) <= 24):
        errors.append("scheduling.business_hours_per_day must be between 0 and 24")
    if not (0 < sch.get("business_days_per_week", -1) <= 7):
        errors.append("scheduling.business_days_per_week must be between 0 and 7")

    # Risk section
    r = config.get("risk", {})

    weights = r.get("weights", {})
    total_weight = sum(weights.values()) if weights else 0.0
    if not (0.99 <= total_weight <= 1.01):
        errors.append(f"risk.weights must sum to 1.0 (currently {total_weight:.3f})")

    for name, val in r.get("factor_scores", {}).items():
        if not (0 <= val <= 100):
            errors.append(f"risk.factor_scores.{name} must be between 0 and 100 (got {val})")

    for name, val in r.get("capacity_scores", {}).items():
        if not (0 <= val <= 100):
            errors.append(f"risk.capacity_scores.{name} must be between 0 and 100 (got {val})")

    vt = r.get("verdict_thresholds", {})
    approve_max = vt.get("approve_max", -1)
    conditional_max = vt.get("conditional_max", 101)
    if not (0 <= approve_max < conditional_max <= 100):
        errors.append(
            f"risk.verdict_thresholds must satisfy 0 <= approve_max ({approve_max}) "
            f"< conditional_max ({conditional_max}) <= 100"
        )

    ct = r.get("capacity_thresholds_pct", {})
    tight = ct.get("tight", -1)
    moderate = ct.get("moderate", -1)
    if not (0 <= tight < moderate):
        errors.append(
            f"risk.capacity_thresholds_pct.tight ({tight}) must be less than .moderate ({moderate})"
        )

    return (len(errors) == 0), errors


# ─────────────────────────────────────────────────────────────────────────────
# Persistence helpers
# ─────────────────────────────────────────────────────────────────────────────
def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into a copy of base."""
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config(path: str = CONFIG_FILE_PATH) -> dict:
    """
    Load saved config from `path`, deep-merging onto DEFAULT_CONFIG so that
    missing keys always fall back to defaults (old config files stay compatible).
    If the file doesn't exist or contains invalid JSON, returns DEFAULT_CONFIG.
    """
    config = copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        config = _deep_merge(config, saved)
    except FileNotFoundError:
        pass
    except Exception:
        pass  # malformed JSON — use defaults silently
    return config


def save_config(config: dict, path: str = CONFIG_FILE_PATH) -> None:
    """
    Persist config to disk after validation.
    Raises ValueError if validation fails — never silently write a broken config.
    """
    is_valid, errors = validate_config(config)
    if not is_valid:
        raise ValueError(f"Refusing to save invalid config: {errors}")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
