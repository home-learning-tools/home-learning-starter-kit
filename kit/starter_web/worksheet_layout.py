"""ワークシートの解答配置宣言と、赤シート用の実要素を読む。"""

from html.parser import HTMLParser
from typing import List


ANSWER_LAYOUTS = ("separate-pages", "red-sheet")
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
             "meta", "param", "source", "track", "wbr"}


# HTMLParser is a tokenizer, not an HTML5 tree builder. Red-sheet validation
# deliberately accepts a conservative, explicitly nested HTML subset instead
# of guessing the browser's implied closing tags / tree repair.
PHRASING_TAGS = set("a abbr b bdi bdo br button cite code data dfn em i img input kbd mark "
                    "q rp rt ruby s samp small span strong sub sup time u var wbr".split())
PHRASING_PARENTS = (PHRASING_TAGS - VOID_TAGS) | set("p pre h1 h2 h3 h4 h5 h6".split())
FLOW_TAGS = PHRASING_TAGS | set("address article aside blockquote div dl figure figcaption "
                               "footer h1 h2 h3 h4 h5 h6 header hr main nav ol p pre "
                               "section table ul".split())
CHILDREN = {
    None: {"html"}, "html": {"head", "body"},
    "head": {"meta", "link", "title", "style", "script"},
    "title": set(), "style": set(), "script": set(),
    "ol": {"li"}, "ul": {"li"}, "dl": {"dt", "dd"},
    "table": {"caption", "colgroup", "thead", "tbody", "tfoot"},
    "colgroup": {"col"}, "thead": {"tr"}, "tbody": {"tr"}, "tfoot": {"tr"},
    "tr": {"td", "th"},
}
SUPPORTED_TAGS = (FLOW_TAGS | set(CHILDREN) | set().union(*CHILDREN.values())) - {None}
ELEMENT_ONLY = {None, "html", "head", "ol", "ul", "dl", "table", "colgroup",
                "thead", "tbody", "tfoot", "tr"}


class WorksheetLayout(HTMLParser):
    """コメント・script・style・templateを教材の構造と数えない。"""

    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.declarations = []
        self.errors: List[str] = []
        self.structure_errors: List[str] = []
        self.document_tags = []
        self.problems = []
        self.answers = []
        self.instructions = []
        self.feed(text)
        self.close()
        if self.stack:
            self.structure_error("終了タグが不足しています: " + ", ".join(f["tag"] for f in self.stack))
        if self.document_tags != ["html", "head", "body"]:
            self.structure_error("html・head・bodyをこの順序で1つずつ明示してください")
        self.layout = "separate-pages"
        if len(self.declarations) > 1:
            self.errors.append("answer-layout宣言が重複しています")
        elif self.declarations:
            value = self.declarations[0]
            if value not in ANSWER_LAYOUTS:
                self.errors.append("answer-layoutはseparate-pages / red-sheetを指定してください")
            elif not self.errors:
                self.layout = value

    def structure_error(self, message):
        self.structure_errors.append("赤シートHTML構造: " + message)

    def validate_starttag(self, tag, attrs):
        parent = self.stack[-1]["tag"] if self.stack else None
        ancestors = [f["tag"] for f in self.stack]
        if tag in {"html", "head", "body"}:
            self.document_tags.append(tag)
        if tag not in SUPPORTED_TAGS:
            self.structure_error("対応していない要素です: " + tag)
        allowed = CHILDREN.get(parent, PHRASING_TAGS if parent in PHRASING_PARENTS else FLOW_TAGS)
        # script/style are inert for worksheet text, but still need explicit ends.
        if tag not in allowed and not (tag in {"script", "style"} and parent == "body"):
            self.structure_error("<{}>の直下に<{}>は置けません".format(parent, tag))
        if tag in {"a", "button", "ruby"} and tag in ancestors:
            self.structure_error(tag + "要素を入れ子にできません")
        if tag in {"rt", "rp"} and parent != "ruby":
            self.structure_error(tag + "要素はrubyの直下に置いてください")
        if tag == "table" and "caption" in ancestors:
            self.structure_error("caption内にtableは置けません")
        if len(dict(attrs)) != len(attrs):
            self.structure_error("属性が重複しています: " + tag)

    def handle_starttag(self, tag, attrs):
        self.validate_starttag(tag, attrs)
        values = dict(attrs)
        ignored = tag in {"script", "style", "template"} or any(f["ignored"] for f in self.stack)
        hidden = "hidden" in values or any(f["hidden"] for f in self.stack)
        classes = set((values.get("class") or "").split())
        in_body = any(f["tag"] == "body" for f in self.stack)
        if tag == "meta" and not ignored and (values.get("name") or "").lower() == "answer-layout":
            value = (values.get("content") or "").strip().lower()
            self.declarations.append(value)
            if in_body or not any(f["tag"] == "head" for f in self.stack):
                self.errors.append("answer-layout宣言はhead内に置いてください")
            if len(values) != len(attrs):
                self.errors.append("answer-layout宣言の属性が重複しています")
        frame = {"tag": tag, "classes": classes, "ignored": ignored, "hidden": hidden, "has_text": False}
        if in_body and not ignored and not hidden:
            if "problems" in classes:
                self.problems.append(frame)
            if "answers" in classes:
                frame["inside_problems"] = any("problems" in f["classes"] and not f["ignored"] and not f["hidden"]
                                               for f in self.stack)
                self.answers.append(frame)
            if "red-sheet-instructions" in classes:
                self.instructions.append(frame)
        if tag not in VOID_TAGS:
            self.stack.append(frame)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.structure_error("void要素以外は自己終了できません: " + tag)
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1]["tag"] != tag:
            self.structure_error("終了タグを省略せず、開始タグと逆順で閉じてください: " + tag)
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        parent = self.stack[-1]["tag"] if self.stack else None
        if data.strip() and parent in ELEMENT_ONLY:
            self.structure_error("<{}>直下に本文は置けません".format(parent))
        if any(f["ignored"] or f["hidden"] for f in self.stack):
            return
        if data.strip():
            in_answer = any("answers" in f["classes"] for f in self.stack)
            for frame in self.stack:
                if "problems" in frame["classes"] and in_answer:
                    continue  # 解答だけを問題本文の代わりに数えない。
                frame["has_text"] = True

    def red_sheet_errors(self) -> List[str]:
        """光学的な隠れ方は判定せず、明示した配置と説明の構造を照合する。"""
        issues = list(dict.fromkeys(self.structure_errors))
        if not self.problems or not all(p["has_text"] for p in self.problems):
            issues.append('赤シート形式には空でない問題要素（class="problems"）が必要です')
        if not self.answers:
            issues.append('赤シート形式には解答要素（class="answers red-sheet"）が必要です')
        elif any("red-sheet" not in a["classes"] or not a["inside_problems"] or not a["has_text"]
                 for a in self.answers):
            issues.append("赤シート形式の各answersはproblems内に置き、red-sheet classと空でない解答を付けてください")
        if not self.instructions or not all(i["has_text"] for i in self.instructions):
            issues.append('赤シートの使い方（class="red-sheet-instructions"）が必要です')
        return issues
