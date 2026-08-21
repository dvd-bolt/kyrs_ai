"""Deprecated compatibility boundary for the removed arbitrary-code executor."""

from __future__ import annotations


class UnsafeExecutionDisabled(RuntimeError):
    pass


class ScriptExecutor:
    """Reject legacy model-generated Python instead of executing it in-process.

    PaperCraft v2 renders validated ChartSpec objects through
    ``papercraft.infrastructure.visuals.ChartRenderer``. This class remains
    importable only so old project files fail with a clear migration message.
    """

    def __init__(self, output_dir: str | None = None) -> None:
        self.output_dir = output_dir

    def execute_chart_script(self, script_code: str, output_png_path: str) -> bool:
        del script_code, output_png_path
        raise UnsafeExecutionDisabled(
            "Arbitrary Python execution was removed for security. "
            "Use a validated ChartSpec with ChartRenderer."
        )


__all__ = ["ScriptExecutor", "UnsafeExecutionDisabled"]
