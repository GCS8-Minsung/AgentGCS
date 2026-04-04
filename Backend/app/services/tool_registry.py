from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from app.services.integrations import (
    create_google_calendar_event,
    send_gmail_notification,
    upload_to_google_drive,
)
from app.services.notebooklm import generate_notebooklm_assets
from app.services.school_api_client import get_school_client_for_user
from app.tools.web_search import (
    search_academic_sources,
    search_general_sources,
    search_trusted_sources,
)

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "openapi_1000school.json"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _normalize_action_name(name: str) -> str:
    lowered = name.strip().lower()
    lowered = re.sub(r"[^a-z0-9_]+", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered


class ToolRegistry:
    def __init__(self) -> None:
        self.spec: dict[str, Any] | None = None
        self.openapi_actions: dict[str, dict[str, str]] = {}
        self._load_openapi_spec()

    def _load_openapi_spec(self) -> None:
        if not OPENAPI_PATH.exists():
            self.spec = None
            self.openapi_actions = {}
            return
        try:
            self.spec = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
        except Exception:
            self.spec = None
            self.openapi_actions = {}
            return

        paths = self.spec.get("paths") if isinstance(self.spec, dict) else None
        if not isinstance(paths, dict):
            self.openapi_actions = {}
            return

        generated: dict[str, dict[str, str]] = {}
        for path, methods in paths.items():
            if not isinstance(path, str) or not isinstance(methods, dict):
                continue
            for method, operation in methods.items():
                if method not in HTTP_METHODS or not isinstance(operation, dict):
                    continue
                operation_id = operation.get("operationId")
                if isinstance(operation_id, str) and operation_id.strip():
                    base_name = _normalize_action_name(operation_id)
                else:
                    base_name = _normalize_action_name(f"{method}_{path.replace('/', '_')}")
                action_name = f"school_{base_name}"
                generated[action_name] = {"method": method.upper(), "path": path}
        self.openapi_actions = generated

    async def call(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> Any:
        params = params or {}
        normalized_action = _normalize_action_name(action)

        if normalized_action in {"web_search", "search_web"}:
            query = str(params.get("query") or params.get("q") or "")
            max_results = int(params.get("max_results") or 5)
            return await search_trusted_sources(query, max_results=max(1, min(max_results, 12)))

        if normalized_action in {"web_search_general", "search_general"}:
            query = str(params.get("query") or params.get("q") or "")
            max_results = int(params.get("max_results") or 5)
            return await search_general_sources(query, max_results=max(1, min(max_results, 12)))

        if normalized_action in {"web_search_academic", "search_academic"}:
            query = str(params.get("query") or params.get("q") or "")
            max_results = int(params.get("max_results") or 5)
            return await search_academic_sources(query, max_results=max(1, min(max_results, 12)))

        if normalized_action in {"school_api", "school_api_call"}:
            if not user_id:
                return {"error": "missing_user_id"}
            method = str(params.get("method") or "GET").upper()
            path = str(params.get("path") or "/auth/me")
            query = params.get("query")
            body = params.get("body")
            client = await get_school_client_for_user(user_id)
            return await client.request_openapi_path(
                method=method,
                path=path,
                params=query if isinstance(query, dict) else None,
                json_body=body if isinstance(body, dict) else None,
            )

        if normalized_action in self.openapi_actions:
            if not user_id:
                return {"error": "missing_user_id"}
            op = self.openapi_actions[normalized_action]
            path = op["path"]
            path_params = params.get("path_params")
            if isinstance(path_params, dict):
                for key, value in path_params.items():
                    path = path.replace("{" + str(key) + "}", str(value))
            query = params.get("query")
            body = params.get("body")
            client = await get_school_client_for_user(user_id)
            return await client.request_openapi_path(
                method=op["method"],
                path=path,
                params=query if isinstance(query, dict) else None,
                json_body=body if isinstance(body, dict) else None,
            )

        if normalized_action in {"notebooklm_generate", "notebooklm"}:
            run_id = str(params.get("run_id") or "tool-run")
            task = str(params.get("task") or "Untitled task")
            final_summary = str(params.get("final_summary") or params.get("summary") or "")
            return await generate_notebooklm_assets(run_id=run_id, task=task, final_summary=final_summary)

        if normalized_action in {"generate_pptx", "pptx_generate"}:
            try:
                from app.services.pptx_generator import generate_pptx_from_summary
            except Exception as exc:
                return {
                    "status": "error",
                    "error": f"pptx dependency unavailable: {str(exc)[:200]}",
                }
            run_id = str(params.get("run_id") or "tool-run")
            title = str(params.get("title") or "AgentGCS Output")
            sections = params.get("sections")
            if not isinstance(sections, list):
                sections = [str(params.get("summary") or "")]
            out_dir = str(params.get("out_dir") or "./outputs")
            pptx_path = generate_pptx_from_summary(
                run_id=run_id,
                title=title,
                sections=[str(item) for item in sections if str(item).strip()],
                out_dir=out_dir,
            )
            return {"status": "generated", "pptx_path": pptx_path}

        if normalized_action in {"upload_drive_file", "google_drive_upload"}:
            if not user_id:
                return {"error": "missing_user_id"}
            file_path = str(params.get("file_path") or "")
            return await upload_to_google_drive(file_path=file_path, user_id=user_id)

        if normalized_action in {"send_gmail", "gmail_send"}:
            if not user_id:
                return {"error": "missing_user_id"}
            return await send_gmail_notification(
                user_id=user_id,
                to_email=str(params.get("to_email") or ""),
                subject=str(params.get("subject") or "AgentGCS Notification"),
                body=str(params.get("body") or ""),
            )

        if normalized_action in {"create_calendar_event", "google_calendar_create"}:
            if not user_id:
                return {"error": "missing_user_id"}
            return await create_google_calendar_event(
                user_id=user_id,
                summary=str(params.get("summary") or ""),
                start_at=str(params.get("start_at") or ""),
                end_at=str(params.get("end_at") or ""),
                description=str(params.get("description") or ""),
                calendar_id=str(params.get("calendar_id") or "primary"),
            )

        if normalized_action in {"list_tools", "tools"}:
            builtin = [
                "web_search",
                "web_search_general",
                "web_search_academic",
                "school_api",
                "notebooklm_generate",
                "generate_pptx",
                "upload_drive_file",
                "send_gmail",
                "create_calendar_event",
            ]
            return {"builtin_tools": builtin, "openapi_tools": sorted(self.openapi_actions.keys())}

        return {"error": "unknown_tool", "action": action, "params": params}


tool_registry = ToolRegistry()
