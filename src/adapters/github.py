import json
from datetime import datetime
from pathlib import Path

from src.adapters.base import BaseAdapter
from src.config import settings

FIXTURE_PATH = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "github_deploys.json"


class GitHubAdapter(BaseAdapter):
    name = "github"

    async def _fetch(self, service: str, start: datetime, end: datetime) -> dict:
        if settings.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        # TODO: implement live GitHub API calls
        # - GET /repos/{org}/{repo}/deployments filtered by date
        # - GET /repos/{org}/{repo}/compare/{base}...{head} for diffs
        raise NotImplementedError("Live GitHub integration not yet implemented")
