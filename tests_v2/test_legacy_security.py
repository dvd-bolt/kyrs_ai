from pathlib import Path

import pytest
from core.executor import ScriptExecutor, UnsafeExecutionDisabled


def test_legacy_executor_cannot_run_arbitrary_code(tmp_path: Path) -> None:
    target = tmp_path / "owned.txt"
    with pytest.raises(UnsafeExecutionDisabled):
        ScriptExecutor().execute_chart_script(
            f"open({str(target)!r}, 'w').write('owned')",
            str(tmp_path / "chart.png"),
        )
    assert not target.exists()
