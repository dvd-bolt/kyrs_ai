"""Update Writer fields in memory and export PDF through a local UNO socket."""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import uno  # type: ignore[import-not-found]


def _property(name: str, value: Any) -> Any:
    item = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
    item.Name = name
    item.Value = value
    return item


def _connect(port: int) -> Any:
    local_context = uno.getComponentContext()
    resolver = local_context.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver",
        local_context,
    )
    target = (
        f"uno:socket,host=127.0.0.1,port={port};urp;"
        "StarOffice.ComponentContext"
    )
    last_error: Exception | None = None
    for _ in range(80):
        try:
            return resolver.resolve(target)
        except Exception as error:
            last_error = error
            time.sleep(0.25)
    raise RuntimeError("could not connect to the local LibreOffice UNO socket") from last_error


def update_and_export(docx: Path, pdf: Path, port: int) -> None:
    context = _connect(port)
    desktop = context.ServiceManager.createInstanceWithContext(
        "com.sun.star.frame.Desktop",
        context,
    )
    source_url = uno.systemPathToFileUrl(str(docx.resolve()))
    update_mode = uno.getConstantByName("com.sun.star.document.UpdateDocMode.FULL_UPDATE")
    document = desktop.loadComponentFromURL(
        source_url,
        "_blank",
        0,
        (
            _property("Hidden", True),
            _property("ReadOnly", True),
            _property("UpdateDocMode", update_mode),
            _property("MacroExecutionMode", 0),
        ),
    )
    if document is None:
        raise RuntimeError("LibreOffice could not open the DOCX")
    try:
        if hasattr(document, "updateLinks"):
            document.updateLinks()
        indexes = document.getDocumentIndexes()
        for index in range(indexes.getCount()):
            indexes.getByIndex(index).update()
        fields = document.getTextFields().createEnumeration()
        while fields.hasMoreElements():
            field = fields.nextElement()
            if hasattr(field, "update"):
                field.update()
        if hasattr(document, "refresh"):
            document.refresh()
        pdf.parent.mkdir(parents=True, exist_ok=True)
        document.storeToURL(
            uno.systemPathToFileUrl(str(pdf.resolve())),
            (_property("FilterName", "writer_pdf_Export"), _property("Overwrite", True)),
        )
    finally:
        document.close(True)
        desktop.terminate()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--docx", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    arguments = parser.parse_args()
    update_and_export(arguments.docx, arguments.pdf, arguments.port)


if __name__ == "__main__":
    main()
