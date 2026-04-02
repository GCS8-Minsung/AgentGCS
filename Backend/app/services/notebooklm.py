from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import settings


async def generate_notebooklm_assets(
    *, run_id: str, task: str, final_summary: str
) -> dict[str, str]:
    """
    Runs notebooklm-mcp-cli if available.
    Falls back to a deterministic mock payload to keep local dev unblocked.
    """
    output_dir = Path(settings.notebooklm_output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    ppt_path = output_dir / f"{run_id}.pptx"
    script_path = output_dir / f"{run_id}.md"

    cmd = [
        settings.notebooklm_cli_path,
        "generate",
        "--title",
        task[:80],
        "--summary",
        final_summary[:5000],
        "--pptx-out",
        str(ppt_path),
        "--script-out",
        str(script_path),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="ignore"))
        return {
            "status": "generated",
            "ppt_path": str(ppt_path),
            "script_path": str(script_path),
            "stdout": stdout.decode("utf-8", errors="ignore")[:1000],
        }
    except Exception as exc:
        script_path.write_text(
            "# NotebookLM Mock Script\n\n"
            f"Task: {task}\n\n"
            "CLI could not run in this environment, so this mock script was generated.\n\n"
            f"Summary:\n{final_summary}\n",
            encoding="utf-8",
        )
        return {
            "status": "mocked",
            "ppt_path": str(ppt_path),
            "script_path": str(script_path),
            "reason": str(exc),
        }

