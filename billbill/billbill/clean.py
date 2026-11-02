"""把银行说明拆成「交易对方 / 说明 / 对手账户」。

流水常把商户、地点、卡号、参考号全塞进一列。这里只做确定性的部分，
宁可拆不出（留给关键词或模型），也不给一个半对不对的名字。
"""

from __future__ import annotations

import re

_VALUE_DATE = re.compile(r"\bvalue\s*date\s*:?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", re.I)
_CARD = re.compile(r"\bcard\s*x{1,4}\s*(\d{3,4})\b", re.I)      # 掩码和尾号之间可能带空格
_RECEIPT = re.compile(r"\b(?:rcpt|receipt|ref|reference|trn|txn)\s*[:#]?\s*\d{4,}\b", re.I)
_LONG_DIGITS = re.compile(r"(?<![\d.])\d{8,}(?![\d.])")
# 原文常写成 Transferfromxx4321，from 紧贴前一个词，不能加词边界。
_COUNTER_ACCOUNT = re.compile(r"(?:from|to)\s*x{2,4}\s*(\d{3,4})", re.I)
_TRAILING_JUNK = re.compile(r"[\s*·\-—/\\|,;:：，、]+$")
_BOUNDARIES = (re.compile(r"(?<=[a-z])(?=[A-Z])"), re.compile(r"(?<=[A-Za-z])(?=\d)"),
               re.compile(r"(?<=\d)(?=[A-Za-z])"), re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])"))

_FEES = (re.compile(r"charging\s+ovs\s+cup\s+pos", re.I),
         re.compile(r"\b(?:overseas|international)\s+(?:transaction\s+)?fee\b", re.I),
         re.compile(r"\b(?:account|monthly|annual|service|unpaid\s+payment)\s+fee\b", re.I),
         re.compile(r"手续费|年费|服务费|管理费"))

# 固定句式。第三项表示后面直接跟账户尾号时是自己账户之间的划转，没有「对方」。
_PATTERNS = ((re.compile(r"^transfer\s*to\b\s*(.*)$", re.I), "转账支出", True),
             (re.compile(r"^transfer\s*from\b\s*(.*)$", re.I), "转账收入", True),
             (re.compile(r"^transfer\s*(?:to|from)x*\s*(.*)$", re.I), "转账", True),
             (re.compile(r"^(?:inward|outward)\s*transfer(?:\s*ret)?\s*:?\s*(.*)$", re.I), "跨行转账", True),
             (re.compile(r"^direct\s*debit\b\s*(.*)$", re.I), "自动扣款", False),     # 开头的数字是商户编号
             (re.compile(r"^(?:转账|汇款)(?:给|至|到)\s*(.*)$", re.I), "转账支出", False))
_ACCOUNT_LIKE = re.compile(r"^x*\d{3,}$", re.I)
_CHANNEL_WORDS = {"app", "bank", "commbank", "transfer", "pay", "payid", "phone", "ibank", "branch",
                  "limited", "from", "to", "online", "mobile"}
_PARTY_REJECT = re.compile(r"^(?:\W*|x*\d+|account\s*number.*|statement.*|page.*|balance.*)$", re.I)

# 刷卡说明是卡组织报文：商户名（最多 22 字符）+ 城市 + 州 + 国家码。只认以国家码结尾的。
_COUNTRIES = frozenset("AUS AU NZL NZ USA US GBR GB UK CAN IRL SGP SG HKG HK CHN CN TWN TW MAC MO JPN JP KOR KR "
                       "THA TH MYS MY IDN PHL PH VNM VN IND ARE AE FJI FJ DEU FRA FR NLD NL ITA ESP ES CHE CH".split())
_STATES = frozenset("NSW VIC QLD WA SA TAS ACT NT NS VI QL".split())
_CITY_LEAD = frozenset("ST MT MOUNT PORT NORTH SOUTH EAST WEST UPPER LOWER NEW LAKE POINT KINGS".split())
_CITY_TAIL = frozenset("PARK BAY FARM FLATS JUNCTION JUNCTIO HEIGHTS HILLS BEACH CITY CBD VALE CREEK GROVE WHARF".split())
_NOT_CITY = frozenset("LIMITED LTD PTY INC CO CORP GROUP STORE SHOP MARKET MARKETS SERVICES HOLDINGS".split())
# 支付通道前缀只认名单里的：「UBER *TRIP」里 Uber 本身就是商户。
_PROCESSOR = re.compile(r"^(?:SQ|SMP|KPY|ZLR|EZI|DBS|MED|IZ|LS|PP|PAYPAL|TST|DD|ALIPAY)\s?\*+\s*", re.I)
_TXN_PREFIX = re.compile(r"^(?:pre-?authori[sz]ation\s+completed|consumption|pos\s+purchase|visa\s+purchase"
                         r"|eftpos\s+purchase|purchase)\s+", re.I)
_NOT_PURCHASE = re.compile(r"\b(?:transfer|deposit|withdrawal|atm|direct\s*debit|refund|return|reversal|salary"
                           r"|interest|fee|payid)\b", re.I)
_CARD_NOISE = re.compile(r"\*+\d+\*?|\bHELP\.\S+|\bPOS_\w+", re.I)
_MERCHANT_WIDTH = 22
_CITY_WORD = re.compile(r"[A-Za-z][A-Za-z'.-]{2,}")
_STORE_CODE = re.compile(r"\d{1,7}|[A-Z]")


