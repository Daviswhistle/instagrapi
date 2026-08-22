from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable
from urllib.parse import parse_qs, urlparse

from .engine import (
    BrowserClosedError,
    ChromeLaunchError,
    FollowingFeedUnavailableError,
    InstagramRestrictionError,
    LikeState,
    LoginRequiredError,
    PlaywrightMissingError,
    normalize_post_key,
)

if TYPE_CHECKING:
    from playwright.sync_api import ElementHandle, Locator, Page

INSTAGRAM_HOME_URL = "https://www.instagram.com/"
FOLLOWING_FEED_URL = "https://www.instagram.com/?variant=following"

LIKE_LABELS = (
    "Like",
    "좋아요",
    "いいね！",
    "Me gusta",
    "J’aime",
    "Gefällt mir",
    "Curtir",
    "Нравится",
    "赞",
    "讚",
)
UNLIKE_LABELS = (
    "Unlike",
    "좋아요 취소",
    "「いいね！」を取り消す",
    "Ya no me gusta",
    "Je n’aime plus",
    "Gefällt mir nicht mehr",
    "Descurtir",
    "Не нравится",
    "取消赞",
    "收回讚",
)
COMMENT_LABELS = (
    "Comment",
    "댓글 달기",
    "コメント",
    "Comentar",
    "Commenter",
    "Kommentieren",
    "Комментировать",
    "评论",
    "留言",
)
SHARE_LABELS = (
    "Share Post",
    "Share",
    "공유",
    "シェア",
    "Compartir",
    "Partager",
    "Teilen",
    "Compartilhar",
    "Поделиться",
    "分享",
)
SPONSORED_LABELS = (
    "Sponsored",
    "광고",
    "広告",
    "Publicidad",
    "Sponsorisé",
    "Gesponsert",
    "Patrocinado",
    "Реклама",
    "赞助内容",
    "贊助",
)
RECOMMENDATION_LABELS = (
    "Suggested for you",
    "Suggested post",
    "Because you follow",
    "회원님을 위한 추천",
    "추천 게시물",
    "おすすめの投稿",
    "Sugerencias para ti",
    "Publicación sugerida",
    "Suggestions pour vous",
    "Publication suggérée",
    "Vorschläge für dich",
    "Vorgeschlagener Beitrag",
    "Sugestões para você",
    "Publicação sugerida",
    "Рекомендации для вас",
    "Рекомендуемая публикация",
    "为你推荐",
    "為你推薦",
)
RESTRICTION_PHRASES = (
    # English
    "try again later",
    "we restrict certain activity",
    "action blocked",
    "temporarily blocked",
    "please wait a few minutes before you try again",
    # Korean
    "나중에 다시 시도하세요",
    "잠시 후 다시 시도",
    "특정 활동을 제한",
    "활동을 제한",
    "작업이 차단",
    "일시적으로 차단",
    "몇 분 후에 다시 시도",
    # Japanese
    "しばらくしてからもう一度実行してください",
    "後ほどもう一度お試しください",
    "特定のアクティビティを制限",
    "アクションがブロックされました",
    # Spanish
    "vuelve a intentarlo más tarde",
    "restringimos cierta actividad",
    "acción bloqueada",
    "espera unos minutos",
    # French
    "réessayez plus tard",
    "nous limitons certaines activités",
    "action bloquée",
    "veuillez patienter quelques minutes",
    # German
    "versuche es später noch einmal",
    "wir schränken bestimmte aktivitäten ein",
    "aktion blockiert",
    "warte bitte einige minuten",
    # Portuguese
    "tente novamente mais tarde",
    "restringimos determinadas atividades",
    "ação bloqueada",
    "aguarde alguns minutos",
    # Russian
    "повторите попытку позже",
    "мы ограничиваем определенные действия",
    "действие заблокировано",
    "подождите несколько минут",
    # Simplified / Traditional Chinese
    "请稍后再试",
    "我们会限制某些操作",
    "操作已被阻止",
    "请等待几分钟",
    "請稍後再試",
    "我們會限制某些操作",
    "操作已被封鎖",
    "請等待幾分鐘",
)
CAUGHT_UP_PHRASES = (
    "you're all caught up",
    "you’re all caught up",
    "모두 확인했습니다",
    "새 게시물을 모두 확인했습니다",
    "최신 게시물을 모두 확인했습니다",
    "以上是最新动态",
    "以上是最新動態",
)
DISMISS_BUTTON_LABELS = (
    # English / Korean
    "Not Now",
    "Not now",
    "Later",
    "Close",
    "나중에 하기",
    "나중에",
    "지금은 안 함",
    "닫기",
    # Japanese
    "後で",
    "今はしない",
    "閉じる",
    # Spanish
    "Ahora no",
    "Más tarde",
    "Cerrar",
    # French
    "Plus tard",
    "Pas maintenant",
    "Fermer",
    # German
    "Jetzt nicht",
    "Später",
    "Schließen",
    # Portuguese
    "Agora não",
    "Mais tarde",
    "Fechar",
    # Russian
    "Не сейчас",
    "Позже",
    "Закрыть",
    # Simplified / Traditional Chinese
    "暂不",
    "以后再说",
    "稍后",
    "关闭",
    "暫不",
    "稍後再說",
    "稍後",
    "關閉",
)
EMPTY_FOLLOWING_PHRASES = (
    "when you follow people, you'll see the photos and videos they post here",
    "사람들을 팔로우하면 그들이 공유한 사진과 동영상을 여기에서 볼 수 있습니다",
    "フォローした人の写真や動画がここに表示されます",
    "cuando sigas a personas, verás aquí las fotos y los videos que publiquen",
    "lorsque vous suivez des personnes, vous voyez ici les photos et vidéos qu’elles publient",
    "wenn du personen folgst, siehst du hier die von ihnen geposteten fotos und videos",
    "quando você seguir pessoas, verá aqui as fotos e os vídeos que elas publicarem",
    "когда вы подпишетесь на людей, здесь будут показываться их фото и видео",
    "关注用户后，你会在这里看到他们发布的照片和视频",
    "追蹤其他人後，你會在這裡看到他們發佈的相片和影片",
)
FEED_ERROR_PHRASES = (
    "something went wrong",
    "couldn't refresh feed",
    "could not refresh feed",
    "문제가 발생했습니다",
    "피드를 새로 고칠 수 없습니다",
    "エラーが発生しました",
    "se produjo un error",
    "une erreur s’est produite",
    "une erreur s'est produite",
    "etwas ist schiefgelaufen",
    "ocorreu um erro",
    "произошла ошибка",
    "出错了",
    "發生錯誤",
)
RESERVED_PATHS = {
    "about",
    "accounts",
    "api",
    "challenge",
    "developer",
    "direct",
    "directory",
    "emails",
    "explore",
    "legal",
    "oauth",
    "p",
    "privacy",
    "push",
    "reel",
    "reels",
    "session",
    "stories",
    "terms",
    "tv",
    "web",
}

