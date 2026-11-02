"""把一批文件变成账本：读取 → 解析 → 核对 → 清洗 → 去重 → 分类。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import classify, clean, config, documents, ledger, statement
from .model import Model, ModelError


@dataclass
class Result:
    """一个文件的导入结果，界面直接显示。"""
    filename: str
    status: str             # verified / mismatch / unchecked / empty / skipped / failed / locked
    count: int = 0
    label: str = ""
    message: str = ""
    warnings: list[str] = field(default_factory=list)


def _model(settings: dict) -> Model | None:
    return Model(settings) if settings.get("llm_enable") else None


def _worst(statuses: list[str]) -> str:
    for status in ("mismatch", "unchecked", "verified", "empty"):
        if status in statuses:
            return status
    return "failed"


def import_file(path: Path, settings: dict, password: str = "", progress=None) -> Result:
    """解析一个文件并写进账本。一张表认不出不影响同一文件的其他表。"""
    name = path.name
    sha = ledger.file_sha(path)
    if ledger.has_document(sha):
        return Result(name, "skipped", message="这个文件已经导入过")
    model = _model(settings)
    ocr = model.ocr if model and model.can_read_images else None
    try:
        tables = documents.read(path, ocr=ocr, password=password, progress=progress)
    except documents.PasswordRequired as exc:
        return Result(name, "locked", message=str(exc))
    except (documents.ReadError, ModelError) as exc:
        return Result(name, "failed", message=str(exc))
    except Exception as exc:  # noqa: BLE001 - 损坏的文件什么异常都可能抛，不能拖垮整批
        return Result(name, "failed", message=f"读取失败：{exc}")

    records: list[dict] = []
    statuses, details, warnings, label = [], [], [], ""
    for table in tables:
        try:
            parsed = statement.parse_table(table, name, model if model and model.can_chat else None, ledger)
        except statement.Unrecognized as exc:
            details.append(str(exc))
            warnings += [w for w in table.warnings if w not in warnings]
            continue
        except ModelError as exc:
            details.append(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 - 一份怪文件不能拖垮整批
            details.append(f"解析出错：{exc}")
            continue
        label = label or parsed.label
        statuses.append(parsed.check)
        details.append(parsed.detail)
        warnings += [w for w in parsed.warnings if w not in warnings]
        account = f"{parsed.label} {parsed.account}".strip() if parsed.account else ""
        if account:
            classify.learn_account(account)
        currency = parsed.currency or settings.get("default_currency") or ""
        # 文件里完全没有机构名时，用文件名当来源，免得账户一栏空着。
        source = parsed.label or path.stem
        clean.prepare(parsed.rows, source, account or source, currency)
        for row in parsed.rows:
            if row.get("account"):
                classify.learn_account(row["account"])
            row["fingerprint"] = statement.fingerprint(row, parsed.label)
            row["unsure_direction"] = bool(row.pop("_unsure", False))
            row["broken"] = bool(row.pop("_broken", False))
            row["ocr"] = table.ocr
            records.append({k: v for k, v in row.items() if not k.startswith("_")})
    if not statuses:
        return Result(name, "failed", message="；".join(d for d in details if d) or "没有解析出交易",
                      warnings=warnings)
    status = _worst(statuses)
    detail = "；".join(d for d in dict.fromkeys(details) if d)
    ledger.add_document(sha, name, {"status": status, "label": label, "count": len(records),
                                    "detail": detail, "warnings": warnings}, records)
    return Result(name, status, len(records), label, detail, warnings)


def refresh(settings: dict, progress=None) -> dict:
    """重新跑去重和分类。规则、账户、模板改了之后调用，已导入的账单不用重读。"""
    rows = ledger.entries()
    ledger.deduplicate(rows, int(settings.get("dedup_days", 3)))
    stats = classify.classify(rows, settings, _model(settings), progress)
    ledger.save_entries(rows)
    return stats


def default_output_name() -> str:
    from datetime import datetime
    return f"账单整理-{datetime.now():%Y%m%d-%H%M}.xlsx"


def data_ready() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    ledger.init()
