from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from app.core.config import settings


def _shorten(text: str, limit: int = 500) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def _extract_json_payload(raw: str) -> Any | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    for start, end in (("[", "]"), ("{", "}")):
        first = text.find(start)
        last = text.rfind(end)
        if first >= 0 and last > first:
            candidate = text[first : last + 1]
            try:
                return json.loads(candidate)
            except Exception:
                continue
    return None


def _trim_text(text: str, limit: int = 220) -> str:
    compact = re.sub(r"\s+", " ", (text or "").strip())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)] + "..."


def _parse_notebook_evidence_text(text: str, *, query_label: str) -> list[dict[str, Any]]:
    parsed_payload = _extract_json_payload(text or "")
    if isinstance(parsed_payload, dict):
        value = parsed_payload.get("value")
        if isinstance(value, dict):
            answer = str(value.get("answer") or "").strip()
            if answer:
                text = answer
        else:
            answer = str(parsed_payload.get("answer") or "").strip()
            if answer:
                text = answer

    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    rows: list[dict[str, Any]] = []
    for raw in lines:
        cleaned = re.sub(r"^\s*(?:[-*]|[0-9]+[.)])\s*", "", raw).strip()
        if not cleaned:
            continue
        if cleaned.startswith("#"):
            continue
        if len(cleaned) < 10:
            continue
        title = cleaned
        if ":" in cleaned:
            title = cleaned.split(":", 1)[0].strip() or cleaned
        rows.append(
            {
                "title": _trim_text(title, 96),
                "url": "",
                "snippet": _trim_text(cleaned, 280),
                "source": "notebooklm_query",
                "query_label": query_label,
            }
        )
        if len(rows) >= 12:
            break
    return rows


def _dedupe_evidence(rows: list[dict[str, Any]], *, max_items: int = 12) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        title = str(row.get("title") or "").strip().lower()
        snippet = str(row.get("snippet") or "").strip().lower()
        key = f"{title}|{snippet[:140]}"
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(row)
        if len(deduped) >= max_items:
            break
    return deduped


def _extract_query_answer_text(raw: str) -> str:
    parsed = _extract_json_payload(raw or "")
    if isinstance(parsed, dict):
        value = parsed.get("value")
        if isinstance(value, dict):
            for key in ("answer", "text", "response"):
                candidate = str(value.get(key) or "").strip()
                if candidate:
                    return candidate
        for key in ("answer", "text", "response"):
            candidate = str(parsed.get(key) or "").strip()
            if candidate:
                return candidate
    return (raw or "").strip()


def _render_notebook_evidence_markdown(
    *,
    task: str,
    run_id: str,
    evidence: list[dict[str, Any]],
    query_outputs: list[dict[str, Any]],
) -> str:
    lines: list[str] = [
        "# NotebookLM Evidence",
        "",
        f"- Task: {task}",
        f"- Run ID: {run_id}",
        f"- Evidence Count: {len(evidence)}",
        "",
        "## Evidence Index",
    ]
    if evidence:
        for idx, row in enumerate(evidence, start=1):
            lines.append(f"- [S{idx}] {row.get('title') or '제목 없음'}")
            snippet = str(row.get("snippet") or "").strip()
            if snippet:
                lines.append(f"  - 요약: {snippet}")
            lines.append(f"  - source: {row.get('source') or 'notebooklm_query'}")
            label = str(row.get("query_label") or "").strip()
            if label:
                lines.append(f"  - query: {label}")
    else:
        lines.append("- (empty)")

    lines.extend(["", "## Query Outputs"])
    if query_outputs:
        for item in query_outputs:
            label = str(item.get("label") or "query")
            status = str(item.get("status") or "unknown")
            lines.append(f"- {label}: {status}")
            text = str(item.get("text") or "").strip()
            if text:
                lines.append(f"  - {_trim_text(text, 320)}")
            error = str(item.get("error") or "").strip()
            if error:
                lines.append(f"  - error: {_trim_text(error, 220)}")
    else:
        lines.append("- (none)")
    lines.append("")
    return "\n".join(lines)


