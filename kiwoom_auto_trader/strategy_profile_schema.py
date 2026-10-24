"""Editable per-symbol strategy profile defaults for the V8 settings UI."""
from copy import deepcopy


def _line(minute=False):
    value = {
        "shortPeriod": 5, "shortEnabled": True, "longPeriod": 20,
        "longEnabled": True, "slopeBars": 1, "slopeEpsilonPct": 0.0,
        "crossValidBars": 3, "useUnclosedBar": True,
    }
    if minute:
        value.update(buyInterval=5, sellInterval=5,
                     detailAverages={"period2": True, "period3": True, "period5": True, "period10": True})
    return value


_PROFILE = {
    "name": "기본",
    "buyEnabled": True,
    "sellEnabled": True,
    "bar": {"combine": "BOTH", "minute": _line(True), "daily": _line(),
            "candle": {"buyRequireBullish": True, "sellRequireBearish": True, "dojiCountsAs": "NEITHER"},
            "pivot": {"lookback": 2, "compareCount": 2, "enabled": True}},
    "buy": {
        "B01_maUpBullish": {"enabled": True},
        "B02_minuteCross": {"enabled": True, "shortOnly": False, "requireDailyCross": False},
        "B03_fromDayLow": {"enabled": False, "risePct": 1.0},
        "B04_openBreakUp": {"enabled": False, "useMinute": True, "useDaily": True},
        "B05_rebuyAfterSell": {"enabled": False, "dropFromHighPct": 2.0},
        "B06_buyBackOnDip": {"enabled": False, "dropFromHighPct": -2.0},
        "B07_screenerAutoAdd": {"enabled": False}},
    "buyBlock": {
        "X01_belowDayOpen": {"enabled": True},
        "X02_overExtended": {"enabled": True, "gapFromPrevClosePct": 5.0},
        "X03_maFalling": {"enabled": True, "checkMinute": True, "checkDaily": True},
        "X04_breakoutCooldown": {"enabled": True, "minutes": 30},
        "X05_timeWindow": {"enabled": True}, "X06_limits": {"enabled": True}},
    "sell": {
        "S01_maTurnDown": {"enabled": True},
        "S02_minuteCross": {"enabled": True, "shortOnly": False},
        "S03_dayTargetProfit": {"enabled": False, "targetPct": 3.0},
        "S04_prevCloseUpTurn": {"enabled": False}, "S05_prevCloseDown": {"enabled": False},
        "S06_openSurgeWatch": {"enabled": False, "triggerPct": 5.0, "from": "08:00", "to": "08:30"},
        "S07_openPlungeWatch": {"enabled": False, "triggerPct": -3.0, "from": "08:00", "to": "08:30"},
        "S08_openBreakDown": {"enabled": False, "useMinute": True, "useDaily": True},
        "S09_breakoutTarget": {"enabled": False, "targetPct": 3.0},
        "S10_stopLoss": {"enabled": True, "pct": 3.5}},
    "sellBlock": {
        "Y01_earlySession": {"enabled": True, "from": "08:00", "to": "09:00"},
        "Y02_gapDownWait": {"enabled": True, "minutes": 5},
        "Y03_maRising": {"enabled": True, "checkMinute": True, "checkDaily": True},
        "Y04_timeWindow": {"enabled": True}},
    "order": {"sellPriceType": "LIMIT_CURRENT", "repostUnfilledSec": 30,
              "repostMaxCount": 5, "breakoutCooldownMin": 30},
}

