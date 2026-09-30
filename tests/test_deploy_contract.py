"""Static gates for permanent first-deployment choices."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "scripts" / "deploy.sh"
CLOUD_BUILD = ROOT / "cloudbuild.yaml"


class DeploymentContractTests(unittest.TestCase):
    def test_deploy_script_dry_run_pins_safe_contract(self):
        result = subprocess.run(
            ["bash", str(DEPLOY), "--dry-run", "--allow-dirty"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        output = result.stdout
        self.assertIn("--project=work-dashboards", output)
        self.assertIn("--region=asia-southeast1", output)
        self.assertIn("family-expenses", output)
        self.assertIn("--update-secrets=MCP_MEMBERS_JSON=", output)
        self.assertIn("family-expenses-mcp-members", output)
        self.assertIn("--min-instances=0", output)
        self.assertNotIn("MCP_SECRET", output)
        self.assertNotIn("work-dashboards-database", output)
        self.assertRegex(output, r"family-expenses:[0-9a-f]{7,40}")

    def test_auth_cutover_preserves_existing_portal_configuration(self):
        result = subprocess.run(
            ["bash", str(DEPLOY), "--dry-run", "--allow-dirty"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        output = result.stdout.replace("\\", "")
        self.assertIn("--update-env-vars=^~^MCP_AUTH_ISSUER=https://work-os.jp.auth0.com/", output)
        self.assertIn("MCP_RESOURCE_URL=https://family-expenses-bejtu5m47a-as.a.run.app/mcp", output)
        self.assertIn("APPROVED_VERSION_REQUIRED", output)
        for forbidden in ("--set-env-vars", "--set-secrets", "SESSION_SECRET=",
                          "AUTH0_CLIENT_SECRET=", "PORTAL_ALLOWED_EMAILS=",
                          "AUTH0_CLIENT_ID=", "MCP_SECRET"):
            self.assertNotIn(forbidden, output)

    def test_cloud_build_requires_explicit_commit_sha_and_no_latest_tag(self):
        config = CLOUD_BUILD.read_text(encoding="utf-8")
        self.assertIn("family-expenses:$COMMIT_SHA", config)
        self.assertNotIn("family-expenses:latest", config)


if __name__ == "__main__":
    unittest.main()
