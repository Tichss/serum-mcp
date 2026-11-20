"""Drive the real serum-mcp server over stdio, exactly like Claude Desktop does,
without restarting Claude Desktop.

    uv run python scripts/mcp_smoke.py                 # full smoke run, temp presets dir
    uv run python scripts/mcp_smoke.py --keep          # keep the generated files
    uv run python scripts/mcp_smoke.py --presets-dir "C:/.../Serum 2 Presets/User"
    uv run python scripts/mcp_smoke.py --call describe_preset '{"preset_path": "X.SerumPreset"}'

Each run spawns a fresh server process, so code changes are picked up immediately.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[1]


def _sub_spec(name: str, **sub_fields) -> dict:
    return {
        "name": name,
        "description": "smoke test: sub layer",
        "oscillators": [
            {"enabled": True, "octave": -1, "volume": 0.8},
            {},
            {},
            {},
            {"enabled": True, **sub_fields},
        ],
    }


async def _call(session: ClientSession, tool: str, args: dict) -> str:
    result = await session.call_tool(tool, args)
    text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
    if result.isError:
        raise RuntimeError(f"{tool} failed:\n{text}")
    return text


def _check(cond: bool, msg: str, failures: list[str]) -> None:
    print(f"  [{'OK' if cond else 'FAIL'}] {msg}")
    if not cond:
        failures.append(msg)


async def _smoke(session: ClientSession) -> list[str]:
    failures: list[str] = []

    tools = {t.name for t in (await session.list_tools()).tools}
    print(f"tools: {sorted(tools)}")
    _check({"generate_preset", "edit_preset", "describe_preset"} <= tools, "core tools", failures)

    params = json.loads(await _call(session, "list_parameters", {}))
    _check("sine" in params["simple_sub_shapes"], "list_parameters offers sub_shape 'sine'", failures)

    # 1) default sub -> sine
    path = (await _call(session, "generate_preset", {"spec": _sub_spec("SMOKE Sub Default")})).strip()
    print(f"generated: {path}")
    desc = await _call(session, "describe_preset", {"preset_path": path})
    _check("sub_shape=sine" in desc, "default sub reads back as sine", failures)

    # 2) explicit saw + phase + contiguous
    path2 = (
        await _call(
            session,
            "generate_preset",
            {
                "spec": _sub_spec(
                    "SMOKE Sub Saw Phase",
                    sub_shape="saw",
                    sub_initial_phase=180.0,
                    sub_contiguous_phase=True,
                )
            },
        )
    ).strip()
    desc2 = await _call(session, "describe_preset", {"preset_path": path2})
    print(desc2)
    _check("sub_shape=saw" in desc2, "explicit saw survives round-trip", failures)
    _check("phase=180" in desc2, "sub_initial_phase=180 shown", failures)
    _check("contiguous_phase" in desc2, "sub_contiguous_phase shown", failures)

    # 3) edit back to defaults -> phase/contiguous gone, sine again
    await _call(session, "edit_preset", {"preset_path": path2, "spec": _sub_spec("SMOKE Sub Saw Phase")})
    desc3 = await _call(session, "describe_preset", {"preset_path": path2})
    _check("sub_shape=sine" in desc3, "edit back to default -> sine", failures)
    _check("phase=" not in desc3 and "contiguous_phase" not in desc3, "phase fields reset", failures)

    return failures


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--presets-dir", help="default: a fresh temp dir")
    ap.add_argument("--keep", action="store_true", help="don't delete the temp dir")
    ap.add_argument("--call", nargs=2, metavar=("TOOL", "JSON_ARGS"), help="call one tool and print")
    args = ap.parse_args()

    tmp = None
    presets_dir = args.presets_dir
    if not presets_dir:
        tmp = tempfile.mkdtemp(prefix="serum-mcp-smoke-")
        presets_dir = tmp
    print(f"presets dir: {presets_dir}")

    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "serum_mcp.server"],
        cwd=str(REPO),
        env={**os.environ, "SERUM_PRESETS_PATH": presets_dir},
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if args.call:
                print(await _call(session, args.call[0], json.loads(args.call[1])))
                return 0
            failures = await _smoke(session)

    if tmp and not args.keep:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    print("\nALL OK" if not failures else f"\n{len(failures)} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
