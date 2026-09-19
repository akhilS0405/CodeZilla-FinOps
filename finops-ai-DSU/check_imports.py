import streamlit
import pandas
import plotly
import boto3
from fleet_data import get_fleet
from logic_agents import UsageDetectiveAgent, RightsizingOptimizerAgent
from ai_agents import SRERiskOfficerAgent, FinOpsArbitratorAgent
from beeceptor_gateway import dispatch_to_beeceptor
from localstack_service import LocalStackService
from terraform_generator import TerraformGitOpsGenerator

print("ALL IMPORTS OK")
fleet = get_fleet()
scans = UsageDetectiveAgent.scan_fleet(fleet)
flagged = sum(1 for s in scans if s["is_flagged"])
print(f"Fleet: {len(fleet)} servers, {flagged} flagged")

from localstack_service import LocalStackService
ls = LocalStackService()
print(f"LocalStack connected: {ls.is_connected()}")
