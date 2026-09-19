"""
risk_config_store.py — SRE Risk Policy Configuration Store
Extracted into its own module so Streamlit always imports it fresh,
with no dependency on ai_agents or any Google SDK.
"""

import copy
import json
import os

CONFIG_FILE_PATH = os.path.join(os.path.dirname(__file__), "risk_config.json")

# Default Enterprise SRE Risk Configuration (Aligned with FinOps Foundation Standards)
DEFAULT_RISK_CONFIG: dict = {
    "weights": {
        "environment_risk": 0.25,
        "criticality_risk": 0.25,
        "capacity_risk": 0.20,
        "uncertainty_risk": 0.15,
        "dependency_risk": 0.15,
    },
    "factor_scores": {
        "environment_production": 90,
        "environment_nonprod": 20,
        "criticality_high": 90,
        "criticality_low": 30,
        "dependency_database": 70,
        "dependency_other": 25,
        "uncertainty_high": 60,
        "uncertainty_low": 20,
    },
    "capacity_thresholds": {
        "tight": 15,       # Under 15% headroom -> high risk
        "moderate": 30,    # 15-30% headroom -> moderate risk
    },
    "capacity_scores": {
        "tight": 85,
        "moderate": 50,
        "safe": 15,
    },
    "verdict_thresholds": {
        "low_max": 30,      # Score <= 30 -> APPROVED
        "medium_max": 60,   # 31-60 -> APPROVED_WITH_CONDITIONS, >60 -> REJECTED
    },
}


def validate_risk_config(config: dict) -> tuple[bool, str]:
    """
    Guards against malformed or out-of-bounds configurations.
    Validates weight normalization (sum ~ 1.0), factor ranges (0-100), and verdict cutoffs.
    """
    if not isinstance(config, dict):
        return False, "Risk configuration must be a dictionary."

    weights = config.get("weights", {})
    total = sum(weights.values())
    if not (0.99 <= total <= 1.01):
        return False, f"Factor weights must sum to 1.0 (currently {total:.2f})."

    for name, val in config.get("factor_scores", {}).items():
        if not (0 <= val <= 100):
            return False, f"Factor score '{name}' must be between 0 and 100 (got {val})."

    for name, val in config.get("capacity_scores", {}).items():
        if not (0 <= val <= 100):
            return False, f"Capacity score '{name}' must be between 0 and 100 (got {val})."

    vt = config.get("verdict_thresholds", {})
    low_max = vt.get("low_max", 30)
    medium_max = vt.get("medium_max", 60)
    if not (0 <= low_max < medium_max <= 100):
        return False, f"Thresholds must satisfy 0 <= Auto-Approve ({low_max}) < Reject ({medium_max}) <= 100."

    return True, "OK"


def load_risk_config() -> dict:
    """Loads saved risk config from disk or returns default configuration."""
    if os.path.exists(CONFIG_FILE_PATH):
        try:
            with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                is_valid, _ = validate_risk_config(cfg)
                if is_valid:
                    return cfg
        except Exception:
            pass
    return copy.deepcopy(DEFAULT_RISK_CONFIG)


def save_risk_config(config: dict) -> bool:
    """Persists risk configuration to disk if valid."""
    is_valid, _ = validate_risk_config(config)
    if not is_valid:
        return False
    try:
        with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        return True
    except Exception:
        return False