_UNSET = object()
_STATUS_SURFACE_SELECTOR = (
    '[role="dialog"]:visible, [role="alert"]:visible, [role="status"]:visible, '
    '[aria-live="assertive"]:visible, [aria-live="polite"]:visible'
)


class ChromeBrowserSession:
    def __init__(self, profile_dir: Path, *, on_log=None):
        self.profile_dir = Path(profile_dir)
        self.on_log = on_log or (lambda _message: None)
        self._playwright: Any = None
        self.context: Any = None
        self.page: Any = None

    def start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise PlaywrightMissingError(
                "브라우저 자동화 구성요소가 빠져 있습니다. 배포된 실행 파일을 다시 내려받아 주세요."
            ) from exc

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._playwright = sync_playwright().start()
            self.context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                channel="chrome",
                headless=False,
                no_viewport=True,
                accept_downloads=False,
                args=["--start-maximized"],
            )
        except Exception as exc:
            self._stop_playwright_only()
            self._raise_launch_error(exc)

        pages = [page for page in self.context.pages if not page.is_closed()]
        self.page = pages[0] if pages else self.context.new_page()
        self.page.set_default_timeout(5_000)
        # A shorter navigation ceiling makes app shutdown responsive while still
        # allowing normal Instagram loads on a typical connection.
        self.page.set_default_navigation_timeout(20_000)
        self.on_log("자동 좋아요 전용 Chrome 창을 열었습니다.")

    def wait_until_logged_in(self, stop_event: threading.Event) -> None:
        page = self._require_page()
        self._safe_goto(page, INSTAGRAM_HOME_URL)

        if self._has_session_cookie() and not self._page_looks_logged_out(page):
            self.on_log("전용 Chrome에 저장된 Instagram 로그인을 사용합니다.")
            return

        self.on_log(
            "처음 한 번만 열린 Chrome 창에서 Instagram에 로그인하세요. 앱에는 아이디나 비밀번호를 입력하지 않습니다."
        )
        while not stop_event.is_set():
            page = self._require_page(create_if_missing=True)
            if self._has_session_cookie() and not self._page_looks_logged_out(page):
                self.on_log("Instagram 로그인을 확인했습니다. 다음 실행에도 이 로그인 상태를 사용합니다.")
                return
            stop_event.wait(2)

    def following_feed(self) -> PlaywrightFollowingFeed:
        if not self.context:
            raise BrowserClosedError("Chrome이 실행되지 않았습니다.")
        return PlaywrightFollowingFeed(self)

    def close(self) -> None:
        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                pass
            finally:
                self.context = None
                self.page = None
        self._stop_playwright_only()

    def _stop_playwright_only(self) -> None:
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            finally:
                self._playwright = None

    def _has_session_cookie(self) -> bool:
        if self.context is None:
            return False
        try:
            cookies = self.context.cookies([INSTAGRAM_HOME_URL])
        except Exception as exc:
            self.raise_browser_error(exc)
        return any(cookie.get("name") == "sessionid" and cookie.get("value") for cookie in cookies)

    def _page_looks_logged_out(self, page: Page) -> bool:
        try:
            if "/accounts/login" in page.url:
                return True
            return page.locator('input[name="username"]').count() > 0
        except Exception as exc:
            self.raise_browser_error(exc)

    def _require_page(self, *, create_if_missing: bool = False) -> Page:
        if self.context is None:
            raise BrowserClosedError("Chrome 창이 닫혔습니다. 다시 시작해 주세요.")
        if self.page is not None and not self.page.is_closed():
            return self.page

        try:
            pages = [page for page in self.context.pages if not page.is_closed()]
        except Exception as exc:
            self.raise_browser_error(exc)
        if pages:
            self.page = pages[0]
            return self.page
        if create_if_missing:
            try:
                self.page = self.context.new_page()
            except Exception as exc:
                self.raise_browser_error(exc)
            return self.page
        raise BrowserClosedError("Chrome 창이 닫혔습니다. 다시 시작해 주세요.")

    def _safe_goto(self, page: Page, url: str) -> None:
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            self.raise_browser_error(exc)

    @staticmethod
    def _raise_launch_error(exc: Exception) -> None:
        detail = str(exc).lower()
        if any(
            phrase in detail
            for phrase in (
                "executable doesn't exist",
                "chrome distribution",
                "channel 'chrome'",
                "could not find chrome",
            )
        ):
            raise ChromeLaunchError("Google Chrome을 찾지 못했습니다. Chrome을 설치한 뒤 다시 시작하세요.") from exc
        if any(
            phrase in detail
            for phrase in (
                "processsingleton",
                "user data directory is already in use",
                "profile is in use",
            )
        ):
            raise ChromeLaunchError(
                "자동 좋아요 전용 Chrome 프로필이 이미 사용 중입니다. "
                "이 앱이 열었던 Chrome 창을 모두 닫은 뒤 다시 시작하세요."
            ) from exc
        raise ChromeLaunchError(f"Chrome을 열지 못했습니다: {safe_error_text(exc)}") from exc

    @staticmethod
    def raise_browser_error(exc: Exception) -> None:
        detail = str(exc).lower()
        if any(phrase in detail for phrase in ("target page", "browser has been closed", "target closed")):
            raise BrowserClosedError("Chrome 창이 닫혔습니다. 다시 시작해 주세요.") from exc
        raise exc


