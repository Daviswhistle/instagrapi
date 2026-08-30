from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_browser() -> None:
    path = Path("apps/instagram_tools/browser.py")
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '''INSTAGRAM_WEB_HOST = urlparse(INSTAGRAM_HOME_URL).hostname or "www.instagram.com"
''',
        '''INSTAGRAM_WEB_HOST = urlparse(INSTAGRAM_HOME_URL).hostname or "www.instagram.com"
INSTAGRAM_WEB_ORIGIN = "https://www.instagram.com"
''',
        "Instagram web origin constant",
    )

    text = replace_once(
        text,
        '''_BROWSER_REQUEST_SCRIPT = r"""
async ({path, method, csrfToken, appId, asbdId, timeoutMs, includeIgHeaders}) => {
  const headers = {
''',
        '''_BROWSER_REQUEST_SCRIPT = r"""
async ({path, method, csrfToken, appId, asbdId, timeoutMs, includeIgHeaders, expectedOrigin}) => {
  // Check inside the page immediately before constructing headers. The Python
  // caller performs the same check first, while this guard closes the navigation
  // race between reading page.url and executing this function.
  if (window.location.origin !== expectedOrigin) {
    return {originError: window.location.href};
  }
  const requestUrl = new URL(path, `${expectedOrigin}/`);
  if (requestUrl.origin !== expectedOrigin) {
    return {originError: window.location.href};
  }

  const headers = {
''',
        "in-page origin guard",
    )

    text = replace_once(
        text,
        '''    const response = await fetch(path, {
''',
        '''    const response = await fetch(requestUrl.href, {
''',
        "absolute guarded request URL",
    )

    text = replace_once(
        text,
        '''def is_instagram_web_host(hostname: str | None) -> bool:
    return str(hostname or "").casefold().rstrip(".") == INSTAGRAM_WEB_HOST.casefold()


class SharedChromeBrowserSession(ChromeBrowserSession):
''',
        '''def is_instagram_web_host(hostname: str | None) -> bool:
    return str(hostname or "").casefold().rstrip(".") == INSTAGRAM_WEB_HOST.casefold()


def is_instagram_web_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() == "https"
        and is_instagram_web_host(parsed.hostname)
        and port in {None, 443}
    )


class SharedChromeBrowserSession(ChromeBrowserSession):
''',
        "exact Instagram HTTPS URL helper",
    )

    text = replace_once(
        text,
        '''        page = self._require_page(create_if_missing=True)
        current_host = urlparse(page.url).hostname

        # A persistent context often already has an authenticated Instagram page.
        # Reusing it avoids the visible blank-page -> Home -> Home reload sequence.
        navigated_home = False
        if not is_instagram_web_host(current_host):
''',
        '''        page = self._require_page(create_if_missing=True)

        # A persistent context often already has an authenticated Instagram page.
        # Reusing it avoids the visible blank-page -> Home -> Home reload sequence.
        navigated_home = False
        if not is_instagram_web_url(page.url):
''',
        "login exact origin validation",
    )

    text = replace_once(
        text,
        '''        page = self.session._require_page(create_if_missing=True)
        if not is_instagram_web_host(urlparse(page.url).hostname):
            self.session._safe_goto(page, INSTAGRAM_HOME_URL)
''',
        '''        page = self.session._require_page(create_if_missing=True)
        if not is_instagram_web_url(page.url):
            self.session._safe_goto(page, INSTAGRAM_HOME_URL)
''',
        "prepare exact origin validation",
    )

    text = replace_once(
        text,
        '''        if honor_stop:
            self._raise_if_stopped()
        page = self.session._require_page()
        try:
''',
        '''        if honor_stop:
            self._raise_if_stopped()
        page = self.session._require_page()
        if not is_instagram_web_url(page.url):
            raise FriendshipRequestError(
                "Instagram 요청 전에 전용 Chrome 탭이 다른 사이트로 이동했습니다. "
                "Instagram 화면으로 돌아간 뒤 다시 시도해 주세요."
            )
        try:
''',
        "Python request origin precheck",
    )

    text = replace_once(
        text,
        '''                    "timeoutMs": self.request_timeout_ms,
                    "includeIgHeaders": include_ig_headers,
''',
        '''                    "timeoutMs": self.request_timeout_ms,
                    "includeIgHeaders": include_ig_headers,
                    "expectedOrigin": INSTAGRAM_WEB_ORIGIN,
''',
        "pass expected origin to browser guard",
    )

    text = replace_once(
        text,
        '''        if not isinstance(result, dict):
            raise FriendshipRequestError("Instagram 요청 결과를 읽지 못했습니다.")
        if result.get("timedOut"):
''',
        '''        if not isinstance(result, dict):
            raise FriendshipRequestError("Instagram 요청 결과를 읽지 못했습니다.")
        if result.get("originError"):
            raise FriendshipRequestError(
                "Instagram 요청 중 전용 Chrome 탭이 다른 사이트로 이동해 작업을 중지했습니다. "
                "Instagram 화면으로 돌아간 뒤 다시 시도해 주세요."
            )
        if result.get("timedOut"):
''',
        "handle in-page origin race",
    )

    path.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    path = Path("tests/regression/test_instagram_tools.py")
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '''    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.evaluate_calls: list[tuple[str, dict[str, object]]] = []
''',
        '''    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.url = "https://www.instagram.com/"
        self.evaluate_calls: list[tuple[str, dict[str, object]]] = []
''',
        "fake page default Instagram URL",
    )

    text = replace_once(
        text,
        '''    def test_top_level_unfollow_confirmation_is_normalized_for_the_cleaner(self) -> None:
''',
        '''    def test_off_origin_page_is_rejected_before_sensitive_evaluate(self) -> None:
        backend, session = self.backend([])
        session.page.url = "https://example.com/account"

        with self.assertRaises(FriendshipRequestError) as raised:
            backend.unfollow("2")

        self.assertIn("다른 사이트", str(raised.exception))
        self.assertEqual(session.page.evaluate_calls, [])

    def test_navigation_race_is_rejected_inside_browser_evaluate(self) -> None:
        backend, session = self.backend([{"originError": "https://example.com/"}])

        with self.assertRaises(FriendshipRequestError) as raised:
            backend.unfollow("2")

        self.assertIn("다른 사이트", str(raised.exception))
        self.assertEqual(len(session.page.evaluate_calls), 1)
        script, arguments = session.page.evaluate_calls[0]
        self.assertEqual(arguments["expectedOrigin"], "https://www.instagram.com")
        self.assertIn("window.location.origin", script)
        self.assertIn("fetch(requestUrl.href", script)

    def test_top_level_unfollow_confirmation_is_normalized_for_the_cleaner(self) -> None:
''',
        "origin guard regressions",
    )

    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_browser()
    patch_tests()


if __name__ == "__main__":
    main()
