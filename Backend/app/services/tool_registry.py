import json
import os
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings
from app.tools.web_search import search_trusted_sources

OPENAPI_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "openapi_1000school.json")


class ToolRegistry:
    def __init__(self):
        self.spec = None
        try:
            if os.path.exists(OPENAPI_PATH):
                with open(OPENAPI_PATH, "r", encoding="utf-8") as f:
                    self.spec = json.load(f)
        except Exception:
            self.spec = None

    async def call(self, action: str, params: Optional[Dict[str, Any]] = None, user_id: Optional[str] = None) -> Any:
        params = params or {}
        # builtin web_search
        if action == "web_search":
            query = params.get("query") or params.get("q") or ""
            return await search_trusted_sources(query, max_results=5)

        # school API generic call: action == 'school_api' with params {'method':'GET','path':'/auth/me','params':{}}
        if action == "school_api":
            method = (params.get("method") or "GET").upper()
            path = params.get("path") or "/"
            payload = params.get("payload") or None
            token = settings.school_api_token or settings.anthropic_auth_token
            base = "https://api.1000.school"
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient(timeout=15.0) as client:
                url = base.rstrip("/") + path
                resp = await client.request(method, url, headers=headers, json=payload)
                try:
                    return resp.json()
                except Exception:
                    return resp.text

        # fallback: unknown tool
        return {"error": "unknown_tool", "action": action, "params": params}


# singleton
tool_registry = ToolRegistry()
