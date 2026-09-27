from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from ..db import now_iso

logger = logging.getLogger(__name__)


class WebhookEmitter:
    """Onay/aksiyon olaylarini sirket ici sistemlere (dashboard, back office) bildirir.

    Olay govdesi: {"event": <tip>, "ts": <iso>, ...payload}
    Kimlik dogrulama: Authorization: Bearer <INTEGRATION_TOKEN>
    """

    def __init__(self, url: str, token: str = "", client: httpx.AsyncClient | None = None) -> None:
        self._url = url
        self._token = token
        self._client = client

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=8)
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        body = {"event": event, "ts": now_iso(), **payload}
        try:
            response = await self._client.post(self._url, json=body, headers=headers)
            if response.status_code >= 300:
                logger.warning("webhook %s -> %s: %s", event, response.status_code, response.text[:200])
        except Exception as exc:  # noqa: BLE001 - webhook hatasi run'i bozmamali
            logger.warning("webhook gonderilemedi (%s): %s", event, exc)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def parse_tool_payload(content: Any) -> dict:
    """Tool sonucunu webhook icin JSON'a cevirir."""
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {"text": content[:2000]}
    return {"value": str(content)[:2000]}
