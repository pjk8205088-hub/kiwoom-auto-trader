from __future__ import annotations


SYMBOL_ALIASES = {
    "00915": "009150",
}

KNOWN_SYMBOL_NAMES = {
    "005930": "삼성전자",
    "009150": "삼성전기",
}


def normalize_symbol(symbol: str) -> str:
    digits = "".join(ch for ch in str(symbol or "") if ch.isdigit())
    if not digits:
        return ""
    if digits in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[digits]
    if len(digits) < 6:
        return digits.zfill(6)
    return digits[:6]


def known_symbol_name(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    return KNOWN_SYMBOL_NAMES.get(normalized, "")


def clean_account_number(account: str) -> str:
    return "".join(ch for ch in str(account or "") if ch.isdigit())


def display_account_number(account: str) -> str:
    digits = clean_account_number(account)
    if len(digits) >= 8:
        return digits[:8]
    return digits


def mask_account_number(account: str) -> str:
    digits = display_account_number(account)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:8]}"
    return digits or "미선택"
