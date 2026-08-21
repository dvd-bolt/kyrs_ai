"""Atomic JSON and standalone HTML output for QA reports."""

from __future__ import annotations

import html
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from papercraft.domain import QAReport


@dataclass(frozen=True, slots=True)
class QAReportArtifacts:
    json_path: Path
    html_path: Path


class QAReportWriter:
    def write(
        self,
        report: QAReport,
        *,
        json_path: str | os.PathLike[str],
        html_path: str | os.PathLike[str],
    ) -> QAReportArtifacts:
        json_output = Path(json_path).expanduser().resolve()
        html_output = Path(html_path).expanduser().resolve()
        if json_output.suffix.lower() != ".json" or html_output.suffix.lower() not in {
            ".html",
            ".htm",
        }:
            raise ValueError("QA report paths must use .json and .html extensions")
        payload = report.model_dump(mode="json")
        _atomic_text(json_output, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        _atomic_text(html_output, self._html(report))
        return QAReportArtifacts(json_path=json_output, html_path=html_output)

    @staticmethod
    def _html(report: QAReport) -> str:
        issue_rows = "\n".join(
            "<tr>"
            f'<td><span class="severity {html.escape(issue.severity.value)}">{html.escape(issue.severity.value.upper())}</span></td>'
            f"<td>{html.escape(issue.category)}</td>"
            f"<td>{html.escape(issue.message)}</td>"
            f"<td>{'Да' if issue.resolved else 'Нет'}</td>"
            "</tr>"
            for issue in report.issues
        ) or '<tr><td colspan="4">Проблемы не обнаружены</td></tr>'
        metric_rows = "\n".join(
            "<tr>"
            f"<td>{html.escape(metric.name)}</td>"
            f"<td>{metric.value:g}</td>"
            f"<td>{html.escape(metric.unit or '')}</td>"
            f"<td>{'' if metric.passed is None else ('Да' if metric.passed else 'Нет')}</td>"
            "</tr>"
            for metric in report.metrics
        ) or '<tr><td colspan="4">Метрики отсутствуют</td></tr>'
        status = html.escape(report.status.value)
        return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>QA — {html.escape(report.project_id)}</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f5f7fb; color: #172033; }}
    main {{ max-width: 1100px; margin: 32px auto; padding: 0 20px; }}
    header, section {{ background: white; border: 1px solid #dfe5ef; border-radius: 12px; padding: 20px; margin-bottom: 18px; }}
    h1, h2 {{ margin-top: 0; }}
    .status {{ display: inline-block; padding: 5px 10px; border-radius: 999px; background: #e7edf8; font-weight: 700; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px; border-bottom: 1px solid #e6eaf1; text-align: left; vertical-align: top; }}
    .severity {{ font-weight: 700; }} .blocker, .critical, .error {{ color: #a11b1b; }} .warning {{ color: #8a5a00; }} .info {{ color: #315ea8; }}
    .meta {{ color: #59657a; }}
  </style>
</head>
<body><main>
  <header><h1>Отчёт контроля качества</h1><p><span class="status">{status.upper()}</span></p>
  <p>{html.escape(report.summary)}</p><p class="meta">Проект: {html.escape(report.project_id)} · Run: {html.escape(report.run_id)}</p></header>
  <section><h2>Проблемы</h2><table><thead><tr><th>Уровень</th><th>Категория</th><th>Описание</th><th>Решено</th></tr></thead><tbody>{issue_rows}</tbody></table></section>
  <section><h2>Метрики</h2><table><thead><tr><th>Название</th><th>Значение</th><th>Единица</th><th>Пройдено</th></tr></thead><tbody>{metric_rows}</tbody></table></section>
</main></body></html>"""


def write_qa_report(
    report: QAReport,
    json_path: str | os.PathLike[str],
    html_path: str | os.PathLike[str],
) -> QAReportArtifacts:
    return QAReportWriter().write(report, json_path=json_path, html_path=html_path)


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=path.suffix, dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
