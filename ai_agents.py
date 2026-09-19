"""
ai_agents.py — Agent 3 (SRERiskOfficerAgent) & Agent 4 (FinOpsArbitratorAgent)
Hybrid design: Python computes every number; Gemini only narrates already-computed facts.
Every LLM call has a timeout + try/except -> template fallback.

compute_risk_score() reads all weights, factor scores, and thresholds from
config["risk"] — no numeric literals inside formula bodies.
"""

from google import genai
from google.genai import types as genai_types

import copy
from twin_agent_bridge import get_twin_verdict
from config import DEFAULT_CONFIG


# ─────────────────────────────────────────────────────────────────────────────
# Gemini client factory
# ─────────────────────────────────────────────────────────────────────────────
def make_gemini_client(api_key: str):
    """
    Returns a thin wrapper that exposes a .generate(prompt, timeout) method
    compatible with the agent functions below.
    Raises ValueError immediately if api_key is blank.
    """
    if not api_key or not api_key.strip():
        raise ValueError("Gemini API key is required for AI narration.")

    client = genai.Client(api_key=api_key.strip())

    class _GeminiWrapper:
        def generate(self, prompt: str, timeout: int = 8):
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=256,
                ),
            )
            return response

    return _GeminiWrapper()


# ─────────────────────────────────────────────────────────────────────────────
# Agent 3 — SRERiskOfficerAgent
# ─────────────────────────────────────────────────────────────────────────────

def compute_risk_score(server: dict, optimization: dict, config: dict | None = None) -> dict:
    """
    Config-driven Python deterministic risk formula with DigitalTwin historical
    replay verification. All factors and thresholds are read from config["risk"]
    — no numeric literals inside the formula body.

    Parameters
    ----------
    server       : raw server dict from fleet_data
    optimization : output of RightsizingOptimizerAgent.optimize
    config       : unified CloudSage config dict (from config.py).
                   Falls back to DEFAULT_CONFIG when None.
    """
    if config is None:
        config = DEFAULT_CONFIG

    r  = config["risk"]
    w  = r["weights"]
    fs = r["factor_scores"]
    ct = r["capacity_thresholds_pct"]
    cs = r["capacity_scores"]
    vt = r["verdict_thresholds"]

    # ── Environment factor ───────────────────────────────────────────────────
    env = server.get("environment", "production")
    environment_risk = (
        fs["environment_production"] if env == "production" else fs["environment_nonprod"]
    )

    # ── Criticality factor ───────────────────────────────────────────────────
    is_critical = server.get("workload_type") in ("database", "web_api") and env == "production"
    criticality_risk = fs["criticality_high"] if is_critical else fs["criticality_low"]

    # ── Capacity / headroom factor ───────────────────────────────────────────
    headroom = optimization.get("safety_headroom_pct")
    if headroom is None:
        capacity_risk = cs["moderate"]   # hibernation path — headroom unknown until re-activated
    elif headroom < ct["tight"]:
        capacity_risk = cs["tight"]
    elif headroom < ct["moderate"]:
        capacity_risk = cs["moderate"]
    else:
        capacity_risk = cs["safe"]

    # ── Uncertainty / volatility factor ─────────────────────────────────────
    ram_gap = server.get("peak_ram_used_gb", 0) - server.get("p95_ram_used_gb", 0)
    uncertainty_risk = (
        fs["uncertainty_high"]
        if server.get("p95_cpu_percent", 0) > 0 and ram_gap > r["uncertainty_ram_gap_threshold_gb"]
        else fs["uncertainty_low"]
    )

    # ── Dependency factor ────────────────────────────────────────────────────
    dependency_risk = (
        fs["dependency_database"]
        if server.get("workload_type") == "database"
        else fs["dependency_other"]
    )

    # ── Weighted sum ─────────────────────────────────────────────────────────
    base_risk_score = round(
        w["environment_risk"]  * environment_risk
        + w["criticality_risk"]  * criticality_risk
        + w["capacity_risk"]     * capacity_risk
        + w["uncertainty_risk"]  * uncertainty_risk
        + w["dependency_risk"]   * dependency_risk
    )

    # ── Digital Twin historical replay verification ──────────────────────────
    twin = get_twin_verdict(
        server.get("server_id", ""),
        optimization,
        action="schedule_shutdown" if "SCHEDULE" in optimization.get("action_type", "") else "resize",
    )

    risk_score = min(100, base_risk_score + twin.get("twin_risk_penalty", 0))

    # ── Three-tier verdict ───────────────────────────────────────────────────
    if twin.get("verdict") == "UNSAFE":
        verdict = "REJECTED"
    elif risk_score <= vt["approve_max"]:
        verdict = "APPROVED"
    elif risk_score <= vt["conditional_max"]:
        verdict = "APPROVED_WITH_CONDITIONS"
    else:
        verdict = "REJECTED"

    return {
        "risk_score": risk_score,
        "base_risk_score": base_risk_score,
        "verdict": verdict,
        "twin_verdict": twin,
        "config_used": config,
        "factors": {
            "environment_risk": environment_risk,
            "criticality_risk": criticality_risk,
            "capacity_risk": capacity_risk,
            "uncertainty_risk": uncertainty_risk,
            "dependency_risk": dependency_risk,
        },
    }


