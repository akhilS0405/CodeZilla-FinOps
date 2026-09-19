"""
digital_twin.py
================
Python Digital Twin for CloudSage FinOps AI.

Purpose
-------
Loads locally-generated historical telemetry for each simulated server
and replays that history against a PROPOSED configuration change
(e.g. a smaller instance type) to test whether the change would have
survived real historical demand -- instead of trusting a static
"peak x headroom" formula alone.

This module has ZERO network calls. It reads CSV/JSON files created by
generate_fleet_history.py and produces a structured verdict that feeds
directly into Agent 3 (SRERiskOfficerAgent).

No Beeceptor. No LocalStack calls happen here -- this is the pure
behavioral simulation layer that sits between Agent 2 (proposes a tier)
and Agent 3 (scores risk).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


DATA_DIR = Path("data")
HISTORY_DIR = DATA_DIR / "history"
FLEET_METADATA_PATH = DATA_DIR / "fleet_metadata.json"


# ----------------------------------------------------------------------
# Data structures
# ----------------------------------------------------------------------

@dataclass
class ResourceProfile:
    resource_id: str
    instance_type: str
    provider: str
    vcpu: int
    memory_gb: float
    has_gpu: bool
    environment: str
    workload_type: str
    hourly_cost_usd: float
    criticality: str


@dataclass
class ProposedConfig:
    """What Agent 2 wants to test."""
    resource_id: str
    new_instance_type: str
    new_vcpu: int
    new_memory_gb: float
    new_hourly_cost_usd: float
    action: str = "resize"  # resize | schedule_shutdown | delete


@dataclass
class ReplayViolation:
    timestamp: str
    metric: str
    historical_value: float
    proposed_capacity_limit: float
    overage_percent: float


@dataclass
class SimulationResult:
    resource_id: str
    proposed_instance_type: str
    action: str
    total_hours_replayed: int
    violations: list[ReplayViolation] = field(default_factory=list)
    max_cpu_seen: float = 0.0
    p95_cpu_seen: float = 0.0
    avg_cpu_seen: float = 0.0
    worst_violation_percent: float = 0.0
    capacity_sufficient: bool = True
    verdict: str = "SAFE"  # SAFE | SAFE_WITH_WARNING | UNSAFE
    explanation: str = ""

    def to_dict(self) -> dict:
        return {
            "resource_id": self.resource_id,
            "proposed_instance_type": self.proposed_instance_type,
            "action": self.action,
            "total_hours_replayed": self.total_hours_replayed,
            "violations_count": len(self.violations),
            "violations": [v.__dict__ for v in self.violations[:10]],
            "max_cpu_seen": round(self.max_cpu_seen, 2),
            "p95_cpu_seen": round(self.p95_cpu_seen, 2),
            "avg_cpu_seen": round(self.avg_cpu_seen, 2),
            "worst_violation_percent": round(self.worst_violation_percent, 2),
            "capacity_sufficient": self.capacity_sufficient,
            "verdict": self.verdict,
            "explanation": self.explanation,
        }


# ----------------------------------------------------------------------
# The Digital Twin
# ----------------------------------------------------------------------

class DigitalTwin:
    """
    Local, offline behavioral simulator.

    Responsibilities:
      1. Load historical telemetry for a resource.
      2. Load the resource's real metadata (current instance specs).
      3. Replay history against a proposed smaller/different config.
      4. Detect every hour where historical demand would have exceeded
         the proposed capacity.
      5. Produce a SimulationResult with a clear verdict.
    """

    # Safety threshold: if replayed CPU would exceed this % of the new
    # capacity's equivalent, flag it as a violation.
    CPU_VIOLATION_THRESHOLD = 90.0
    WARNING_THRESHOLD = 80.0

    def __init__(self, history_dir: Path = HISTORY_DIR,
                 metadata_path: Path = FLEET_METADATA_PATH):
        self.history_dir = Path(history_dir)
        self.metadata_path = Path(metadata_path)
        self._metadata_cache: dict[str, ResourceProfile] | None = None

    # -- loading -----------------------------------------------------

    def load_metadata(self) -> dict[str, ResourceProfile]:
        if self._metadata_cache is not None:
            return self._metadata_cache

        with open(self.metadata_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        profiles = {}
        for item in raw:
            profiles[item["resource_id"]] = ResourceProfile(**item)

        self._metadata_cache = profiles
        return profiles

    def load_history(self, resource_id: str) -> pd.DataFrame:
        path = self.history_dir / f"{resource_id}_history.csv"

        if not path.exists():
            raise FileNotFoundError(
                f"No historical data found for '{resource_id}' at {path}. "
                f"Run generate_fleet_history.py first."
            )

        df = pd.read_csv(path, parse_dates=["timestamp"])
        return df.sort_values("timestamp").reset_index(drop=True)

    def get_profile(self, resource_id: str) -> ResourceProfile:
        profiles = self.load_metadata()

        if resource_id not in profiles:
            from fleet_data import get_fleet
            for s in get_fleet():
                if s["server_id"] == resource_id:
                    p = ResourceProfile(
                        resource_id=s["server_id"],
                        instance_type=s["instance_type"],
                        provider=s["provider"].lower(),
                        vcpu=int(s["vcpus"]),
                        memory_gb=float(s["total_ram_gb"]),
                        has_gpu="gpu" in s["instance_type"].lower() or s.get("workload_type") == "ai_inference",
                        environment=s["environment"],
                        workload_type=s["workload_type"],
                        hourly_cost_usd=float(s["hourly_cost_usd"]),
                        criticality="critical" if s["environment"] == "production" and s["workload_type"] in ("database", "web_api") else ("high" if s["environment"] == "production" else "medium"),
                    )
                    profiles[resource_id] = p
                    return p
            raise KeyError(f"Unknown resource_id: {resource_id}")

        return profiles[resource_id]

    # -- core simulation ----------------------------------------------

    def replay_against_proposal(
        self,
        proposal: ProposedConfig,
    ) -> SimulationResult:
        """
        The main method. Replays real historical CPU/memory demand
        against a proposed smaller configuration and checks, hour by
        hour, whether the proposed capacity would have been enough.
        """
        current = self.get_profile(proposal.resource_id)
        history = self.load_history(proposal.resource_id)

        result = SimulationResult(
            resource_id=proposal.resource_id,
            proposed_instance_type=proposal.new_instance_type,
            action=proposal.action,
            total_hours_replayed=len(history),
        )

        if proposal.action == "schedule_shutdown":
            return self._replay_schedule_shutdown(current, history, proposal, result)

        return self._replay_resize(current, history, proposal, result)

    def _replay_resize(
        self,
        current: ResourceProfile,
        history: pd.DataFrame,
        proposal: ProposedConfig,
        result: SimulationResult,
    ) -> SimulationResult:
        """
        For a resize action: scale historical CPU% onto the NEW vcpu
        count. If current has 8 vcpu at 40% and proposed has 4 vcpu,
        the equivalent load on the new machine is roughly:

            new_cpu_percent = old_cpu_percent * (old_vcpu / new_vcpu)

        This is a standard, explainable capacity-scaling formula, not
        a black box. If new_cpu_percent ever exceeds our violation
        threshold, the proposed resize is flagged as unsafe for that
        hour.
        """
        vcpu_ratio = current.vcpu / proposal.new_vcpu if proposal.new_vcpu else 1.0
        mem_ratio = current.memory_gb / proposal.new_memory_gb if proposal.new_memory_gb else 1.0

        projected_cpu = history["cpu_percent"] * vcpu_ratio
        projected_mem = history["memory_percent"] * mem_ratio

        result.max_cpu_seen = float(projected_cpu.max())
        result.p95_cpu_seen = float(projected_cpu.quantile(0.95))
        result.avg_cpu_seen = float(projected_cpu.mean())

        violations = []
        for idx, row in history.iterrows():
            proj_cpu = projected_cpu.iloc[idx]
            proj_mem = projected_mem.iloc[idx]

            if proj_cpu > self.CPU_VIOLATION_THRESHOLD:
                overage = proj_cpu - self.CPU_VIOLATION_THRESHOLD
                violations.append(ReplayViolation(
                    timestamp=str(row["timestamp"]),
                    metric="cpu_percent",
                    historical_value=round(float(row["cpu_percent"]), 2),
                    proposed_capacity_limit=self.CPU_VIOLATION_THRESHOLD,
                    overage_percent=round(float(overage), 2),
                ))
            elif proj_mem > self.CPU_VIOLATION_THRESHOLD:
                overage = proj_mem - self.CPU_VIOLATION_THRESHOLD
                violations.append(ReplayViolation(
                    timestamp=str(row["timestamp"]),
                    metric="memory_percent",
                    historical_value=round(float(row["memory_percent"]), 2),
                    proposed_capacity_limit=self.CPU_VIOLATION_THRESHOLD,
                    overage_percent=round(float(overage), 2),
                ))

        result.violations = violations
        result.worst_violation_percent = (
            max(v.overage_percent for v in violations) if violations else 0.0
        )

        result.capacity_sufficient = len(violations) == 0

        if not violations:
            result.verdict = "SAFE"
            result.explanation = (
                f"Replayed {len(history)} hours of real historical demand against "
                f"the proposed {proposal.new_instance_type}. Projected peak CPU was "
                f"{result.max_cpu_seen:.1f}%, staying under the "
                f"{self.CPU_VIOLATION_THRESHOLD:.0f}% safety threshold every hour."
            )
        elif result.max_cpu_seen <= self.CPU_VIOLATION_THRESHOLD + 15:
            result.verdict = "SAFE_WITH_WARNING"
            result.explanation = (
                f"{len(violations)} hour(s) out of {len(history)} would have exceeded "
                f"safe capacity on the proposed {proposal.new_instance_type} "
                f"(worst case {result.worst_violation_percent:.1f}% over threshold). "
                f"Recommend monitoring or a slightly larger tier."
            )
        else:
            result.verdict = "UNSAFE"
            result.explanation = (
                f"{len(violations)} hour(s) out of {len(history)} would have exceeded "
                f"safe capacity on the proposed {proposal.new_instance_type} "
                f"(worst case {result.worst_violation_percent:.1f}% over threshold). "
                f"This resize is NOT recommended without further headroom."
            )

        return result

    def _replay_schedule_shutdown(
        self,
        current: ResourceProfile,
        history: pd.DataFrame,
        proposal: ProposedConfig,
        result: SimulationResult,
        business_start_hour: int = 8,
        business_end_hour: int = 18,
    ) -> SimulationResult:
        """
        For a schedule_shutdown action: check whether any historically
        real activity happened OUTSIDE the proposed active window.
        This catches cases like gpu-001's nightly training job, which
        a naive 'low average utilization -> shut it down' rule would
        miss.
        """
        history = history.copy()
        history["hour"] = pd.to_datetime(history["timestamp"]).dt.hour

        outside_window = history[
            (history["hour"] < business_start_hour) |
            (history["hour"] > business_end_hour)
        ]

        active_outside = outside_window[outside_window["cpu_percent"] > 25]

        result.max_cpu_seen = float(history["cpu_percent"].max())
        result.p95_cpu_seen = float(history["cpu_percent"].quantile(0.95))
        result.avg_cpu_seen = float(history["cpu_percent"].mean())

        violations = []
        for _, row in active_outside.iterrows():
            violations.append(ReplayViolation(
                timestamp=str(row["timestamp"]),
                metric="cpu_percent",
                historical_value=round(float(row["cpu_percent"]), 2),
                proposed_capacity_limit=25.0,
                overage_percent=round(float(row["cpu_percent"]) - 25.0, 2),
            ))

        result.violations = violations
        result.capacity_sufficient = len(violations) == 0
        result.worst_violation_percent = (
            max(v.overage_percent for v in violations) if violations else 0.0
        )

        if not violations:
            result.verdict = "SAFE"
            result.explanation = (
                f"No meaningful activity detected outside the proposed "
                f"{business_start_hour}:00-{business_end_hour}:00 active window "
                f"across {len(history)} replayed hours. Safe to schedule shutdown."
            )
        else:
            result.verdict = "UNSAFE"
            worst = max(violations, key=lambda v: v.historical_value)
            result.explanation = (
                f"Found {len(violations)} hour(s) of real activity outside the "
                f"proposed active window (worst: {worst.historical_value:.1f}% CPU "
                f"at {worst.timestamp}). Shutting down on this schedule would have "
                f"disrupted a real historical workload -- likely a scheduled job "
                f"or training run."
            )

        return result

    # -- convenience ----------------------------------------------------

    def summarize_history(self, resource_id: str) -> dict:
        history = self.load_history(resource_id)
        return {
            "resource_id": resource_id,
            "hours_of_data": len(history),
            "avg_cpu_percent": round(float(history["cpu_percent"].mean()), 2),
            "p95_cpu_percent": round(float(history["cpu_percent"].quantile(0.95)), 2),
            "max_cpu_percent": round(float(history["cpu_percent"].max()), 2),
            "avg_memory_percent": round(float(history["memory_percent"].mean()), 2),
            "p95_memory_percent": round(float(history["memory_percent"].quantile(0.95)), 2),
        }
