import asyncio
import os
import signal
from pathlib import Path

from pa.common import PAError

ENV_ALLOW = {
    "HOME",
    "USER",
    "LOGNAME",
    "PATH",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "TERM",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "all_proxy",
    "no_proxy",
}


async def run_process(
    command,
    *,
    cwd: Path,
    stdin: str = "",
    timeout_s=180,
    max_output_bytes=2_097_152,
    extra_env=None,
):
    env = {key: value for key, value in os.environ.items() if key in ENV_ALLOW}
    env.update(extra_env or {})
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env=env,
        start_new_session=True,
    )
    total = 0

    async def read(stream):
        nonlocal total
        chunks = []
        while chunk := await stream.read(16384):
            total += len(chunk)
            if total > max_output_bytes:
                raise PAError("engine output exceeded the configured byte limit")
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")

    async def write():
        try:
            proc.stdin.write(stdin.encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.stdin.close()

    tasks = [
        asyncio.create_task(fn)
        for fn in (
            read(proc.stdout),
            read(proc.stderr),
            write(),
            proc.wait(),
        )
    ]
    try:
        async with asyncio.timeout(timeout_s):
            stdout, stderr, _, code = await asyncio.gather(*tasks)
        return code, stdout, stderr
    except BaseException:
        if proc.returncode is None:
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
