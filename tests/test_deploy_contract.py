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
        self.assertIn("family-expenses-database-url", output)
        self.assertIn("--set-secrets=DATABASE_URL=", output)
        self.assertIn("--min-instances=0", output)
        self.assertNotIn("MCP_SECRET", output)
        self.assertNotIn("work-dashboards-database", output)
        self.assertRegex(output, r"family-expenses:[0-9a-f]{7,40}")

    def test_portal_oauth_is_wired_without_touching_the_mcp_posture(self):
        """Portal OAuth gates the PORTAL only. /mcp must stay header-free."""
        result = subprocess.run(
            ["bash", str(DEPLOY), "--dry-run", "--allow-dirty"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        output = result.stdout
        # secrets arrive via Secret Manager, never as literal env values
        self.assertIn("AUTH0_CLIENT_SECRET=family-expenses-auth0-client-secret", output)
        self.assertIn("SESSION_SECRET=family-expenses-session-secret", output)
        self.assertIn("AUTH0_DOMAIN=work-os.jp.auth0.com", output)
        self.assertIn("PORTAL_BASE_URL=", output)
        # the allowlist must never ship empty-but-present: that is the open-door
        # config, and it is far easier to introduce here than to notice later
        self.assertRegex(output, r"PORTAL_ALLOWED_EMAILS=[^,\"\s]+@")
        # A8: enabling portal login must not drag the MCP behind a header
        self.assertNotIn("MCP_SECRET", output)

    def test_cloud_build_requires_explicit_commit_sha_and_no_latest_tag(self):
        config = CLOUD_BUILD.read_text(encoding="utf-8")
        self.assertIn("family-expenses:$COMMIT_SHA", config)
        self.assertNotIn("family-expenses:latest", config)


if __name__ == "__main__":
    unittest.main()
