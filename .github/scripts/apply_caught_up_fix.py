from __future__ import annotations

from pathlib import Path


def read_lines(path: str) -> list[str]:
    return Path(path).read_text(encoding="utf-8").splitlines()


def write_lines(path: str, lines: list[str]) -> None:
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def find_line(lines: list[str], value: str, start: int = 0) -> int:
    try:
        return lines.index(value, start)
    except ValueError as exc:
        raise SystemExit(f"missing line marker: {value!r}") from exc


def replace_range(
    lines: list[str],
    start_marker: str,
    end_marker: str,
    replacement: list[str],
) -> None:
    start = find_line(lines, start_marker)
    end = find_line(lines, end_marker, start + 1)
    lines[start:end] = replacement


def update_browser() -> None:
    path = "apps/following_auto_liker/browser.py"
    lines = read_lines(path)

    caught_up_phrases = [
        "CAUGHT_UP_PHRASES = (",
        "    # English",
        '    "you\'re all caught up",',
        '    "you’re all caught up",',
        "    # Korean",
        '    "모두 확인했습니다",',
        '    "새 게시물을 모두 확인했습니다",',
        '    "최신 게시물을 모두 확인했습니다",',
        "    # Japanese",
        '    "すべてチェック済みです",',
        '    "最新の投稿は以上です",',
        '    "新しい投稿は以上です",',
        '    "すべての新しい投稿をチェックしました",',
        "    # Spanish",
        '    "estás al día",',
        '    "ya estás al día",',
        '    "has visto todas las publicaciones nuevas",',
        '    "ya viste todas las publicaciones nuevas",',
        "    # French",
        '    "vous êtes à jour",',
        '    "vous avez tout vu",',
        '    "vous avez vu toutes les nouvelles publications",',
        "    # German",
        '    "du bist auf dem neuesten stand",',
        '    "du hast alles gesehen",',
        '    "du hast alle neuen beiträge gesehen",',
        "    # Portuguese",
        '    "você está em dia",',
        '    "você viu tudo",',
        '    "você viu todas as publicações novas",',
        "    # Russian",
        '    "вы всё просмотрели",',
        '    "вы все просмотрели",',
        '    "вы просмотрели все новые публикации",',
        "    # Simplified / Traditional Chinese",
        '    "以上是最新动态",',
        '    "以上是最新動態",',
        ")",
    ]
    replace_range(
        lines,
        "CAUGHT_UP_PHRASES = (",
        "DISMISS_BUTTON_LABELS = (",
        caught_up_phrases,
    )

    boundary_constant_marker = '_CAUGHT_UP_BOUNDARY_SCRIPT = r"""'
    if boundary_constant_marker not in lines:
        class_index = find_line(lines, "class ChromeBrowserSession:")
        boundary_script = [
            '_CAUGHT_UP_BOUNDARY_SCRIPT = r"""',
            "(phrases) => {",
            "  const normalize = value =>",
            '    (value || "").replace(/\\s+/g, " ").trim().toLocaleLowerCase();',
            "  const wanted = phrases.map(normalize);",
            '  const root = document.querySelector("main") || document.body;',
            "  const isVisible = element => {",
            "    const style = window.getComputedStyle(element);",
            "    const rect = element.getBoundingClientRect();",
            "    return (",
            '      style.display !== "none" &&',
            '      style.visibility !== "hidden" &&',
            '      style.opacity !== "0" &&',
            "      rect.width > 0 &&",
            "      rect.height > 0",
            "    );",
            "  };",
            "",
            "  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);",
            "  let marker = null;",
            "  while (walker.nextNode()) {",
            "    const textNode = walker.currentNode;",
            "    const parent = textNode.parentElement;",
            '    if (!parent || parent.closest("article") || !isVisible(parent)) continue;',
            "    const text = normalize(textNode.nodeValue);",
            "    if (text && wanted.some(phrase => text.includes(phrase))) {",
            "      marker = parent;",
            "      break;",
            "    }",
            "  }",
            "",
            '  const articles = [...document.querySelectorAll("article")];',
            "  if (!marker) return {found: false, boundary: articles.length};",
            "",
            "  let boundary = articles.length;",
            "  for (let index = 0; index < articles.length; index += 1) {",
            "    const relation = marker.compareDocumentPosition(articles[index]);",
            "    if (relation & Node.DOCUMENT_POSITION_FOLLOWING) {",
            "      boundary = index;",
            "      break;",
            "    }",
            "  }",
            "  return {found: true, boundary};",
            "}",
            '"""',
            "",
            "",
        ]
        lines[class_index:class_index] = boundary_script

    boundary_method_marker = (
        "    def posts_before_caught_up(self) -> tuple[list[PlaywrightFeedPost], bool]:"
    )
    if boundary_method_marker not in lines:
        scroll_index = find_line(lines, "    def scroll_for_more(self) -> bool:")
        method = [
            boundary_method_marker,
            '        """Return only posts above the first visible caught-up marker."""',
            "        self._dismiss_common_dialogs()",
            "        marker_found, boundary = self._caught_up_boundary()",
            "        try:",
            '            articles = self.page.locator("article")',
            "            count = articles.count()",
            "        except Exception as exc:",
            "            self.session.raise_browser_error(exc)",
            "",
            "        limit = min(count, boundary) if marker_found else count",
            "        posts = []",
            "        for index in range(limit):",
            "            post = PlaywrightFeedPost(self.session, articles.nth(index))",
            "            if post.key:",
            "                posts.append(post)",
            "        return posts, marker_found",
            "",
        ]
        lines[scroll_index:scroll_index] = method

    caught_method_start = find_line(lines, "    def is_caught_up(self) -> bool:")
    next_static = caught_method_start + 1
    while next_static < len(lines):
        if (
            lines[next_static] == "    @staticmethod"
            and next_static + 1 < len(lines)
            and lines[next_static + 1].startswith("    def _is_following_url")
        ):
            break
        next_static += 1
    if next_static >= len(lines):
        raise SystemExit("could not find _is_following_url after is_caught_up")

    caught_methods = [
        "    def is_caught_up(self) -> bool:",
        '        """Match the localized feed marker outside articles."""',
        "        found, _boundary = self._caught_up_boundary()",
        "        return found",
        "",
        "    def _caught_up_boundary(self) -> tuple[bool, int]:",
        "        try:",
        "            result = self.page.evaluate(",
        "                _CAUGHT_UP_BOUNDARY_SCRIPT,",
        "                list(CAUGHT_UP_PHRASES),",
        "            )",
        "        except Exception as exc:",
        "            self.session.raise_browser_error(exc)",
        "",
        "        if not isinstance(result, dict):",
        "            return False, 0",
        '        found = bool(result.get("found"))',
        "        try:",
        '            boundary = max(0, int(result.get("boundary", 0)))',
        "        except (TypeError, ValueError):",
        "            boundary = 0",
        "        return found, boundary",
        "",
    ]
    lines[caught_method_start:next_static] = caught_methods
    write_lines(path, lines)


