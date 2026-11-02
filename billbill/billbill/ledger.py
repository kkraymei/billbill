"""账本：一个 SQLite 文件。规则和账户是长期数据，账单明细随时可以清空重导。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT, sha TEXT NOT NULL UNIQUE, filename TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_entries_document ON entries(document_id);
CREATE TABLE IF NOT EXISTS learned_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, signature TEXT NOT NULL UNIQUE, channel TEXT NOT NULL,
    channel_label TEXT NOT NULL, mapping TEXT NOT NULL, hits INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS user_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT NOT NULL, category TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS own_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual', position INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS category_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, category TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'llm',
    counterparty TEXT NOT NULL DEFAULT '', clean_desc TEXT NOT NULL DEFAULT '',
    counter_account TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '',
    hits INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


@contextmanager
def _db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(config.DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init() -> None:
    with _db() as db:
        db.executescript(_SCHEMA)


# ------------------------------------------------------------------ 账单明细
def file_sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def has_document(sha: str) -> bool:
    with _db() as db:
        return db.execute("SELECT 1 FROM documents WHERE sha = ?", (sha,)).fetchone() is not None


def add_document(sha: str, filename: str, info: dict, rows: list[dict]) -> None:
    """一个文件一个事务：要么整份进账本，要么一笔都不进。"""
    with _db() as db:
        cursor = db.execute("INSERT INTO documents (sha, filename, data) VALUES (?, ?, ?)",
                            (sha, filename, json.dumps(info, ensure_ascii=False)))
        db.executemany("INSERT INTO entries (document_id, data) VALUES (?, ?)",
                       [(cursor.lastrowid, json.dumps(row, ensure_ascii=False)) for row in rows])


def documents() -> list[dict]:
    with _db() as db:
        return [{"id": r["id"], "filename": r["filename"], **json.loads(r["data"])}
                for r in db.execute("SELECT * FROM documents ORDER BY id")]


def remove_document(document_id: int) -> None:
    with _db() as db:
        db.execute("DELETE FROM documents WHERE id = ?", (document_id,))


def clear() -> None:
    with _db() as db:
        db.execute("DELETE FROM documents")


def entries() -> list[dict]:
    with _db() as db:
        rows = db.execute("SELECT id, data FROM entries ORDER BY id").fetchall()
    records = [{**json.loads(r["data"]), "id": r["id"]} for r in rows]
    records.sort(key=lambda r: (r.get("time") or r.get("date") or "", r["id"]))
    return records


def save_entries(rows: list[dict]) -> None:
    with _db() as db:
        db.executemany("UPDATE entries SET data = ? WHERE id = ?",
                       [(json.dumps({k: v for k, v in row.items() if k != "id"}, ensure_ascii=False), row["id"])
                        for row in rows])


# ------------------------------------------------------------------ 关键词 / 账户
def user_rules() -> list[dict]:
    with _db() as db:
        return [dict(r) for r in db.execute("SELECT keyword, category FROM user_rules ORDER BY position, id")]


def replace_user_rules(rules: list[dict]) -> None:
    with _db() as db:
        db.execute("DELETE FROM user_rules")
        db.executemany("INSERT INTO user_rules (keyword, category, position, updated_at) VALUES (?, ?, ?, ?)",
                       [(r["keyword"], r["category"], i, _now()) for i, r in enumerate(rules)])


def own_accounts() -> list[dict]:
    with _db() as db:
        return [dict(r) for r in db.execute("SELECT key, label, source FROM own_accounts ORDER BY position, id")]


def learn_account(key: str, label: str) -> None:
    with _db() as db:
        db.execute("INSERT OR IGNORE INTO own_accounts (key, label, source, position, updated_at) "
                   "VALUES (?, ?, 'statement', 9999, ?)", (key, label, _now()))


def replace_manual_accounts(accounts: list[dict]) -> None:
    with _db() as db:
        db.execute("DELETE FROM own_accounts WHERE source != 'statement'")
        db.executemany("INSERT OR REPLACE INTO own_accounts (key, label, source, position, updated_at) "
                       "VALUES (?, ?, 'manual', ?, ?)",
                       [(a["key"], a["label"], i, _now()) for i, a in enumerate(accounts)])


# ------------------------------------------------------------------ 记下来的判断
def merchant_profiles(keys: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    unique = list(dict.fromkeys(k for k in keys if k))
    with _db() as db:
        for start in range(0, len(unique), 500):
            chunk = unique[start:start + 500]
            marks = ",".join("?" * len(chunk))
            for row in db.execute(f"SELECT * FROM category_cache WHERE key IN ({marks})", chunk):
                found[row["key"]] = dict(row)
    return found


def save_profiles(items: list[tuple[str, dict, str]]) -> None:
    """items: (key, profile, source)"""
    with _db() as db:
        db.executemany(
            "INSERT INTO category_cache (key, category, source, counterparty, clean_desc, tags, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET category = excluded.category, "
            "source = excluded.source, counterparty = excluded.counterparty, clean_desc = excluded.clean_desc, "
            "tags = excluded.tags, updated_at = excluded.updated_at",
            [(key, p["category"], source, p.get("counterparty", ""), p.get("clean_desc", ""),
              p.get("tags", ""), _now()) for key, p, source in items])


def learned_mapping(signature: str) -> dict | None:
    with _db() as db:
        row = db.execute("SELECT mapping FROM learned_rules WHERE signature = ?", (signature,)).fetchone()
    try:
        return json.loads(row["mapping"]) if row else None
    except ValueError:
        return None


def save_mapping(signature: str, columns: dict) -> None:
    now = _now()
    with _db() as db:
        db.execute("INSERT OR REPLACE INTO learned_rules (signature, channel, channel_label, mapping, created_at, "
                   "updated_at) VALUES (?, 'model', '', ?, ?, ?)", (signature, json.dumps(columns), now, now))


# ------------------------------------------------------------------ 去重
WALLETS = ("支付宝", "微信", "京东", "美团", "抖音", "拼多多", "云闪付", "PayPal")
# 银行账单里出现这些字样，说明这笔钱其实是通过第三方支付花出去的。
GATEWAYS = ("财付通", "微信", "支付宝", "网上支付", "银联", "快捷支付", "京东", "美团", "抖音", "第三方", "网络支付",
            "手机支付", "云闪付", "代扣", "tenpay", "alipay", "wechat")


def _rank(row: dict) -> int:
    """越小越权威：第三方支付的明细比银行摘要详细，银行之间看字段填得多不多。"""
    if any(name in (row.get("label") or "") for name in WALLETS):
        return 10
    return 50 - sum(1 for f in ("counterparty", "description", "raw_category") if row.get(f))


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def deduplicate(rows: list[dict], window_days: int = 3) -> None:
    """就地标记重复，不删除。人工改过的 excluded 不动。

    同渠道重复只来自导出区间重叠的不同文件：同一份账单里一模一样的多条，
    是同一天在同一家店洗了三次车。跨渠道只认「第三方支付 vs 带支付通道字样的银行扣款」。
    """
    for row in rows:
        if not row.get("excluded_manual"):
            row["excluded"], row["exclude_reason"] = False, ""

    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row.get("fingerprint", "")].append(row)
    dropped: set[int] = set()
    for key, group in groups.items():
        by_file: dict[str, list[dict]] = defaultdict(list)
        for row in group:
            by_file[row.get("source_file", "")].append(row)
        if not key or len(by_file) < 2:
            continue
        keep = max(by_file, key=lambda name: (len(by_file[name]), -_rank(by_file[name][0])))
        for name, members in by_file.items():
            if name == keep:
                continue
            for row in members:
                dropped.add(row["id"])
                if not row.get("excluded_manual"):
                    row["excluded"], row["exclude_reason"] = True, f"与《{keep}》中同一笔重复"

    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if row["id"] not in dropped and row.get("direction") in ("expense", "income"):
            buckets[(f'{row["amount"]:.2f}', row["direction"])].append(row)
    window = timedelta(days=window_days)
    for group in buckets.values():
        group.sort(key=lambda r: (_rank(r), r.get("date", "")))
        paired: set[int] = set()
        for index, primary in enumerate(group):
            first = _parse_day(primary.get("date", ""))
            if primary["id"] in paired or not first:
                continue
            for other in group[index + 1:]:
                second = _parse_day(other.get("date", ""))
                blob = " ".join(str(other.get(f) or "") for f in
                                ("counterparty", "description", "raw_category", "account", "remark")).lower()
                if (other["id"] in paired or not second or other.get("label") == primary.get("label")
                        or abs(second - first) > window or _rank(other) <= _rank(primary)
                        or not any(g.lower() in blob for g in GATEWAYS)):
                    continue
                paired.update((primary["id"], other["id"]))
                if not other.get("excluded_manual"):
                    other["excluded"] = True
                    other["exclude_reason"] = (f'已由{primary.get("label") or "第三方"}明细覆盖'
                                               f'（{primary.get("counterparty") or primary.get("description") or ""}）')
                break