class PlaywrightFollowingFeed:
    def __init__(self, session: ChromeBrowserSession):
        self.session = session

    @property
    def page(self) -> Page:
        return self.session._require_page()

    def open_following(self) -> None:
        if not self.session._has_session_cookie():
            raise LoginRequiredError(
                "Instagram 로그인이 풀렸습니다. 전용 Chrome 데이터 지우기 후 다시 로그인하거나 다시 시작하세요."
            )

        page = self.page
        self.session._safe_goto(page, FOLLOWING_FEED_URL)
        try:
            page.wait_for_timeout(2_000)
        except Exception as exc:
            self.session.raise_browser_error(exc)

        if self._looks_logged_out():
            raise LoginRequiredError(
                "Instagram 로그인이 풀렸습니다. 열린 Chrome에서 다시 로그인한 뒤 앱을 다시 시작하세요."
            )
        if not self._is_following_url(page.url):
            raise FollowingFeedUnavailableError(
                "Instagram이 시간순 팔로잉 피드를 열지 않았습니다. 웹 화면이 변경되었을 수 있어 "
                "일반 홈 피드를 잘못 처리하지 않도록 중지했습니다."
            )
        self._wait_until_following_surface()
        try:
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(1_500)
        except Exception as exc:
            self.session.raise_browser_error(exc)

    def _wait_until_following_surface(self, timeout_seconds: float = 12.0) -> None:
        """Require real feed content or a recognized legitimate empty state."""
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            if self._looks_logged_out():
                raise LoginRequiredError(
                    "Instagram 로그인이 풀렸습니다. 열린 Chrome에서 다시 로그인한 뒤 앱을 다시 시작하세요."
                )
            if not self._is_following_url(self.page.url):
                raise FollowingFeedUnavailableError(
                    "Instagram이 팔로잉 피드가 아닌 다른 화면으로 이동했습니다. "
                    "체크포인트나 오류 화면일 수 있어 자동화를 중지했습니다."
                )

            restriction = self.restriction_message()
            if restriction:
                raise InstagramRestrictionError(
                    f"Instagram이 활동을 제한했습니다. 자동화를 중지했습니다. 표시 내용: {restriction}"
                )

            self._dismiss_common_dialogs()
            if self._has_post_surface() or self.is_caught_up() or self._has_legitimate_empty_state():
                return

            error_message = self._feed_error_message()
            if error_message:
                raise FollowingFeedUnavailableError(
                    f"Instagram 팔로잉 피드를 불러오지 못했습니다. 표시 내용: {truncate_text(error_message)}"
                )
            if time.monotonic() >= deadline:
                break
            try:
                self.page.wait_for_timeout(500)
            except Exception as exc:
                self.session.raise_browser_error(exc)

        raise FollowingFeedUnavailableError(
            "Instagram 팔로잉 피드의 게시물 영역을 확인하지 못했습니다. "
            "오류·본인 확인 화면인지 확인한 뒤 다시 실행하세요."
        )

    def _has_post_surface(self) -> bool:
        try:
            selector = 'article:has(a[href*="/p/"]), article:has(a[href*="/reel/"]), article:has(a[href*="/tv/"])'
            return self.page.locator(selector).count() > 0
        except Exception as exc:
            self.session.raise_browser_error(exc)

    def _has_legitimate_empty_state(self) -> bool:
        return self._matching_non_post_surface_text(EMPTY_FOLLOWING_PHRASES) is not None

    def _feed_error_message(self) -> str | None:
        if self._has_post_surface():
            return None
        return self._matching_non_post_surface_text(FEED_ERROR_PHRASES)

    def _matching_non_post_surface_text(self, phrases: tuple[str, ...]) -> str | None:
        selectors = f"{_STATUS_SURFACE_SELECTOR}, main:visible"
        try:
            surfaces = self.page.locator(selectors)
            for index in range(min(surfaces.count(), 16)):
                surface = surfaces.nth(index)
                if surface.locator("xpath=ancestor::article").count():
                    continue
                text = surface.inner_text(timeout=1_500)
                normalized = normalize_ui_text(text)
                if any(normalize_ui_text(phrase) in normalized for phrase in phrases):
                    return text
        except Exception as exc:
            self.session.raise_browser_error(exc)
        return None

    def posts(self) -> Iterable[PlaywrightFeedPost]:
        self._dismiss_common_dialogs()
        try:
            articles = self.page.locator("article")
            count = articles.count()
        except Exception as exc:
            self.session.raise_browser_error(exc)

        posts = []
        for index in range(count):
            post = PlaywrightFeedPost(self.session, articles.nth(index))
            if post.key:
                posts.append(post)
        return posts

    def scroll_for_more(self) -> bool:
        self._dismiss_common_dialogs()
        page = self.page
        try:
            before = page.evaluate(
                """() => ({
                    y: window.scrollY,
                    height: document.documentElement.scrollHeight,
                    articles: document.querySelectorAll('article').length
                })"""
            )
            page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.9, 900))")
            page.wait_for_timeout(1_800)
            after = page.evaluate(
                """() => ({
                    y: window.scrollY,
                    height: document.documentElement.scrollHeight,
                    articles: document.querySelectorAll('article').length
                })"""
            )
        except Exception as exc:
            self.session.raise_browser_error(exc)

        return bool(
            after["y"] > before["y"] + 20
            or after["height"] > before["height"]
            or after["articles"] > before["articles"]
        )

    def restriction_message(self) -> str | None:
        """Read only Instagram status UI, never arbitrary caption/comment text."""
        try:
            surfaces = self.page.locator(_STATUS_SURFACE_SELECTOR)
            texts: list[str] = []
            for index in range(min(surfaces.count(), 12)):
                surface = surfaces.nth(index)
                if surface.locator("xpath=ancestor::article").count():
                    continue
                text = surface.inner_text(timeout=2_000)
                if text:
                    texts.append(text)
        except Exception as exc:
            self.session.raise_browser_error(exc)

        for text in texts:
            normalized = normalize_ui_text(text)
            for phrase in RESTRICTION_PHRASES:
                if normalize_ui_text(phrase) in normalized:
                    return truncate_text(text)
        return None

    def is_caught_up(self) -> bool:
        """Match the feed marker outside articles so captions cannot stop a scan."""
        for phrase in CAUGHT_UP_PHRASES:
            try:
                matches = self.page.get_by_text(re.compile(re.escape(phrase), re.IGNORECASE), exact=False)
                for index in range(min(matches.count(), 12)):
                    match = matches.nth(index)
                    if not match.is_visible():
                        continue
                    if match.locator("xpath=ancestor::article").count():
                        continue
                    return True
            except Exception as exc:
                self.session.raise_browser_error(exc)
        return False

    @staticmethod
    def _is_following_url(url: str) -> bool:
        parsed = urlparse(str(url or ""))
        return (
            parsed.scheme in {"http", "https"}
            and is_instagram_hostname(parsed.hostname)
            and parsed.path in {"", "/"}
            and parse_qs(parsed.query).get("variant") == ["following"]
        )

    def _looks_logged_out(self) -> bool:
        page = self.page
        try:
            if "/accounts/login" in page.url:
                return True
            return page.locator('input[name="username"]').count() > 0
        except Exception as exc:
            self.session.raise_browser_error(exc)

    def _dismiss_common_dialogs(self) -> None:
        """Dismiss only safe secondary actions, including supported localized labels."""
        safe_labels = {normalize_ui_text(label) for label in DISMISS_BUTTON_LABELS}
        for _round in range(4):
            dismissed = False
            try:
                dialogs = self.page.locator('[role="dialog"]:visible')
                for dialog_index in range(min(dialogs.count(), 8)):
                    dialog = dialogs.nth(dialog_index)
                    if self._surface_contains_restriction(dialog):
                        continue
                    if self._click_safe_dismiss_control(dialog, safe_labels):
                        dismissed = True
                        break

                if not dismissed:
                    # Some Instagram overlays omit role=dialog. Restrict the
                    # fallback to visible controls outside posts and never click
                    # a control belonging to a restriction dialog.
                    controls = self.page.locator('button:visible, [role="button"]:visible')
                    for index in range(min(controls.count(), 80)):
                        control = controls.nth(index)
                        if control.locator("xpath=ancestor::article").count():
                            continue
                        dialog = control.locator("xpath=ancestor::*[@role='dialog'][1]")
                        if dialog.count() and self._surface_contains_restriction(dialog.first):
                            continue
                        if self._control_matches_safe_label(control, safe_labels):
                            control.click(timeout=2_000)
                            dismissed = True
                            break

                if dismissed:
                    self.page.wait_for_timeout(300)
                else:
                    break
            except Exception as exc:
                detail = str(exc).lower()
                if any(phrase in detail for phrase in ("target page", "target closed", "browser has been closed")):
                    self.session.raise_browser_error(exc)
                break

    def _click_safe_dismiss_control(self, surface: Locator, safe_labels: set[str]) -> bool:
        controls = surface.locator('button:visible, [role="button"]:visible')
        for index in range(min(controls.count(), 20)):
            control = controls.nth(index)
            if self._control_matches_safe_label(control, safe_labels):
                control.click(timeout=2_000)
                return True
        return False

    @staticmethod
    def _control_matches_safe_label(control: Locator, safe_labels: set[str]) -> bool:
        values = [
            control.get_attribute("aria-label") or "",
            control.get_attribute("title") or "",
            control.inner_text(timeout=800),
        ]
        return any(normalize_ui_text(value) in safe_labels for value in values if value)

    @staticmethod
    def _surface_contains_restriction(surface: Locator) -> bool:
        try:
            normalized = normalize_ui_text(surface.inner_text(timeout=1_000))
        except Exception:
            return False
        return any(normalize_ui_text(phrase) in normalized for phrase in RESTRICTION_PHRASES)


