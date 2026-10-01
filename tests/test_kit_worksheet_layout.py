"""解答配置の明示・赤シート構造・従来契約の維持（夜間CI）。"""

from pathlib import Path
import tempfile
import unittest

from kit.starter_web.checkup import check_worksheet, check_repo
from kit.starter_web.worksheet_layout import WorksheetLayout


HTML = '''<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="material-type" content="worksheet">
<meta name="answer-layout" content="red-sheet">
<title>確認カード</title><style>@page { size: A4; }</style></head><body>
<p class="red-sheet-instructions">赤シートで隠してから外して確認します。</p>
<ol class="problems"><li>2 + 3 = <span class="answers red-sheet">5</span></li></ol>
</body></html>'''
DECLARATION = '<meta name="answer-layout" content="red-sheet">'


class WorksheetLayoutTest(unittest.TestCase):
    def check(self, text, strict=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'card.html'
            path.write_text(text, encoding='utf-8')
            return check_worksheet(path, strict=strict)

    def test_red_sheet_passes_with_default_and_forced_strict(self):
        self.assertEqual(self.check(HTML), [])
        self.assertEqual(self.check(HTML, True), [])

    def test_missing_or_separate_declaration_keeps_page_break_requirement(self):
        for text in [HTML.replace(DECLARATION, ''), HTML.replace('content="red-sheet"', 'content="separate-pages"')]:
            self.assertTrue(any('改ページ' in issue for issue in self.check(text)))

    def test_unknown_empty_duplicate_and_body_declarations_fail(self):
        variants = [HTML.replace('content="red-sheet"', 'content="inline"'),
                    HTML.replace('content="red-sheet"', 'content=""'),
                    HTML.replace(DECLARATION, DECLARATION * 2),
                    HTML.replace(DECLARATION, '').replace('<body>', '<body>' + DECLARATION),
                    HTML.replace('content="red-sheet"', 'content="red-sheet" content="separate-pages"')]
        for text in variants:
            with self.subTest(text=text):
                self.assertTrue(any('answer-layout' in issue for issue in self.check(text)))

    def test_comment_script_and_template_meta_do_not_enable_red_sheet(self):
        for wrapper in ['<!-- {} -->', '<script>{}</script>', '<template>{}</template>']:
            text = HTML.replace(DECLARATION, wrapper.format(DECLARATION))
            self.assertEqual(WorksheetLayout(text).layout, 'separate-pages')
            self.assertTrue(any('改ページ' in issue for issue in self.check(text)))

    def test_reference_cannot_declare_answer_layout(self):
        self.assertTrue(any('worksheet教材だけ' in issue for issue in self.check(HTML.replace('content="worksheet"', 'content="reference"'))))

    def test_base_requirements_are_not_skipped(self):
        for text in [HTML.replace('@page { size: A4; }', ''), HTML.replace('<!doctype html>', ''),
                     HTML.replace('</head>', '<link rel="stylesheet" href="https://example.invalid/a.css"></head>'),
                     HTML.replace('</body>', '<img src="/a.png" alt="図"></body>')]:
            self.assertTrue(self.check(text))

    def test_answers_must_be_marked_nonempty_and_inside_problems(self):
        variants = [HTML.replace('answers red-sheet', 'answers'), HTML.replace('>5</span>', '></span>'),
                    HTML.replace('<span class="answers red-sheet">5</span>', '').replace('</body>', '<span class="answers red-sheet">5</span></body>')]
        for text in variants:
            self.assertTrue(any('各answers' in issue for issue in self.check(text)))

    def test_each_answer_is_checked_and_nested_ruby_text_is_kept(self):
        text = HTML.replace('>5</span>', '><ruby>五<rt>ご</rt></ruby></span>')
        self.assertEqual(self.check(text), [])
        text = text.replace('</li>', '<span class="answers">6</span></li>')
        self.assertTrue(any('各answers' in issue for issue in self.check(text)))

    def test_fake_or_hidden_answer_elements_do_not_count(self):
        answer = '<span class="answers red-sheet">5</span>'
        for replacement in ['<!-- ' + answer + ' -->', '<script>' + answer + '</script>',
                            '<style>/* ' + answer + ' */</style>', '<template>' + answer + '</template>',
                            '<div hidden>' + answer + '</div>']:
            self.assertTrue(any('解答要素' in issue for issue in self.check(HTML.replace(answer, replacement))))

    def test_answer_text_alone_does_not_count_as_a_problem(self):
        self.assertTrue(any('空でない問題' in issue for issue in self.check(HTML.replace('2 + 3 = ', ''))))

    def test_instruction_must_be_present_and_nonempty(self):
        for text in [HTML.replace('class="red-sheet-instructions"', 'class="other"'),
                     HTML.replace('赤シートで隠してから外して確認します。', ' '),
                     HTML.replace('class="red-sheet-instructions"', 'hidden class="red-sheet-instructions"')]:
            self.assertTrue(any('使い方' in issue for issue in self.check(text)))

    def test_new_template_passes_and_declares_red_sheet(self):
        path = Path(__file__).resolve().parents[1] / 'templates/worksheet-red-sheet.html'
        self.assertEqual(check_worksheet(path, strict=True), [])
        self.assertEqual(WorksheetLayout(path.read_text()).layout, 'red-sheet')

    def test_repo_checks_the_red_sheet_template(self):
        from test_kit_checkup import make_public_tree, write
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_public_tree(root)
            write(root / 'templates/worksheet-red-sheet.html', HTML.replace(DECLARATION, ''))
            self.assertTrue(any('赤シート雛形' in issue for issue in check_repo(root)))

    def test_browser_repaired_markup_is_rejected(self):
        problem = '<ol class="problems"><li>2 + 3 = <span class="answers red-sheet">5</span></li></ol>'
        answer = '<span class="answers red-sheet">5</span>'
        variants = {
            'implicit-p-close': '<p class="problems">2 + 3 = <div class="answers red-sheet">5</div>',
            'balanced-invalid-p': '<p class="problems">2 + 3 = <div class="answers red-sheet">5</div></p>',
            'implicit-li-close': '<ol><li class="problems">2 + 3 = <li class="answers red-sheet">5</ol>',
            'balanced-invalid-li': '<ol><li class="problems">2 + 3 = <li class="answers red-sheet">5</li></li></ol>',
            'implicit-dd-close': '<dl><dt class="problems">2 + 3 = <dd class="answers red-sheet">5</dl>',
            'implicit-td-close': '<table><tbody><tr><td class="problems">2 + 3 = <td class="answers red-sheet">5</tr></tbody></table>',
            'foster-parented-answer': '<table class="problems"><div>2 + 3 = ' + answer + '</div></table>',
            'foster-parented-text': '<table class="problems">2 + 3 = <tbody><tr><td>' + answer + '</td></tr></tbody></table>',
            'implicit-tbody': '<table class="problems"><tr><td>2 + 3 = ' + answer + '</td></tr></table>',
            'ignored-self-close': '<div class="problems">2 + 3 = <span hidden/>' + answer + '</div>',
            'mismatched-close': '<div class="problems"><b>2 + 3 = </div>' + answer + '</b>',
            'unclosed-at-eof': '<div class="problems">2 + 3 = ' + answer,
            'nested-anchor': '<a class="problems">2 + 3 = <a class="answers red-sheet">5</a></a>',
            'nested-ruby-annotation': '<ruby class="problems">2 + 3 = <rt>ご<rt class="answers red-sheet">5</rt></rt></ruby>',
            'table-in-caption': '<table><caption class="problems">2 + 3 = <table><tbody><tr><td>' + answer + '</td></tr></tbody></table></caption></table>',
            'unsupported-select': '<select class="problems">2 + 3 = ' + answer + '</select>',
            'unsupported-foreign-content': '<svg class="problems"><text>2 + 3 = ' + answer + '</text></svg>',
            'duplicate-class': '<div class="other" class="problems">2 + 3 = ' + answer + '</div>',
        }
        for name, replacement in variants.items():
            with self.subTest(name=name):
                self.assertTrue(any('赤シートHTML構造' in issue for issue in self.check(HTML.replace(problem, replacement))))

    def test_explicit_lists_tables_paragraphs_and_void_elements_pass(self):
        problem = '<ol class="problems"><li>2 + 3 = <span class="answers red-sheet">5</span></li></ol>'
        answer = '<span class="answers red-sheet"><ruby>五<rp>（</rp><rt>ご</rt><rp>）</rp></ruby></span>'
        for replacement in [
            '<div class="problems"><p>2 + 3 = ' + answer + '</p></div>',
            '<ol class="problems"><li>計算<ul><li>2 + 3 = ' + answer + '</li></ul></li></ol>',
            '<dl class="problems"><dt>2 + 3 = </dt><dd>' + answer + '</dd></dl>',
            '<table class="problems"><caption>計算</caption><tbody><tr><td>2 + 3 = ' + answer + '</td></tr></tbody></table>',
            '<div class="problems">2 + 3 = <br/><input type="text">' + answer + '</div>',
        ]:
            with self.subTest(replacement=replacement):
                self.assertEqual(self.check(HTML.replace(problem, replacement)), [])

    def test_red_sheet_requires_one_explicit_document_structure(self):
        for text in [HTML.replace('<body>', ''), HTML.replace('</body>', ''),
                     HTML.replace('<body>', '<body><body>'), HTML.replace('</body>', '</body></body>'),
                     HTML + '<body></body>']:
            with self.subTest(text=text):
                self.assertTrue(any('赤シートHTML構造' in issue for issue in self.check(text)))

    def test_new_structure_restrictions_do_not_change_separate_pages(self):
        text = HTML.replace(DECLARATION, '').replace('</style>', '.answers { break-before: page; }</style>')
        text = text.replace('</li>', '').replace('</body>', '<select><option>例</select></body>')
        self.assertEqual(self.check(text), [])

    def test_answer_visibility_button_is_supported(self):
        text = HTML.replace('<body>', '<body><div class="toolbar"><button type="button" id="toggle">答えを隠す／見せる</button></div>')
        self.assertEqual(self.check(text), [])

    def test_nested_buttons_are_rejected_even_with_balanced_tags(self):
        text = HTML.replace('<body>', '<body><button>外側<span><button>内側</button></span></button>')
        self.assertTrue(any('button要素を入れ子にできません' in issue for issue in self.check(text)))
