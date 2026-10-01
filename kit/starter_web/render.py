"""共通モデルを依存のない1枚の静的HTMLへ描画する。"""

import html
import os
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, List, NamedTuple, Optional, Set, Tuple
from urllib.parse import quote, urlsplit

from .model import (
    ActivityItem,
    DashboardData,
    MaterialLink,
    MaterialStatus,
    MATERIAL_STATUS_LABELS,
    PrepIssue,
    PREP_ISSUE_LABELS,
    ScheduleItem,
    ScheduleKind,
    SourceRef,
    Status,
    STATUS_LABELS,
    UnpreparedItem,
)
from .presentation import (
    AttentionExtra,
    CatalogView,
    GuideView,
    MaterialLinkScope,
    PendingBadgeMode,
    Presentation,
)


WEEKDAYS = ("月", "火", "水", "木", "金", "土", "日")
STATUS_ICONS = {
    Status.PLANNED: "○",
    Status.READY: "●",
    Status.COMPLETED: "✓",
    Status.PARTIAL: "◐",
    Status.MISSED: "!",
    Status.UNKNOWN: "?",
    Status.DEFERRED: "→",
}

MATERIAL_STATUS_ICONS = {
    MaterialStatus.AVAILABLE: "●",
    # 3週間の見通しでは、どちらも「そのまま実施できる」という同じ判断を
    # 示すため記号を共通にする。予定カードと凡例ではラベル・色で区別する。
    MaterialStatus.EXTERNAL_AVAILABLE: "●",
    MaterialStatus.PARTIAL: "◐",
    MaterialStatus.MISSING: "⚠",
    MaterialStatus.NOT_REQUIRED: "—",
}

# 教材の準備状態を出すモードで、実施状態を優先して表示する状態と記号。
READINESS_STATUS_ICONS = {
    Status.COMPLETED: "✓",
    Status.PARTIAL: "◐",
    Status.MISSED: "×",
    Status.DEFERRED: "→",
    Status.UNKNOWN: "?",
}

#: 過去日なのに実施結果が予定へ反映されていない枠の表記。
UNCONFIRMED_LABEL = "実施状況未確認"

PREP_ISSUE_ICONS = {
    PrepIssue.NO_MATERIAL: "□",
    PrepIssue.MISSING_FILE: "!",
}


class BadgeParts(NamedTuple):
    """状態バッジの見た目（CSS修飾子・記号・読み上げ用ラベル）。"""

    modifier: str
    icon: str
    label: str


ROUTINE_BADGE = BadgeParts("routine", "↻", "反復")
TEST_BADGE = BadgeParts("test-event", "◆", "本番")
UNCONFIRMED_BADGE = BadgeParts(Status.UNKNOWN.value, "?", UNCONFIRMED_LABEL)

WEEK_CATEGORY_LABELS = {
    "japanese": "国語",
    "math": "算数",
    "science": "理科",
    "social": "社会",
    "kanji": "漢字",
    "test": "テスト",
    "other": "その他",
}


def escape_text(value: object) -> str:
    """画面へ出す文字列をHTMLエスケープする。

    追加セクションを描画する下流も、この関数で本文をエスケープする。
    """

    return html.escape(str(value), quote=True)


# 内部の呼び出しは短い別名を使う。公開名は escape_text。
_e = escape_text


def _badge_html(parts: BadgeParts) -> str:
    return (
        '<span class="status status-{modifier}">'
        '<span aria-hidden="true">{icon}</span>{label}</span>'
    ).format(
        modifier=_e(parts.modifier),
        icon=_e(parts.icon),
        label=_e(parts.label),
    )


def _status_badge(status: Status) -> str:
    return _badge_html(
        BadgeParts(status.value, STATUS_ICONS[status], STATUS_LABELS[status])
    )


def _material_badge_parts(item: ScheduleItem) -> BadgeParts:
    status = item.material_status()
    return BadgeParts(
        "material-" + status.value,
        MATERIAL_STATUS_ICONS[status],
        MATERIAL_STATUS_LABELS[status],
    )


def _schedule_badge_parts(
    item: ScheduleItem,
    anchor_date: date,
    mode: PendingBadgeMode,
) -> BadgeParts:
    """予定枠に出す状態を、決められた優先順位で1つ選ぶ。

    ``MATERIAL_READINESS`` では ①実施状態 → ②反復ルーティン → ③テスト本番 →
    ④過去日で実績が未反映の予定 → ⑤今日以降の教材状態 の順に決める。既定の
    ``STATUS`` は従来どおり予定表上の状態をそのまま出す。
    """

    if mode == PendingBadgeMode.MATERIAL_READINESS:
        if item.status in READINESS_STATUS_ICONS:
            label = (
                UNCONFIRMED_LABEL
                if item.status == Status.UNKNOWN
                else STATUS_LABELS[item.status]
            )
            return BadgeParts(
                item.status.value, READINESS_STATUS_ICONS[item.status], label
            )
        if item.kind == ScheduleKind.ROUTINE:
            return ROUTINE_BADGE
        if item.kind == ScheduleKind.TEST:
            return TEST_BADGE
        if item.date < anchor_date:
            return UNCONFIRMED_BADGE
        return _material_badge_parts(item)
    if item.kind == ScheduleKind.ROUTINE and item.status == Status.PLANNED:
        return ROUTINE_BADGE
    return BadgeParts(
        item.status.value, STATUS_ICONS[item.status], STATUS_LABELS[item.status]
    )


def _week_category(item: ScheduleItem) -> str:
    """3週間表示の左端色を、状態ではなく予定種別から決める。"""

    if item.kind == ScheduleKind.TEST:
        return "test"

    subject = item.subject
    if "漢字" in subject:
        return "kanji"
    if "国語" in subject or "ことば" in subject:
        return "japanese"
    if "算数" in subject or "数学" in subject:
        return "math"
    if "理科" in subject:
        return "science"
    if "社会" in subject:
        return "social"
    return "other"


def _split_target(target: str) -> Tuple[str, str]:
    split = urlsplit(target)
    if split.scheme in {"http", "https"}:
        return target, ""
    return split.path, split.fragment


def _href(target: str, output_dir: Path) -> str:
    path_text, fragment = _split_target(target)
    if path_text.startswith(("http://", "https://")):
        return _e(target)
    relative = os.path.relpath(path_text, str(output_dir.resolve()))
    encoded = quote(relative.replace(os.sep, "/"), safe="/:@-._~")
    if fragment:
        encoded += "#" + quote(fragment, safe="-._~")
    return _e(encoded)


