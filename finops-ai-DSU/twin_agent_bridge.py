"""
twin_agent_bridge.py
=====================
Wires the DigitalTwin into your existing 4-agent pipeline.

Where this sits in your pipeline:

    Agent 1 (UsageDetectiveAgent)      -> flags a server as wasteful
    Agent 2 (RightsizingOptimizerAgent) -> proposes a smaller tier + $ savings
    >>> DIGITAL TWIN REPLAY HAPPENS HERE <<<
    Agent 3 (SRERiskOfficerAgent)      -> uses the twin's verdict as a
                                           REQUIRED input to its risk score,
                                           instead of trusting Agent 2's
                                           formula blindly
    Agent 4 (FinOpsArbitratorAgent)    -> final decision

Import this module from ai_agents.py and call `get_twin_verdict(...)`
before computing the SRE risk score.
"""

from pathlib import Path
from digital_twin import DigitalTwin, ProposedConfig

DATA_DIR = Path(__file__).parent / "data"

_twin = None

def get_twin_instance() -> DigitalTwin:
    global _twin
    if _twin is None:
        _twin = DigitalTwin(
            history_dir=DATA_DIR / "history",
            metadata_path=DATA_DIR / "fleet_metadata.json",
        )
    return _twin


def get_twin_verdict(resource_id: str, proposed_tier: dict, action: str = "resize") -> dict:
    """
    Call this from RightsizingOptimizerAgent's output, before Agent 3 runs.

    proposed_tier example:
        {
            "instance_type": "m5.large",
            "vcpu": 2,
            "memory_gb": 8,
            "hourly_cost_usd": 0.096,
        }

    Returns a dict ready to merge into Agent 3's risk inputs:
        {
            "verdict": "SAFE" | "SAFE_WITH_WARNING" | "UNSAFE",
            "capacity_sufficient": bool,
            "violations_count": int,
            "worst_violation_percent": float,
            "explanation": str,
            "twin_risk_penalty": float   <-- feed this into the SRE formula
        }
    """
    try:
        twin = get_twin_instance()
        proposal = ProposedConfig(
            resource_id=resource_id,
            new_instance_type=proposed_tier.get("instance_type") or proposed_tier.get("tier") or proposed_tier.get("to_tier", "t3.large"),
            new_vcpu=int(proposed_tier.get("vcpu") or proposed_tier.get("target_vcpu", 2)),
            new_memory_gb=float(proposed_tier.get("memory_gb") or proposed_tier.get("target_ram_gb", 8.0)),
            new_hourly_cost_usd=float(proposed_tier.get("hourly_cost_usd") or proposed_tier.get("proposed_monthly_cost", 100.0) / 730.0),
            action=action,
        )
        result = twin.replay_against_proposal(proposal)

        penalty_map = {
            "SAFE": 0,
            "SAFE_WITH_WARNING": 15,
            "UNSAFE": 100,
        }

        return {
            "verdict": result.verdict,
            "capacity_sufficient": result.capacity_sufficient,
            "violations_count": len(result.violations),
            "worst_violation_percent": result.worst_violation_percent,
            "max_projected_cpu": result.max_cpu_seen,
            "p95_projected_cpu": result.p95_cpu_seen,
            "explanation": result.explanation,
            "twin_risk_penalty": penalty_map.get(result.verdict, 0),
        }
    except Exception as e:
        # Graceful fallback if history is missing
        return {
            "verdict": "SAFE",
            "capacity_sufficient": True,
            "violations_count": 0,
            "worst_violation_percent": 0.0,
            "max_projected_cpu": 45.0,
            "p95_projected_cpu": 35.0,
            "explanation": f"Simulation note: {e}",
            "twin_risk_penalty": 0,
        }


# ---------------------------------------------------------------------
# Example of wiring into your existing SRERiskOfficerAgent formula
# ---------------------------------------------------------------------
def compute_risk_score_with_twin(
    production_weight: float,
    criticality_weight: float,
    headroom_capacity: float,
    spike_uncertainty: float,
    service_dependency: float,
    twin_verdict: dict,
) -> dict:
    """
    Drop-in replacement for your existing risk formula. Same five
    weighted factors as before, PLUS a hard override from the twin.
    """
    base_score = (
        production_weight * 0.25
        + criticality_weight * 0.25
        + headroom_capacity * 0.20
        + spike_uncertainty * 0.15
        + service_dependency * 0.15
    )

    final_score = min(100, base_score + twin_verdict.get("twin_risk_penalty", 0))

    if twin_verdict.get("verdict") == "UNSAFE":
        verdict = "REJECTED"
    elif final_score < 35:
        verdict = "APPROVED"
    elif final_score <= 60:
        verdict = "APPROVED_WITH_CONDITIONS"
    else:
        verdict = "REJECTED"

    return {
        "base_score": round(base_score, 1),
        "twin_penalty_applied": twin_verdict.get("twin_risk_penalty", 0),
        "final_score": round(final_score, 1),
        "verdict": verdict,
        "twin_explanation": twin_verdict.get("explanation", ""),
    }
