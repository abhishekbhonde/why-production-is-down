import json
import logging
from datetime import datetime
from pathlib import Path

import httpx

from src.adapters.base import BaseAdapter
from src.config import settings
from src.utils.rate_limit import check_and_record

logger = logging.getLogger(__name__)

FIXTURE_PATH = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "github_deploys.json"

_BASE = "https://api.github.com"


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


class GitHubAdapter(BaseAdapter):
    name = "github"

    async def _fetch(self, service: str, start: datetime, end: datetime) -> dict:
        if settings.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        repo = f"{settings.github_org}/{service}"

        async with httpx.AsyncClient(timeout=settings.adapter_timeout_seconds) as client:
            deployments = await self._fetch_deployments(client, repo, start, end)
            diff = None
            if deployments:
                # Get the diff for the most recent deployment
                latest = deployments[0]
                diff = await self._fetch_diff(client, repo, latest)

        return {
            "deployments": deployments,
            "diff": diff,
        }

    async def _fetch_deployments(
        self,
        client: httpx.AsyncClient,
        repo: str,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """Lists production deployments within the investigation window.

        GitHub's deployments API doesn't support date filtering natively,
        so we fetch the most recent page and filter client-side.
        """
        if not check_and_record("github_rest"):
            logger.warning("GitHub REST rate limit reached, skipping deployments")
            return []

        response = await client.get(
            f"{_BASE}/repos/{repo}/deployments",
            headers=_auth_headers(),
            params={"environment": "production", "per_page": 30},
        )

        if response.status_code != 200:
            logger.warning(
                "GitHub deployments API returned %d: %s",
                response.status_code,
                response.text[:200],
            )
            return []

        deployments = []
        for d in response.json():
            created = datetime.fromisoformat(d["created_at"].rstrip("Z"))
            if start <= created <= end:
                deployments.append(
                    {
                        "id": d["id"],
                        "ref": d["ref"],
                        "sha": d["sha"],
                        "environment": d["environment"],
                        "creator": d.get("creator", {}).get("login", "unknown"),
                        "created_at": d["created_at"],
                        "description": d.get("description") or "",
                        "statuses_url": d["statuses_url"],
                    }
                )

        # Most recent first
        deployments.sort(key=lambda x: x["created_at"], reverse=True)
        return deployments

    async def _fetch_diff(
        self,
        client: httpx.AsyncClient,
        repo: str,
        deployment: dict,
    ) -> dict | None:
        """Fetches the commit diff for a deployment.

        Compares the deployment SHA against the previous commit (SHA^).
        Truncates patch content to settings.max_diff_lines total.
        """
        if not check_and_record("github_rest"):
            logger.warning("GitHub REST rate limit reached, skipping diff")
            return None

        sha = deployment["sha"]
        base = f"{sha}^"
        head = sha

        response = await client.get(
            f"{_BASE}/repos/{repo}/compare/{base}...{head}",
            headers=_auth_headers(),
        )

        if response.status_code != 200:
            logger.warning(
                "GitHub compare API returned %d: %s",
                response.status_code,
                response.text[:200],
            )
            return None

        payload = response.json()
        files = payload.get("files", [])

        # Truncate patches to stay within the diff line budget
        total_lines = 0
        truncated_files = []
        for f in files:
            patch = f.get("patch", "")
            patch_lines = patch.splitlines()
            remaining = settings.max_diff_lines - total_lines
            if remaining <= 0:
                break
            if len(patch_lines) > remaining:
                patch = "\n".join(patch_lines[:remaining]) + f"\n... (truncated, {len(patch_lines) - remaining} lines omitted)"
            total_lines += min(len(patch_lines), remaining)
            truncated_files.append(
                {
                    "filename": f["filename"],
                    "additions": f["additions"],
                    "deletions": f["deletions"],
                    "patch": patch,
                }
            )

        return {
            "base": payload.get("base_commit", {}).get("sha", base),
            "head": payload.get("merge_base_commit", {}).get("sha", head),
            "url": payload.get("html_url", ""),
            "files_changed": truncated_files,
        }