# コピーbuttonのアイコン。外部アセットを増やさないinline SVGにする。
_COPY_ICON = (
    '<svg viewBox="0 0 16 16" width="12" height="12" focusable="false">'
    '<path d="M5.6 4.1V2.7a1 1 0 0 1 1-1h6.7a1 1 0 0 1 1 1v6.7a1 1 0 0 1-1 1h-1.4" '
    'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>'
    '<rect x="1.7" y="5.6" width="8.7" height="8.7" rx="1" fill="none" '
    'stroke="currentColor" stroke-width="1.5"/></svg>'
)
_COPIED_ICON = (
    '<svg viewBox="0 0 16 16" width="12" height="12" focusable="false">'
    '<path d="M2.4 8.6l3.6 3.6 7.6-8.4" fill="none" stroke="currentColor" '
    'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


def _copy_button(link: MaterialLink) -> str:
    """教材リンクの隣へ置く、論理パスのコピーbutton。

    ``copy_path`` を持つリンクにだけ出す。外部URLや、下流が2項目だけで組み立てた
    リンクには出さない（``target`` から相対ルートを推測しない）。
    """

    if not link.copy_path:
        return ""
    return (
        '<button type="button" class="copy-path" data-copy-path="{path}" '
        'aria-label="教材パスをコピー: {label}" title="教材パスをコピー: {path}">'
        '<span class="copy-path-icon copy-path-idle" aria-hidden="true">{icon}</span>'
        '<span class="copy-path-icon copy-path-done" aria-hidden="true">{done}</span>'
        "</button>"
    ).format(
        path=_e(link.copy_path),
        label=_e(link.label),
        icon=_COPY_ICON,
        done=_COPIED_ICON,
    )


def _copy_support() -> str:
    """コピー操作の通知領域・手動コピー欄・処理scriptを1組だけ返す。

    教材リンクの数にかかわらずページへ1つずつ置く。クリップボード以外への
    送信はせず、ネットワークAPIも新しい外部依存も使わない。
    """

    return """
  <div id="copy-path-status" class="sr-only" role="status" aria-live="polite"></div>
  <div id="copy-path-manual" class="copy-path-manual" hidden>
    <label for="copy-path-manual-field">コピーできませんでした。選択してコピーしてください。</label>
    <input id="copy-path-manual-field" type="text" readonly>
    <button type="button" id="copy-path-manual-close">閉じる</button>
  </div>
  <script>
  (function () {
    var RESTORE_MS = 1800;
    var status = document.getElementById("copy-path-status");
    var manual = document.getElementById("copy-path-manual");
    var manualField = document.getElementById("copy-path-manual-field");
    var manualClose = document.getElementById("copy-path-manual-close");
    var restoreTimer = null;
    var copiedButton = null;
    // クリックごとの世代番号。writeText() の結果は並行して届くので、
    // 遅れて返ってきた古いクリックの結果で新しい表示を上書きしない。
    var generation = 0;
    function announce(text) {
      if (status) { status.textContent = text; }
    }
    function clearCopied() {
      if (copiedButton) { copiedButton.classList.remove("is-copied"); }
      copiedButton = null;
      restoreTimer = null;
    }
    function showCopied(button, path) {
      // 連続操作で古いtimerが新しい状態を消さないよう、毎回止めてから張り直す。
      if (restoreTimer !== null) { clearTimeout(restoreTimer); }
      if (copiedButton && copiedButton !== button) {
        copiedButton.classList.remove("is-copied");
      }
      button.classList.add("is-copied");
      copiedButton = button;
      announce("コピーしました: " + path);
      restoreTimer = setTimeout(clearCopied, RESTORE_MS);
    }
    function hideManual() {
      if (!manual) { return; }
      manual.hidden = true;
      if (manualField) { manualField.value = ""; }
    }
    function showManual(path) {
      // 失敗したので成功表示は出さない。手で選べる状態にするだけ。
      announce("コピーできませんでした。表示された欄から手動でコピーしてください。");
      if (!manual || !manualField) { return; }
      manual.hidden = false;
      manualField.value = path;
      manualField.focus();
      manualField.select();
    }
    function legacyCopy(path) {
      var area = document.createElement("textarea");
      area.value = path;
      area.setAttribute("readonly", "readonly");
      area.style.position = "fixed";
      area.style.top = "0";
      area.style.left = "0";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      var copied = false;
      try {
        copied = document.execCommand("copy");
      } catch (error) {
        copied = false;
      }
      document.body.removeChild(area);
      return copied;
    }
    function fallback(button, path) {
      if (legacyCopy(path)) { showCopied(button, path); return; }
      showManual(path);
    }
    function findButton(node) {
      while (node && node !== document.body) {
        if (node.nodeType === 1 && node.classList &&
            node.classList.contains("copy-path")) {
          return node;
        }
        node = node.parentNode;
      }
      return null;
    }
    if (manualClose) {
      manualClose.addEventListener("click", hideManual);
    }
    document.addEventListener("click", function (event) {
      var button = findButton(event.target);
      if (!button) { return; }
      // コピー操作では教材を開かない。
      event.preventDefault();
      var path = button.getAttribute("data-copy-path") || "";
      if (!path) { return; }
      generation += 1;
      var current = generation;
      function isLatest() { return current === generation; }
      hideManual();
      if (navigator.clipboard && navigator.clipboard.writeText) {
        try {
          navigator.clipboard.writeText(path).then(function () {
            if (!isLatest()) { return; }
            showCopied(button, path);
          }, function () {
            if (!isLatest()) { return; }
            fallback(button, path);
          });
          return;
        } catch (error) {
          fallback(button, path);
          return;
        }
      }
      fallback(button, path);
    });
  })();
  </script>"""


def _links(
    links: Iterable[MaterialLink],
    output_dir: Path,
    reference_targets: Iterable[str] = (),
) -> str:
    """教材リンクを並べる。

    ``reference_targets`` に挙げた参考・在庫のリンクは、その日の予定教材と
    同じ見た目にしない。教材未準備の枠に通常の教材リンクが並ぶと、準備済みに
    見えてしまうため。
    """

    references = set(reference_targets)
    rendered = []
    for link in links:
        is_reference = link.target in references
        rendered.append(
            # リンクとコピーbuttonは入れ子にせず、共通wrapper内の兄弟にする。
            '<span class="material-item">'
            '<a class="material-link{modifier}" href="{href}" '
            'target="_blank" rel="noopener">'
            '<span aria-hidden="true">{icon}</span>{prefix}{label}'
            '<span class="sr-only">（別タブで開く）</span></a>{copy}</span>'.format(
                modifier=" material-link-reference" if is_reference else "",
                icon="◇" if is_reference else "↗",
                prefix=(
                    '<span class="material-link-tag">参考</span>'
                    if is_reference
                    else ""
                ),
                href=_href(link.target, output_dir),
                label=_e(link.label),
                copy=_copy_button(link),
            )
        )
    return "".join(rendered)


def _source_link(source: Optional[SourceRef], output_dir: Path) -> str:
    if not source:
        return ""
    return (
        '<a class="source-link" href="{href}#L{line}" '
        'title="正本Markdownの該当行">正本 L{line}</a>'
    ).format(
        href=_href(source.path, output_dir),
        line=source.line,
    )


def _duration_minutes(duration: str) -> int:
    match = re.search(r"(\d+)\s*分", duration)
    return int(match.group(1)) if match else 0


def _month_day(value: date) -> str:
    """OS固有のstrftime拡張に頼らず月日を表示する。"""

    return "{}/{}".format(value.month, value.day)


def _slot_materials(
    item: ScheduleItem,
    scope: MaterialLinkScope,
) -> Tuple[List[MaterialLink], Set[str]]:
    """予定カードに出す教材リンクと、そのうち参考扱いにする対象。

    ``MATERIAL_FILES`` では、その枠で使う教材ファイルだけに絞る＝正本
    Markdownなどの参照リンクも参考・在庫リンクも出さないため、参考扱いの
    対象は空になる。
    """

    if scope == MaterialLinkScope.MATERIAL_FILES:
        return item.material_files(), set()
    return (
        list(item.materials),
        # 実施項目の教材は、参考にも書かれていた場合でも参考扱いにしない
        # （その組み合わせは入力契約違反で、生成前に止まる）。
        {link.target for link in item.reference_materials}
        - {link.target for link in item.matching_materials()},
    )


def _schedule_card(
    item: ScheduleItem,
    output_dir: Path,
    anchor_date: date,
    mode: PendingBadgeMode,
    scope: MaterialLinkScope = MaterialLinkScope.ALL,
) -> str:
    deferred = ""
    if item.deferred_to:
        deferred = '<span class="deferred">→ {}</span>'.format(
            _e(_month_day(item.deferred_to))
        )
    links, references = _slot_materials(item, scope)
    return """
      <article class="study-card status-edge-{status} schedule-kind-{kind}">
        <div class="study-card-top">
          <span class="subject">{subject}</span>
          {badge}
        </div>
        <h4>{title}</h4>
        <div class="card-meta"><span>{slot}</span>{deferred}{source}</div>
        <div class="link-row">{materials}</div>
      </article>
    """.format(
        status=_e(item.status.value),
        subject=_e(item.subject),
        kind=_e(item.kind.value),
        badge=_badge_html(_schedule_badge_parts(item, anchor_date, mode)),
        title=_e(item.title),
        slot=_e(item.slot),
        deferred=deferred,
        source=_source_link(item.source, output_dir),
        materials=_links(links, output_dir, reference_targets=references),
    )


def _empty(message: str) -> str:
    return (
        '<div class="empty"><span aria-hidden="true">◇</span>'
        "<p>{}</p></div>".format(_e(message))
    )


def _today_section(
    data: DashboardData,
    output_dir: Path,
    mode: PendingBadgeMode,
    scope: MaterialLinkScope = MaterialLinkScope.ALL,
) -> str:
    grouped = defaultdict(list)
    for item in data.today_schedule():
        grouped[(item.learner_id, item.learner_name)].append(item)
    if not grouped:
        return _empty("この日の予定はありません。余白も学習の一部です。")

    columns = []
    for (_, learner_name), items in grouped.items():
        cards = "".join(
            _schedule_card(item, output_dir, data.anchor_date, mode, scope)
            for item in items
        )
        columns.append(
            '<section class="learner-column"><div class="learner-heading">'
            '<span class="avatar">{initial}</span><h3>{name}</h3>'
            '<span>{count}件</span></div>{cards}</section>'.format(
                initial=_e(learner_name[:1] or "学"),
                name=_e(learner_name),
                count=len(items),
                cards=cards,
            )
        )
    return '<div class="today-grid">{}</div>'.format("".join(columns))


def _week_material_links(
    item: ScheduleItem,
    scope: MaterialLinkScope,
) -> List[MaterialLink]:
    """3週間の見通しに出す教材リンク。

    見通しは記号中心の一覧で参考の見た目を作れないため、``《参考:…》`` の
    在庫は出さず、その枠で使う教材（実施項目の教材）だけを出す。
    ``MATERIAL_FILES`` ではさらに教材ファイル（``.html`` / ``.pdf``）へ絞る。

    同じ教材を1つの予定セルで複数回リンクしている場合は1つにまとめる。
    アイコンだけの表示では同じ教材が並んでも見分けられないため。
    **まとめる単位はfragmentを除いたリンク先**＝`ws.html#q1` と `ws.html#q2`
    は同じ教材なので1つにする（残すのは最初に現れたリンク）。
    """

    links = (
        item.material_files()
        if scope == MaterialLinkScope.MATERIAL_FILES
        else item.matching_materials()
    )
    seen = set()
    unique = []
    for link in links:
        target = link.target.split("#", 1)[0]
        if target in seen:
            continue
        seen.add(target)
        unique.append(link)
    return unique


def _week_links(
    item: ScheduleItem,
    output_dir: Path,
    scope: MaterialLinkScope,
) -> str:
    """見通しの1件に並べる、教材を開くアイコンリンク。

    セル幅が狭いので教材名は出さず、``title`` とスクリーンリーダー用の
    テキストに持たせる。コピーbuttonは置かない（コピーは予定カード側で行う）。
    """

    links = _week_material_links(item, scope)
    if not links:
        return ""
    return '<span class="week-item-links">{}</span>'.format(
        "".join(
            '<a class="week-item-link" href="{href}" target="_blank" '
            'rel="noopener" title="教材を開く: {label}">'
            '<span aria-hidden="true">↗</span>'
            '<span class="sr-only">教材を開く: {label}（別タブで開く）</span>'
            "</a>".format(href=_href(link.target, output_dir), label=_e(link.label))
            for link in links
        )
    )


def _week_row(
    data: DashboardData,
    week_start: date,
    label: str,
    row_class: str,
    mode: PendingBadgeMode,
    output_dir: Path,
    scope: MaterialLinkScope = MaterialLinkScope.ALL,
    show_label: bool = False,
    hidden: bool = False,
) -> str:
    end = week_start + timedelta(days=6)
    by_date = defaultdict(list)
    for item in data.schedule_for_week(week_start):
        by_date[item.date].append(item)

    days = []
    for day_offset, weekday in enumerate(WEEKDAYS):
        current = week_start + timedelta(days=day_offset)
        day_class = " week-day-today" if current == data.anchor_date else ""
        cards = ""
        for item in by_date[current]:
            parts = _schedule_badge_parts(item, data.anchor_date, mode)
            icon = parts.icon
            status_label = (
                "反復ルーティン" if parts == ROUTINE_BADGE else parts.label
            )
            category = _week_category(item)
            cards += (
                '<article class="week-item week-category-{category} schedule-kind-{kind}">'
                '<span class="sr-only">予定種別: {category_label}</span>'
                '<span class="week-item-name">{name}</span>'
                '<span class="week-item-title">{title}</span>{materials}'
                '<span class="week-item-state">'
                '<span aria-hidden="true">{icon}</span>'
                '<span class="sr-only">{status_label}</span></span></article>'
            ).format(
                category=_e(category),
                category_label=_e(WEEK_CATEGORY_LABELS[category]),
                kind=_e(item.kind.value),
                name=_e(item.learner_name),
                title=_e(item.title),
                materials=_week_links(item, output_dir, scope),
                icon=_e(icon),
                status_label=_e(status_label),
            )
        if not cards:
            cards = '<span class="week-rest">—</span>'
        days.append(
            '<section class="week-day{day_class}">'
            '<header><span>{weekday}</span><strong>{number}</strong></header>'
            '<div class="week-items">{cards}</div></section>'.format(
                day_class=day_class,
                weekday=_e(weekday),
                number=current.day,
                cards=cards,
            )
        )

    visible_label = ""
    navigation_attributes = ""
    if show_label:
        current_badge = (
            '<span class="week-row-current-badge">今週</span>'
            if week_start == data.week_start
            else ""
        )
        visible_label = (
            '<div class="week-row-label"><strong>{start}週</strong>{badge}</div>'
        ).format(start=_e(_month_day(week_start)), badge=current_badge)
        navigation_attributes = (
            ' data-week-start="{iso_start}" data-period-start="{period_start}" '
            'data-period-end="{period_end}"'
        ).format(
            iso_start=_e(week_start.isoformat()),
            period_start=_e(
                "{}/{}/{}".format(week_start.year, week_start.month, week_start.day)
            ),
            period_end=_e("{}/{}/{}".format(end.year, end.month, end.day)),
        )
    return (
        '<section class="week-row week-row-{row_class}"{navigation_attributes} '
        'aria-label="{label} {start}から{end}"{hidden}>'
        '{visible_label}<div class="week-grid">{days}</div></section>'
    ).format(
        row_class=_e(row_class),
        navigation_attributes=navigation_attributes,
        label=_e(label),
        start=_e(_month_day(week_start)),
        end=_e(_month_day(end)),
        hidden=" hidden" if hidden else "",
        visible_label=visible_label,
        days="".join(days),
    )


def _week_section(
    data: DashboardData,
    mode: PendingBadgeMode,
    output_dir: Path,
    scope: MaterialLinkScope = MaterialLinkScope.ALL,
    navigation_enabled: bool = False,
) -> str:
    if navigation_enabled:
        default_start = data.week_start - timedelta(days=7)
        first_week = min(data.available_week_start, default_start)
        last_week = max(data.available_week_end, data.week_start + timedelta(days=7))
        week_starts = []
        current = first_week
        while current <= last_week:
            week_starts.append(current)
            current += timedelta(days=7)
        rows = [
            _week_row(
                data,
                week_start,
                "{}週{}".format(
                    _month_day(week_start),
                    " 今週" if week_start == data.week_start else "",
                ),
                "current" if week_start == data.week_start else "navigation",
                mode,
                output_dir,
                scope,
                show_label=True,
                hidden=not (default_start <= week_start <= default_start + timedelta(days=14)),
            )
            for week_start in week_starts
        ]
        default_end = default_start + timedelta(days=20)
        controls = (
            '<div class="week-navigation" data-week-navigation hidden '
            'data-default-week-start="{default_start}">'
            '<div class="week-navigation-buttons">'
            '<button type="button" data-week-action="previous" '
            'aria-label="3週間の見通しを1週前へ">← 1週前</button>'
            '<button type="button" data-week-action="reset">今週に戻る</button>'
            '<button type="button" data-week-action="next" '
            'aria-label="3週間の見通しを1週後へ">1週後 →</button></div>'
            '<output class="week-period" data-week-period aria-live="polite">'
            '{period_start}〜{period_end}</output></div>'
            '<p class="week-scope-note">予定と実績はWeb入力元にある範囲だけを表示しています。'
            '実績をアーカイブへ移した過去週は、実施状況未確認になる場合があります。</p>'
        ).format(
            default_start=_e(default_start.isoformat()),
            period_start=_e(
                "{}/{}/{}".format(
                    default_start.year, default_start.month, default_start.day
                )
            ),
            period_end=_e(
                "{}/{}/{}".format(
                    default_end.year, default_end.month, default_end.day
                )
            ),
        )
        return (
            controls
            + '<div class="week-stack" data-week-stack>{}</div>'.format(
                "".join(rows)
            )
            + _week_navigation_script()
        )

    rows = [
        _week_row(
            data,
            data.week_start + timedelta(days=offset),
            label,
            row_class,
            mode,
            output_dir,
            scope,
        )
        for offset, label, row_class in (
            (-7, "前週", "previous"),
            (0, "今週", "current"),
            (7, "翌週", "next"),
        )
    ]
    return '<div class="week-stack">{}</div>'.format("".join(rows))


def _week_navigation_script() -> str:
    """事前生成した週行から3行だけを表示し、1週間ずつ切り替える。"""

    return """
<script>
(function () {
  var navigation = document.querySelector("[data-week-navigation]");
  var stack = document.querySelector("[data-week-stack]");
  if (!navigation || !stack) { return; }
  var rows = Array.prototype.slice.call(stack.querySelectorAll("[data-week-start]"));
  var windowSize = 3;
  if (rows.length < windowSize) { return; }
  var defaultWeekStart = navigation.getAttribute("data-default-week-start");
  var defaultIndex = rows.findIndex(function (row) {
    return row.getAttribute("data-week-start") === defaultWeekStart;
  });
  if (defaultIndex < 0) { defaultIndex = 0; }
  var startIndex = defaultIndex;
  var previous = navigation.querySelector('[data-week-action="previous"]');
  var reset = navigation.querySelector('[data-week-action="reset"]');
  var next = navigation.querySelector('[data-week-action="next"]');
  var period = navigation.querySelector("[data-week-period]");
  if (!previous || !reset || !next || !period) { return; }

  function render() {
    rows.forEach(function (row, index) {
      row.hidden = index < startIndex || index >= startIndex + windowSize;
    });
    previous.disabled = startIndex === 0;
    next.disabled = startIndex + windowSize >= rows.length;
    reset.disabled = startIndex === defaultIndex;
    var first = rows[startIndex];
    var last = rows[startIndex + windowSize - 1];
    period.textContent = first.getAttribute("data-period-start") + "〜" +
      last.getAttribute("data-period-end");
  }

  previous.addEventListener("click", function () {
    if (startIndex > 0) { startIndex -= 1; render(); }
  });
  next.addEventListener("click", function () {
    if (startIndex + windowSize < rows.length) { startIndex += 1; render(); }
  });
  reset.addEventListener("click", function () {
    startIndex = defaultIndex;
    render();
  });
  render();
  navigation.hidden = false;
})();
</script>"""


def _legend_group(title: str, aria_label: str, parts: Iterable[BadgeParts]) -> str:
    return (
        '<div class="week-legend" role="group" aria-label="{aria}">'
        '<span class="week-legend-title">{title}</span>{items}</div>'
    ).format(
        aria=_e(aria_label),
        title=_e(title),
        items="".join(_badge_html(item) for item in parts),
    )


def _status_legend(mode: PendingBadgeMode) -> str:
    """凡例に出す記号。表示していない状態は載せない。"""

    if mode == PendingBadgeMode.MATERIAL_READINESS:
        # 教材状態と実施状態を別の行に分け、どちらの軸かを読み取れるようにする。
        return (
            _legend_group(
                "教材",
                "教材の準備状態の凡例",
                [
                    BadgeParts(
                        "material-" + status.value,
                        MATERIAL_STATUS_ICONS[status],
                        MATERIAL_STATUS_LABELS[status],
                    )
                    for status in (
                        MaterialStatus.AVAILABLE,
                        MaterialStatus.EXTERNAL_AVAILABLE,
                        MaterialStatus.PARTIAL,
                        MaterialStatus.MISSING,
                        MaterialStatus.NOT_REQUIRED,
                    )
                ],
            )
            + _legend_group(
                "実施",
                "実施状態の凡例",
                [
                    BadgeParts(
                        status.value,
                        READINESS_STATUS_ICONS[status],
                        UNCONFIRMED_LABEL
                        if status == Status.UNKNOWN
                        else STATUS_LABELS[status],
                    )
                    for status in (
                        Status.COMPLETED,
                        Status.PARTIAL,
                        Status.MISSED,
                        Status.DEFERRED,
                        Status.UNKNOWN,
                    )
                ],
            )
            + _legend_group(
                "その他", "反復・本番の凡例", [ROUTINE_BADGE, TEST_BADGE]
            )
        )
    statuses = (
        Status.PLANNED,
        Status.READY,
        Status.COMPLETED,
        Status.PARTIAL,
        Status.MISSED,
        Status.UNKNOWN,
        Status.DEFERRED,
    )
    return _legend_group(
        "状態",
        "予定状態の凡例",
        [
            BadgeParts(
                status.value, STATUS_ICONS[status], STATUS_LABELS[status]
            )
            for status in statuses
        ]
        + [ROUTINE_BADGE],
    )


def _week_legend(mode: PendingBadgeMode) -> str:
    status_legend = _status_legend(mode)
    category_legend = (
        '<div class="week-category-legend" role="group" '
        'aria-label="予定種別の色の凡例">'
        '<span class="week-legend-title">色</span>{items}</div>'
    ).format(
        items="".join(
            '<span class="week-category-key">'
            '<span class="week-category-swatch week-category-swatch-{category}" '
            'aria-hidden="true"></span>{label}</span>'.format(
                category=_e(category),
                label=_e(label),
            )
            for category, label in WEEK_CATEGORY_LABELS.items()
        )
    )
    return '<div class="week-legends">{}{}</div>'.format(
        status_legend,
        category_legend,
    )


def _ai_catalog_body(data: DashboardData, catalog: CatalogView) -> str:
    """「こう頼めば、こうなる」の一覧を、この記録の到達状況つきで返す。

    行と文言・判定は ``CatalogView``（既定はstarter形式）から受け取る。
    ``CatalogView.progress`` が ``None`` の場合は判定を持たない表示にし、
    済／未のバッジと「N/M」の集計を出さない。
    """

    progress = catalog.progress(data) if catalog.progress else None

    def _item(item, group) -> str:
        badge = ""
        if progress is None:
            state_class = ""
            icon = "・"
        elif not group.judged:
            state_class = " ai-item-optional"
            icon = "＋"
            badge = "任意"
        else:
            done = bool(progress.get(item.id, False))
            state_class = " ai-item-done" if done else ""
            icon = "✓" if done else "○"
            badge = "済" if done else "未"
        return (
            '<li class="ai-item{state_class}" data-ask="{item_id}">'
            '<span class="ai-item-state" aria-hidden="true">{icon}</span>'
            '<div><p class="ai-ask">「{ask}」</p>'
            '<p class="ai-effect">{effect}</p></div>{badge}</li>'
        ).format(
            item_id=_e(item.id),
            state_class=state_class,
            icon=icon,
            ask=_e(item.ask),
            effect=_e(item.effect),
            badge=(
                '<span class="ai-item-badge">{}</span>'.format(_e(badge))
                if badge
                else ""
            ),
        )

    counted = [item for group in catalog.groups if group.counted for item in group.items]
    counter = ""
    lead = catalog.lead
    if progress is not None and counted:
        done_count = sum(
            1 for item in counted if progress.get(item.id, False)
        )
        counter = (
            '<div class="ai-progress"><span>{label}</span>'
            '<strong>{done}/{total}</strong></div>'
        ).format(
            label=_e(catalog.counter_label),
            done=done_count,
            total=len(counted),
        )
        if done_count == len(counted) and catalog.complete_lead:
            lead = catalog.complete_lead

    blocks = []
    for group in catalog.groups:
        if group.heading:
            blocks.append(
                '<div class="subsection-heading"><h3>{heading}</h3>'
                "{note}</div>".format(
                    heading=_e(group.heading),
                    note=(
                        "<p>{}</p>".format(_e(group.note)) if group.note else ""
                    ),
                )
            )
        blocks.append(
            '<ul class="ai-list">{}</ul>'.format(
                "".join(_item(item, group) for item in group.items)
            )
        )
    return '<p class="scope-note">{lead}</p>{counter}{groups}'.format(
        lead=_e(lead),
        counter=counter,
        groups="".join(blocks),
    )


def _guide_section(guide: GuideView) -> str:
    """画面の読み方を説明する折りたたみ。

    文言は ``GuideView``（既定はstarter形式）から受け取る。ここには運用手順を
    書かない。starter既定の手順の正本は ``docs/AI-OPERATIONS.md`` と
    ``examples/walkthrough.md`` で、この節はWeb画面固有の用語だけを扱う。
    """

    terms = "".join(
        '<div class="guide-term"><dt>{term}</dt><dd>{desc}</dd></div>'.format(
            term=_e(term.term),
            desc=_e(term.description),
        )
        for term in guide.terms
    )
    return (
        '<details class="guide"{open}>'
        '<summary>{summary}</summary>'
        '<div class="guide-body">'
        '<p class="guide-lead">{lead}</p>'
        '<dl class="guide-terms">{terms}</dl>'
        '<p class="guide-lead">{week_note}</p>'
        '<p class="guide-next"><span>次の一歩</span>{next_step}</p>'
        '</div></details>'
    ).format(
        open=" open" if guide.opened else "",
        summary=_e(guide.summary),
        lead=_e(guide.lead),
        terms=terms,
        week_note=_e(guide.week_note),
        next_step=_e(guide.next_step),
    )


def _attention_section(data: DashboardData, output_dir: Path) -> str:
    items = data.attention_items()
    if not items:
        return _empty("今週、確認が必要な予定はありません。")
    rows = []
    for item in items:
        if isinstance(item, ActivityItem):
            next_text = item.next_action or "記録を確認"
        else:
            fallback = (
                "繰越先 {}".format(_month_day(item.deferred_to))
                if item.deferred_to
                else "記録を確認"
            )
            candidates = [
                activity
                for activity in data.activities
                if activity.date == item.date
                and activity.learner_id == item.learner_id
                and activity.subject == item.subject
            ]
            next_text = next(
                (
                    activity.next_action
                    for activity in candidates
                    if activity.next_action
                ),
                fallback,
            )
        rows.append(
            '<article class="attention-row">'
            '<time datetime="{iso}">{date}</time>'
            '<div><strong>{name}・{subject}</strong><span>{title}</span></div>'
            '{badge}<span class="attention-next">{next_text}</span>'
            '{source}</article>'.format(
                iso=item.date.isoformat(),
                date=_e(_month_day(item.date)),
                name=_e(item.learner_name),
                subject=_e(item.subject),
                title=_e(item.title),
                badge=_status_badge(item.status),
                next_text=_e(next_text),
                source=_source_link(item.source, output_dir),
            )
        )
    return '<div class="attention-list">{}</div>'.format("".join(rows))


def _prep_badge(issue: PrepIssue) -> str:
    return (
        '<span class="status status-prep-{issue}">'
        '<span aria-hidden="true">{icon}</span>{label}</span>'
    ).format(
        issue=_e(issue.value),
        icon=_e(PREP_ISSUE_ICONS[issue]),
        label=_e(PREP_ISSUE_LABELS[issue]),
    )


def _unprepared_detail(entry: UnpreparedItem) -> str:
    """不足の内訳。項目を明示したセルでは、項目名と理由を並べる。"""

    if entry.item.material_requirements:
        details = []
        for unprepared in entry.requirements:
            if unprepared.issue == PrepIssue.MISSING_FILE:
                reason = "リンク切れ: {}".format(
                    "・".join(unprepared.missing_labels)
                )
            else:
                reason = PREP_ISSUE_LABELS[PrepIssue.NO_MATERIAL]
            details.append(
                "{}（{}）".format(unprepared.requirement.label, reason)
            )
        return "{}／{}".format(
            MATERIAL_STATUS_LABELS[entry.material_status],
            "／".join(details),
        )
    if entry.issue == PrepIssue.MISSING_FILE:
        return "見つからない教材: {}".format("・".join(entry.missing_labels))
    return "教材リンクなし"


def _unprepared_section(data: DashboardData, output_dir: Path) -> str:
    entries = data.unprepared_items()
    if not entries:
        return _empty("今週・来週で、教材が未準備の予定はありません。")
    rows = []
    for entry in entries:
        item = entry.item
        detail = _unprepared_detail(entry)
        rows.append(
            '<article class="attention-row">'
            '<time datetime="{iso}">{date}</time>'
            '<div><strong>{name}・{subject}</strong><span>{title}</span></div>'
            '{badge}<span class="attention-next">{detail}</span>'
            '{source}</article>'.format(
                iso=item.date.isoformat(),
                date=_e(_month_day(item.date)),
                name=_e(item.learner_name),
                subject=_e(item.subject),
                title=_e(item.title),
                badge=_prep_badge(entry.issue),
                detail=_e(detail),
                source=_source_link(item.source, output_dir),
            )
        )
    return '<div class="attention-list">{}</div>'.format("".join(rows))


def _test_event_section(data: DashboardData, output_dir: Path, extra=None) -> str:
    dated = data.upcoming_tests()
    undated = data.undated_tests()

    cards = []
    for item in dated:
        days_until = (item.date - data.anchor_date).days
        relative = (
            "今日"
            if days_until == 0
            else "明日"
            if days_until == 1
            else "あと{}日".format(days_until)
        )
        note = '<p>{}</p>'.format(_e(item.note)) if item.note else ""
        materials = _links(item.materials, output_dir)
        material_row = (
            '<div class="link-row">{}</div>'.format(materials)
            if materials else ""
        )
        cards.append(
            '<article class="test-event-card">'
            '<div class="test-event-date"><time datetime="{iso}">{date}</time>'
            '<span>{relative}</span></div>'
            '<div class="test-event-body"><div><span class="test-category">{category}</span>'
            '<span class="test-learner">{learner}</span></div>'
            '<h3>{title}</h3>{note}{materials}{extra}</div>{source}</article>'.format(
                iso=item.date.isoformat(),
                date=_e(_month_day(item.date)),
                relative=_e(relative),
                category=_e(item.category),
                learner=_e(item.learner_name),
                title=_e(item.title),
                extra=extra(item, data, output_dir) if extra else "",
                note=note,
                materials=material_row,
                source=_source_link(item.source, output_dir),
            )
        )

    dated_block = (
        '<div class="test-event-list">{}</div>'.format("".join(cards))
        if cards
        else _empty("今後3か月のテスト予定はありません。")
    )

    undated_rows = []
    for item in undated:
        note = (
            '<span class="undated-note">備考: {}</span>'.format(_e(item.note))
            if item.note
            else ""
        )
        undated_rows.append(
            '<li><span>{category}</span><strong>{title}</strong>'
            '<span class="undated-learner">対象: {learner}</span>'
            '{note}{source}</li>'.format(
                category=_e(item.category),
                title=_e(item.title),
                learner=_e(item.learner_name),
                note=note,
                source=_source_link(item.source, output_dir),
            )
        )
    undated_block = ""
    if undated_rows:
        undated_block = (
            '<div class="undated-tests"><h3>日程未定</h3><ul>{}</ul></div>'.format(
                "".join(undated_rows)
            )
        )
    return dated_block + undated_block


def _activity_section(
    activities: List[ActivityItem],
    output_dir: Path,
) -> str:
    if not activities:
        return _empty("実績はまだありません。終わったら短く記録しましょう。")
    cards = []
    for item in activities:
        next_action = ""
        if item.next_action:
            next_action = (
                '<p class="next-action"><span>次</span>{}</p>'.format(
                    _e(item.next_action)
                )
            )
        cards.append(
            '<article class="activity-card"><header>'
            '<div><time datetime="{iso}">{date}</time>'
            '<span>{name}・{subject}</span></div>{badge}</header>'
            '<h3>{title}</h3><p>{summary}</p>{next_action}'
            '<footer><span class="duration">◷ {duration}</span>'
            '<div class="link-row">{materials}</div>{source}</footer></article>'.format(
                iso=item.date.isoformat(),
                date=_e(_month_day(item.date)),
                name=_e(item.learner_name),
                subject=_e(item.subject),
                badge=_status_badge(item.status),
                title=_e(item.title),
                summary=_e(item.result_summary),
                next_action=next_action,
                duration=_e(item.duration or "記録なし"),
                materials=_links(item.materials, output_dir),
                source=_source_link(item.source, output_dir),
            )
        )
    return '<div class="activity-grid">{}</div>'.format("".join(cards))


def _material_section(
    data: DashboardData,
    activities: List[ActivityItem],
    output_dir: Path,
) -> str:
    seen = set()
    links: List[MaterialLink] = []
    for item in list(data.week_schedule()) + list(activities):
        for link in item.materials:
            if link.target not in seen:
                seen.add(link.target)
                links.append(link)
    if not links:
        return _empty("今週リンクされている教材はありません。")
    return '<div class="material-shelf">{}</div>'.format(
        "".join(
            # タイルでもコピーbuttonをリンクの外へ置き、モードによる機能差を作らない。
            '<div class="material-item material-tile-item">'
            '<a class="material-tile" href="{href}" '
            'target="_blank" rel="noopener"><span>教材</span>'
            '<strong>{label}</strong><small>別タブで開く ↗</small></a>{copy}</div>'.format(
                href=_href(link.target, output_dir),
                label=_e(link.label),
                copy=_copy_button(link),
            )
            for link in links
        )
    )


def _page_css() -> str:
    return """
    :root {
      --ink: #24312d; --muted: #68736f; --paper: #fffdf7;
      --canvas: #f4f0e5; --line: #ddd7c8; --green: #1f6a55;
      --green-soft: #e7f1eb; --amber: #c46b28; --rose: #b34b55;
      --blue: #3c6f8f; --subject-japanese:#b34b55; --subject-math:#3c6f8f;
      --subject-science:#1f6a55; --subject-social:#c46b28;
      --subject-kanji:#7772b3; --subject-test:#2f3437; --subject-other:#858b87;
      --shadow: 0 14px 36px rgba(45, 53, 46, .08);
    }
    * { box-sizing: border-box; }
    .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px;
      overflow:hidden; clip:rect(0,0,0,0); clip-path:inset(50%);
      white-space:nowrap; border:0; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0; color: var(--ink); background:
      radial-gradient(circle at 12% 4%, rgba(255,255,255,.9), transparent 28rem),
      var(--canvas); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
      "Hiragino Sans", "Yu Gothic", sans-serif; line-height: 1.6;
    }
    a { color: inherit; }
    .page { max-width: 1240px; margin: auto; padding: 28px 28px 80px; }
    .topbar { display:flex; justify-content:space-between; align-items:center;
      gap:20px; margin-bottom:44px; }
    .brand { display:flex; align-items:center; gap:12px; font-weight:750; }
    .brand-mark { width:38px; height:38px; display:grid; place-items:center;
      color:#fff; background:var(--green); border-radius:12px 12px 12px 4px; }
    .mode-pill { padding:6px 12px; border:1px solid var(--line);
      border-radius:999px; color:var(--muted); font-size:.78rem; background:#fff8; }
    nav { display:flex; gap:6px; flex-wrap:wrap; }
    nav a { text-decoration:none; padding:7px 11px; border-radius:9px;
      color:var(--muted); font-size:.88rem; }
    nav a:hover { color:var(--green); background:var(--green-soft); }
    .hero { display:grid; grid-template-columns:1.5fr 1fr; gap:36px;
      align-items:end; margin-bottom:42px; }
    .eyebrow { color:var(--green); font-size:.82rem; font-weight:800;
      letter-spacing:.12em; text-transform:uppercase; }
    h1 { font-size:clamp(2rem,3.5vw,3rem); font-weight:800;
      line-height:1.04; margin:.12em 0 .18em; letter-spacing:-.055em; }
    .hero p { color:var(--muted); margin:0; }
    .privacy-note { border-left:3px solid var(--green); padding:8px 14px;
      background:rgba(255,255,255,.45); font-size:.84rem; }
    .metrics { display:grid; grid-template-columns:repeat(5,1fr); gap:12px;
      margin-bottom:46px; }
    .metric { background:var(--paper); border:1px solid var(--line);
      border-radius:16px; padding:17px 18px; box-shadow:var(--shadow); }
    .metric span { display:block; color:var(--muted); font-size:.78rem; }
    .metric strong { display:block; font-size:1.75rem; font-weight:700; line-height:1.2;
      white-space:nowrap; }
    .metric small { font-size:.58em; margin-left:2px; }
    section.block { scroll-margin-top:20px; margin-top:54px; }
    .section-heading { display:flex; justify-content:space-between; align-items:end;
      gap:20px; margin-bottom:18px; border-bottom:1px solid var(--line); padding-bottom:12px; }
    .section-heading h2 { font-size:1.55rem; font-weight:700; line-height:1.2; margin:0; }
    .section-heading p { margin:0; color:var(--muted); font-size:.85rem; }
    .subsection-heading { display:flex; justify-content:space-between; align-items:end;
      gap:20px; margin:34px 0 14px; }
    .subsection-heading h3 { font-size:1.05rem; font-weight:700; line-height:1.2; margin:0; }
    .subsection-heading p { margin:0; color:var(--muted); font-size:.8rem; }
    .stale-banner { display:flex; align-items:center; justify-content:center;
      flex-wrap:wrap; gap:10px; padding:10px 16px; background:#fdf1d6;
      border-bottom:1px solid #e6cf9a; color:#6b4e11; font-size:.85rem; }
    .stale-banner button { padding:4px 12px; border:1px solid #c9a441; border-radius:6px;
      background:white; color:#6b4e11; font:inherit; cursor:pointer; }
    .today-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:22px; }
    .learner-column { background:rgba(255,255,255,.42); border:1px solid var(--line);
      border-radius:22px; padding:18px; }
    .learner-heading { display:flex; align-items:center; gap:10px; margin-bottom:12px; }
    .learner-heading h3 { margin:0; flex:1; }
    .learner-heading > span:last-child { color:var(--muted); font-size:.78rem; }
    .avatar { width:34px; height:34px; display:grid; place-items:center;
      border-radius:50%; background:var(--green); color:white; font-weight:800; }
    .study-card { position:relative; overflow:hidden; background:var(--paper);
      border:1px solid var(--line); border-radius:14px; padding:15px 16px;
      margin-top:10px; }
    .study-card::before, .week-item::before { content:""; position:absolute; left:0;
      top:0; bottom:0; width:4px; }
    .study-card::before { background:var(--blue); }
    .status-edge-completed::before { background:var(--green); }
    .status-edge-partial::before, .status-edge-deferred::before { background:var(--amber); }
    .status-edge-missed::before, .status-edge-unknown::before { background:var(--rose); }
    .study-card-top { display:flex; justify-content:space-between; gap:12px; }
    .study-card h4 { font-size:1rem; margin:8px 0; }
    .subject { color:var(--green); font-size:.76rem; font-weight:800; }
    .status { display:inline-flex; align-items:center; gap:5px; padding:2px 8px;
      border-radius:999px; color:var(--blue); background:#eaf1f5; font-size:.7rem;
      font-weight:700; white-space:nowrap; }
    .status-completed { color:var(--green); background:var(--green-soft); }
    .status-partial,.status-deferred { color:#8a4a18; background:#f8e9da; }
    .status-missed,.status-unknown { color:#913945; background:#f7e5e7; }
    .status-routine { color:#4d4a87; background:#ecebfa; }
    .status-test-event { color:#2f3437; background:#e6e8e9; }
    .status-prep-no-material { color:#8a4a18; background:#f8e9da; }
    .status-prep-missing-file { color:#913945; background:#f7e5e7; }
    .status-material-available { color:var(--green); background:var(--green-soft); }
    .status-material-external-available { color:#275f73; background:#e2f1f5; }
    .status-material-partial { color:#8a4a18; background:#f8e9da; }
    .status-material-missing { color:#913945; background:#f7e5e7; }
    .status-material-not-required { color:var(--muted); background:#f1eee4; }
    .schedule-kind-routine.status-edge-planned::before { background:#7772b3; }
    .card-meta { display:flex; align-items:center; flex-wrap:wrap; gap:10px;
      color:var(--muted); font-size:.74rem; }
    .deferred { color:var(--amber); font-weight:700; }
    .link-row { display:flex; gap:7px; flex-wrap:wrap; }
    .study-card .link-row { margin-top:10px; }
    .material-link, .source-link { color:var(--green); font-size:.73rem;
      text-decoration:none; border-bottom:1px solid #9ab8aa; }
    .material-link { display:inline-flex; align-items:center; gap:4px; }
    .material-link-reference { color:var(--muted); border-bottom-style:dotted;
      border-bottom-color:#c3beb2; }
    .material-link-tag { padding:0 5px; border-radius:999px; background:#f1eee4;
      font-size:.62rem; font-weight:800; }
    .material-item { display:inline-flex; align-items:center; gap:6px;
      max-width:100%; }
    .copy-path { flex:none; display:inline-flex; align-items:center;
      justify-content:center; width:30px; height:30px; padding:0; cursor:pointer;
      color:var(--green); background:var(--paper); border:1px solid var(--line);
      border-radius:7px; line-height:0; }
    .copy-path:hover { background:var(--green-soft); }
    .copy-path:focus-visible { outline:2px solid var(--green); outline-offset:1px; }
    .copy-path .copy-path-done, .copy-path.is-copied .copy-path-idle { display:none; }
    .copy-path.is-copied .copy-path-done { display:inline-flex; }
    .copy-path.is-copied { color:var(--paper); background:var(--green);
      border-color:var(--green); }
    .material-tile-item { position:relative; display:flex; }
    .material-tile-item .material-tile { flex:1; }
    .material-tile-item .copy-path { position:absolute; top:12px; right:12px; }
    .copy-path-manual { position:fixed; inset:auto 16px 16px auto; z-index:20;
      display:flex; align-items:center; flex-wrap:wrap; gap:8px; max-width:min(92vw,520px);
      padding:12px 14px; color:var(--ink); font-size:.74rem; background:var(--paper);
      border:1px solid var(--line); border-radius:12px; box-shadow:6px 7px 0 #dcd4c2; }
    .copy-path-manual[hidden] { display:none; }
    .copy-path-manual input { flex:1 1 220px; min-width:0; padding:5px 7px;
      color:var(--ink); font-family:inherit; font-size:.74rem;
      border:1px solid var(--line); border-radius:6px; }
    .copy-path-manual button { padding:5px 10px; color:var(--green);
      font-family:inherit; font-size:.72rem; font-weight:700; cursor:pointer;
      background:var(--paper); border:1px solid var(--line); border-radius:6px; }
    .test-event-list { display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
      gap:12px; }
    .test-event-card { display:grid; grid-template-columns:74px 1fr auto;
      align-items:center; gap:16px; background:var(--paper); border:1px solid var(--line);
      border-radius:16px; padding:15px 16px; box-shadow:0 8px 20px rgba(45,53,46,.05); }
    .test-event-date { display:flex; flex-direction:column; align-items:center;
      border-right:1px solid var(--line); padding-right:14px; }
    .test-event-date time { font-size:1.2rem; font-weight:800; line-height:1.2; }
    .test-event-date span { color:var(--amber); font-size:.72rem; font-weight:800; }
    .test-event-body h3 { font-size:.96rem; margin:5px 0 0; }
    .test-event-body p { color:var(--muted); font-size:.74rem; margin:4px 0 0; }
    .test-category { color:var(--green); background:var(--green-soft);
      border-radius:999px; padding:2px 8px; font-size:.68rem; font-weight:800; }
    .test-learner { color:var(--muted); font-size:.72rem; margin-left:8px; }
    .undated-tests { margin-top:12px; padding:13px 16px; border:1px dashed #c8c1b2;
      border-radius:14px; background:rgba(255,255,255,.35); }
    .undated-tests h3 { margin:0 0 7px; color:var(--muted); font-size:.78rem; }
    .undated-tests ul { display:flex; flex-wrap:wrap; gap:8px 18px; margin:0; padding:0;
      list-style:none; }
    .undated-tests li { display:flex; align-items:center; flex-wrap:wrap; gap:7px;
      font-size:.76rem; }
    .undated-tests li>span:first-child { color:var(--green); font-weight:800; }
    .undated-learner,.undated-note { color:var(--muted); }
    .week-legends { display:grid; gap:7px; margin:-4px 0 12px; }
    .week-legend,.week-category-legend { display:flex; align-items:center;
      flex-wrap:wrap; gap:7px; }
    .week-legend-title { color:var(--muted); font-size:.74rem; font-weight:700;
      margin-right:2px; }
    .week-category-key { display:inline-flex; align-items:center; gap:4px;
      color:var(--muted); font-size:.7rem; font-weight:700; }
    .week-category-swatch { width:4px; height:14px; border-radius:2px;
      background:var(--subject-other); }
    .week-category-swatch-japanese,.week-category-japanese::before {
      background:var(--subject-japanese); }
    .week-category-swatch-math,.week-category-math::before {
      background:var(--subject-math); }
    .week-category-swatch-science,.week-category-science::before {
      background:var(--subject-science); }
    .week-category-swatch-social,.week-category-social::before {
      background:var(--subject-social); }
    .week-category-swatch-kanji,.week-category-kanji::before {
      background:var(--subject-kanji); }
    .week-category-swatch-test,.week-category-test::before {
      background:var(--subject-test); }
    .week-category-other::before { background:var(--subject-other); }
    .week-navigation { display:flex; align-items:center; justify-content:space-between;
      flex-wrap:wrap; gap:12px; margin-bottom:8px; }
    .week-navigation[hidden] { display:none; }
    .week-navigation-buttons { display:flex; flex-wrap:wrap; gap:7px; }
    .week-navigation button { border:1px solid var(--line); border-radius:9px;
      background:var(--paper); color:var(--green); padding:7px 12px; font:inherit;
      font-size:.78rem; font-weight:750; cursor:pointer; }
    .week-navigation button:hover:not(:disabled) { border-color:var(--green);
      background:var(--green-soft); }
    .week-navigation button:focus-visible { outline:2px solid var(--green);
      outline-offset:2px; }
    .week-navigation button:disabled { color:var(--muted); opacity:.45;
      cursor:not-allowed; }
    .week-period { color:var(--ink); font-size:.8rem; font-weight:750; }
    .week-scope-note { margin:0 0 10px; color:var(--muted); font-size:.72rem; }
    .week-stack { overflow-x:auto; background:var(--paper); border:1px solid var(--line);
      border-radius:18px; box-shadow:var(--shadow); }
    .week-row { min-width:840px; border-bottom:1px solid var(--line); }
    .week-row:last-child { border-bottom:0; }
    .week-row-current { background:#f7faf7; }
    .week-row-label { display:flex; align-items:center; gap:8px; padding:7px 12px;
      border-bottom:1px solid var(--line); color:var(--muted); font-size:.72rem; }
    .week-row-label strong { color:var(--ink); font-size:.78rem; }
    .week-row-current-badge { padding:2px 7px; border-radius:999px;
      background:var(--green-soft); color:var(--green); font-size:.65rem;
      font-weight:800; }
    .week-grid { display:grid; grid-template-columns:repeat(7,minmax(120px,1fr)); }
    .week-day { min-height:150px; border-right:1px solid var(--line); padding:12px 9px; }
    .week-day:last-child { border-right:0; }
    .week-day header { display:flex; justify-content:space-between; color:var(--muted);
      font-size:.74rem; padding:0 4px 8px; }
    .week-day header strong { color:var(--ink); font-size:1.12rem;
      font-weight:700; line-height:normal; }
    .week-day-today { background:#eef5f0; }
    .week-day-today header span { color:var(--green); font-weight:800; }
    .week-item { position:relative; display:block; overflow:hidden;
      background:#f7f4eb; border-radius:8px; margin-bottom:7px; padding:7px 20px 7px 10px; }
    .week-item-name { display:block; color:var(--muted); font-size:.63rem; }
    .week-item-title { display:block; font-size:.71rem; line-height:1.35; }
    .week-item-links { display:flex; flex-wrap:wrap; gap:4px; margin-top:5px; }
    .week-item-link { display:inline-flex; align-items:center; justify-content:center;
      width:17px; height:17px; border-radius:6px; background:var(--green-soft);
      color:var(--green); font-size:.62rem; line-height:1; text-decoration:none; }
    .week-item-link:hover { background:var(--green); color:var(--paper); }
    .week-item-link:focus-visible { outline:2px solid var(--green); outline-offset:1px; }
    .week-item-state { position:absolute; right:6px; top:50%; transform:translateY(-50%);
      font-size:.7rem; }
    .week-rest { display:block; text-align:center; color:#c3beb2; padding-top:40px; }
    .attention-list { background:var(--paper); border:1px solid var(--line);
      border-radius:18px; overflow:hidden; }
    .attention-row { display:grid; grid-template-columns:55px 1.3fr auto 100px auto;
      align-items:center; gap:15px; padding:13px 16px; border-bottom:1px solid var(--line); }
    .attention-row:last-child { border:0; }
    .attention-row time { font-size:.9rem; font-weight:700; line-height:normal; }
    .attention-row div span { display:block; color:var(--muted); font-size:.76rem; }
    .attention-next { color:var(--amber); font-size:.72rem; font-weight:700; }
    .activity-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; }
    .activity-card { display:flex; flex-direction:column; min-height:230px;
      background:var(--paper); border:1px solid var(--line); border-radius:17px;
      padding:17px; box-shadow:0 8px 20px rgba(45,53,46,.05); }
    .activity-card header { display:flex; justify-content:space-between; gap:8px; }
    .activity-card header div span { display:block; color:var(--muted); font-size:.72rem; }
    .activity-card time { font-size:1.12rem; font-weight:700; line-height:normal; }
    .activity-card h3 { font-size:.96rem; margin:14px 0 5px; }
    .activity-card > p { color:var(--muted); font-size:.79rem; margin:0; }
    .activity-card footer { display:flex; align-items:end; flex-wrap:wrap; gap:9px;
      margin-top:auto; padding-top:14px; }
    .duration { color:var(--muted); font-size:.72rem; }
    .next-action { border-left:2px solid var(--amber); padding-left:8px; margin-top:10px!important; }
    .next-action span { color:var(--amber); font-weight:800; margin-right:7px; }
    .material-shelf { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }
    .material-tile { min-height:150px; display:flex; flex-direction:column;
      justify-content:space-between; text-decoration:none; color:var(--ink);
      background:var(--paper); border:1px solid var(--line); border-radius:4px 18px 18px 4px;
      padding:17px; box-shadow:6px 7px 0 #dcd4c2; }
    .material-tile > span { color:var(--green); font-size:.65rem; font-weight:800;
      letter-spacing:.12em; }
    .material-tile small { color:var(--muted); }
    .empty { min-height:130px; display:grid; place-items:center; align-content:center;
      border:1px dashed #c8c1b2; border-radius:18px; color:var(--muted); }
    .empty span { color:var(--green); font-size:1.4rem; }
    .empty p { margin:2px 0; font-size:.85rem; }
    .scope-note { margin:-4px 0 16px; color:var(--muted); font-size:.78rem; }
    .guide { background:var(--paper); border:1px solid var(--line); border-radius:18px;
      box-shadow:var(--shadow); margin-bottom:46px; }
    .guide > summary { display:flex; align-items:center; gap:8px; padding:15px 20px;
      color:var(--green); font-size:.88rem; font-weight:800; cursor:pointer;
      list-style:none; }
    .guide > summary::-webkit-details-marker { display:none; }
    .guide > summary::before { content:"?"; width:20px; height:20px; flex:none;
      display:grid; place-items:center; border-radius:50%; background:var(--green-soft);
      font-size:.72rem; }
    .guide[open] > summary { border-bottom:1px solid var(--line); }
    .guide-body { padding:16px 20px 20px; }
    .guide-lead { margin:0; color:var(--muted); font-size:.82rem; }
    .guide-terms { display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
      gap:10px 26px; margin:16px 0; }
    .guide-term dt { color:var(--ink); font-size:.8rem; font-weight:800; }
    .guide-term dd { margin:2px 0 0; color:var(--muted); font-size:.78rem; }
    .guide-next { display:flex; flex-wrap:wrap; gap:8px; margin:16px 0 0;
      border-left:2px solid var(--amber); padding-left:10px; font-size:.8rem; }
    .guide-next span { color:var(--amber); font-weight:800; }
    .ai-progress { display:inline-flex; align-items:center; gap:8px; margin-bottom:14px;
      padding:5px 13px; border:1px solid var(--line); border-radius:999px;
      background:var(--paper); color:var(--muted); font-size:.76rem; }
    .ai-progress strong { color:var(--green); font-size:.9rem; font-weight:800; }
    .ai-list { display:grid; gap:10px; margin:0; padding:0; list-style:none; }
    .ai-item { display:grid; grid-template-columns:26px 1fr auto; align-items:start;
      gap:14px; background:var(--paper); border:1px solid var(--line);
      border-radius:14px; padding:14px 16px; }
    .ai-item-state { width:24px; height:24px; display:grid; place-items:center;
      border-radius:50%; background:#f1eee4; color:var(--muted); font-size:.78rem;
      font-weight:800; }
    .ai-item-done .ai-item-state { background:var(--green-soft); color:var(--green); }
    .ai-item-optional .ai-item-state { background:#ecebfa; color:#4d4a87; }
    .ai-ask { margin:0; font-size:.92rem; font-weight:700; }
    .ai-effect { margin:3px 0 0; color:var(--muted); font-size:.79rem; }
    .ai-item-badge { align-self:center; padding:2px 9px; border-radius:999px;
      background:#f1eee4; color:var(--muted); font-size:.68rem; font-weight:800; }
    .ai-item-done .ai-item-badge { background:var(--green-soft); color:var(--green); }
    .ai-item-optional .ai-item-badge { background:#ecebfa; color:#4d4a87; }
    .footer { margin-top:70px; padding-top:18px; border-top:1px solid var(--line);
      color:var(--muted); font-size:.75rem; display:flex; justify-content:space-between; gap:12px; }
    @media (max-width:900px) {
      .hero { grid-template-columns:1fr; }
      .activity-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .test-event-list { grid-template-columns:1fr; }
      .material-shelf { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .attention-row { grid-template-columns:48px 1fr auto; }
      .attention-next,.attention-row>.source-link { display:none; }
    }
    @media (max-width:620px) {
      .page { padding:20px 16px 60px; }
      .topbar { align-items:flex-start; margin-bottom:34px; }
      nav { display:none; }
      .hero { gap:18px; }
      .metrics { grid-template-columns:repeat(2,1fr); }
      .subsection-heading { align-items:start; flex-direction:column; gap:2px; }
      .guide-terms { grid-template-columns:1fr; }
      .today-grid,.activity-grid,.material-shelf,.test-event-list { grid-template-columns:1fr; }
      .week-grid { grid-template-columns:repeat(7,122px); }
      .week-navigation { align-items:flex-start; flex-direction:column; }
      .section-heading { align-items:start; flex-direction:column; gap:2px; }
      .footer { flex-direction:column; }
    }
    @media print {
      body { background:white; } .page { max-width:none; padding:0; }
      nav,.privacy-note,.stale-banner,.guide,.week-navigation,.week-scope-note {
        display:none; }
      .copy-path,#copy-path-status,.copy-path-manual { display:none !important; }
      .metric,.week-stack,.activity-card,.test-event-card { box-shadow:none; }
      .week-stack { overflow:visible; }
      .week-row { min-width:0; }
      .week-grid { grid-template-columns:repeat(7,minmax(0,1fr)); }
      .week-day { min-width:0; min-height:0; padding:5px 3px; }
      .week-item { padding:4px 13px 4px 6px; }
      .week-item-name { font-size:.5rem; }
      .week-item-title { font-size:.55rem; overflow-wrap:anywhere; }
      .week-item-links { display:none; }
      .week-item-state { right:3px; }
      section.block { break-inside:avoid; }
    }
    """


def _favicon_link(favicon: Optional[str]) -> str:
    """`Presentation.favicon` を `<link rel="icon">` にする。

    未指定なら要素ごと出さない。空のhrefを置くとブラウザがページ自身を
    アイコンとして取りに行くため、「指定しない」と「空」を同じにしない。
    """

    if not favicon:
        return ""
    # 値の妥当性（`data:` URIであること）は `Presentation` 側で担保済み。
    # ここでは前後の空白だけ落として href をそのまま組み立てる。
    return '<link rel="icon" href="{}">\n  '.format(_e(favicon.strip()))


def _auto_reload_script(anchor_date: date) -> str:
    """基準日が実行日のとき、日付が変わったら自動で再読み込みさせる。

    生成物のファイル監視はしない。基準日と閲覧端末のローカル日付がずれたときだけ
    再読み込みし、読み込み直後からずれている場合は再読み込みを繰り返さずに
    告知バーだけを出す。
    """

    return """
  <script>
  (function () {{
    var anchor = "{anchor}";
    var CHECK_MS = 10000;
    var GRACE_MS = 20000;
    function localDate() {{
      var now = new Date();
      var month = now.getMonth() + 1;
      var day = now.getDate();
      return now.getFullYear() + "-" +
        (month < 10 ? "0" : "") + month + "-" + (day < 10 ? "0" : "") + day;
    }}
    function showBanner() {{
      var bar = document.createElement("div");
      bar.className = "stale-banner";
      var text = document.createElement("span");
      text.textContent = "日付が変わりました。表示は " + anchor +
        " 基準のままです。表示内容を再生成してから再読み込みしてください。";
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = "再読み込み";
      button.addEventListener("click", function () {{ location.reload(); }});
      bar.appendChild(text);
      bar.appendChild(button);
      document.body.insertBefore(bar, document.body.firstChild);
    }}
    if (localDate() !== anchor) {{
      showBanner();
      return;
    }}
    var staleSince = null;
    function check() {{
      if (localDate() === anchor) {{
        staleSince = null;
        return;
      }}
      var now = new Date().getTime();
      if (staleSince === null) {{
        staleSince = now;
        return;
      }}
      if (now - staleSince >= GRACE_MS) {{
        location.reload();
      }}
    }}
    setInterval(check, CHECK_MS);
    document.addEventListener("visibilitychange", function () {{
      if (!document.hidden) {{ check(); }}
    }});
  }})();
  </script>""".format(anchor=_e(anchor_date.isoformat()))


def _section_html(section_id: str, heading: str, subheading: str, body: str) -> str:
    return (
        '    <section class="block" id="{id}">\n'
        '      <div class="section-heading"><h2>{heading}</h2>'
        "<p>{subheading}</p></div>\n"
        "      {body}\n"
        "    </section>"
    ).format(
        id=_e(section_id),
        heading=_e(heading),
        subheading=_e(subheading),
        body=body,
    )


def _page_sections(
    data: DashboardData,
    presentation: Presentation,
    activities: List[ActivityItem],
    output_dir: Path,
    attention_extra: AttentionExtra = AttentionExtra(),
) -> List[Tuple[str, str, str]]:
    """画面本体を ``(セクションID, ナビ表記, HTML)`` の並びで組み立てる。

    coreのセクションを並べたうえで、下流が ``Presentation.extra_sections`` で
    足したセクションを ``after`` の直後へ差し込む。同じ位置を指す追加
    セクションは ``extra_sections`` の定義順のまま並べる。挿入位置が見つから
    ない場合と、IDが重複する場合はエラーにする（黙って末尾へ落とさない）。

    ``Presentation.hidden_section_ids`` に挙げたcoreセクションは、本体もナビも
    出さない。非表示にしたセクションを ``after`` に指定した追加セクションは、
    挿入位置なしとしてエラーになる。
    """

    hidden = set(presentation.hidden_section_ids)
    # 非表示のセクションは本文も組み立てない（body は遅延評価）。
    specs = [
        (
            "today",
            "今日",
            "今日の学習",
            "無理なく、終わったら記録",
            lambda: (
                '<p class="scope-note">{}</p>'.format(_e(presentation.routine_note))
                if presentation.routine_note
                else ""
            )
            + _today_section(
                data,
                output_dir,
                presentation.pending_badge_mode,
                presentation.card_material_links,
            ),
        ),
        (
            "tests",
            "テスト予定",
            "今後3か月のテスト予定",
            "予定状態の実施回を日付順に表示",
            lambda: _test_event_section(data, output_dir, presentation.test_event_extra),
        ),
        (
            "week",
            "3週間",
            "3週間の見通し",
            (
                "1週間ずつ前後へ移動できます"
                if presentation.week_navigation_enabled
                else "前週・今週・翌週"
            ),
            lambda: _week_legend(presentation.pending_badge_mode)
            + _week_section(
                data,
                presentation.pending_badge_mode,
                output_dir,
                presentation.card_material_links,
                presentation.week_navigation_enabled,
            ),
        ),
        (
            "attention",
            "要確認",
            "要確認",
            "未実施・一部実施・繰越など",
            lambda: _attention_section(data, output_dir)
            + '<div class="subsection-heading"><h3>教材未準備（今週・来週）</h3>'
            "<p>予定に対して教材が用意できていない枠"
            "（反復ルーティン・テスト本番は対象外）</p></div>"
            + _unprepared_section(data, output_dir)
            + attention_extra.html,
        ),
        (
            "records",
            "実績",
            "最近の実績",
            "所見はMarkdown正本に残します",
            lambda: _activity_section(activities, output_dir),
        ),
        (
            "materials",
            "教材",
            "今週の教材",
            "予定と記録から集約",
            lambda: _material_section(data, activities, output_dir),
        ),
    ]
    catalog = presentation.catalog
    if catalog:
        specs.append(
            (
                "ai",
                catalog.heading,
                catalog.heading,
                catalog.subheading,
                lambda: _ai_catalog_body(data, catalog),
            )
        )
    sections = [
        (
            section_id,
            nav_label,
            _section_html(section_id, heading, subheading, body()),
        )
        for section_id, nav_label, heading, subheading, body in specs
        if section_id not in hidden
    ]

    # 追加セクションの挿入位置（IDごと）。同じ位置を指す追加セクションを
    # 定義順に並べるため、既に入れた分を飛び越してから挿入する。
    anchors = {}

    def _follows(section_id: str, anchor_id: str) -> bool:
        current = anchors.get(section_id)
        while current is not None:
            if current == anchor_id:
                return True
            current = anchors.get(current)
        return False

    for extra in presentation.extra_sections:
        if any(section_id == extra.id for section_id, _, _ in sections):
            raise ValueError(
                "追加セクションのIDが既存と重複しています: {}".format(extra.id)
            )
        position = next(
            (
                index
                for index, (section_id, _, _) in enumerate(sections)
                if section_id == extra.after
            ),
            None,
        )
        if position is None:
            raise ValueError(
                "追加セクションの挿入位置が見つかりません: {} (after={})".format(
                    extra.id, extra.after
                )
            )
        while position + 1 < len(sections) and _follows(
            sections[position + 1][0], extra.after
        ):
            position += 1
        anchors[extra.id] = extra.after
        sections.insert(
            position + 1,
            (
                extra.id,
                extra.nav_label or extra.heading,
                _section_html(
                    extra.id,
                    extra.heading,
                    extra.subheading,
                    extra.body(data, output_dir),
                ),
            ),
        )
    return sections


def render_dashboard(
    data: DashboardData,
    output_path: Path,
    *,
    auto_reload: bool = False,
    presentation: Optional[Presentation] = None,
) -> Path:
    """DashboardDataを静的HTMLとして保存し、保存先を返す。

    ``auto_reload`` は基準日が実行日と一致する生成でだけ有効にする。
    ``--date`` で過去日・未来日を固定した生成では付けない。

    ``presentation`` は画面に出す文言・セクション構成の差し替え点で、省略時は
    ``Presentation.for_mode(data.mode)``＝core既定を使う。契約は
    ``docs/EXTENDING.md`` を参照。
    """

    if presentation is None:
        presentation = Presentation.for_mode(data.mode)
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    today = data.today_schedule()
    completed = sum(
        1
        for item in data.week_schedule()
        if item.kind != ScheduleKind.ROUTINE
        and item.status == Status.COMPLETED
    )
    completed += sum(
        1
        for item in data.activities
        if item.is_test
        and data.week_start <= item.date <= data.week_end
        and item.status == Status.COMPLETED
    )
    # 実績カード・「最近の学習時間」・「今週の教材」は同じ抽出結果を共有する。
    activities = presentation.recent_activities(data)
    minutes = sum(_duration_minutes(item.duration) for item in activities)
    guide = _guide_section(presentation.guide) if presentation.guide else ""
    # 下流の追加分は**1回だけ**取り出し、セクションHTMLと上部の件数で共有する。
    # 2回呼ぶと、下流が重い読み取りをしている場合に二重コストになる。
    attention_extra = (
        presentation.attention_extra(data, output_path.parent)
        if presentation.attention_extra is not None
        else AttentionExtra()
    )
    sections = _page_sections(
        data, presentation, activities, output_path.parent, attention_extra
    )
    nav = "".join(
        '<a href="#{id}">{label}</a>'.format(id=_e(section_id), label=_e(label))
        for section_id, label, _ in sections
    )
    week_label = "{} – {}".format(
        data.week_start.strftime("%Y.%m.%d"),
        data.week_end.strftime("%m.%d"),
    )
    page = """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{title} | {date}</title>
  {favicon}<style>{css}</style>
</head>
<body>
  <main class="page">
    <header class="topbar">
      <div class="brand"><span class="brand-mark" aria-hidden="true">学</span>
        <span>Home Learning Kit</span><span class="mode-pill">{mode}</span></div>
      <nav aria-label="ページ内ナビゲーション">
        {nav}
      </nav>
    </header>
    <section class="hero">
      <div><span class="eyebrow">{week}</span>
        <h1>{title}</h1>
        <p>{date}（{weekday}）の予定と、最近の学び。</p></div>
      <p class="privacy-note">{note}</p>
    </section>
    <section class="metrics" aria-label="今週の概要">
      <div class="metric"><span>今日の予定</span><strong>{today_count}</strong></div>
      <div class="metric"><span>今週の完了</span><strong>{completed}</strong></div>
      <div class="metric"><span>要確認</span><strong>{attention}</strong></div>
      <div class="metric"><span>教材未準備</span><strong>{unprepared}</strong></div>
      <div class="metric"><span>最近の学習時間</span><strong>{minutes}<small>分</small></strong></div>
    </section>
    {guide}
{sections}
    <footer class="footer"><span>Markdownから生成した読み取り専用ビュー</span>
      <span>基準日 {date}</span></footer>
  </main>{copy_support}{auto_reload}
</body>
</html>
""".format(
        title=_e(data.title),
        date=_e(data.anchor_date.isoformat()),
        weekday=_e(WEEKDAYS[data.anchor_date.weekday()]),
        week=_e(week_label),
        mode=_e(presentation.mode_label),
        note=_e(presentation.privacy_note),
        today_count=len(today),
        completed=completed,
        attention=len(data.attention_items()) + attention_extra.count,
        unprepared=len(data.unprepared_items()),
        minutes=minutes,
        guide=guide,
        nav=nav,
        favicon=_favicon_link(presentation.favicon),
        sections="\n".join(html for _, _, html in sections),
        css=_page_css(),
        copy_support=_copy_support(),
        auto_reload=_auto_reload_script(data.anchor_date) if auto_reload else "",
    )
    output_path.write_text(page, encoding="utf-8")
    return output_path
