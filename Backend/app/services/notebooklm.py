from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.core.config import settings


async def _run_nlm_command(args: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        settings.notebooklm_cli_path,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="ignore"),
        stderr.decode("utf-8", errors="ignore"),
    )


async def generate_notebooklm_assets(
    *,
    run_id: str,
    task: str,
    final_summary: str,
) -> dict[str, Any]:
    """
    NotebookLM CLI pipeline:
    1) nlm notebook create <name>
    2) nlm source add <name> --text <discussion> --wait
    3) nlm notebook query <name> ... OR nlm studio create <name> ...
    """
    output_dir = Path(settings.notebooklm_output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    notebook_name = f"agentgcs-{run_id[:8]}"
    summary_path = output_dir / f"{run_id}-notebooklm-summary.md"
    transcript_path = output_dir / f"{run_id}-discussion.txt"
    transcript_path.write_text(final_summary, encoding="utf-8")

    command_logs: list[dict[str, Any]] = []

    try:
        rc, out, err = await _run_nlm_command(["notebook", "create", notebook_name])
        command_logs.append({"cmd": ["notebook", "create", notebook_name], "rc": rc, "stderr": err[:500]})
        if rc != 0:
            raise RuntimeError(f"notebook create failed: {err[:300]}")

        rc, out, err = await _run_nlm_command(
            ["source", "add", notebook_name, "--text", final_summary, "--wait"]
        )
        command_logs.append(
            {
                "cmd": ["source", "add", notebook_name, "--text", "<omitted>", "--wait"],
                "rc": rc,
                "stderr": err[:500],
            }
        )
        if rc != 0:
            raise RuntimeError(f"source add failed: {err[:300]}")

        # Prefer notebook query first.
        rc, out, err = await _run_nlm_command(
            [
                "notebook",
                "query",
                notebook_name,
                "--prompt",
                "위 토론 결과를 발표용 요약(핵심 메시지, 슬라이드 아웃라인, 발표 대본)으로 정리해줘.",
            ]
        )
        command_logs.append({"cmd": ["notebook", "query", notebook_name, "--prompt", "..."], "rc": rc, "stderr": err[:500]})

        extracted_text = out.strip()
        if rc != 0 or not extracted_text:
            # Fallback to studio create.
            rc2, out2, err2 = await _run_nlm_command(
                ["studio", "create", notebook_name, "--prompt", "토론 결과 기반 발표 문서 생성"]
            )
            command_logs.append({"cmd": ["studio", "create", notebook_name, "--prompt", "..."], "rc": rc2, "stderr": err2[:500]})
            if rc2 == 0 and out2.strip():
                extracted_text = out2.strip()
            else:
                raise RuntimeError(f"notebook query/studio create failed: {(err2 or err)[:320]}")

        summary_path.write_text(extracted_text, encoding="utf-8")
        return {
            "status": "generated",
            "notebook_name": notebook_name,
            "summary_path": str(summary_path),
            "transcript_path": str(transcript_path),
            "commands": command_logs,
        }
    except Exception as exc:
        # deterministic fallback for local/dev
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
            "summary_path": str(summary_path),
            "transcript_path": str(transcript_path),
            "reason": str(exc),
            "commands": command_logs,
        }

