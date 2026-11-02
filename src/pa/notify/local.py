"""macOS local notification fallback via ``osascript`` (PRD §12.3)."""

import asyncio


def apple_string(value) -> str:
    """Quote a value as an AppleScript string literal."""
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


class LocalNotifier:
    async def display(self, title: str, message: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            f"display notification {apple_string(message)} with title {apple_string(title)}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()


def default_notifier() -> LocalNotifier:
    return LocalNotifier()
