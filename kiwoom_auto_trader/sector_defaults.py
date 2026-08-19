from __future__ import annotations


SECTOR_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("#반도체", ("삼성전자", "SK하이닉스", "하이닉스", "DB하이텍", "원익", "리노공업", "한미반도체")),
    ("#화장품", ("아모레", "LG생활건강", "코스맥스", "한국콜마", "클리오", "토니모리")),
    ("#조선", ("HD현대중공업", "한화오션", "삼성중공업", "현대미포", "조선")),
    ("#우주", ("한화에어로", "쎄트렉아이", "AP위성", "인텔리안", "켄코아")),
    ("#방산", ("LIG넥스원", "풍산", "현대로템", "한국항공우주", "한화시스템")),
    ("#2차전지", ("LG에너지솔루션", "삼성SDI", "에코프로", "포스코퓨처엠", "엘앤에프", "천보")),
    ("#바이오", ("셀트리온", "삼성바이오", "유한양행", "한미약품", "알테오젠")),
    ("#게임", ("크래프톤", "엔씨소프트", "넷마블", "펄어비스", "카카오게임즈")),
    ("#엔터", ("HYBE", "하이브", "JYP", "에스엠", "YG", "와이지")),
    ("#자동차", ("현대차", "기아", "현대모비스", "만도", "HL만도")),
    ("#금융", ("KB금융", "신한지주", "하나금융", "우리금융", "삼성생명")),
    ("#전기장비", ("계양전기", "LS ELECTRIC", "효성중공업", "일진전기", "HD현대일렉트릭")),
)

SECTOR_SYMBOL_DEFAULTS: dict[str, str] = {
    "005930": "#반도체",
    "000660": "#반도체",
    "012200": "#전기장비",
    "009150": "#전기장비",
    "373220": "#2차전지",
    "000270": "#자동차",
    "005380": "#자동차",
    "105560": "#금융",
}


def default_sector_for_stock(symbol: str, name: str = "") -> str:
    normalized = "".join(ch for ch in str(symbol or "") if ch.isdigit()).zfill(6)[-6:]
    if normalized in SECTOR_SYMBOL_DEFAULTS:
        return SECTOR_SYMBOL_DEFAULTS[normalized]
    clean_name = str(name or "").strip().upper()
    if clean_name:
        for sector, keywords in SECTOR_KEYWORDS:
            if any(keyword.upper() in clean_name for keyword in keywords):
                return sector
    return "#미분류"


def memo_with_default_sector(symbol: str, name: str = "", memo: str = "") -> str:
    cleaned = str(memo or "").strip()
    if cleaned.startswith("#"):
        return cleaned
    sector = default_sector_for_stock(symbol, name)
    return f"{sector} {cleaned}".strip()