GROUP_LABELS = {
    "bar": "요즘 평균 가격", "buy": "매수 조건", "buyBlock": "매수 제한 조건",
    "sell": "매도 조건", "sellBlock": "매도 제한 조건", "order": "주문 방법",
    "minute": "분봉 가격 흐름", "daily": "일봉 가격 흐름", "candle": "캔들 모양",
    "pivot": "최근 고점·저점", "detailAverages": "세부 이동평균선",
    "buyEnabled": "매수 허용", "sellEnabled": "매도 허용",
}
FIELD_LABELS = {
    "combine": "함께 확인할 자료", "shortPeriod": "짧은 이동평균 기간", "longPeriod": "긴 이동평균 기간",
    "shortEnabled": "짧은 이동평균 사용", "longEnabled": "긴 이동평균 사용", "slopeBars": "기울기 확인 봉 수",
    "slopeEpsilonPct": "기울기 판정 기준(%)", "crossValidBars": "교차 후 인정 봉 수",
    "useUnclosedBar": "진행 중인 봉 포함", "buyInterval": "매수 판단 분봉 간격",
    "sellInterval": "매도 판단 분봉 간격", "period2": "2일선 사용", "period3": "3일선 사용",
    "period5": "5일선 사용", "period10": "10일선 사용", "buyRequireBullish": "양봉일 때 매수",
    "sellRequireBearish": "음봉일 때 매도", "dojiCountsAs": "십자봉 판정", "lookback": "과거 확인 봉 수",
    "compareCount": "비교 봉 수", "enabled": "이 조건 사용", "shortOnly": "짧은 평균선만 확인",
    "requireDailyCross": "일봉 교차도 확인", "useMinute": "분봉 확인", "useDaily": "일봉 확인",
    "risePct": "저점 대비 상승률(%)", "dropFromHighPct": "고점 대비 하락률(%)",
    "gapFromPrevClosePct": "전일 종가 대비 상승률(%)", "checkMinute": "분봉 확인",
    "checkDaily": "일봉 확인", "minutes": "대기 시간(분)", "targetPct": "목표 수익률(%)",
    "triggerPct": "감시 시작 변동률(%)", "from": "시작 시각", "to": "종료 시각",
    "pct": "손실 제한률(%)", "sellPriceType": "매도 가격 방식",
    "repostUnfilledSec": "미체결 후 대기(초)", "repostMaxCount": "재주문 최대 횟수",
    "breakoutCooldownMin": "돌파 후 대기(분)",
    "B01_maUpBullish": "이평선 상승·양봉 매수", "B02_minuteCross": "단기선의 장기선 상향 돌파 매수",
    "B03_fromDayLow": "당일 저점 반등 매수", "B04_openBreakUp": "시초가 상향 돌파 매수",
    "B05_rebuyAfterSell": "매도 후 재매수", "B06_buyBackOnDip": "고점 조정 후 재매수",
    "B07_screenerAutoAdd": "조건 검색 종목 자동 추가", "X01_belowDayOpen": "시초가 아래 매수 제한",
    "X02_overExtended": "단기 과열 매수 제한", "X03_maFalling": "이평선 하락 시 매수 제한",
    "X04_breakoutCooldown": "돌파 후 대기 중 매수 제한", "X05_timeWindow": "거래 시간 외 매수 제한",
    "X06_limits": "금액·횟수 초과 시 매수 제한", "S01_maTurnDown": "이평선 하락 전환 매도",
    "S02_minuteCross": "단기선의 장기선 하향 돌파 매도", "S03_dayTargetProfit": "일일 목표 수익 도달 매도",
    "S04_prevCloseUpTurn": "전일 종가 위에서 하락 전환 매도", "S05_prevCloseDown": "전일 종가 하향 이탈 매도",
    "S06_openSurgeWatch": "시초 이후 급등 감시 매도", "S07_openPlungeWatch": "시초 이후 급락 감시 매도",
    "S08_openBreakDown": "시초가 하향 이탈 매도", "S09_breakoutTarget": "돌파 후 목표 수익 매도",
    "S10_stopLoss": "손실 제한 매도", "Y01_earlySession": "장 초반 매도 제한",
    "Y02_gapDownWait": "급락 후 대기 시간 중 매도 제한", "Y03_maRising": "이평선 상승 중 매도 제한",
    "Y04_timeWindow": "정한 시간 외 매도 제한",
}
CHOICES = {
    "combine": {"BOTH": "분봉과 일봉 모두", "MINUTE_ONLY": "분봉만", "DAILY_ONLY": "일봉만"},
    "dojiCountsAs": {"NEITHER": "상승·하락 아님", "BULLISH": "양봉으로 판단", "BEARISH": "음봉으로 판단"},
    "sellPriceType": {"LIMIT_CURRENT": "현재가 지정가", "MARKET": "시장가"},
}


def new_profile(index: int):
    value = deepcopy(_PROFILE)
    value.update(slot=index + 1, name=f"설정 {index + 1}", symbol="", symbol_name="", dmi_period=14)
    return value


