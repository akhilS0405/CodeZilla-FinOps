"""
beeceptor_gateway.py — REST client for Beeceptor mock API
Frozen field names match Agent 2-4 output exactly (Section 3F of build spec).
"""

import requests


def dispatch_to_beeceptor(
    beeceptor_base_url: str,
    optimization: dict,
    risk: dict,
    roi: dict,
) -> dict:
    """
    POST a FINOPS_EXECUTION_TRIGGERED event to the Beeceptor mock endpoint.
    All field names are frozen and match the agent output schema exactly.

    Returns a dict with keys: status_code, payload, response (or error).
    """
    clean_url = beeceptor_base_url.strip().strip("`'\"").rstrip("/")
    if not clean_url.startswith(("http://", "https://")):
        clean_url = f"https://{clean_url}"
    url = f"{clean_url}/api/v1/infrastructure/execute"

    payload = {
        "event": "FINOPS_EXECUTION_TRIGGERED",
        "server_id": optimization["server_id"],
        "provider": optimization["provider"],
        "action": optimization["action_type"],
        "from_tier": optimization["from_tier"],
        "to_tier": optimization["to_tier"],
        "monthly_savings_usd": optimization["monthly_savings_usd"],
        "annual_savings_usd": roi["annual_savings_usd"],
        "safety_headroom_pct": optimization.get("safety_headroom_pct"),
        "risk_score": risk["risk_score"],
        "risk_verdict": risk["verdict"],
        "executed_by": "FinOps_Agentic_Core",
        "status": "APPLIED_SUCCESSFULLY",
    }

    try:
        res = requests.post(url, json=payload, timeout=5)
        response_data = None
        if res.ok:
            try:
                response_data = res.json()
            except Exception:
                response_data = {"message": res.text}
        return {
            "status_code": res.status_code,
            "payload": payload,
            "response": response_data,
        }
    except Exception as e:
        return {
            "status_code": 500,
            "error": str(e),
            "payload": payload,
        }
