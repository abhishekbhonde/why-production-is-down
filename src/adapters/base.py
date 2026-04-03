import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.config import settings


@dataclass
class AdapterResult:
    source: str
    data: Any
    fetched_at: datetime = field(default_factory=datetime.utcnow)
    error: str | None = None
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out


class BaseAdapter(ABC):
    """All adapters implement this interface.

    Each adapter fetches data for a given service within a time window.
    Failures are isolated — a failing adapter never aborts the investigation.
    """

    name: str = "base"

    async def fetch(self, service: str, start: datetime, end: datetime) -> AdapterResult:
        """Wraps _fetch with a per-adapter timeout and error suppression."""
        try:
            data = await asyncio.wait_for(
                self._fetch(service, start, end),
                timeout=settings.adapter_timeout_seconds,
            )
            return AdapterResult(source=self.name, data=data)
        except asyncio.TimeoutError:
            return AdapterResult(
                source=self.name,
                data=None,
                timed_out=True,
                error=f"{self.name} timed out after {settings.adapter_timeout_seconds}s",
            )
        except Exception as exc:
            return AdapterResult(source=self.name, data=None, error=str(exc))

    @abstractmethod
    async def _fetch(self, service: str, start: datetime, end: datetime) -> Any:
        """Subclasses implement actual data fetching here."""
        ...