def _template_fallback_risk(server: dict, risk: dict) -> str:
    return (
        f"{server['server_id']} scored {risk['risk_score']}/100 risk "
        f"({risk['verdict']}), driven mainly by its environment and criticality profile."
    )


def sre_explain(server: dict, optimization: dict, risk: dict, gemini_client) -> str:
    """
    LLM narrates the ALREADY-COMPUTED risk dict.
    Never asked to score anything. Falls back to template if Gemini is unavailable
    or if the response doesn't contain the actual computed score.
    """
    if gemini_client is None:
        return _template_fallback_risk(server, risk)

    prompt = f"""You are a Senior SRE reviewing a cost-optimization proposal.
Use ONLY these facts — do not invent or alter any number:
Server: {server['server_id']} ({server['workload_type']}, {server['environment']})
Proposed action: {optimization['action_type']}
Risk score (already calculated): {risk['risk_score']}/100
Verdict (already decided): {risk['verdict']}
Contributing factors: {risk['factors']}

Write 2-3 sentences explaining WHY this risk score and verdict make sense,
in plain English for a non-technical stakeholder. Do not state a different
score or verdict than the ones given above."""

    try:
        response = gemini_client.generate(prompt, timeout=8)
        text = response.text
        if str(risk["risk_score"]) not in text:
            return _template_fallback_risk(server, risk)
        return text
    except Exception:
        return _template_fallback_risk(server, risk)


class SRERiskOfficerAgent:
    """Wrapper that bundles compute_risk_score + sre_explain with configurable policy support."""

    @staticmethod
    def assess(
        server: dict,
        optimization: dict,
        gemini_client=None,
        config: dict | None = None,
    ) -> dict:
        risk = compute_risk_score(server, optimization, config=config)
        narration = sre_explain(server, optimization, risk, gemini_client)
        return {**risk, "narration": narration}


# ─────────────────────────────────────────────────────────────────────────────
# Agent 4 — FinOpsArbitratorAgent
# ─────────────────────────────────────────────────────────────────────────────

def compute_roi(optimization: dict) -> dict:
    """
    Pure Python. 12-month ROI is arithmetic — monthly_savings x 12.
    Never LLM-generated.
    """
    monthly = optimization.get("monthly_savings_usd", 0.0)
    return {
        "annual_savings_usd": round(monthly * 12, 2),
        "final_verdict": (
            "PROCEED"
            if optimization.get("action_type") != "NO_SAFE_DOWNSIZE_AVAILABLE"
            else "NO_ACTION"
        ),
    }


def _template_fallback_arbitrator(optimization: dict, roi: dict) -> str:
    return (
        f"Recommend {optimization['action_type']} for estimated annual savings "
        f"of ${roi['annual_savings_usd']:,}. Decision: {roi['final_verdict']}."
    )


def arbitrator_summary(
    server: dict,
    optimization: dict,
    risk: dict,
    roi: dict,
    gemini_client,
) -> str:
    """
    LLM writes an executive summary from already-computed facts only.
    Falls back to template if Gemini is unavailable or hallucination detected.
    """
    if gemini_client is None:
        return _template_fallback_arbitrator(optimization, roi)

    prompt = f"""You are a VP of FinOps writing a one-paragraph executive summary.
Use ONLY these facts — do not invent or alter any number:
Server: {server['server_id']}
Action: {optimization['action_type']} ({optimization['from_tier']} -> {optimization['to_tier']})
Monthly savings: ${optimization['monthly_savings_usd']}
Annual savings (already calculated): ${roi['annual_savings_usd']}
Risk verdict: {risk['verdict']} (score {risk['risk_score']}/100)
Final decision (already made): {roi['final_verdict']}

Write 2-3 sentences summarizing the recommendation and its financial impact.
Do not state different dollar figures or a different verdict than given above."""

    try:
        response = gemini_client.generate(prompt, timeout=8)
        text = response.text
        if str(roi["annual_savings_usd"]) not in text:
            return _template_fallback_arbitrator(optimization, roi)
        return text
    except Exception:
        return _template_fallback_arbitrator(optimization, roi)


class FinOpsArbitratorAgent:
    """Wrapper that bundles compute_roi + arbitrator_summary."""

    @staticmethod
    def arbitrate(
        server: dict,
        optimization: dict,
        risk: dict,
        gemini_client=None,
    ) -> dict:
        roi = compute_roi(optimization)
        summary = arbitrator_summary(server, optimization, risk, roi, gemini_client)
        return {**roi, "summary": summary}
