"""
logic_agents.py — Agent 1 (UsageDetectiveAgent) & Agent 2 (RightsizingOptimizerAgent)
All arithmetic is pure Python — no LLM involved here.
Cloud Catalog is frozen per Section 3A of the build spec.

Both agents now accept a `config: dict` parameter (from config.py) so that
every detection threshold, sizing multiplier, and scheduling constant comes
from the unified config rather than being baked in as a literal.
"""

import math
from config import DEFAULT_CONFIG, compute_active_hours_per_month

# ─────────────────────────────────────────────────────────────────────────────
# Multi-Cloud Pricing Catalog (frozen, Section 3A)
# ─────────────────────────────────────────────────────────────────────────────
CLOUD_CATALOG = {
    "AWS": [
        {"tier": "t3.small",    "vcpu": 2, "ram_gb": 2.0,  "cost_hr": 0.0208, "gpu": False},
        {"tier": "t3.medium",   "vcpu": 2, "ram_gb": 4.0,  "cost_hr": 0.0416, "gpu": False},
        {"tier": "t3.large",    "vcpu": 2, "ram_gb": 8.0,  "cost_hr": 0.0832, "gpu": False},
        {"tier": "t3.xlarge",   "vcpu": 4, "ram_gb": 16.0, "cost_hr": 0.1664, "gpu": False},
        {"tier": "m6i.large",   "vcpu": 2, "ram_gb": 8.0,  "cost_hr": 0.0960, "gpu": False},
        {"tier": "m6i.xlarge",  "vcpu": 4, "ram_gb": 16.0, "cost_hr": 0.1920, "gpu": False},
        {"tier": "m6i.2xlarge", "vcpu": 8, "ram_gb": 32.0, "cost_hr": 0.3840, "gpu": False},
        {"tier": "g5.2xlarge",  "vcpu": 8, "ram_gb": 32.0, "cost_hr": 1.2120, "gpu": True},
    ],
    "AZURE": [
        {"tier": "Standard_B1ms",    "vcpu": 1, "ram_gb": 2.0,   "cost_hr": 0.0207, "gpu": False},
        {"tier": "Standard_B2s",     "vcpu": 2, "ram_gb": 4.0,   "cost_hr": 0.0416, "gpu": False},
        {"tier": "Standard_B2ms",    "vcpu": 2, "ram_gb": 8.0,   "cost_hr": 0.0832, "gpu": False},
        {"tier": "Standard_B4ms",    "vcpu": 4, "ram_gb": 16.0,  "cost_hr": 0.1660, "gpu": False},
        {"tier": "Standard_D4s_v5",  "vcpu": 4, "ram_gb": 16.0,  "cost_hr": 0.1920, "gpu": False},
        {"tier": "Standard_D8s_v5",  "vcpu": 8, "ram_gb": 32.0,  "cost_hr": 0.3840, "gpu": False},
        {"tier": "Standard_NC6s_v3", "vcpu": 6, "ram_gb": 112.0, "cost_hr": 1.1420, "gpu": True},
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# FinOps Governance Policy Engine (kept for documentation and per-env naming)
# ─────────────────────────────────────────────────────────────────────────────
from dataclasses import dataclass

@dataclass
class FinOpsEnvironmentPolicy:
    """
    Enterprise FinOps policy threshold configuration aligned with FinOps Foundation standards.
    Kept as a named container for per-environment documentation.
    When a config dict is supplied to scan_server(), config["detection"] takes precedence.
    """
    max_p95_cpu_percent: float = 15.0
    max_ram_utilization_ratio: float = 0.35
    min_gpu_utilization_percent: float = 2.0
    max_gpu_idle_hours: float = 6.0
    min_network_transfer_mb_day: float = 5.0
    business_hours_weekly_cap: int = 168
    policy_tier_name: str = "Enterprise Standard"


# Default enterprise policy matrix by environment
FINOPS_GOVERNANCE_POLICIES: dict[str, FinOpsEnvironmentPolicy] = {
    "production": FinOpsEnvironmentPolicy(
        max_p95_cpu_percent=15.0,
        max_ram_utilization_ratio=0.35,
        min_gpu_utilization_percent=2.0,
        max_gpu_idle_hours=6.0,
        min_network_transfer_mb_day=5.0,
        policy_tier_name="Production Core (Preserves SRE Safety Headroom)",
    ),
    "staging": FinOpsEnvironmentPolicy(
        max_p95_cpu_percent=15.0,
        max_ram_utilization_ratio=0.35,
        min_gpu_utilization_percent=2.0,
        max_gpu_idle_hours=6.0,
        min_network_transfer_mb_day=5.0,
        policy_tier_name="Pre-Production Staging Verification Policy",
    ),
    "dev": FinOpsEnvironmentPolicy(
        max_p95_cpu_percent=15.0,
        max_ram_utilization_ratio=0.35,
        min_gpu_utilization_percent=2.0,
        max_gpu_idle_hours=6.0,
        min_network_transfer_mb_day=5.0,
        policy_tier_name="Development Sandbox Optimization Policy",
    ),
    "qa": FinOpsEnvironmentPolicy(
        max_p95_cpu_percent=15.0,
        max_ram_utilization_ratio=0.35,
        min_gpu_utilization_percent=2.0,
        max_gpu_idle_hours=6.0,
        min_network_transfer_mb_day=5.0,
        policy_tier_name="Quality Assurance Fleet Policy",
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Agent 1 — UsageDetectiveAgent (Config-Driven, Enterprise Policy-Aware)
# ─────────────────────────────────────────────────────────────────────────────
class UsageDetectiveAgent:
    """
    Agent 1 — Usage Detective
    Policy-driven multi-cloud infrastructure audit engine.
    Evaluates fleets against 4 key FinOps waste vectors.
    Pure Python — zero LLM hallucinations.

    All detection thresholds are read from config["detection"] when a config
    dict is supplied, so organizations can tune sensitivity without code changes.
    Falls back to the FinOpsEnvironmentPolicy object for backward compatibility.
    """

    @staticmethod
    def get_policy_for_env(env: str) -> FinOpsEnvironmentPolicy:
        return FINOPS_GOVERNANCE_POLICIES.get(
            str(env).lower(),
            FINOPS_GOVERNANCE_POLICIES.get("production", FinOpsEnvironmentPolicy())
        )

    @classmethod
    def scan_server(
        cls,
        server: dict,
        config: dict | None = None,
        policy: FinOpsEnvironmentPolicy | None = None,
    ) -> dict:
        """
        Scan one server for waste.

        Parameters
        ----------
        server : dict  — raw server record from fleet_data
        config : dict  — unified CloudSage config (preferred); detection thresholds
                         are read from config["detection"] when supplied
        policy : FinOpsEnvironmentPolicy — fallback when config is None
        """
        env = str(server.get("environment", "production")).lower()

        # ── Resolve detection thresholds ─────────────────────────────────────
        if config is not None:
            d = config["detection"]
            cpu_max_pct         = d["overprovisioned_cpu_max_pct"]
            ram_max_ratio       = d["overprovisioned_ram_max_ratio"]
            gpu_util_max_pct    = d["zombie_gpu_util_max_pct"]
            gpu_idle_hours_min  = d["zombie_gpu_idle_hours_min"]
            net_max_mb_day      = d["zombie_network_max_mb_day"]
            sched_hours_cap     = d["unscheduled_hours_per_week_threshold"]
            policy_tier_name    = "Unified CloudSage Config"
        else:
            active_policy = policy or cls.get_policy_for_env(env)
            cpu_max_pct         = active_policy.max_p95_cpu_percent
            ram_max_ratio       = active_policy.max_ram_utilization_ratio
            gpu_util_max_pct    = active_policy.min_gpu_utilization_percent
            gpu_idle_hours_min  = active_policy.max_gpu_idle_hours
            net_max_mb_day      = active_policy.min_network_transfer_mb_day
            sched_hours_cap     = active_policy.business_hours_weekly_cap
            policy_tier_name    = active_policy.policy_tier_name

        reasons = []
        confidence_scores = {}
        evidence = {}
        is_gpu_workload = server.get("workload_type") == "ai_inference"

        # ── Vector 1: Overprovisioned Compute & Memory ───────────────────────
        # GPU workloads excluded — evaluated on accelerator efficiency instead
        if not is_gpu_workload:
            p95_cpu = server.get("p95_cpu_percent", 0.0)
            tot_ram = max(server.get("total_ram_gb", 1.0), 1.0)
            ram_ratio = server.get("p95_ram_used_gb", 0.0) / tot_ram

            if p95_cpu < cpu_max_pct and ram_ratio < ram_max_ratio:
                reasons.append("OVERPROVISIONED")
                cpu_slack = (cpu_max_pct - p95_cpu) / cpu_max_pct
                ram_slack = (ram_max_ratio - ram_ratio) / ram_max_ratio
                conf = round(min(99.0, 70.0 + 30.0 * ((cpu_slack + ram_slack) / 2.0)), 1)
                confidence_scores["OVERPROVISIONED"] = conf
                evidence["OVERPROVISIONED"] = (
                    f"p95 CPU ({p95_cpu:.1f}%) < policy limit ({cpu_max_pct:.1f}%) "
                    f"AND RAM ratio ({ram_ratio*100:.1f}%) < policy limit ({ram_max_ratio*100:.1f}%)"
                )

        # ── Vector 2: Zombie GPU Workload (Costly Accelerator Abandonment) ───
        if is_gpu_workload:
            gpu_util = server.get("gpu_util_percent", 100.0)
            idle_hrs = server.get("gpu_idle_hours", 0.0)

            if gpu_util < gpu_util_max_pct and idle_hrs >= gpu_idle_hours_min:
                reasons.append("ZOMBIE_GPU")
                conf = round(min(99.0, 85.0 + min(14.0, (idle_hrs - gpu_idle_hours_min) * 0.5)), 1)
                confidence_scores["ZOMBIE_GPU"] = conf
                evidence["ZOMBIE_GPU"] = (
                    f"GPU utilization ({gpu_util:.1f}%) < {gpu_util_max_pct:.1f}% "
                    f"for {idle_hrs:.1f} consecutive hours (policy threshold: {gpu_idle_hours_min:.1f}h)"
                )

        # ── Vector 3: Zombie Idle Instance (Dead Server) ─────────────────────
        net_traffic = server.get("network_in_out_mb_day", 999.0)
        if net_traffic < net_max_mb_day:
            reasons.append("ZOMBIE_IDLE")
            confidence_scores["ZOMBIE_IDLE"] = 96.5
            evidence["ZOMBIE_IDLE"] = (
                f"Network throughput ({net_traffic:.1f} MB/day) < policy minimum ({net_max_mb_day:.1f} MB/day)"
            )

        # ── Vector 4: Unscheduled Non-Production Instance ────────────────────
        runtime_hrs = server.get("runtime_hours_per_week", 0)
        if env in ["dev", "staging", "qa"] and runtime_hrs == sched_hours_cap:
            reasons.append("UNSCHEDULED_NONPROD")
            confidence_scores["UNSCHEDULED_NONPROD"] = 99.0
            evidence["UNSCHEDULED_NONPROD"] = (
                f"{env.upper()} environment running {int(sched_hours_cap)}h/week "
                f"(24/7 continuous spend instead of business-hours schedule)"
            )

        return {
            "server_id": server["server_id"],
            "is_flagged": len(reasons) > 0,
            "waste_reasons": reasons,
            "raw_server": server,
            "policy_applied": policy_tier_name,
            "confidence_scores": confidence_scores,
            "policy_evidence": evidence,
        }

    @classmethod
    def scan_fleet(
        cls,
        fleet: list,
        config: dict | None = None,
        policy: FinOpsEnvironmentPolicy | None = None,
    ) -> list:
        """Scan a full fleet list and return a list of scan results."""
        return [cls.scan_server(s, config=config, policy=policy) for s in fleet]


# ─────────────────────────────────────────────────────────────────────────────
# Agent 2 — RightsizingOptimizerAgent (Config-Driven)
# ─────────────────────────────────────────────────────────────────────────────
class RightsizingOptimizerAgent:
    """
    Produces a rightsizing recommendation for a flagged server.
    Pure Python — zero LLM calls.

    All sizing multipliers and scheduling constants are read from config["sizing"]
    and config["scheduling"] when a config dict is supplied.

    Frozen output schema:
        server_id, provider, from_tier, to_tier, target_ram_gb,
        target_vcpu, current_monthly_cost, proposed_monthly_cost,
        monthly_savings_usd, savings_pct, safety_headroom_pct, action_type
    """

    @staticmethod
    def optimize(server_scan: dict, config: dict | None = None) -> dict:
        """
        Parameters
        ----------
        server_scan : dict — output of UsageDetectiveAgent.scan_server
        config      : dict — unified CloudSage config; sizing & scheduling
                             thresholds are read from config["sizing"] and
                             config["scheduling"] when supplied
        """
        if config is None:
            config = DEFAULT_CONFIG

        server = server_scan["raw_server"]
        reasons = server_scan["waste_reasons"]
        provider = server.get("provider")

        sizing = config["sizing"]
        sched  = config["scheduling"]

        full_month_hours = sched["full_month_hours"]

        # Raise immediately for unknown provider — no silent fallback
        if provider not in CLOUD_CATALOG:
            raise ValueError(f"No catalog defined for provider: {provider}")

        catalog = CLOUD_CATALOG[provider]
        current_monthly = server.get("hourly_cost_usd", 0.1) * full_month_hours

        # ── Zombie GPU path ──────────────────────────────────────────────────
        # Retain a configurable fraction of total cost — no resize, just hibernate.
        if "ZOMBIE_GPU" in reasons:
            hibernate_fraction = sizing["zombie_gpu_hibernate_cost_fraction"]
            proposed_monthly = current_monthly * hibernate_fraction
            savings_pct = round((1 - hibernate_fraction) * 100, 1)
            return {
                "server_id": server["server_id"],
                "provider": provider,
                "from_tier": server["instance_type"],
                "to_tier": f"{server['instance_type']} (HIBERNATED)",
                "current_tier": server["instance_type"],
                "target_tier": f"{server['instance_type']} (HIBERNATED)",
                "target_ram_gb": server.get("total_ram_gb", 16.0),
                "target_vcpu": server.get("vcpus", 4),
                "current_monthly_cost": round(current_monthly, 2),
                "proposed_monthly_cost": round(proposed_monthly, 2),
                "monthly_savings_usd": round(current_monthly - proposed_monthly, 2),
                "savings_pct": savings_pct,
                "safety_headroom_pct": None,  # not applicable — no resize occurred
                "action_type": "HIBERNATE_ZOMBIE_GPU",
            }

        # ── Standard rightsizing path ────────────────────────────────────────
        peak_ram = server.get("peak_ram_used_gb", server.get("p95_ram_used_gb", 2.0))
        p95_ram  = server.get("p95_ram_used_gb", peak_ram)
        target_ram = max(
            peak_ram * sizing["ram_headroom_over_peak"],
            p95_ram  * sizing["ram_headroom_over_p95"],
        )

        # peak_vcpus_used is required — no silent guess
        peak_vcpu = server.get("peak_vcpus_used")
        if peak_vcpu is None:
            raise ValueError(
                f"Server {server.get('server_id')} missing required field: peak_vcpus_used"
            )
        target_vcpu = max(1, math.ceil(peak_vcpu * sizing["vcpu_headroom_over_peak"]))

        # Exclude GPU tiers — never rightsize a non-GPU workload onto GPU hardware.
        eligible = [
            inst
            for inst in catalog
            if inst["ram_gb"] >= target_ram
            and inst["vcpu"] >= target_vcpu
            and not inst["gpu"]
        ]

        if not eligible:
            return {
                "server_id": server["server_id"],
                "provider": provider,
                "from_tier": server["instance_type"],
                "to_tier": server["instance_type"],
                "current_tier": server["instance_type"],
                "target_tier": server["instance_type"],
                "target_ram_gb": server.get("total_ram_gb"),
                "target_vcpu": server.get("vcpus"),
                "current_monthly_cost": round(current_monthly, 2),
                "proposed_monthly_cost": round(current_monthly, 2),
                "monthly_savings_usd": 0.0,
                "savings_pct": 0.0,
                "safety_headroom_pct": None,
                "action_type": "NO_SAFE_DOWNSIZE_AVAILABLE",
            }

        best_match = min(eligible, key=lambda x: x["cost_hr"])

        # UNSCHEDULED_NONPROD servers billed at business-hours schedule; others at full month
        active_hours = (
            compute_active_hours_per_month(sched)
            if "UNSCHEDULED_NONPROD" in reasons
            else full_month_hours
        )
        proposed_monthly = best_match["cost_hr"] * active_hours
        monthly_savings = max(0.0, current_monthly - proposed_monthly)
        savings_pct = (monthly_savings / current_monthly * 100) if current_monthly > 0 else 0
        headroom_pct = ((best_match["ram_gb"] - peak_ram) / best_match["ram_gb"]) * 100
        action = "DOWNSIZE_AND_SCHEDULE" if active_hours < full_month_hours else "DOWNSIZE_INSTANCE"

        return {
            "server_id": server["server_id"],
            "provider": provider,
            "from_tier": server["instance_type"],
            "to_tier": best_match["tier"],
            "current_tier": server["instance_type"],
            "target_tier": best_match["tier"],
            "target_ram_gb": best_match["ram_gb"],
            "target_vcpu": best_match["vcpu"],
            "current_monthly_cost": round(current_monthly, 2),
            "proposed_monthly_cost": round(proposed_monthly, 2),
            "monthly_savings_usd": round(monthly_savings, 2),
            "savings_pct": round(savings_pct, 1),
            "safety_headroom_pct": round(headroom_pct, 1),
            "action_type": action,
        }