def merge_profile(saved, index: int):
    """Migrate old DMI-only profile data and retain any saved detailed values."""
    result = new_profile(index)
    if not isinstance(saved, dict):
        return result

    def merge(target, source):
        for key, value in target.items():
            if key in source:
                if isinstance(value, dict) and isinstance(source[key], dict):
                    merge(value, source[key])
                elif isinstance(value, bool) and isinstance(source[key], bool):
                    target[key] = source[key]
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    try:
                        target[key] = type(value)(source[key])
                    except (TypeError, ValueError):
                        pass
                elif isinstance(value, str) and isinstance(source[key], str):
                    target[key] = source[key]
    merge(result, saved)
    result["slot"] = index + 1
    result["name"] = f"설정 {index + 1}"
    return result


def validate_profile(profile, index):
    errors = []
    def walk(value, path=()):
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, path + (key,))
        elif isinstance(value, bool):
            return
        elif isinstance(value, (int, float)):
            if not isinstance(value, (int, float)) or not float("-inf") < float(value) < float("inf"):
                errors.append(".".join(path))
        elif isinstance(value, str) and not value.strip() and path[-1] not in {"symbol", "symbol_name"}:
            errors.append(".".join(path))
    walk(profile)
    try:
        dmi = int(profile["dmi_period"])
        if not 1 <= dmi <= 99:
            errors.append("DMI 기간(1~99)")
        symbol = str(profile.get("symbol", "")).strip()
        if symbol and (len(symbol) != 6 or not symbol.isdigit()):
            errors.append("연결 종목번호: 비워 두거나 숫자 6자리로 입력해 주세요")
        if symbol and not str(profile.get("symbol_name", "")).strip():
            errors.append("회사명: 연결 종목번호를 입력하면 회사명도 입력해 주세요")
        for key, options in CHOICES.items():
            if key == "dojiCountsAs":
                selected = profile["bar"]["candle"][key]
            elif key == "sellPriceType":
                selected = profile["order"][key]
            else:
                selected = profile["bar"][key]
            if selected not in options:
                errors.append(f"{FIELD_LABELS[key]} 선택을 확인해 주세요")
        for key, section in (("분봉", profile["bar"]["minute"]), ("일봉", profile["bar"]["daily"])):
            short, long = int(section["shortPeriod"]), int(section["longPeriod"])
            if not 1 <= short <= 200 or not 1 <= long <= 200 or short >= long:
                errors.append(f"{key} 이동평균: 짧은 기간은 긴 기간보다 작아야 합니다")
        ranges = {
            ("bar", "minute", "buyInterval"): (1, 60), ("bar", "minute", "sellInterval"): (1, 60),
            ("bar", "minute", "slopeBars"): (1, 200), ("bar", "daily", "slopeBars"): (1, 200),
            ("bar", "minute", "crossValidBars"): (1, 200), ("bar", "daily", "crossValidBars"): (1, 200),
            ("bar", "minute", "slopeEpsilonPct"): (0, 100), ("bar", "daily", "slopeEpsilonPct"): (0, 100),
            ("bar", "pivot", "lookback"): (1, 100), ("bar", "pivot", "compareCount"): (1, 100),
            ("buy", "B03_fromDayLow", "risePct"): (0, 100),
            ("buy", "B05_rebuyAfterSell", "dropFromHighPct"): (-100, 100),
            ("buy", "B06_buyBackOnDip", "dropFromHighPct"): (-100, 100),
            ("buyBlock", "X02_overExtended", "gapFromPrevClosePct"): (0, 100),
            ("buyBlock", "X04_breakoutCooldown", "minutes"): (0, 1440),
            ("sell", "S03_dayTargetProfit", "targetPct"): (0, 1000),
            ("sell", "S06_openSurgeWatch", "triggerPct"): (-100, 1000),
            ("sell", "S07_openPlungeWatch", "triggerPct"): (-100, 1000),
            ("sell", "S09_breakoutTarget", "targetPct"): (0, 1000),
            ("sell", "S10_stopLoss", "pct"): (0, 100),
            ("sellBlock", "Y02_gapDownWait", "minutes"): (0, 1440),
            ("order", "repostUnfilledSec"): (1, 3600), ("order", "repostMaxCount"): (0, 100),
            ("order", "breakoutCooldownMin"): (0, 1440),
        }
        for path, (low, high) in ranges.items():
            value = profile
            for part in path:
                value = value[part]
            if not low <= float(value) <= high:
                errors.append(f"{FIELD_LABELS.get(path[-1], path[-1])}: {low}~{high}")
    except (KeyError, TypeError, ValueError, OverflowError):
        errors.append("숫자 입력값을 확인해 주세요")
    return [f"설정 {index + 1}: {item}" for item in errors]