async def _run_nlm_command(
    args: list[str],
    *,
    timeout_sec: int = 150,
) -> tuple[int, str, str]:
    command = [settings.notebooklm_cli_path, *args]

    def _run_blocking() -> tuple[int, str, str]:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
            return (
                int(completed.returncode or 0),
                completed.stdout or "",
                completed.stderr or "",
            )
        except subprocess.TimeoutExpired:
            return 124, "", f"timeout({timeout_sec}s)"
        except FileNotFoundError:
            return 127, "", f"cli_not_found:{settings.notebooklm_cli_path}"
        except Exception as exc:
            return 1, "", f"cli_exec_error:{str(exc)[:240]}"

    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (NotImplementedError, RuntimeError):
        # Windows event loop 정책에 따라 subprocess transport가 미지원일 수 있다.
        return await asyncio.to_thread(_run_blocking)
    except FileNotFoundError:
        return 127, "", f"cli_not_found:{settings.notebooklm_cli_path}"

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        return (
            int(proc.returncode or 0),
            stdout.decode("utf-8", errors="ignore"),
            stderr.decode("utf-8", errors="ignore"),
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return 124, "", f"timeout({timeout_sec}s)"


async def _run_nlm_json_command(
    args: list[str],
    *,
    timeout_sec: int = 120,
) -> tuple[int, Any | None, str, str]:
    rc, out, err = await _run_nlm_command(args, timeout_sec=timeout_sec)
    parsed = _extract_json_payload(out)
    return rc, parsed, out, err


def _extract_notebook_identifier(create_output: str, fallback_name: str) -> str:
    raw = (create_output or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for key in ("id", "notebook_id", "notebookId", "name"):
                    value = str(parsed.get(key) or "").strip()
                    if value:
                        return value
        except Exception:
            pass
        uuid_match = re.search(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
            raw,
        )
        if uuid_match:
            return uuid_match.group(0)
        token_candidates = re.findall(r"\b[a-zA-Z0-9_-]{16,}\b", raw)
        if token_candidates:
            return token_candidates[-1]
    return fallback_name


def _looks_like_not_ready_error(message: str) -> bool:
    lowered = (message or "").lower()
    signals = (
        "not ready",
        "try again",
        "in progress",
        "temporar",
        "timeout",
        "artifact",
        "pending",
    )
    return any(token in lowered for token in signals)


def _looks_like_unavailable_slides_error(message: str) -> bool:
    lowered = (message or "").lower()
    signals = (
        "could not create slide deck",
        "slide deck not available",
        "slides not available",
        "unknown command",
        "invalid choice",
    )
    return any(token in lowered for token in signals)


def _looks_like_slide_capability_error(message: str) -> bool:
    lowered = (message or "").lower()
    signals = (
        "could not create slide deck",
        "not_found",
        "permission",
        "forbidden",
        "unauthorized",
        "feature not available",
    )
    return any(token in lowered for token in signals)


def _parse_profile_names_from_list_output(raw: str) -> list[str]:
    lines = (raw or "").splitlines()
    profiles: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("available profiles"):
            continue
        matched = re.match(r"^\s*([^:]+?)\s*:\s*.+$", line)
        if not matched:
            continue
        profile = str(matched.group(1) or "").strip()
        if not profile or profile in seen:
            continue
        seen.add(profile)
        profiles.append(profile)
    return profiles


async def _download_slide_deck_with_polling(
    *,
    notebook_ref: str,
    output_path: Path,
    command_logs: list[dict[str, Any]],
    max_attempts: int = 18,
    interval_sec: int = 5,
) -> tuple[bool, str | None]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(max_attempts):
        if output_path.exists():
            try:
                output_path.unlink()
            except Exception:
                pass
        rc, out, err = await _run_nlm_command(
            [
                "download",
                "slide-deck",
                "--format",
                "pptx",
                notebook_ref,
                "-o",
                str(output_path),
            ],
            timeout_sec=180,
        )
        command_logs.append(
            _build_command_log(
                ["download", "slide-deck", "--format", "pptx", notebook_ref, "-o", str(output_path)],
                rc,
                out,
                err,
            )
        )
        if rc == 0 and output_path.exists() and output_path.stat().st_size > 0:
            return True, None
        failure = _shorten(err or out, 240)
        if _looks_like_unavailable_slides_error(failure):
            return False, failure
        if _looks_like_not_ready_error(failure):
            await asyncio.sleep(max(1, interval_sec))
            continue
        await asyncio.sleep(max(1, interval_sec))
    return False, "slide_deck_download_timeout"


async def _wait_for_slide_deck_ready(
    *,
    notebook_ref: str,
    command_logs: list[dict[str, Any]],
    max_wait_sec: int = 1200,
    poll_sec: int = 10,
) -> tuple[bool, str | None]:
    wait_forever = max_wait_sec <= 0
    loop = asyncio.get_running_loop()
    deadline = None if wait_forever else loop.time() + max_wait_sec
    while True:
        rc, parsed, out, err = await _run_nlm_json_command(
            ["status", "artifacts", notebook_ref, "--json"],
            timeout_sec=120,
        )
        command_logs.append(
            _build_command_log(["status", "artifacts", notebook_ref, "--json"], rc, out, err)
        )
        if rc == 0 and isinstance(parsed, list):
            slide_rows = [
                row for row in parsed
                if isinstance(row, dict) and str(row.get("type") or "").strip() == "slide_deck"
            ]
            if slide_rows:
                latest = slide_rows[-1]
                status = str(latest.get("status") or "").strip().lower()
                if status in {"completed", "complete", "done", "ready", "succeeded"}:
                    return True, None
                if status in {"failed", "error", "cancelled", "canceled"}:
                    return False, f"slide_status:{status}"
        if deadline is not None and loop.time() >= deadline:
            return False, "slide_ready_timeout"
        await asyncio.sleep(max(1, poll_sec))


async def _run_notebooklm_search_cli(
    *,
    notebook_ref: str,
    query: str,
    command_logs: list[dict[str, Any]],
) -> tuple[str, str | None]:
    research_commands: list[list[str]] = [
        ["research", "start", query, "--source", "web", "--mode", "fast", "--notebook-id", notebook_ref, "--force"],
        ["research", "status", notebook_ref, "--max-wait", "45", "--poll-interval", "5", "--full"],
        ["research", "import", notebook_ref, "--timeout", "180"],
    ]
    for args in research_commands:
        rc, out, err = await _run_nlm_command(args, timeout_sec=180)
        command_logs.append(_build_command_log(args, rc, out, err))
        if rc != 0:
            return "", _shorten(err or out, 240) or "research_command_failed"

    search_summary_question = (
        "지금 노트북에 포함된 최신 외부 검색 소스들을 기준으로, "
        "과제 해결에 직접 쓰일 사실 근거 8개를 번호 목록으로 요약해줘."
    )
    rc_q, out_q, err_q = await _run_nlm_command(
        ["notebook", "query", notebook_ref, search_summary_question],
        timeout_sec=180,
    )
    command_logs.append(
        _build_command_log(
            ["notebook", "query", notebook_ref, "<search-summary-question>"],
            rc_q,
            out_q,
            err_q,
        )
    )
    if rc_q == 0 and (out_q or "").strip():
        return out_q.strip(), None
    return "", _shorten(err_q or out_q, 240) or "research_summary_query_failed"


def _build_command_log(
    args: list[str],
    rc: int,
    stdout: str,
    stderr: str,
    *,
    redact_text: bool = False,
) -> dict[str, Any]:
    rendered = list(args)
    if redact_text and "--text" in rendered:
        text_index = rendered.index("--text") + 1
        if text_index < len(rendered):
            rendered[text_index] = "<omitted>"
    return {
        "cmd": rendered,
        "rc": rc,
        "stdout": _shorten(stdout, 380),
        "stderr": _shorten(stderr, 380),
    }


def _email_in_output(email: str | None, output: str) -> bool:
    expected = str(email or "").strip().lower()
    if not expected:
        return True
    return expected in (output or "").lower()


async def _ensure_notebooklm_login(
    *,
    preferred_google_account: str | None,
    command_logs: list[dict[str, Any]],
) -> dict[str, Any]:
    login_meta: dict[str, Any] = {
        "status": "unknown",
        "preferred_google_account": preferred_google_account,
        "active_account_match": None,
        "details": None,
    }

    rc, out, err = await _run_nlm_command(["login", "--check"], timeout_sec=60)
    command_logs.append(_build_command_log(["login", "--check"], rc, out, err))
    combined = f"{out}\n{err}"
    if rc == 0:
        login_meta["status"] = "ready"
        login_meta["active_account_match"] = _email_in_output(preferred_google_account, combined)
        if not login_meta["active_account_match"] and preferred_google_account:
            # OAuth 연결 계정 우선: 가능한 경우 프로필 전환을 시도한다.
            rc_sw, out_sw, err_sw = await _run_nlm_command(
                ["login", "switch", preferred_google_account],
                timeout_sec=60,
            )
            command_logs.append(
                _build_command_log(["login", "switch", preferred_google_account], rc_sw, out_sw, err_sw)
            )
            rc_re, out_re, err_re = await _run_nlm_command(["login", "--check"], timeout_sec=60)
            command_logs.append(_build_command_log(["login", "--check"], rc_re, out_re, err_re))
            login_meta["active_account_match"] = rc_re == 0 and _email_in_output(
                preferred_google_account, f"{out_re}\n{err_re}"
            )
            login_meta["details"] = (
                "profile_switched"
                if login_meta["active_account_match"]
                else "profile_switch_failed_or_unavailable"
            )
        return login_meta

    # 로그인 미완료 상태: 자동 로그인 시도 후 재확인
    rc_login, out_login, err_login = await _run_nlm_command(["login"], timeout_sec=180)
    command_logs.append(_build_command_log(["login"], rc_login, out_login, err_login))

    rc_recheck, out_recheck, err_recheck = await _run_nlm_command(["login", "--check"], timeout_sec=60)
    command_logs.append(_build_command_log(["login", "--check"], rc_recheck, out_recheck, err_recheck))
    if rc_recheck != 0:
        login_meta["status"] = "login_failed"
        login_meta["details"] = _shorten(err_recheck or out_recheck or err or out, 240)
        return login_meta

    login_meta["status"] = "ready_after_login"
    login_meta["active_account_match"] = _email_in_output(
        preferred_google_account, f"{out_recheck}\n{err_recheck}"
    )
    return login_meta


async def generate_notebooklm_assets(
    *,
    run_id: str,
    task: str,
    final_summary: str,
    output_dir: str | None = None,
    transcript_text: str | None = None,
    preferred_google_account: str | None = None,
) -> dict[str, Any]:
    """
    NotebookLM CLI pipeline:
    1) login --check (OAuth 연결 계정 우선 프로필 검증/전환)
    2) nlm notebook create <name>
    3) nlm source add <name> --text <discussion> --wait
    4) nlm notebook query ...
    5) nlm slides create + status wait + download slide-deck
    """
    out_root = Path(output_dir or settings.notebooklm_output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    notebook_name = f"agentgcs-{run_id[:8]}"
    notebook_ref = notebook_name
    summary_path = out_root / f"{run_id}-notebooklm-summary.md"
    transcript_path = out_root / f"{run_id}-discussion.txt"
    slide_pptx_path = out_root / f"{run_id}-notebooklm.pptx"
    transcript = (transcript_text or final_summary or "").strip()
    if len(transcript) > 60000:
        transcript = transcript[:60000]
    transcript_path.write_text(transcript, encoding="utf-8")

    command_logs: list[dict[str, Any]] = []
    login_info: dict[str, Any] = {"status": "unknown"}

    try:
        login_info = await _ensure_notebooklm_login(
            preferred_google_account=preferred_google_account,
            command_logs=command_logs,
        )
        if str(login_info.get("status") or "").startswith("login_failed"):
            raise RuntimeError(f"NotebookLM login unavailable: {login_info.get('details')}")
        if preferred_google_account and login_info.get("active_account_match") is False:
            raise RuntimeError(
                "NotebookLM account mismatch. "
                f"expected={preferred_google_account} details={login_info.get('details') or '-'}"
            )

        rc, out, err = await _run_nlm_command(["notebook", "create", notebook_name], timeout_sec=90)
        command_logs.append(_build_command_log(["notebook", "create", notebook_name], rc, out, err))
        if rc != 0:
            raise RuntimeError(f"notebook create failed: {_shorten(err or out, 240)}")
        notebook_ref = _extract_notebook_identifier(out, notebook_name)

        rc, out, err = await _run_nlm_command(
            ["source", "add", notebook_ref, "--file", str(transcript_path), "--wait"],
            timeout_sec=300,
        )
        command_logs.append(
            _build_command_log(
                ["source", "add", notebook_ref, "--file", str(transcript_path), "--wait"],
                rc,
                out,
                err,
            )
        )
        if rc != 0:
            raise RuntimeError(f"source add failed: {_shorten(err or out, 240)}")

        rc, out, err = await _run_nlm_command(
            [
                "notebook",
                "query",
                notebook_ref,
                "위 토론 결과를 발표용 요약(핵심 메시지, 슬라이드 아웃라인, 발표 대본)으로 정리해줘.",
            ],
            timeout_sec=180,
        )
        command_logs.append(
            _build_command_log(["notebook", "query", notebook_ref, "<presentation-summary-question>"], rc, out, err)
        )

        extracted_text = _extract_query_answer_text(out)
        if rc != 0 or not extracted_text:
            raise RuntimeError(f"notebook query failed: {_shorten(err or out, 320)}")

        summary_path.write_text(extracted_text, encoding="utf-8")
        slides_status = "mocked"
        slides_reason = None
        rc_s, out_s, err_s = await _run_nlm_command(
            [
                "slides",
                "create",
                notebook_ref,
                "--format",
                "presenter_slides",
                "--length",
                "short",
                "--language",
                "ko",
                "--confirm",
            ],
            timeout_sec=300,
        )
        command_logs.append(
            _build_command_log(
                [
                    "slides",
                    "create",
                    notebook_ref,
                    "--format",
                    "presenter_slides",
                    "--length",
                    "short",
                    "--language",
                    "ko",
                    "--confirm",
                ],
                rc_s,
                out_s,
                err_s,
            )
        )
        if rc_s == 0:
            ready, ready_reason = await _wait_for_slide_deck_ready(
                notebook_ref=notebook_ref,
                command_logs=command_logs,
                max_wait_sec=1200,
                poll_sec=10,
            )
            if ready:
                downloaded, download_reason = await _download_slide_deck_with_polling(
                    notebook_ref=notebook_ref,
                    output_path=slide_pptx_path,
                    command_logs=command_logs,
                    max_attempts=120,
                    interval_sec=10,
                )
                if downloaded:
                    slides_status = "generated"
                    slides_reason = None
                else:
                    slides_status = "mocked"
                    slides_reason = download_reason or "slide_download_failed"
            else:
                slides_status = "mocked"
                slides_reason = ready_reason or "slide_not_ready"
        else:
            slides_status = "mocked"
            slides_reason = _shorten(err_s or out_s, 240) or "slides_create_failed"

        return {
            "status": "generated" if slides_status == "generated" else "partial",
            "notebook_name": notebook_name,
            "notebook_ref": notebook_ref,
            "summary_path": str(summary_path),
            "script_path": str(summary_path),
            "transcript_path": str(transcript_path),
            "slides_status": slides_status,
            "slides_reason": slides_reason,
            "ppt_path": str(slide_pptx_path) if slide_pptx_path.exists() else None,
            "slide_path": str(slide_pptx_path) if slide_pptx_path.exists() else None,
            "login": login_info,
            "commands": command_logs,
        }
    except Exception as exc:
        fallback_text = (
            "# NotebookLM Fallback Summary\n\n"
            f"- Task: {task}\n"
            f"- Run ID: {run_id}\n\n"
            "## Final Summary\n\n"
            f"{final_summary}\n"
        )
        summary_path.write_text(fallback_text, encoding="utf-8")
        return {
            "status": "mocked",
            "notebook_name": notebook_name,
            "notebook_ref": notebook_ref,
            "summary_path": str(summary_path),
            "script_path": str(summary_path),
            "transcript_path": str(transcript_path),
            "slides_status": "mocked",
            "slides_reason": str(exc),
            "ppt_path": None,
            "slide_path": None,
            "reason": str(exc),
            "login": login_info,
            "commands": command_logs,
        }


async def collect_notebooklm_evidence(
    *,
    run_id: str,
    task: str,
    source_text: str,
    output_dir: str | None = None,
    preferred_google_account: str | None = None,
    search_query: str | None = None,
) -> dict[str, Any]:
    """
    Build NotebookLM-based evidence before debate.
    Pipeline:
    1) login --check
    2) notebook create
    3) source add --text <source pack>
    4) notebook query (multi prompts) -> parse/compress evidence
    """
    out_root = Path(output_dir or settings.notebooklm_output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    notebook_name = f"agentgcs-evidence-{run_id[:8]}"
    source_pack_path = out_root / "notebook_source_pack.md"
    evidence_json_path = out_root / "notebook_evidence.json"
    evidence_md_path = out_root / "notebook_evidence.md"

    normalized_source = (source_text or "").strip()
    if len(normalized_source) > 60000:
        normalized_source = normalized_source[:60000]
    if not normalized_source:
        normalized_source = f"# Task\n\n{task}\n\n(입력 요약이 비어 있어 task 텍스트를 사용합니다.)"
    source_pack_path.write_text(normalized_source, encoding="utf-8")

    command_logs: list[dict[str, Any]] = []
    login_info: dict[str, Any] = {"status": "unknown"}

    prompts: list[tuple[str, str]] = [
        (
            "핵심 사실",
            "위 자료에서 과제 해결에 직접 필요한 핵심 사실 8개를 짧은 문장 목록으로 정리해줘. "
            "중복 없이 작성해줘.",
        ),
        (
            "근거 인덱스",
            "위 자료 기준으로 실무 의사결정에 쓸 수 있는 근거 포인트 8개를 목록으로 정리해줘. "
            "각 항목은 근거 요약 중심으로 작성해줘.",
        ),
        (
            "리스크/대응",
            "위 자료 기반으로 예상 리스크와 대응안을 짝으로 6개 목록으로 작성해줘.",
        ),
    ]

    query_outputs: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []

    try:
        login_info = await _ensure_notebooklm_login(
            preferred_google_account=preferred_google_account,
            command_logs=command_logs,
        )
        if str(login_info.get("status") or "").startswith("login_failed"):
            raise RuntimeError(f"NotebookLM login unavailable: {login_info.get('details')}")
        if preferred_google_account and login_info.get("active_account_match") is False:
            raise RuntimeError(
                "NotebookLM account mismatch. "
                f"expected={preferred_google_account} details={login_info.get('details') or '-'}"
            )

        rc, out, err = await _run_nlm_command(["notebook", "create", notebook_name], timeout_sec=90)
        command_logs.append(_build_command_log(["notebook", "create", notebook_name], rc, out, err))
        if rc != 0:
            raise RuntimeError(f"notebook create failed: {_shorten(err or out, 240)}")
        notebook_ref = _extract_notebook_identifier(out, notebook_name)

        rc, out, err = await _run_nlm_command(
            ["source", "add", notebook_ref, "--file", str(source_pack_path), "--wait"],
            timeout_sec=300,
        )
        command_logs.append(
            _build_command_log(
                ["source", "add", notebook_ref, "--file", str(source_pack_path), "--wait"],
                rc,
                out,
                err,
            )
        )
        if rc != 0:
            raise RuntimeError(f"source add failed: {_shorten(err or out, 240)}")

        search_text, search_error = await _run_notebooklm_search_cli(
            notebook_ref=notebook_ref,
            query=(search_query or task).strip()[:500],
            command_logs=command_logs,
        )
        if search_text:
            parsed_search = _parse_notebook_evidence_text(search_text, query_label="NotebookLM Search")
            evidence_rows.extend(parsed_search)
            query_outputs.append(
                {
                    "label": "NotebookLM Search",
                    "status": "ok",
                    "text": _shorten(search_text, 1200),
                }
            )
        else:
            query_outputs.append(
                {
                    "label": "NotebookLM Search",
                    "status": "error",
                    "text": "",
                    "error": _shorten(search_error or "search_failed", 220),
                }
            )

        for label, prompt in prompts:
            rc, out, err = await _run_nlm_command(
                ["notebook", "query", notebook_ref, prompt],
                timeout_sec=180,
            )
            command_logs.append(
                _build_command_log(["notebook", "query", notebook_ref, "<question>"], rc, out, err)
            )
            text = _extract_query_answer_text(out)
            if rc == 0 and text:
                parsed = _parse_notebook_evidence_text(text, query_label=label)
                evidence_rows.extend(parsed)
                query_outputs.append({"label": label, "status": "ok", "text": _shorten(text, 1200)})
            else:
                query_outputs.append(
                    {
                        "label": label,
                        "status": "error",
                        "text": _shorten(text, 240),
                        "error": _shorten(err or out, 220),
                    }
                )

        evidence = _dedupe_evidence(evidence_rows, max_items=12)
        if not evidence:
            raise RuntimeError("NotebookLM evidence query returned empty evidence set.")

        evidence_json_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "task": task,
                    "notebook_name": notebook_name,
                    "evidence": evidence,
                    "query_outputs": query_outputs,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        evidence_md_path.write_text(
            _render_notebook_evidence_markdown(
                task=task,
                run_id=run_id,
                evidence=evidence,
                query_outputs=query_outputs,
            ),
            encoding="utf-8",
        )

        return {
            "status": "generated",
            "run_id": run_id,
            "notebook_name": notebook_name,
            "source_pack_path": str(source_pack_path),
            "evidence_json_path": str(evidence_json_path),
            "evidence_md_path": str(evidence_md_path),
            "evidence": evidence,
            "query_outputs": query_outputs,
            "login": login_info,
            "commands": command_logs,
        }
    except Exception as exc:
        fallback_evidence = [
            {
                "title": _trim_text(task, 96) or "Task Summary",
                "url": "",
                "snippet": _trim_text(normalized_source, 280),
                "source": "notebooklm_fallback",
                "query_label": "fallback",
            }
        ]
        evidence_json_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "task": task,
                    "notebook_name": notebook_name,
                    "status": "mocked",
                    "reason": str(exc),
                    "evidence": fallback_evidence,
                    "query_outputs": query_outputs,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        evidence_md_path.write_text(
            _render_notebook_evidence_markdown(
                task=task,
                run_id=run_id,
                evidence=fallback_evidence,
                query_outputs=query_outputs,
            ),
            encoding="utf-8",
        )
        return {
            "status": "mocked",
            "run_id": run_id,
            "notebook_name": notebook_name,
            "source_pack_path": str(source_pack_path),
            "evidence_json_path": str(evidence_json_path),
            "evidence_md_path": str(evidence_md_path),
            "evidence": fallback_evidence,
            "query_outputs": query_outputs,
            "reason": str(exc),
            "login": login_info,
            "commands": command_logs,
        }


async def ensure_notebooklm_login_ready(
    *,
    preferred_google_account: str | None = None,
    force_oauth_match: bool = True,
) -> dict[str, Any]:
    """
    Public preflight helper.
    - Executes `login --check`
    - If needed, executes interactive `login` (browser window)
    - Rechecks login status
    """
    command_logs: list[dict[str, Any]] = []
    login_info = await _ensure_notebooklm_login(
        preferred_google_account=preferred_google_account,
        command_logs=command_logs,
    )
    status = str(login_info.get("status") or "").strip()
    account_mismatch = bool(
        preferred_google_account
        and force_oauth_match
        and login_info.get("active_account_match") is False
    )
    if account_mismatch:
        status = "account_mismatch"
    return {
        "status": status,
        "ready": status in {"ready", "ready_after_login"} and not account_mismatch,
        "preferred_google_account": preferred_google_account,
        "login": login_info,
        "commands": command_logs,
        "cli_path": settings.notebooklm_cli_path,
        "account_mismatch": account_mismatch,
    }


async def ensure_notebooklm_slides_capability_ready(
    *,
    preferred_google_account: str | None = None,
) -> dict[str, Any]:
    """
    Preflight check to verify whether current NotebookLM account can create slide decks.
    """
    command_logs: list[dict[str, Any]] = []
    login_info = await _ensure_notebooklm_login(
        preferred_google_account=preferred_google_account,
        command_logs=command_logs,
    )
    status = str(login_info.get("status") or "").strip()
    if status not in {"ready", "ready_after_login"}:
        return {
            "ready": False,
            "status": "login_unavailable",
            "reason": f"login_status:{status}",
            "preferred_google_account": preferred_google_account,
            "login": login_info,
            "commands": command_logs,
        }

    probe_name = f"agentgcs-slides-probe-{int(asyncio.get_running_loop().time())}"
    notebook_ref = probe_name
    cleanup_args: list[str] | None = None

    try:
        rc, out, err = await _run_nlm_command(["notebook", "create", probe_name], timeout_sec=60)
        command_logs.append(_build_command_log(["notebook", "create", probe_name], rc, out, err))
        if rc != 0:
            return {
                "ready": False,
                "status": "probe_notebook_create_failed",
                "reason": _shorten(err or out, 280),
                "preferred_google_account": preferred_google_account,
                "login": login_info,
                "commands": command_logs,
            }
        notebook_ref = _extract_notebook_identifier(out, probe_name)
        cleanup_args = ["notebook", "delete", notebook_ref, "--confirm"]

        rc, out, err = await _run_nlm_command(
            ["source", "add", notebook_ref, "--text", "슬라이드 생성 가능 여부 점검용 테스트 소스입니다.", "--wait"],
            timeout_sec=120,
        )
        command_logs.append(
            _build_command_log(
                ["source", "add", notebook_ref, "--text", "<probe-text>", "--wait"],
                rc,
                out,
                err,
            )
        )
        if rc != 0:
            return {
                "ready": False,
                "status": "probe_source_add_failed",
                "reason": _shorten(err or out, 280),
                "preferred_google_account": preferred_google_account,
                "login": login_info,
                "commands": command_logs,
            }

        rc, out, err = await _run_nlm_command(
            [
                "slides",
                "create",
                notebook_ref,
                "--format",
                "presenter_slides",
                "--length",
                "short",
                "--language",
                "ko",
                "--confirm",
            ],
            timeout_sec=120,
        )
        command_logs.append(
            _build_command_log(
                [
                    "slides",
                    "create",
                    notebook_ref,
                    "--format",
                    "presenter_slides",
                    "--length",
                    "short",
                    "--language",
                    "ko",
                    "--confirm",
                ],
                rc,
                out,
                err,
            )
        )
        if rc != 0:
            reason = _shorten(err or out, 320)
            return {
                "ready": False,
                "status": "slides_unavailable",
                "reason": reason,
                "is_capability_error": _looks_like_slide_capability_error(reason),
                "preferred_google_account": preferred_google_account,
                "login": login_info,
                "commands": command_logs,
            }

        return {
            "ready": True,
            "status": "slides_available",
            "reason": None,
            "preferred_google_account": preferred_google_account,
            "login": login_info,
            "commands": command_logs,
        }
    finally:
        if cleanup_args:
            try:
                rc_del, out_del, err_del = await _run_nlm_command(cleanup_args, timeout_sec=60)
                command_logs.append(_build_command_log(cleanup_args, rc_del, out_del, err_del))
            except Exception:
                pass


async def list_notebooklm_profiles() -> dict[str, Any]:
    command_logs: list[dict[str, Any]] = []
    rc, out, err = await _run_nlm_command(["login", "profile", "list"], timeout_sec=60)
    command_logs.append(_build_command_log(["login", "profile", "list"], rc, out, err))
    if rc != 0:
        return {
            "status": "error",
            "profiles": [],
            "reason": _shorten(err or out, 280) or "profile_list_failed",
            "commands": command_logs,
        }
    profiles = _parse_profile_names_from_list_output(out)
    return {
        "status": "ok",
        "profiles": profiles,
        "reason": None,
        "commands": command_logs,
    }


async def find_slides_capable_notebooklm_profile(
    *,
    preferred_profile: str | None = None,
    max_candidates: int = 6,
) -> dict[str, Any]:
    """
    Find a NotebookLM profile that can create slide decks.
    - Tries preferred_profile first (if provided)
    - Then tries the remaining configured profiles
    """
    listing = await list_notebooklm_profiles()
    profile_candidates: list[str] = []
    seen: set[str] = set()

    preferred = str(preferred_profile or "").strip()
    if preferred:
        profile_candidates.append(preferred)
        seen.add(preferred)

    for profile in listing.get("profiles") or []:
        name = str(profile or "").strip()
        if not name or name in seen:
            continue
        profile_candidates.append(name)
        seen.add(name)
        if len(profile_candidates) >= max_candidates:
            break

    if not profile_candidates:
        # Fallback: test current active profile context.
        probe = await ensure_notebooklm_slides_capability_ready(preferred_google_account=None)
        return {
            "ready": bool(probe.get("ready")),
            "selected_profile": None,
            "attempts": [
                {
                    "profile": None,
                    "ready": bool(probe.get("ready")),
                    "status": probe.get("status"),
                    "reason": probe.get("reason"),
                }
            ],
            "listing": listing,
            "probe": probe,
        }

    attempts: list[dict[str, Any]] = []
    for profile in profile_candidates:
        probe = await ensure_notebooklm_slides_capability_ready(
            preferred_google_account=profile,
        )
        ready = bool(probe.get("ready"))
        attempts.append(
            {
                "profile": profile,
                "ready": ready,
                "status": probe.get("status"),
                "reason": probe.get("reason"),
            }
        )
        if ready:
            return {
                "ready": True,
                "selected_profile": profile,
                "attempts": attempts,
                "listing": listing,
                "probe": probe,
            }

    return {
        "ready": False,
        "selected_profile": None,
        "attempts": attempts,
        "listing": listing,
        "probe": None,
    }
