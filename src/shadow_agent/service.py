"""Application orchestration, evidence output and a separate user confirmation service."""

import hashlib
import sqlite3
import subprocess
from dataclasses import asdict
from pathlib import Path
from time import monotonic

from .agent import run_item
from .common import BusinessError, dumps, new_id, now
from .config import Budget, Limits, ResourceLimits
from .context import ItemContext
from .contracts import Request, RequestResult
from .models.prompts import PROMPT_VERSION, SYSTEM
from .result import aggregate, assemble_result, exit_code, render_summary
from .tools.registry import schemas
from .trace import NullTrace, TraceSink, atomic_write


def source_metadata() -> dict:
    root = Path(__file__).resolve().parent
    sha = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.suffix in (".py", ".sql", ".json"):
            sha.update(path.relative_to(root).as_posix().encode())
            sha.update(path.read_bytes())
    commit = None
    try:
        process = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        commit = process.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return {"base_commit": commit, "source_sha256": sha.hexdigest()}


async def run_request(
    request: Request, repo, model, *, mode: str, output: Path, limits: Limits | None = None
) -> RequestResult:
    limits = limits or Limits()
    run_id = new_id("RUN")
    trace = TraceSink(output, run_id, mode)
    started = now()
    deadline = monotonic() + limits.request_seconds
    metadata = {
        "schema_version": "1.0",
        "run_id": run_id,
        "mode": mode,
        "started_at": started,
        **source_metadata(),
        "limits": asdict(limits),
        "fault_injection": repo.fault,
        "model": getattr(getattr(model, "settings", None), "model", "MockModel"),
        "command": "run",
        "request_id": request.request_id,
        "clock_mode": "replay" if repo.replay_at else repo.clock_mode,
        "reference_time": repo.replay_at,
        "resources": asdict(ResourceLimits()),
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
        "schema_sha256": hashlib.sha256(dumps(schemas()).encode()).hexdigest(),
        "state": "in_progress",
    }
    atomic_write(output / "input.json", request.model_dump_json(indent=2))
    atomic_write(output / "run.json", dumps(metadata))
    results = []
    contexts = []
    evidence_failed = False
    registration_error = None
    try:
        request = repo.register_request(request)
    except (BusinessError, sqlite3.Error) as error:
        registration_error = (
            repo.database_error(error) if isinstance(error, sqlite3.Error) else error
        )
    for item in sorted(request.items, key=lambda x: x.material_id):
        ctx = ItemContext(request, item, run_id, repo, Budget(limits, deadline), trace)
        contexts.append(ctx)
        try:
            if evidence_failed:
                raise BusinessError(
                    "EVIDENCE_ERROR", "证据写入失败，停止后续业务写入；请 inspect-draft 核查"
                )
            if registration_error:
                raise registration_error
            ctx.budget.ensure()
            repo.busy_ms = min(2000, int(ctx.budget.remaining() * 1000))
            repo.revalidate_current_draft(request.request_id, item.material_id, run_id=run_id)
            await run_item(ctx, model)
        except (BusinessError, sqlite3.Error) as error:
            error = repo.database_error(error) if isinstance(error, sqlite3.Error) else error
            ctx.forced_status = "incomplete" if error.code == "BUDGET_EXHAUSTED" else "failed"
            ctx.technical_error = error.as_dict()
        except OSError:
            evidence_failed = True
            ctx.forced_status = "failed"
            ctx.technical_error = {
                "code": "EVIDENCE_ERROR",
                "message": "证据写入失败，保留已提交草稿，请 inspect-draft 核查",
            }
            ctx.trace = NullTrace()
        except Exception as error:
            ctx.forced_status = "failed"
            ctx.technical_error = {
                "code": "INTERNAL_ERROR",
                "message": "内部异常，已停止当前项",
                "error_type": type(error).__name__,
            }
        result = assemble_result(ctx)
        try:
            if not evidence_failed:
                trace.emit(
                    "final",
                    material_id=item.material_id,
                    budget=ctx.budget,
                    result=result.model_dump(mode="json"),
                )
        except OSError:
            evidence_failed = True
            ctx.forced_status = "failed"
            ctx.technical_error = {
                "code": "EVIDENCE_ERROR",
                "message": "结果事件写入失败，已提交状态保留",
            }
            result = assemble_result(ctx)
        results.append(result)
    # Later items may import data or revise the plan of an earlier item.
    # Reassemble from current local state without replaying any business tools.
    for index, ctx in enumerate(contexts):
        refreshed = assemble_result(ctx)
        if refreshed != results[index] and not evidence_failed:
            try:
                trace.emit(
                    "final",
                    material_id=ctx.material_id,
                    budget=ctx.budget,
                    reason="batch_final_recheck",
                    result=refreshed.model_dump(mode="json"),
                )
            except OSError:
                evidence_failed = True
                refreshed.task_status = "failed"
                refreshed.error = {"code": "EVIDENCE_ERROR", "message": "最终复核事件写入失败"}
        results[index] = refreshed
    result = RequestResult(
        run_id=run_id,
        request_id=request.request_id,
        mode=mode,
        task_status=aggregate(results),
        items=results,
    )
    try:
        repo.record_task_result(request, result, output)
    except (BusinessError, sqlite3.Error) as error:
        public = repo.database_error(error) if isinstance(error, sqlite3.Error) else error
        result.items[-1].task_status = "failed"
        result.items[-1].error = public.as_dict()
        result.task_status = aggregate(result.items)
        try:
            trace.emit(
                "final",
                material_id=result.items[-1].material_id,
                budget=contexts[-1].budget,
                reason="task_record_failed",
                result=result.items[-1].model_dump(mode="json"),
            )
        except OSError:
            evidence_failed = True
    metadata.update(
        {
            "finished_at": now(),
            "exit_code": exit_code(result),
            "files": ["input.json", "output.json", "summary.md", "trace.jsonl", "run.json"],
            "state": "finished",
            "model_attempts": sum(x.budget.models for x in contexts),
            "tool_attempts": sum(x.budget.tools for x in contexts),
            "model_attempts_without_valid_response": sum(
                x.budget.models - len(x.provider_records) for x in contexts
            ),
            "provider_responses": [r for x in contexts for r in x.provider_records if r],
            "evidence_error": evidence_failed,
        }
    )

    def mark_output_error():
        result.items[-1].task_status = "failed"
        result.items[-1].error = {
            "code": "EVIDENCE_ERROR",
            "message": "结果文件写入失败，已提交草稿保留；请核查数据库",
        }
        result.task_status = aggregate(result.items)
        metadata["exit_code"] = exit_code(result)
        metadata["evidence_error"] = True

    for _ in range(2):
        failed = False
        for name, content in (
            ("output.json", result.model_dump_json(indent=2)),
            ("summary.md", render_summary(result)),
        ):
            try:
                atomic_write(output / name, content)
            except OSError:
                failed = True
                mark_output_error()
        if not failed:
            break
    try:
        atomic_write(output / "run.json", dumps(metadata))
    except OSError:
        mark_output_error()
        try:
            atomic_write(output / "output.json", result.model_dump_json(indent=2))
        except OSError:
            pass  # stdout still returns the truthful failed result and committed draft identity.
    return result
