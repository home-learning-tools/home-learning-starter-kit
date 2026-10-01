"""HTML教材の種別宣言を読む、依存のない共通モジュール。

``checkup``（検証CLI）と ``render``（画面生成）の双方から参照する。
``render`` が ``checkup`` を直接importすると、``python3 -m kit.starter_web.checkup``
の実行時にモジュールの二重読み込み警告が出るため、ここへ分離している。
"""

import re
from typing import Optional


# HTML教材の種別宣言。materials/配下の全HTMLに必須で、宣言を消しても
# 厳格検査を回避できない（宣言なし＝検証エラー）。
META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
META_NAME_MATERIAL_TYPE_RE = re.compile(
    r"""name\s*=\s*["']material-type["']""", re.IGNORECASE
)
META_CONTENT_RE = re.compile(r"""content\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
MATERIAL_TYPES = ("worksheet", "reference")


def material_type(text: str) -> Optional[str]:
    """HTML教材の種別宣言（meta name="material-type"）を読み取る。"""

    for tag in META_TAG_RE.findall(text):
        if not META_NAME_MATERIAL_TYPE_RE.search(tag):
            continue
        content = META_CONTENT_RE.search(tag)
        return content.group(1).strip().lower() if content else ""
    return None
