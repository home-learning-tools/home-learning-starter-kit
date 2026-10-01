"""反復ルーティンの日付展開に使う共通契約。"""

from datetime import date, timedelta
import re
from typing import Optional, Tuple


STARTER_ROUTINE_HEADER = (
    "開始日",
    "終了日",
    "曜日",
    "学習者ID",
    "学習者",
    "枠",
    "教科",
    "内容",
    "教材",
)
ROUTINE_HEADING = "Web表示用・反復ルーティン"
WEEKDAY_INDEX = {
    "月": 0,
    "火": 1,
    "水": 2,
    "木": 3,
    "金": 4,
    "土": 5,
    "日": 6,
}


def parse_weekdays(value: str) -> Tuple[int, ...]:
    """「平日」「毎日」または「月・水・金」を曜日番号へ変換する。"""

    normalized = value.strip()
    if normalized == "平日":
        return (0, 1, 2, 3, 4)
    if normalized == "毎日":
        return (0, 1, 2, 3, 4, 5, 6)

    tokens = [
        token.removesuffix("曜日").removesuffix("曜")
        for token in re.split(r"[・、,/\s]+", normalized)
        if token
    ]
    if not tokens or any(token not in WEEKDAY_INDEX for token in tokens):
        raise ValueError(
            "曜日は「平日」「毎日」または「月・水・金」の形で指定してください"
        )
    indexes = tuple(WEEKDAY_INDEX[token] for token in tokens)
    if len(set(indexes)) != len(indexes):
        raise ValueError("曜日を重複して指定しないでください")
    return indexes


def routine_dates(
    start: date,
    end: Optional[date],
    weekdays: Tuple[int, ...],
    horizon_start: date,
    horizon_end: date,
) -> Tuple[date, ...]:
    """反復条件を表示対象期間内の具体的な日付へ展開する。"""

    first = max(start, horizon_start)
    last = min(end, horizon_end) if end else horizon_end
    if first > last:
        return ()
    result = []
    current = first
    while current <= last:
        if current.weekday() in weekdays:
            result.append(current)
        current += timedelta(days=1)
    return tuple(result)