def update_engine() -> None:
    path = "apps/following_auto_liker/engine.py"
    lines = read_lines(path)

    protocol_method = (
        "    def posts_before_caught_up(self) -> tuple[Iterable[FeedPost], bool]: ..."
    )
    if protocol_method not in lines:
        posts_index = find_line(lines, "    def posts(self) -> Iterable[FeedPost]: ...")
        lines[posts_index + 1 : posts_index + 1] = ["", protocol_method]

    loop_index = find_line(lines, "            for post in feed.posts():")
    lines[loop_index : loop_index + 1] = [
        "            batch_posts, caught_up_visible = feed.posts_before_caught_up()",
        "            for post in batch_posts:",
    ]

    caught_index = find_line(lines, "            if feed.is_caught_up():")
    lines[caught_index] = "            if caught_up_visible or feed.is_caught_up():"
    write_lines(path, lines)


def update_tests() -> None:
    path = "tests/regression/test_following_auto_liker.py"
    lines = read_lines(path)

    init_old = "    def __init__(self, pages, *, caught_up_at=None):"
    init_new = (
        "    def __init__(self, pages, *, caught_up_at=None, caught_up_boundaries=None):"
    )
    init_index = find_line(lines, init_old)
    lines[init_index] = init_new
    assignment_index = find_line(lines, "        self.caught_up_at = caught_up_at", init_index)
    lines.insert(
        assignment_index + 1,
        "        self.caught_up_boundaries = caught_up_boundaries or {}",
    )

    fake_boundary_marker = "    def posts_before_caught_up(self):"
    if fake_boundary_marker not in lines:
        scroll_index = find_line(lines, "    def scroll_for_more(self):")
        method = [
            fake_boundary_marker,
            "        posts = self.posts()",
            "        boundary = self.caught_up_boundaries.get(self.index)",
            "        if boundary is None:",
            "            return posts, self.is_caught_up()",
            "        return posts[: max(0, int(boundary))], True",
            "",
        ]
        lines[scroll_index:scroll_index] = method

    scanner_test_marker = (
        "    def test_caught_up_boundary_skips_older_posts_loaded_in_same_page(self):"
    )
    if scanner_test_marker not in lines:
        insertion_index = find_line(
            lines,
            "    def test_restriction_after_like_stops_immediately_and_preserves_count(self):",
        )
        test = [
            scanner_test_marker,
            '        recent = FakePost("/p/recent/")',
            '        older = FakePost("/p/older/")',
            "        feed = FakeFeed(",
            "            [[recent, older]],",
            "            caught_up_boundaries={0: 1},",
            "        )",
            "",
            "        summary = self.scanner().scan_once(feed)",
            "",
            "        self.assertTrue(summary.caught_up)",
            "        self.assertEqual(summary.discovered, 1)",
            "        self.assertEqual(summary.liked, 1)",
            "        self.assertEqual(recent.clicks, 1)",
            "        self.assertEqual(older.clicks, 0)",
            "",
        ]
        lines[insertion_index:insertion_index] = test

    locale_test_marker = (
        "    def test_caught_up_markers_are_recognized_in_every_supported_locale(self):"
    )
    if locale_test_marker not in lines:
        main_index = find_line(lines, 'if __name__ == "__main__":')
        tests = [
            locale_test_marker,
            "        feed = PlaywrightFollowingFeed(self.session)",
            "        phrases = (",
            '            "You\'re all caught up",',
            '            "모두 확인했습니다",',
            '            "すべてチェック済みです",',
            '            "Estás al día",',
            '            "Vous êtes à jour",',
            '            "Du bist auf dem neuesten Stand",',
            '            "Você está em dia",',
            '            "Вы всё просмотрели",',
            '            "以上是最新动态",',
            '            "以上是最新動態",',
            "        )",
            "        for phrase in phrases:",
            "            with self.subTest(phrase=phrase):",
            "                self.page.set_content(",
            '                    f"<main><div id=\'caught-up\'>{phrase}</div></main>"',
            "                )",
            "                self.assertTrue(feed.is_caught_up())",
            "",
            "    def test_posts_before_caught_up_excludes_articles_below_marker(self):",
            "        self.page.set_content(",
            '            """',
            "            <main>",
            '              <article><a href="/p/recent/">recent</a></article>',
            '              <div id="caught-up">Estás al día</div>',
            '              <article><a href="/p/older/">older</a></article>',
            "            </main>",
            '            """',
            "        )",
            "        feed = PlaywrightFollowingFeed(self.session)",
            "",
            "        posts, caught_up = feed.posts_before_caught_up()",
            "",
            "        self.assertTrue(caught_up)",
            '        self.assertEqual([post.key for post in posts], ["/p/recent/"])',
            "",
            "",
        ]
        lines[main_index:main_index] = tests

    write_lines(path, lines)


def main() -> None:
    update_browser()
    update_engine()
    update_tests()


if __name__ == "__main__":
    main()
