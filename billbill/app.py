"""BillBill：离线读取各家银行的账单，按你的模板整理成一份记账 Excel。

    billbill                                 打开窗口
    billbill --convert 输出.xlsx 账单...      不开窗口，直接整理
    billbill --selftest [报告.json]           检查打包是否完整
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def convert(target: str, inputs: list[str]) -> int:
    from billbill import config, documents, i18n, ledger, pipeline, template
    pipeline.data_ready()
    settings = config.load_settings()
    i18n.set_language(settings["language"])
    code = 0
    for path in documents.expand([Path(p) for p in inputs]):
        result = pipeline.import_file(path, settings)
        print(f"{result.filename}: {result.status} {result.count} {result.message}")
        code = code or int(result.status == "failed")
    pipeline.refresh(settings)
    template.export(ledger.entries(), Path(target), settings)
    return code


def selftest(report: str | None) -> int:
    from billbill import config, model, pipeline
    checks = {}
    for name in ("openpyxl", "pdfplumber", "pypdfium2", "xlrd", "olefile", "chardet", "PIL.Image", "PyQt6.QtWidgets", "cryptography"):
        try:
            __import__(name)
            checks[name] = True
        except Exception as exc:  # noqa: BLE001 - 自检就是要把缺什么报出来
            checks[name] = str(exc)
    try:
        pipeline.data_ready()
        checks["data_dir"] = True
    except OSError as exc:
        checks["data_dir"] = str(exc)
    try:
        binary = model._server_binary(config.load_settings())
        # 单文件 exe 退出后解包目录就没了，所以在这里跑，缺动态库也能当场发现。
        run = subprocess.run([str(binary), "--version"], capture_output=True, timeout=30,
                             creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        checks["llama_server"] = run.returncode == 0 or run.stderr.decode("utf-8", "replace")[-300:]
    except (model.ModelError, OSError, subprocess.SubprocessError) as exc:
        checks["llama_server"] = str(exc)
    checks["assets"] = (config.BUNDLE_DIR / "assets/billbill.ico").is_file()
    text = json.dumps(checks, ensure_ascii=False, indent=2)
    print(text)
    if report:
        Path(report).write_text(text, "utf-8")
    return 0 if all(v is True for v in checks.values()) else 1


def main() -> int:
    # 非中文区域的控制台默认编码写不了账单文件名。
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    arguments = sys.argv[1:]
    if arguments[:1] == ["--selftest"]:
        return selftest(arguments[1] if len(arguments) > 1 else None)
    if arguments[:1] == ["--convert"] and len(arguments) >= 3:
        return convert(arguments[1], arguments[2:])
    from billbill.ui import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
