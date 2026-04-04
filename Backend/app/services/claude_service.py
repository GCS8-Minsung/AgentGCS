from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
from typing import Any

import httpx

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover - optional dependency at runtime
    Anthropic = None


GENERATION_MAX_TOKENS = 1600
HTTP_GENERATION_TIMEOUT_SEC = 90.0

# When env provides ANTHROPIC_BASE_URL, default to the gateway URL if not set externally
DEFAULT_CLAUDE_GATEWAY = "https://claude.1000.school"


@dataclass(slots=True)
class ClaudeService:
    api_key: str | None = None
    auth_token: str | None = None
    base_url: str | None = None
    preferred_model: str | None = None
    _client: Any = field(init=False, default=None, repr=False)
    _model_cache: list[str] = field(init=False, default_factory=list, repr=False)
    _model_cache_expires_at: float = field(init=False, default=0.0, repr=False)

    def __post_init__(self) -> None:
        # initialize Anthropic SDK client if available and credentials exist
        # fallback: leave _client None and rely on HTTP fallback path using base_url
        if not Anthropic:
            self._client = None
            return
        # prefer explicit base_url, otherwise environment gateway
        base = self.base_url or DEFAULT_CLAUDE_GATEWAY
        if not self.api_key and not self.auth_token:
            self._client = None
            return
        try:
            self._client = Anthropic(
                api_key=self.api_key,
                auth_token=self.auth_token,
                base_url=base,
            )
        except Exception:
            self._client = None

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        use_mock: bool,
        cache_hint: str = "persona-discussion",
    ) -> str:
        if use_mock:
            return self._mock_response(system_prompt, user_prompt)

        errors: list[str] = []
        secret = self.auth_token or self.api_key
        model_candidates = await self._discover_models(secret=secret)

        # Try SDK first when available (prefer streaming/SDK behaviour). If SDK fails, fall back to HTTP.
        if self._client:
            try:
                sdk_text = await self._generate_via_sdk(
                    system_prompt,
                    user_prompt,
                    cache_hint,
                    model_candidates=model_candidates,
                    max_tokens=GENERATION_MAX_TOKENS,
                )
                if sdk_text:
                    return sdk_text
            except Exception as exc:
                errors.append(f"sdk:{type(exc).__name__}:{str(exc)[:120]}")
        else:
            errors.append("sdk:client_unavailable")

        # If SDK could not produce a response, try HTTP fallback via configured base_url
        http_text = await self._generate_via_http_fallback(
            system_prompt,
            user_prompt,
            model_candidates=model_candidates,
            max_tokens=GENERATION_MAX_TOKENS,
        )
        if http_text:
            return http_text
        errors.append("http:no_success_response")

        suffix = f" (fallback: {' | '.join(errors)})" if errors else ""
        return self._mock_response(system_prompt, user_prompt) + suffix

    async def _generate_via_sdk(
        self,
        system_prompt: str,
        user_prompt: str,
        cache_hint: str,
        model_candidates: list[str],
        max_tokens: int,
    ) -> str | None:
        if not self._client:
            return None

        claude_models = [model for model in model_candidates if model.startswith("claude")]
        if not claude_models:
            claude_models = ["claude-sonnet-4-6", "claude-3-5-sonnet-20241022"]

        last_error: Exception | None = None
        for model in claude_models[:4]:
            try:
                def _run() -> str:
                    message = self._client.messages.create(
                        model=model,
                        max_tokens=max_tokens,
                        system=[
                            {
                                "type": "text",
                                "text": system_prompt,
                                "cache_control": {"type": "ephemeral", "ttl": "5m"},
                            }
                        ],
                        messages=[{"role": "user", "content": user_prompt}],
                        metadata={"cache_hint": cache_hint},
                    )
                    return "".join(
                        block.text
                        for block in message.content
                        if getattr(block, "type", "") == "text"
                    ).strip()

                text = await asyncio.to_thread(_run)
                if text:
                    return text
            except Exception as exc:
                last_error = exc
                continue
        if last_error:
            raise last_error
        return None

    async def _generate_via_http_fallback(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model_candidates: list[str],
        max_tokens: int,
    ) -> str | None:
        if not self.base_url:
            return None
        secret = self.auth_token or self.api_key
        if not secret:
            return None

        attempts = self._http_attempts(
            secret=secret,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            model_candidates=model_candidates,
        )

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=HTTP_GENERATION_TIMEOUT_SEC,
        ) as client:
            for path, headers, payload in attempts:
                try:
                    resp = await client.post(path, headers=headers, json=payload)
                    if resp.status_code >= 400:
                        continue
                    data = resp.json() if resp.text else {}
                    text = self._extract_text(data)
                    if text:
                        return text
                except Exception:
                    continue
        return None

    def _http_attempts(
        self,
        *,
        secret: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        model_candidates: list[str],
    ) -> list[tuple[str, dict[str, str], dict[str, Any]]]:
        attempts: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        headers_variants: list[dict[str, str]] = [
            {
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-anthropic-billing-header": "cc_version=2.1.90.8bd; cc_entrypoint=sdk-cli; cch=00000;",
            },
            {
                "Authorization": f"Bearer {secret}",
                "x-api-key": secret,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-anthropic-billing-header": "cc_version=2.1.90.8bd; cc_entrypoint=sdk-cli; cch=00000;",
            },
            {
                "x-api-key": secret,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-anthropic-billing-header": "cc_version=2.1.90.8bd; cc_entrypoint=sdk-cli; cch=00000;",
            },
        ]
        for model in model_candidates:
            anthropic_payload = {
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            }
            chat_payload = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            responses_payload = {
                "model": model,
                "max_output_tokens": max_tokens,
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                    {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
                ],
            }
            for headers in headers_variants:
                attempts.append(("/v1/messages", headers, anthropic_payload))
                attempts.append(("/v1/chat/completions", headers, chat_payload))
                attempts.append(("/v1/responses", headers, responses_payload))
        return attempts

    async def _probe_models(self, secret: str) -> tuple[bool, list[str], list[dict[str, Any]]]:
        if not self.base_url:
            return False, [], []
        attempts: list[dict[str, Any]] = []
        headers_variants = [
            {
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            {
                "Authorization": f"Bearer {secret}",
                "x-api-key": secret,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            {
                "x-api-key": secret,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
        ]
        async with httpx.AsyncClient(base_url=self.base_url, timeout=15.0) as client:
            for headers in headers_variants:
                try:
                    response = await client.get("/v1/models", headers=headers)
                    attempt = {
                        "path": "/v1/models",
                        "status_code": response.status_code,
                        "ok": response.status_code < 400,
                        "body_preview": (response.text or "")[:200],
                    }
                    attempts.append(attempt)
                    if response.status_code >= 400:
                        continue
                    data = response.json() if response.text else {}
                    rows = data.get("data") if isinstance(data, dict) else None
                    if isinstance(rows, list):
                        models = [
                            str(row.get("id"))
                            for row in rows
                            if isinstance(row, dict) and isinstance(row.get("id"), str)
                        ]
                        return True, models, attempts
                except Exception as exc:
                    attempts.append(
                        {
                            "path": "/v1/models",
                            "error": f"{type(exc).__name__}: {str(exc)[:120]}",
                        }
                    )
        return False, [], attempts

    async def _discover_models(self, *, secret: str | None) -> list[str]:
        defaults = self._dedupe(
            [
                self.preferred_model,
                "claude-sonnet-4-6",
                "gpt-5-mini",
                "gpt-5",
                "claude-3-5-sonnet-20241022",
            ]
        )
        now = time.time()
        if self._model_cache and now < self._model_cache_expires_at:
            return self._model_cache
        if not secret:
            self._model_cache = defaults
            self._model_cache_expires_at = now + 300
            return self._model_cache

        ok, models, _attempts = await self._probe_models(secret)
        if ok and models:
            merged = self._dedupe([self.preferred_model, *models, *defaults])
            self._model_cache = merged
            self._model_cache_expires_at = now + 300
            return merged

        self._model_cache = defaults
        self._model_cache_expires_at = now + 120
        return self._model_cache

    def _dedupe(self, values: list[str | None]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not value:
                continue
            if value in seen:
                continue
            seen.add(value)
            output.append(value)
        return output

    async def diagnose_connection(self) -> dict[str, Any]:
        """
        Lightweight probe used by UI/API status cards.
        """
        secret = self.auth_token or self.api_key
        result: dict[str, Any] = {
            "configured": bool(self.base_url and secret),
            "base_url": self.base_url,
            "has_auth_token": bool(self.auth_token),
            "has_api_key": bool(self.api_key),
            "reachable": False,
            "status": "not_configured",
            "attempts": [],
            "available_models": [],
        }
        if not self.base_url or not secret:
            return result

        ok, models, attempts = await self._probe_models(secret)
        result["attempts"] = attempts
        if ok:
            result["reachable"] = True
            result["status"] = "ok"
            result["available_models"] = models[:25]
            self._model_cache = self._dedupe([self.preferred_model, *models])
            self._model_cache_expires_at = time.time() + 300
            return result

        result["status"] = "unreachable"
        status_codes = [row.get("status_code") for row in attempts if "status_code" in row]
        if status_codes and all(code == 502 for code in status_codes):
            result["status"] = "upstream_502"
        return result

    def _extract_text(self, data: Any) -> str | None:
        if not isinstance(data, dict):
            return None
        if isinstance(data.get("output_text"), str):
            return data["output_text"].strip()
        content = data.get("content")
        if isinstance(content, list):
            texts = [
                item.get("text")
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            if texts:
                return "\n".join(texts).strip()

        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            message = first.get("message") if isinstance(first, dict) else {}
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"].strip()
        output = data.get("output")
        if isinstance(output, list):
            chunks: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content_items = item.get("content")
                if not isinstance(content_items, list):
                    continue
                for content_item in content_items:
                    if isinstance(content_item, dict) and isinstance(content_item.get("text"), str):
                        chunks.append(content_item["text"])
            if chunks:
                return "\n".join(chunks).strip()
        return None

    def _mock_response(self, system_prompt: str, user_prompt: str) -> str:
        condensed_system = system_prompt.split(".")[0].strip()
        return f"[MOCK:{condensed_system}] {user_prompt[:220]} ... 초안 응답입니다. 실제 연결 시 상세 결과가 생성됩니다."