def is_bank_fee(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _FEES)


def _place_code(word: str) -> bool:
    upper = word.upper()
    return upper in _COUNTRIES or upper in _STATES or bool(re.fullmatch(r"\d{2}", word))


def card_merchant(text: str) -> str:
    """MAPLEMART 1234 SYDNEY AU -> MAPLEMART；SQ *CORNER CAFE South Brisban AU -> CORNER CAFE。"""
    text = (text or "").strip()
    if not text or _NOT_PURCHASE.search(text) or is_bank_fee(text):
        return ""
    words = [w for w in _TXN_PREFIX.sub("", text).split() if re.search(r"\w", w)]
    anchored = False
    while len(words) > 1 and _place_code(words[-1]):
        anchored = anchored or words[-1].upper() in _COUNTRIES
        words.pop()
    if not anchored:
        return ""
    body = " ".join(words)
    if len(body) > _MERCHANT_WIDTH + 1 and body[_MERCHANT_WIDTH] == " ":
        name = body[:_MERCHANT_WIDTH].split()      # 名字占满宽度，后面紧跟城市
    else:
        name = list(words)
        upper = [w.upper() for w in name]
        for size in (2, 1):
            tail = upper[-size:]
            if len(name) > size and any(upper[i:i + size] == tail for i in range(len(name) - size)):
                name = name[:-size]                # 城市重复出现（IKEA RHODES RHODES）
                break
        else:
            last = name[-1]
            if len(name) > 1 and _CITY_WORD.fullmatch(last) and last.upper() not in _NOT_CITY:
                name.pop()
                if len(name) > 1 and (last.upper() in _CITY_TAIL or name[-1].upper() in _CITY_LEAD):
                    name.pop()
    while len(name) > 1 and (_STORE_CODE.fullmatch(name[-1]) or _place_code(name[-1])):
        name.pop()
    result = _PROCESSOR.sub("", _CARD_NOISE.sub("", " ".join(name)))
    result = re.sub(r"\s{2,}", " ", result).strip(" -&*/.,")
    return result if re.search(r"[A-Za-z]{2,}", result) and len(result) <= 40 else ""


def plausible_party(party: str) -> bool:
    party = (party or "").strip()
    return len(party) >= 2 and not _PARTY_REJECT.match(party) and any(c.isalnum() for c in party)


def split_detail(text: str) -> tuple[str, str]:
    """按固定句式拆出 (交易对方, 说明)。"""
    if is_bank_fee(text):
        overseas = re.search(r"\bovs\b|overseas|international", text, re.I)
        return "", "境外交易手续费" if overseas else "银行手续费"
    for pattern, label, own_transfer in _PATTERNS:
        found = pattern.match(text)
        if not found:
            continue
        words = _TRAILING_JUNK.sub("", found.group(1).strip()).split()
        # 「xx 4321」并回一个词，否则掩码会被当成人名。
        if len(words) > 1 and re.fullmatch(r"x{2,4}", words[0], re.I) and words[1].isdigit():
            words[:2] = [words[0] + words[1]]
        leading = bool(words) and bool(_ACCOUNT_LIKE.match(words[0]))
        while words and _ACCOUNT_LIKE.match(words[0]):
            words.pop(0)
        if not words or (own_transfer and leading):
            return "", " ".join([label] + words).strip()
        # 人名常分两段（Wei Zhang），第二个词也像名字就一并取。
        named = len(words) > 1 and re.match(r"^[A-Z][A-Za-z'\-]{1,}$", words[1]) \
            and words[1].lower() not in _CHANNEL_WORDS
        take = 2 if len(words) > 1 and (len(words[0]) <= 3 or named) else 1
        party = " ".join(words[:take])
        if not plausible_party(party):
            return "", text
        return party, " ".join([label] + words[take:]).strip()
    return "", ""


def prepare(records: list[dict], label: str, account: str, currency: str) -> None:
    """清噪声、拆对方、补账户和币种。账单原文留在 source_text，之后的归类都看它。"""
    for record in records:
        text = record.get("description") or ""
        card = _CARD.search(text)
        counter = _COUNTER_ACCOUNT.search(text)
        for pattern in (_VALUE_DATE, _CARD, _RECEIPT, _LONG_DIGITS):
            text = pattern.sub(" ", text)
        text = _TRAILING_JUNK.sub("", re.sub(r"\s{2,}", " ", text).strip())
        for pattern in _BOUNDARIES:
            text = pattern.sub(" ", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        record["source_text"] = text
        if card and not record.get("account"):
            record["account"] = f"{label} ···{card.group(1)}".strip()
        if counter and not record.get("counter_account"):
            record["counter_account"] = f"···{counter.group(1)}"
        record["account"] = record.get("account") or account
        record["currency"] = record.get("currency") or currency
        record["label"] = label
        party, rest = split_detail(text)
        if is_bank_fee(text):
            record["description"] = rest
        elif party and not record.get("counterparty"):
            record["counterparty"], record["description"] = party, rest or text
        else:
            record["counterparty"] = record.get("counterparty") or card_merchant(text)
            record["description"] = text or record.get("description") or ""
