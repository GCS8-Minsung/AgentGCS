from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
import warnings
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import uuid4

import httpx

from app.models.schemas import ActionPlan, ExecutionStepResult, PipelineTrace, PlannerOutput
from app.tools.web_search import search_trusted_sources

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None

try:
    from ddgs import DDGS  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS  # type: ignore[assignment]
        warnings.filterwarnings(
            "ignore",
            message=r"This package \(`duckduckgo_search`\) has been renamed to `ddgs`!.*",
            category=RuntimeWarning,
        )
    except ImportError:  # pragma: no cover
        DDGS = None


DEFAULT_CLAUDE_GATEWAY = "https://claude.1000.school"
GENERATION_MAX_TOKENS = 1600
REQUEST_TIMEOUT = 90.0


class ClaudeServiceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "request_failed",
        status_code: int | None = None,
        retryable: bool = False,
        details: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.details = details


def _status_to_code(status_code: int) -> tuple[str, bool]:
    if status_code in (401, 403):
        return "auth_invalid", False
    if status_code == 429:
        return "quota_exceeded", True
    if status_code in (502, 503, 504):
        return "upstream_unavailable", True
    if status_code >= 500:
        return "upstream_error", True
    return "request_failed", False


@dataclass(slots=True)
class ClaudeService:
    api_key: str | None = None
    auth_token: str | None = None
    base_url: str | None = None
    preferred_model: str | None = None
    openai_api_key: str | None = None
    openai_fallback_url: str = "https://api.openai.com/v1/chat/completions"
    openai_fallback_model: str = "gpt-5-mini"
    primary_provider: str = "claude"
    _client: Any = field(init=False, default=None, repr=False)
    _model_cache: list[str] = field(init=False, default_factory=list, repr=False)
    _model_cache_exp: float = field(init=False, default=0.0, repr=False)
    _fail_count: int = field(init=False, default=0, repr=False)
    _circuit_until: float = field(init=False, default=0.0, repr=False)
    _last_error: str | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.getenv("CLAUDE_API_KEY")
        self.auth_token = self.auth_token or os.getenv("ANTHROPIC_AUTH_TOKEN")
        self.base_url = self.base_url or os.getenv("ANTHROPIC_BASE_URL") or DEFAULT_CLAUDE_GATEWAY
        self.openai_api_key = self.openai_api_key or os.getenv("OPENAI_API_KEY")
        self.openai_fallback_url = self.openai_fallback_url or os.getenv(
            "OPENAI_FALLBACK_URL"
        ) or "https://api.openai.com/v1/chat/completions"
        self.openai_fallback_model = self.openai_fallback_model or os.getenv(
            "OPENAI_FALLBACK_MODEL"
        ) or "gpt-5-mini"
        if self.primary_provider not in {"claude", "openai"}:
            self.primary_provider = "claude"
        if Anthropic and (self.api_key or self.auth_token):
            try:
                self._client = Anthropic(api_key=self.api_key, auth_token=self.auth_token, base_url=self.base_url)
            except Exception:
                self._client = None

    def _secret(self) -> str:
        if time.time() < self._circuit_until:
            raise ClaudeServiceError(
                "Claude circuit is open.",
                code="circuit_open",
                retryable=True,
                details=self._last_error,
            )
        secret = self.auth_token or self.api_key
        if not secret:
            raise ClaudeServiceError("Claude token is missing.", code="auth_missing")
        return secret

    def _ok(self) -> None:
        self._fail_count = 0
        self._circuit_until = 0.0
        self._last_error = None

    def _fail(self, code: str) -> None:
        self._fail_count += 1
        self._last_error = code
        if self._fail_count >= 3:
            self._circuit_until = time.time() + 20

    async def _discover_models(self, secret: str) -> list[str]:
        now = time.time()
        if self._model_cache and now < self._model_cache_exp:
            return self._model_cache
        defaults = [m for m in [self.preferred_model, "claude-sonnet-4-6", "claude-sonnet-4.6"] if m]
        ok, models, _ = await self._probe_models(secret)
        merged = list(dict.fromkeys([*defaults, *(models if ok else [])]))
        self._model_cache = merged or defaults or ["claude-sonnet-4-6"]
        self._model_cache_exp = now + (300 if ok else 120)
        return self._model_cache

    async def _probe_models(self, secret: str) -> tuple[bool, list[str], list[dict[str, Any]]]:
        attempts: list[dict[str, Any]] = []
        headers = {"Authorization": f"Bearer {secret}", "anthropic-version": "2023-06-01"}
        try:
            async with httpx.AsyncClient(base_url=self.base_url or DEFAULT_CLAUDE_GATEWAY, timeout=15.0) as client:
                r = await client.get("/v1/models", headers=headers)
            attempts.append({"path": "/v1/models", "status_code": r.status_code, "body_preview": (r.text or "")[:180]})
            if r.status_code >= 400:
                return False, [], attempts
            data = r.json() if r.text else {}
            rows = data.get("data") if isinstance(data, dict) else []
            models = [str(x.get("id")) for x in rows if isinstance(x, dict) and isinstance(x.get("id"), str)]
            return True, models, attempts
        except Exception as exc:
            attempts.append({"path": "/v1/models", "error": str(exc)[:180]})
            return False, [], attempts

    def _extract_text(self, payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        content = payload.get("content")
        if isinstance(content, list):
            chunks = [x.get("text") for x in content if isinstance(x, dict) and x.get("type") == "text" and isinstance(x.get("text"), str)]
            if chunks:
                return "\n".join(chunks).strip()
        if isinstance(payload.get("output_text"), str):
            return payload["output_text"].strip()
        return None

    def _openai_model(self) -> str:
        preferred = (self.preferred_model or "").strip()
        if preferred.startswith("gpt-"):
            return preferred
        return self.openai_fallback_model or "gpt-5-mini"

    def _flatten_messages_for_openai(self, messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for row in messages:
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "user")
            content = row.get("content")
            if isinstance(content, str):
                text = content.strip()
            elif isinstance(content, list):
                chunks: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = str(block.get("type") or "")
                    if btype == "text" and isinstance(block.get("text"), str):
                        chunks.append(block["text"])
                    elif btype == "tool_result":
                        raw = block.get("content")
                        if isinstance(raw, str):
                            chunks.append(f"[tool_result]\n{raw}")
                        else:
                            chunks.append(f"[tool_result]\n{json.dumps(raw, ensure_ascii=False)}")
                    else:
                        chunks.append(json.dumps(block, ensure_ascii=False))
                text = "\n".join(chunks).strip()
            else:
                text = json.dumps(content, ensure_ascii=False)
            if text:
                lines.append(f"{role}: {text}")
        return "\n\n".join(lines)

    async def _openai_chat_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        force_json: bool = False,
    ) -> str:
        key = (self.openai_api_key or "").strip()
        if not key:
            raise ClaudeServiceError("OpenAI fallback key is missing.", code="openai_key_missing")

        payload: dict[str, Any] = {
            "model": self._openai_model(),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if force_json:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

        async def _post_openai(json_payload: dict[str, Any]) -> httpx.Response:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                return await client.post(self.openai_fallback_url, headers=headers, json=json_payload)

        try:
            resp = await _post_openai(payload)
            # Some models may reject response_format=json_object; retry once without it.
            if (
                resp.status_code == 400
                and force_json
                and isinstance(payload.get("response_format"), dict)
            ):
                lower_body = (resp.text or "").lower()
                if "response_format" in lower_body or "unsupported" in lower_body:
                    payload.pop("response_format", None)
                    resp = await _post_openai(payload)
        except httpx.TimeoutException as exc:
            raise ClaudeServiceError(
                "OpenAI fallback timeout.",
                code="openai_timeout",
                retryable=True,
                details=str(exc),
            ) from exc
        except Exception as exc:
            raise ClaudeServiceError(
                "OpenAI fallback request failed.",
                code="openai_request_failed",
                retryable=True,
                details=str(exc),
            ) from exc

        if resp.status_code >= 400:
            code, retryable = _status_to_code(resp.status_code)
            raise ClaudeServiceError(
                "OpenAI fallback request failed.",
                code=f"openai_{code}",
                status_code=resp.status_code,
                retryable=retryable,
                details=(resp.text or "")[:240],
            )

        data = resp.json() if resp.text else {}
        try:
            choice = (data.get("choices") or [])[0]
            message = choice.get("message") if isinstance(choice, dict) else {}
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        parts.append(part["text"])
                text = "\n".join(parts).strip()
                if text:
                    return text
        except Exception:
            pass
        raise ClaudeServiceError(
            "OpenAI fallback returned empty content.",
            code="openai_empty_response",
        )

    async def _http_messages(
        self,
        *,
        secret: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        headers_variants = [
            {"Authorization": f"Bearer {secret}", "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            {"Authorization": f"Bearer {secret}", "x-api-key": secret, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            {"x-api-key": secret, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        ]
        attempts: list[dict[str, Any]] = []
        async with httpx.AsyncClient(base_url=self.base_url or DEFAULT_CLAUDE_GATEWAY, timeout=REQUEST_TIMEOUT) as client:
            for headers in headers_variants:
                try:
                    resp = await client.post("/v1/messages", headers=headers, json=payload)
                except httpx.TimeoutException as exc:
                    raise ClaudeServiceError("Claude timeout.", code="timeout", retryable=True, details=str(exc)) from exc
                except Exception as exc:
                    attempts.append({"error": str(exc)[:160]})
                    continue
                if resp.status_code < 400:
                    return resp.json() if resp.text else {}
                code, retryable = _status_to_code(resp.status_code)
                attempts.append({"status": resp.status_code, "code": code, "body": (resp.text or "")[:180]})
                if not retryable:
                    raise ClaudeServiceError(
                        "Claude request failed.",
                        code=code,
                        status_code=resp.status_code,
                        retryable=False,
                        details=(resp.text or "")[:220],
                    )
        if attempts:
            first = attempts[0]
            raise ClaudeServiceError(
                "Claude request failed.",
                code=str(first.get("code") or "request_failed"),
                status_code=first.get("status") if isinstance(first.get("status"), int) else None,
                retryable=True,
                details=json.dumps(attempts[:3], ensure_ascii=False),
            )
        raise ClaudeServiceError("Claude request failed.", code="request_failed")

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        use_mock: bool,
        cache_hint: str = "default",
    ) -> str:
        if use_mock:
            return self._mock_response(system_prompt, user_prompt)
        if self.primary_provider == "openai":
            return await self._openai_chat_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                force_json=False,
            )
        secret = self._secret()
        models = await self._discover_models(secret)
        saw_quota_exceeded = False

        # SDK first
        if self._client:
            for model in models[:4]:
                try:
                    def _run() -> str:
                        msg = self._client.messages.create(
                            model=model,
                            max_tokens=GENERATION_MAX_TOKENS,
                            system=[{"type": "text", "text": system_prompt}],
                            messages=[{"role": "user", "content": user_prompt}],
                            metadata={"cache_hint": cache_hint},
                        )
                        return "".join(getattr(x, "text", "") for x in getattr(msg, "content", []) if getattr(x, "type", "") == "text").strip()
                    text = await asyncio.to_thread(_run)
                    if text:
                        self._ok()
                        return text
                except Exception as exc:
                    if "429" in str(exc) or "quota" in str(exc).lower():
                        saw_quota_exceeded = True
                    continue

        # HTTP fallback
        errors: list[str] = []
        for model in models[:6]:
            payload = {
                "model": model,
                "max_tokens": GENERATION_MAX_TOKENS,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            }
            try:
                data = await self._http_messages(secret=secret, payload=payload)
                text = self._extract_text(data)
                if text:
                    self._ok()
                    return text
                errors.append(f"{model}:empty_text")
            except ClaudeServiceError as exc:
                errors.append(f"{model}:{exc.code}")
                if exc.code == "quota_exceeded":
                    saw_quota_exceeded = True
                    try:
                        text = await self._openai_chat_completion(
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            force_json=False,
                        )
                        self._ok()
                        return text
                    except ClaudeServiceError as openai_exc:
                        errors.append(f"openai:{openai_exc.code}")
                if not exc.retryable:
                    self._fail(exc.code)
                    raise
                continue

        if saw_quota_exceeded:
            try:
                text = await self._openai_chat_completion(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    force_json=False,
                )
                self._ok()
                return text
            except ClaudeServiceError as openai_exc:
                errors.append(f"openai:{openai_exc.code}")

        self._fail("request_failed")
        raise ClaudeServiceError(
            "Claude generation failed.",
            code="request_failed",
            retryable=True,
            details=" | ".join(errors[:6]),
        )

    async def call_with_tools(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | None,
        use_mock: bool,
        max_tokens: int = 1400,
    ) -> dict[str, Any]:
        if use_mock:
            return {"content": [], "text": self._mock_response(system_prompt, ""), "model": "mock"}
        if self.primary_provider == "openai":
            openai_text = await self._openai_chat_completion(
                system_prompt=(
                    f"{system_prompt}\n\n"
                    "IMPORTANT: Return strictly valid JSON ActionPlan object only."
                ),
                user_prompt=self._flatten_messages_for_openai(messages),
                force_json=True,
            )
            return {"content": [], "text": openai_text, "model": "openai_primary"}
        secret = self._secret()
        models = await self._discover_models(secret)
        saw_quota_exceeded = False
        for model in models[:4]:
            payload: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": messages,
                "tools": tools,
            }
            if tool_choice:
                payload["tool_choice"] = tool_choice
            try:
                data = await self._http_messages(secret=secret, payload=payload)
            except ClaudeServiceError as exc:
                if exc.code == "quota_exceeded":
                    saw_quota_exceeded = True
                    continue
                raise
            content = data.get("content") if isinstance(data, dict) else []
            if not isinstance(content, list):
                content = []
            text = self._extract_text(data) or ""
            self._ok()
            return {"content": content, "text": text, "model": model}

        if saw_quota_exceeded:
            openai_text = await self._openai_chat_completion(
                system_prompt=(
                    f"{system_prompt}\n\n"
                    "IMPORTANT: You are fallback planner. Return strictly valid JSON ActionPlan object only."
                ),
                user_prompt=self._flatten_messages_for_openai(messages),
                force_json=True,
            )
            self._ok()
            return {"content": [], "text": openai_text, "model": "openai_fallback"}

        self._fail("tool_call_failed")
        raise ClaudeServiceError("Tool-call request failed.", code="tool_call_failed", retryable=True)

    async def diagnose_connection(self) -> dict[str, Any]:
        secret = self.auth_token or self.api_key
        result = {
            "configured": bool(self.base_url and secret),
            "base_url": self.base_url,
            "has_auth_token": bool(self.auth_token),
            "has_api_key": bool(self.api_key),
            "reachable": False,
            "status": "not_configured",
            "attempts": [],
            "available_models": [],
        }
        if not secret:
            return result
        ok, models, attempts = await self._probe_models(secret)
        result["attempts"] = attempts
        if ok:
            result["reachable"] = True
            result["status"] = "ok"
            result["available_models"] = models[:25]
        else:
            status_codes = [x.get("status_code") for x in attempts if isinstance(x.get("status_code"), int)]
            if status_codes:
                result["status"] = _status_to_code(int(status_codes[0]))[0]
            else:
                result["status"] = "upstream_unavailable"
        return result

    async def diagnose_openai_connection(self) -> dict[str, Any]:
        key = (self.openai_api_key or "").strip()
        result = {
            "configured": bool(key),
            "reachable": False,
            "status": "not_configured",
            "model": self._openai_model(),
        }
        if not key:
            return result
        headers = {"Authorization": f"Bearer {key}"}
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.get("https://api.openai.com/v1/models", headers=headers)
            if resp.status_code < 400:
                result["reachable"] = True
                result["status"] = "ok"
                return result
            code, _ = _status_to_code(resp.status_code)
            result["status"] = code
            return result
        except httpx.TimeoutException:
            result["status"] = "timeout"
            return result
        except Exception:
            result["status"] = "upstream_unavailable"
            return result

    def _mock_response(self, system_prompt: str, user_prompt: str) -> str:
        return f"[MOCK:{system_prompt[:48]}] {user_prompt[:180]} ..."


class AgentPipelineError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AgentPipeline:
    def __init__(
        self,
        *,
        claude: ClaudeService,
        tool_call: Callable[[str, dict[str, Any] | None, str | None], Awaitable[Any]],
        ws_emit: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None = None,
        max_retries: int = 3,
        planner_timeout_sec: float = 30.0,
        executor_step_timeout_sec: float = 25.0,
        synthesizer_timeout_sec: float = 45.0,
    ) -> None:
        self.claude = claude
        self.tool_call = tool_call
        self.ws_emit = ws_emit
        self.max_retries = max_retries
        self.planner_timeout_sec = planner_timeout_sec
        self.executor_step_timeout_sec = executor_step_timeout_sec
        self.synthesizer_timeout_sec = synthesizer_timeout_sec

    async def run(
        self,
        *,
        user_id: str,
        mode: str,
        message: str,
        persona_stats: dict[str, int],
        knowledge: str,
        thread_id: str,
        use_mock: bool,
    ) -> PipelineTrace:
        run_id = str(uuid4())
        retry_count = 0
        observations: list[str] = []
        planner_output: PlannerOutput | None = None
        execution_results: list[ExecutionStepResult] = []

        await self._emit(user_id, "pipeline.started", {"run_id": run_id, "thread_id": thread_id, "mode": mode})
        while retry_count <= self.max_retries:
            await self._emit(
                user_id,
                "pipeline.planner.started",
                {"run_id": run_id, "thread_id": thread_id, "retry_count": retry_count},
            )
            try:
                planner_output = await asyncio.wait_for(
                    self._planner_stage(
                        user_id=user_id,
                        message=message,
                        persona_stats=persona_stats,
                        knowledge=knowledge,
                        observations=observations,
                        use_mock=use_mock,
                    ),
                    timeout=self.planner_timeout_sec,
                )
            except TimeoutError:
                retry_count += 1
                observations.append("PLANNER_TIMEOUT")
                await self._emit(
                    user_id,
                    "pipeline.planner.failed",
                    {"run_id": run_id, "thread_id": thread_id, "code": "planner_timeout", "retry_count": retry_count},
                )
                await self._emit(
                    user_id,
                    "pipeline.executor.replan_requested",
                    {"run_id": run_id, "thread_id": thread_id, "reason": "PLANNER_TIMEOUT", "retry_count": retry_count},
                )
                continue
            except Exception as exc:
                retry_count += 1
                observations.append(f"PLANNER_ERROR:{str(exc)[:180]}")
                await self._emit(
                    user_id,
                    "pipeline.planner.failed",
                    {"run_id": run_id, "thread_id": thread_id, "code": "planner_error", "error": str(exc)[:180], "retry_count": retry_count},
                )
                await self._emit(
                    user_id,
                    "pipeline.executor.replan_requested",
                    {"run_id": run_id, "thread_id": thread_id, "reason": "PLANNER_ERROR", "retry_count": retry_count},
                )
                continue
            await self._emit(
                user_id,
                "pipeline.planner.completed",
                {"run_id": run_id, "thread_id": thread_id, "step_count": len(planner_output.plan.steps)},
            )

            await self._emit(
                user_id,
                "pipeline.executor.started",
                {"run_id": run_id, "thread_id": thread_id, "step_count": len(planner_output.plan.steps)},
            )
            execution_results, failed = await self._executor_stage(
                user_id=user_id,
                run_id=run_id,
                plan=planner_output.plan,
                use_mock=use_mock,
            )
            await self._emit(
                user_id,
                "pipeline.executor.completed",
                {"run_id": run_id, "thread_id": thread_id, "failed": bool(failed), "step_count": len(execution_results)},
            )
            if not failed:
                break
            retry_count += 1
            observations.append(failed)
            await self._emit(user_id, "pipeline.executor.replan_requested", {"run_id": run_id, "thread_id": thread_id, "reason": failed, "retry_count": retry_count})

        if planner_output is None:
            await self._emit(user_id, "pipeline.failed", {"run_id": run_id, "thread_id": thread_id, "code": "planner_failed"})
            planner_output = PlannerOutput(plan=self._fallback_plan(message), search_context=[], planner_notes="planner_missing_fallback")
        if retry_count > self.max_retries:
            await self._emit(user_id, "pipeline.failed", {"run_id": run_id, "thread_id": thread_id, "code": "max_retries_exceeded"})
            final_markdown = self._build_execution_fallback_markdown(
                message=message,
                execution_results=execution_results,
                observations=observations,
            )
            await self._emit(user_id, "pipeline.completed", {"run_id": run_id, "thread_id": thread_id, "degraded": True})
            return PipelineTrace(
                run_id=run_id,
                mode=mode,  # type: ignore[arg-type]
                planner_output=planner_output,
                execution_results=execution_results,
                final_markdown=final_markdown,
                retry_count=retry_count,
            )

        await self._emit(user_id, "pipeline.synthesizer.started", {"run_id": run_id, "thread_id": thread_id})
        try:
            final_markdown = await asyncio.wait_for(
                self._synthesizer_stage(
                    mode=mode,
                    message=message,
                    persona_stats=persona_stats,
                    planner_output=planner_output,
                    execution_results=execution_results,
                    use_mock=use_mock,
                ),
                timeout=self.synthesizer_timeout_sec,
            )
        except TimeoutError:
            await self._emit(
                user_id,
                "pipeline.failed",
                {"run_id": run_id, "thread_id": thread_id, "code": "synthesizer_timeout"},
            )
            final_markdown = self._build_execution_fallback_markdown(
                message=message,
                execution_results=execution_results,
                observations=["SYNTHESIZER_TIMEOUT"],
            )
        except Exception as exc:
            await self._emit(
                user_id,
                "pipeline.failed",
                {"run_id": run_id, "thread_id": thread_id, "code": "synthesizer_error", "error": str(exc)[:180]},
            )
            final_markdown = self._build_execution_fallback_markdown(
                message=message,
                execution_results=execution_results,
                observations=[f"SYNTHESIZER_ERROR:{str(exc)[:180]}"],
            )
        await self._emit(user_id, "pipeline.synthesizer.completed", {"run_id": run_id, "thread_id": thread_id, "length": len(final_markdown)})
        await self._emit(user_id, "pipeline.completed", {"run_id": run_id, "thread_id": thread_id, "degraded": False})

        return PipelineTrace(
            run_id=run_id,
            mode=mode,  # type: ignore[arg-type]
            planner_output=planner_output,
            execution_results=execution_results,
            final_markdown=final_markdown,
            retry_count=retry_count,
        )

    async def _emit(self, user_id: str, event_type: str, payload: dict[str, Any]) -> None:
        if not self.ws_emit:
            return
        try:
            await self.ws_emit(user_id, event_type, payload)
        except Exception:
            return

    async def web_search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        max_results = max(1, min(max_results, 10))
        try:
            rows = await search_trusted_sources(query=query, max_results=max_results, intent="auto")
            if rows:
                return rows
        except Exception:
            pass
        if DDGS is None:
            return []

        def _run() -> list[dict[str, Any]]:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                with DDGS() as ddgs:
                    raw_rows = list(ddgs.text(query, max_results=max_results))
            out: list[dict[str, Any]] = []
            for row in raw_rows:
                if not isinstance(row, dict):
                    continue
                title = str(row.get("title") or "").strip()
                url = str(row.get("href") or "").strip()
                snippet = str(row.get("body") or "").strip()
                if not title or not url.startswith("http"):
                    continue
                out.append({"title": title, "url": url, "snippet": snippet, "source": "ddgs_direct"})
            return out

        try:
            return await asyncio.to_thread(_run)
        except Exception:
            return []

    def _planner_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "web_search",
                "description": "Search web before final plan when URL/domain knowledge is uncertain.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "submit_plan",
                "description": "Submit ActionPlan JSON.",
                "input_schema": ActionPlan.model_json_schema(),
            },
        ]

    async def _planner_stage(
        self,
        *,
        user_id: str,
        message: str,
        persona_stats: dict[str, int],
        knowledge: str,
        observations: list[str],
        use_mock: bool,
    ) -> PlannerOutput:
        system_prompt = (
            "You are Planner. Build executable ActionPlan JSON.\n"
            "Search-First rule: if URL/domain/current policy is uncertain, call web_search first.\n"
            "Return final plan via submit_plan tool."
        )
        if knowledge:
            system_prompt += f"\n\nKnowledge:\n{knowledge[:3000]}"

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"User request:\n{message}\n\n"
                    f"Persona stats:\n{json.dumps(persona_stats, ensure_ascii=False)}\n\n"
                    f"Execution observations:\n{json.dumps(observations[-3:], ensure_ascii=False)}"
                ),
            }
        ]
        search_context: list[dict[str, Any]] = []

        for _ in range(3):
            response = await self.claude.call_with_tools(
                system_prompt=system_prompt,
                messages=messages,
                tools=self._planner_tools(),
                tool_choice=None,
                use_mock=use_mock,
                max_tokens=1300,
            )
            blocks = response.get("content") if isinstance(response, dict) else []
            if not isinstance(blocks, list):
                blocks = []

            planned = False
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = str(block.get("name") or "")
                payload = block.get("input") if isinstance(block.get("input"), dict) else {}
                if name == "web_search":
                    query = str(payload.get("query") or message)
                    rows = await self.web_search(query=query, max_results=int(payload.get("max_results") or 5))
                    search_context.extend(rows[:8])
                    await self._emit(user_id, "pipeline.planner.updated", {"status": "search_observation", "query": query, "result_count": len(rows)})
                    messages.append({"role": "assistant", "content": blocks})
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.get("id") or f"tool_{uuid4()}",
                                    "content": json.dumps(rows[:8], ensure_ascii=False),
                                }
                            ],
                        }
                    )
                    planned = True
                    break
                if name == "submit_plan":
                    plan = self._coerce_plan(payload, message)
                    return PlannerOutput(plan=plan, search_context=search_context[:12], planner_notes=response.get("text") if isinstance(response, dict) else None)
            if planned:
                continue

            text = str(response.get("text") or "") if isinstance(response, dict) else ""
            parsed = self._extract_json_obj(text)
            if parsed:
                return PlannerOutput(plan=self._coerce_plan(parsed, message), search_context=search_context[:12], planner_notes=text[:800] or None)
            messages.append({"role": "user", "content": "Return final ActionPlan via submit_plan tool now."})

        return PlannerOutput(plan=self._fallback_plan(message), search_context=search_context[:12], planner_notes="fallback_plan")

    def _fallback_plan(self, message: str) -> ActionPlan:
        compact_message = re.sub(r"\s+", " ", (message or "")).strip()
        if len(compact_message) > 1100:
            compact_message = compact_message[:1100]
        steps: list[dict[str, Any]] = [
            {
                "step_id": "step_1",
                "tool": "web_search",
                "purpose": "요청 주제의 최신 정보 검색",
                "query": compact_message,
                "params": {"query": compact_message, "max_results": 5},
            }
        ]
        lowered = compact_message.lower()
        if "auth/me" in lowered or "내 정보" in compact_message:
            steps.append(
                {
                    "step_id": "step_2",
                    "tool": "school_api_call",
                    "purpose": "사용자 인증 정보 조회",
                    "method": "GET",
                    "path": "/auth/me",
                    "params": {"method": "GET", "path": "/auth/me"},
                }
            )
        return ActionPlan(objective=compact_message[:700], requires_search_first=True, steps=steps, notes="fallback_plan")

    def _coerce_plan(self, payload: dict[str, Any], message: str) -> ActionPlan:
        cleaned = self._sanitize_action_plan_payload(payload, message)
        try:
            return ActionPlan(**cleaned)
        except Exception:
            return self._fallback_plan(message)

    def _sanitize_action_plan_payload(self, payload: dict[str, Any], message: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return self._fallback_plan(message).model_dump(mode="json")
        cleaned: dict[str, Any] = dict(payload)
        objective = str(cleaned.get("objective") or message).strip()
        cleaned["objective"] = re.sub(r"\s+", " ", objective)[:780] or message[:780]
        notes = cleaned.get("notes")
        if isinstance(notes, str):
            cleaned["notes"] = re.sub(r"\s+", " ", notes).strip()[:1800]

        steps = cleaned.get("steps")
        normalized_steps: list[dict[str, Any]] = []
        if isinstance(steps, list):
            for idx, raw in enumerate(steps[:20]):
                if not isinstance(raw, dict):
                    continue
                step = dict(raw)
                step["step_id"] = str(step.get("step_id") or f"step_{idx + 1}")[:64]
                step["tool"] = str(step.get("tool") or "web_search")[:120]
                step["purpose"] = str(step.get("purpose") or "검색/도구 실행")[:500]
                if isinstance(step.get("method"), str):
                    step["method"] = str(step["method"])[:12]
                if isinstance(step.get("path"), str):
                    step["path"] = str(step["path"])[:300]
                if isinstance(step.get("query"), str):
                    step["query"] = re.sub(r"\s+", " ", str(step["query"])).strip()[:1100]
                params = step.get("params")
                if isinstance(params, dict):
                    p = dict(params)
                    if isinstance(p.get("query"), str):
                        p["query"] = re.sub(r"\s+", " ", str(p["query"])).strip()[:1100]
                    step["params"] = p
                else:
                    step["params"] = {}
                normalized_steps.append(step)
        if not normalized_steps:
            return self._fallback_plan(message).model_dump(mode="json")
        cleaned["steps"] = normalized_steps
        cleaned["requires_search_first"] = bool(cleaned.get("requires_search_first", True))
        return cleaned

    async def _executor_stage(
        self,
        *,
        user_id: str,
        run_id: str,
        plan: ActionPlan,
        use_mock: bool,
    ) -> tuple[list[ExecutionStepResult], str | None]:
        rows: list[ExecutionStepResult] = []
        failed: str | None = None
        for idx, step in enumerate(plan.steps):
            await self._emit(user_id, "pipeline.executor.step_started", {"run_id": run_id, "index": idx, "step_id": step.step_id, "tool": step.tool})
            params = dict(step.params or {})
            if step.query and "query" not in params:
                params["query"] = step.query
            if step.path:
                params.setdefault("path", step.path)
            if step.method:
                params.setdefault("method", step.method)
            try:
                if use_mock:
                    if step.tool == "web_search":
                        query = str(params.get("query") or "")
                        result = [
                            {
                                "title": f"[MOCK] {query[:60]}",
                                "url": "https://example.com/mock-result",
                                "snippet": "mocked web search result",
                                "source": "mock",
                            }
                        ]
                    else:
                        result = {"mocked": True, "tool": step.tool, "params": params}
                else:
                    result = await asyncio.wait_for(
                        self.tool_call(step.tool, params, user_id),
                        timeout=self.executor_step_timeout_sec,
                    )
            except TimeoutError:
                err = f"timeout({self.executor_step_timeout_sec:.0f}s)"
                rows.append(ExecutionStepResult(step_id=step.step_id, tool=step.tool, status="error", error=err))
                failed = f"TOOL_TIMEOUT:{step.tool}:{err}"
                await self._emit(
                    user_id,
                    "pipeline.executor.step_failed",
                    {"run_id": run_id, "step_id": step.step_id, "tool": step.tool, "error": err},
                )
                break
            except Exception as exc:
                err = str(exc)
                rows.append(ExecutionStepResult(step_id=step.step_id, tool=step.tool, status="error", error=err))
                failed = f"TOOL_ERROR:{step.tool}:{err[:220]}"
                await self._emit(user_id, "pipeline.executor.step_failed", {"run_id": run_id, "step_id": step.step_id, "tool": step.tool, "error": err[:220]})
                break
            if isinstance(result, dict) and result.get("error"):
                err = str(result.get("error"))
                rows.append(ExecutionStepResult(step_id=step.step_id, tool=step.tool, status="error", result=result, error=err))
                failed = f"TOOL_ERROR:{step.tool}:{err[:220]}"
                await self._emit(user_id, "pipeline.executor.step_failed", {"run_id": run_id, "step_id": step.step_id, "tool": step.tool, "error": err[:220]})
                break
            if self._is_empty(result):
                rows.append(ExecutionStepResult(step_id=step.step_id, tool=step.tool, status="empty", result=result, error="empty_result"))
                await self._emit(
                    user_id,
                    "pipeline.executor.step_empty",
                    {"run_id": run_id, "step_id": step.step_id, "tool": step.tool, "error": "empty_result"},
                )
                # 빈 결과는 치명 오류로 보지 않는다. 다음 step 또는 synthesizer로 진행한다.
                continue
            rows.append(ExecutionStepResult(step_id=step.step_id, tool=step.tool, status="ok", result=result))
            await self._emit(user_id, "pipeline.executor.step_succeeded", {"run_id": run_id, "step_id": step.step_id, "tool": step.tool})
        return rows, failed

    def _build_execution_fallback_markdown(
        self,
        *,
        message: str,
        execution_results: list[ExecutionStepResult],
        observations: list[str],
    ) -> str:
        lines = [
            "요청을 처리하는 중 일부 도구 실행이 연속 실패해 자동 복구 한도를 초과했습니다.",
            "",
            "### 현재 상태",
            f"- 요청: {message[:220]}",
            f"- 실행 단계 수: {len(execution_results)}",
        ]
        if observations:
            lines.append(f"- 마지막 오류: `{observations[-1][:220]}`")
        lines.extend(
            [
                "",
                "### 권장 조치",
                "1. 잠시 후 동일 요청을 다시 실행해 주세요.",
                "2. 검색 요청이면 키워드를 더 구체화해 주세요.",
                "3. API 도구 요청이면 연결 상태/권한을 설정에서 확인해 주세요.",
            ]
        )
        return "\n".join(lines)

    async def _synthesizer_stage(
        self,
        *,
        mode: str,
        message: str,
        persona_stats: dict[str, int],
        planner_output: PlannerOutput,
        execution_results: list[ExecutionStepResult],
        use_mock: bool,
    ) -> str:
        system_prompt = (
            "You are Synthesizer. Reply in Korean markdown.\n"
            "Use only raw execution data + logs.\n"
            f"mode={mode}, persona={json.dumps(persona_stats, ensure_ascii=False)}"
        )
        user_prompt = (
            f"User request:\n{message}\n\n"
            f"Plan:\n{planner_output.model_dump_json(indent=2)}\n\n"
            f"Execution:\n{json.dumps([x.model_dump(mode='json') for x in execution_results], ensure_ascii=False, indent=2)}\n\n"
            "Generate the final answer."
        )
        return await self.claude.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            use_mock=use_mock,
            cache_hint=f"synth-{random.randint(1000, 9999)}",
        )

    def _is_empty(self, result: Any) -> bool:
        if result is None:
            return True
        if isinstance(result, str):
            return len(result.strip()) == 0
        if isinstance(result, (list, tuple, set)):
            return len(result) == 0
        if isinstance(result, dict):
            if not result:
                return True
            if "items" in result and isinstance(result["items"], list) and len(result["items"]) == 0:
                return True
        return False

    def _extract_json_obj(self, text: str) -> dict[str, Any] | None:
        raw = (text or "").strip()
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            return None
        try:
            parsed = json.loads(m.group(0))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