class PlaywrightFeedPost:
    def __init__(self, session: ChromeBrowserSession, article: Locator):
        self.session = session
        self.article = article
        self._key: str | None = None
        self._username: str | None = None
        self._exclusion_reason: str | None | object = _UNSET

    @property
    def key(self) -> str:
        if self._key is None:
            self._key = self._extract_key()
        return self._key

    @property
    def username(self) -> str:
        if self._username is None:
            self._username = self._extract_username()
        return self._username

    @property
    def exclusion_reason(self) -> str | None:
        if self._exclusion_reason is _UNSET:
            self._exclusion_reason = self._detect_exclusion_reason()
        return self._exclusion_reason if isinstance(self._exclusion_reason, str) else None

    @property
    def is_sponsored(self) -> bool:
        """Compatibility property for callers written against the first prototype."""
        return self.exclusion_reason == "sponsored"

    @property
    def like_state(self) -> LikeState:
        unlike = self._find_main_control(UNLIKE_LABELS)
        if unlike is not None:
            unlike.dispose()
            return "liked"
        like = self._find_main_control(LIKE_LABELS)
        if like is not None:
            like.dispose()
            return "unliked"
        return "unknown"

    def click_like(self) -> bool:
        control = self._find_main_control(LIKE_LABELS)
        if control is None:
            return self.like_state == "liked"
        try:
            control.scroll_into_view_if_needed(timeout=5_000)
            control.click(timeout=5_000)
        except Exception as exc:
            self.session.raise_browser_error(exc)
        finally:
            try:
                control.dispose()
            except Exception:
                pass

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self.like_state == "liked":
                return True
            try:
                self.session.page.wait_for_timeout(250)
            except Exception as exc:
                self.session.raise_browser_error(exc)
        return False

    def _extract_key(self) -> str:
        try:
            links = self.article.locator('a[href*="/p/"], a[href*="/reel/"], a[href*="/tv/"]')
            for index in range(min(links.count(), 12)):
                href = links.nth(index).get_attribute("href") or ""
                key = normalize_post_key(href)
                if key.startswith(("/p/", "/reel/", "/tv/")):
                    return key
        except Exception as exc:
            self.session.raise_browser_error(exc)
        return ""

    def _extract_username(self) -> str:
        try:
            links = self.article.locator('a[href^="/"]')
            for index in range(min(links.count(), 20)):
                href = links.nth(index).get_attribute("href") or ""
                path = urlparse(href).path
                parts = [part for part in path.split("/") if part]
                if len(parts) == 1 and parts[0].lower() not in RESERVED_PATHS:
                    return parts[0]
        except Exception as exc:
            self.session.raise_browser_error(exc)
        return ""

    def _detect_exclusion_reason(self) -> str | None:
        if self._has_metadata_label(SPONSORED_LABELS):
            return "sponsored"
        if self._has_metadata_label(RECOMMENDATION_LABELS):
            return "recommended"
        return None

    def _has_metadata_label(self, labels: tuple[str, ...]) -> bool:
        """Recognize header/top metadata, not matching words in captions/comments."""
        try:
            article_box = self.article.bounding_box()
            headers = self.article.locator("header")
        except Exception as exc:
            self.session.raise_browser_error(exc)

        for label in labels:
            try:
                for header_index in range(min(headers.count(), 3)):
                    matches = headers.nth(header_index).get_by_text(label, exact=True)
                    for index in range(min(matches.count(), 5)):
                        if matches.nth(index).is_visible():
                            return True

                # Instagram occasionally renders the marker outside a semantic
                # header. Accept it only in the top metadata band of the article.
                matches = self.article.get_by_text(label, exact=True)
                for index in range(min(matches.count(), 10)):
                    match = matches.nth(index)
                    if not match.is_visible():
                        continue
                    box = match.bounding_box()
                    if not article_box or not box:
                        continue
                    metadata_limit = article_box["y"] + min(220.0, max(80.0, article_box["height"] * 0.25))
                    if box["y"] + box["height"] <= metadata_limit:
                        return True
            except Exception as exc:
                self.session.raise_browser_error(exc)
        return False

    def _find_main_control(self, labels: tuple[str, ...]) -> ElementHandle | None:
        """Select the post action-bar control rather than comment-heart buttons."""
        try:
            handle = self.article.evaluate_handle(
                """(article, payload) => {
                    const normalize = value => (value || '').trim();
                    const wantedLabels = new Set(payload.labels);
                    const allLikeLabels = new Set(payload.allLikeLabels);
                    const peerLabels = new Set(payload.peers);
                    const readLabel = button => {
                        const own = normalize(button.getAttribute('aria-label'));
                        const icon = button.querySelector('svg[aria-label]');
                        return own || normalize(icon?.getAttribute('aria-label'));
                    };
                    const visible = button => {
                        const rect = button.getBoundingClientRect();
                        const style = window.getComputedStyle(button);
                        const hidden = style.visibility === 'hidden' || style.display === 'none';
                        return !hidden && rect.width > 0 && rect.height > 0;
                    };

                    // Instagram's main post controls live in a section together
                    // with Comment/Share. Once that section is found, never fall
                    // back to a comment-heart button elsewhere in the article.
                    for (const section of article.querySelectorAll('section')) {
                        const buttons = [...section.querySelectorAll('button, [role="button"]')];
                        const labels = buttons.map(readLabel);
                        const hasLikeControl = labels.some(label => allLikeLabels.has(label));
                        const hasActionPeer = labels.some(label => peerLabels.has(label));
                        if (!hasLikeControl || !hasActionPeer) continue;
                        return buttons.find(button => wantedLabels.has(readLabel(button)) && visible(button)) || null;
                    }

                    const candidates = [];
                    for (const button of article.querySelectorAll('button, [role="button"]')) {
                        if (!wantedLabels.has(readLabel(button)) || !visible(button)) continue;
                        const rect = button.getBoundingClientRect();
                        candidates.push({button, score: rect.width * rect.height - Math.max(rect.top, 0) / 100000});
                    }
                    candidates.sort((a, b) => b.score - a.score);
                    return candidates.length ? candidates[0].button : null;
                }""",
                {
                    "labels": list(labels),
                    "allLikeLabels": list(LIKE_LABELS + UNLIKE_LABELS),
                    "peers": list(COMMENT_LABELS + SHARE_LABELS),
                },
            )
        except Exception as exc:
            self.session.raise_browser_error(exc)

        element = handle.as_element()
        if element is None:
            handle.dispose()
            return None
        return element


def is_instagram_hostname(hostname: str | None) -> bool:
    labels = str(hostname or "").lower().rstrip(".").split(".")
    return len(labels) >= 2 and labels[-2:] == ["instagram", "com"]


def normalize_ui_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def safe_error_text(exc: Exception) -> str:
    return truncate_text(str(exc).replace("\n", " "), limit=240) or type(exc).__name__


def truncate_text(value: str, limit: int = 300) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"
