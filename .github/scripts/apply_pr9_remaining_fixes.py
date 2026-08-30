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
        "\n\ndef is_instagram_web_host(hostname: str | None) -> bool:\n",
        "\n\nclass _AmbiguousWriteTimeout(FriendshipRequestError):\n"
        "    \"\"\"A POST may have completed even though the local fetch timed out.\"\"\"\n\n\n"
        "def is_instagram_web_host(hostname: str | None) -> bool:\n",
        "ambiguous write timeout type",
    )

    text = replace_once(
        text,
        '''        for attempt_number, (path, include_ig_headers) in enumerate(attempts, start=1):
            response = self._request(
                path,
                method="POST",
                csrf_token=csrf_token,
                include_ig_headers=include_ig_headers,
            )
            self._require_expected_final_url(response, path)
''',
        '''        for attempt_number, (path, include_ig_headers) in enumerate(attempts, start=1):
            try:
                response = self._request(
                    path,
                    method="POST",
                    csrf_token=csrf_token,
                    include_ig_headers=include_ig_headers,
                )
            except _AmbiguousWriteTimeout:
                confirmation = self._confirm_unfollow_after_write(
                    user_id,
                    attempt_number=attempt_number,
                    status_code=0,
                    confirmation="post_timeout_friendship_check",
                )
                if confirmation is not None:
                    return confirmation
                diagnostics.append(
                    f"시도 {attempt_number}: 요청 시간 초과 후 팔로우 상태가 그대로임"
                )
                continue

            self._require_expected_final_url(response, path)
''',
        "catch ambiguous POST timeouts",
    )

    text = replace_once(
        text,
        '''            # A 2xx status alone is not proof of a write. Instagram can return a
            # generic HTML/empty response while leaving the relationship intact.
            # Verify only the account just written; this is a post-condition check,
            # not a pre-check or another full follower/following scan.
            self._wait_after_write()
            relationship = self.friendship(user_id, honor_stop=False)
            following = relationship.get("following")
            if following is False:
                return {
                    "status": "ok",
                    "friendship_status": {"following": False},
                    "_transport": {
                        "confirmation": "post_write_friendship_check",
                        "attempt": attempt_number,
                        "status_code": response.status_code,
                    },
                }
            if following is True:
                diagnostics.append(f"시도 {attempt_number}: 팔로우 상태가 그대로임")
                continue
            raise FriendshipRequestError(
                "언팔로우 요청 뒤 현재 팔로우 상태를 명확히 확인하지 못했습니다. 추가 작업을 중지했습니다."
            )
''',
        '''            # A 2xx status alone is not proof of a write. Instagram can return a
            # generic HTML/empty response while leaving the relationship intact.
            # Verify only the account just written; this is a post-condition check,
            # not a pre-check or another full follower/following scan.
            confirmation = self._confirm_unfollow_after_write(
                user_id,
                attempt_number=attempt_number,
                status_code=response.status_code,
                confirmation="post_write_friendship_check",
            )
            if confirmation is not None:
                return confirmation
            diagnostics.append(f"시도 {attempt_number}: 팔로우 상태가 그대로임")
            continue
''',
        "share post-write verification",
    )

    text = replace_once(
        text,
        '''    def _request_json(
''',
        '''    def _confirm_unfollow_after_write(
        self,
        user_id: str,
        *,
        attempt_number: int,
        status_code: int,
        confirmation: str,
    ) -> dict[str, Any] | None:
        self._wait_after_write()
        relationship = self.friendship(user_id, honor_stop=False)
        following = relationship.get("following")
        if following is False:
            return {
                "status": "ok",
                "friendship_status": {"following": False},
                "_transport": {
                    "confirmation": confirmation,
                    "attempt": attempt_number,
                    "status_code": status_code,
                },
            }
        if following is True:
            return None
        raise FriendshipRequestError(
            "언팔로우 요청 뒤 현재 팔로우 상태를 명확히 확인하지 못했습니다. 추가 작업을 중지했습니다."
        )

    def _request_json(
''',
        "post-write verification helper",
    )

    text = replace_once(
        text,
        '''        if result.get("timedOut"):
            raise FriendshipRequestError(
                f"Instagram 네트워크 요청이 {self.request_timeout_seconds:g}초 안에 완료되지 않아 중지했습니다."
            )
''',
        '''        if result.get("timedOut"):
            message = (
                f"Instagram 네트워크 요청이 {self.request_timeout_seconds:g}초 안에 완료되지 않았습니다."
            )
            if method.upper() == "POST":
                raise _AmbiguousWriteTimeout(message)
            raise FriendshipRequestError(f"{message} 작업을 중지했습니다.")
''',
        "specialize POST timeout",
    )

    path.write_text(text, encoding="utf-8")


def patch_worker() -> None:
    path = Path("apps/instagram_tools/worker.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''        while not stop_event.is_set():
            self.events.put(("log", "팔로잉 시간순 피드를 처음부터 확인합니다."))
            summary = scanner.scan_once(feed, stop_event)
            last_scan_at = datetime.now().astimezone().isoformat(timespec="seconds")
            if summary.stopped:
''',
        '''        while not stop_event.is_set():
            self.events.put(("log", "팔로잉 시간순 피드를 처음부터 확인합니다."))
            try:
                summary = scanner.scan_once(feed, stop_event)
            except InstagramRestrictionError as exc:
                last_scan_at = datetime.now().astimezone().isoformat(timespec="seconds")
                if exc.summary is not None:
                    self.events.put(
                        (
                            "log",
                            "제한 감지 전 처리 결과: "
                            f"{FollowingAutoLiker._summary_message(exc.summary)}",
                        )
                    )
                self.events.put(
                    (
                        "auto_status",
                        {
                            "message": f"활동 제한으로 중지 · 누적 {session_likes}개",
                            "session_likes": session_likes,
                            "last_scan_at": last_scan_at,
                        },
                    )
                )
                raise

            last_scan_at = datetime.now().astimezone().isoformat(timespec="seconds")
            if summary.stopped:
''',
        "preserve restricted auto-like status",
    )
    path.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    path = Path("tests/regression/test_instagram_tools.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from unittest.mock import Mock\n",
        "from unittest.mock import Mock, patch\n",
        "mock patch import",
    )
    text = replace_once(
        text,
        '''from apps.following_auto_liker.browser import INSTAGRAM_HOME_URL
''',
        '''from apps.following_auto_liker.browser import INSTAGRAM_HOME_URL
from apps.following_auto_liker.engine import InstagramRestrictionError, ScanSummary
from apps.following_auto_liker.storage import AppConfig
''',
        "auto-like regression imports",
    )
    text = replace_once(
        text,
        '''    def test_fallback_endpoint_runs_when_primary_write_did_not_change_state(self) -> None:
''',
        '''    def test_timed_out_post_is_verified_before_reporting_failure(self) -> None:
        backend, session = self.backend(
            [
                {"networkError": "AbortError", "timedOut": True},
                response(
                    SHOW_URL,
                    text='{"status":"ok","following":false}',
                    content_type="application/json",
                ),
            ]
        )

        payload = backend.unfollow("2")

        self.assertEqual(
            payload["_transport"]["confirmation"],
            "post_timeout_friendship_check",
        )
        self.assertIs(payload["friendship_status"]["following"], False)
        methods = [arguments["method"] for _script, arguments in session.page.evaluate_calls]
        self.assertEqual(methods, ["POST", "GET"])

    def test_fallback_endpoint_runs_when_primary_write_did_not_change_state(self) -> None:
''',
        "POST timeout regression",
    )
    text = replace_once(
        text,
        '''class RecordingWorker(InstagramAutomationWorker):
''',
        '''class RestrictingScanner:
    def __init__(self, _config, *, on_like=None, **_kwargs) -> None:
        self.on_like = on_like or (lambda: None)

    def scan_once(self, _feed, _stop_event) -> ScanSummary:
        self.on_like()
        raise InstagramRestrictionError(
            "Instagram이 좋아요 활동을 제한했습니다.",
            summary=ScanSummary(discovered=2, liked=1, sponsored=1),
        )


class AutoLikeBrowser:
    @staticmethod
    def following_feed():
        return object()


class RecordingWorker(InstagramAutomationWorker):
''',
        "restriction test fixtures",
    )
    text = replace_once(
        text,
        '''    def test_unexpected_worker_exception_is_logged_with_traceback(self) -> None:
''',
        '''    def test_restriction_publishes_partial_auto_like_summary_and_timestamp(self) -> None:
        events: queue.Queue[tuple[str, object]] = queue.Queue()
        worker = InstagramAutomationWorker(
            FakeStorage(),
            events,
            browser_factory=FakePersistentBrowser,
        )

        with patch("apps.instagram_tools.worker.FollowingFeedScanner", RestrictingScanner):
            with self.assertRaises(InstagramRestrictionError):
                worker._run_auto_like(
                    AutoLikeBrowser(),
                    {"config": AppConfig().validate()},
                    threading.Event(),
                )

        captured: list[tuple[str, object]] = []
        while not events.empty():
            captured.append(events.get_nowait())

        logs = [payload for kind, payload in captured if kind == "log"]
        statuses = [payload for kind, payload in captured if kind == "auto_status"]
        self.assertTrue(
            any(
                isinstance(message, str)
                and "제한 감지 전 처리 결과:" in message
                and "좋아요 1개" in message
                for message in logs
            )
        )
        self.assertEqual(statuses[-1]["message"], "활동 제한으로 중지 · 누적 1개")
        self.assertEqual(statuses[-1]["session_likes"], 1)
        self.assertTrue(statuses[-1]["last_scan_at"])

    def test_unexpected_worker_exception_is_logged_with_traceback(self) -> None:
''',
        "partial restriction status regression",
    )
    path.write_text(text, encoding="utf-8")


def patch_following_readme() -> None:
    path = Path("apps/following_auto_liker/README.md")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''기존 버전처럼 Instagram 아이디와 비밀번호를 앱에 입력하지 않습니다. 앱이 별도의 Chrome 프로필을 열며, 첫 실행에 그 창에서 직접 로그인하면 이후 실행에도 로그인 상태가 남습니다.
''',
        '''기존 버전처럼 Instagram 아이디와 비밀번호를 앱에 입력하지 않습니다. 앱이 별도의 Chrome 프로필을 열며, 첫 실행에 그 창에서 직접 로그인하면 이후 실행에도 로그인 상태가 남습니다.

> 이 기능은 현재 **Instagram Tools** 통합 앱의 **자동 좋아요** 탭으로 배포됩니다. 이 디렉터리는 기능 구현과 기존 단독 실행 진입점을 유지하지만, 공식 패키지와 Release는 통합 앱 이름으로만 생성됩니다.
''',
        "following unified distribution notice",
    )
    old_usage_start = text.index("## 가장 쉬운 사용법\n")
    old_usage_end = text.index("## 기본 설정\n", old_usage_start)
    new_usage = '''## 가장 쉬운 사용법

1. 저장소의 **Releases** 또는 GitHub Actions에서 운영체제에 맞는 통합 패키지를 받습니다.
   - Windows: `InstagramTools-Windows`
   - Apple Silicon Mac: `InstagramTools-macOS-Apple-Silicon`
   - Intel Mac: `InstagramTools-macOS-Intel`
2. Windows는 Actions ZIP을 한 번 풀어 `InstagramTools.exe`를 실행합니다. Mac은 Actions ZIP 안의 DMG를 열고 `InstagramTools.app`을 `Applications`로 옮깁니다. Google Chrome이 설치되어 있어야 합니다.
3. 통합 앱의 **자동 좋아요** 탭에서 기본 설정을 확인하고 **자동 좋아요 시작**을 누릅니다.
4. 처음 열린 Chrome 창에서 Instagram에 직접 로그인합니다. 2단계 인증이나 본인 확인도 그 창에서 직접 완료합니다.
5. 앱과 앱이 연 Chrome 창을 열어 둡니다. 이후에는 저장된 로그인 상태를 사용하며, 미팔로워 정리 탭도 같은 창과 로그인을 재사용합니다.

통합 앱은 전용 Chrome 프로필을 보호하기 위해 한 번에 한 인스턴스만 실행됩니다. 이미 실행 중일 때 두 번째 앱을 열면 안내를 표시하고 종료하며, 실행 중인 다른 인스턴스 아래의 로그인 데이터를 삭제하지 않습니다.

Windows SmartScreen이나 macOS Gatekeeper가 경고할 수 있습니다. 배포 파일에 코드 서명을 하지 않았기 때문입니다. macOS에서는 앱을 한 번 실행한 뒤 **시스템 설정 → 개인정보 보호 및 보안 → 보안 → 확인 없이 열기**에서 예외를 승인할 수 있습니다. 출처와 파일 무결성을 신뢰할 때만 실행해야 합니다.

'''
    text = text[:old_usage_start] + new_usage + text[old_usage_end:]
    text = replace_once(
        text,
        '''python -m apps.following_auto_liker.app
''',
        '''python -m apps.instagram_tools.app
''',
        "following unified development entry",
    )
    text = replace_once(
        text,
        '''pyinstaller --noconfirm --clean --windowed --onedir \\
  --name FollowingAutoLiker --paths . \\
  --collect-all playwright --copy-metadata playwright \\
  apps/following_auto_liker/app.py
''',
        '''pyinstaller --noconfirm --clean --windowed --onedir \\
  --name InstagramTools --paths . \\
  --collect-all playwright --copy-metadata playwright \\
  apps/instagram_tools/app.py
''',
        "following unified local package",
    )
    deploy_start = text.index("## 배포 빌드\n")
    text = text[:deploy_start] + '''## 배포 빌드

`.github/workflows/instagram-tools.yml`이 자동 좋아요와 미팔로워 정리를 함께 담은 통합 패키지를 만듭니다.

- Pull request와 `master` 반영 시: 회귀 테스트 후 Windows와 Apple Silicon/Intel macOS 산출물 생성
- 수동 실행: Actions의 `Instagram Tools`에서 `Run workflow`
- `instagram-tools-v*` 태그 푸시 시: `InstagramTools-Windows.zip`과 두 macOS DMG를 GitHub Release로 게시
'''
    path.write_text(text, encoding="utf-8")


def patch_cleaner_readme() -> None:
    path = Path("apps/non_follower_cleaner/README.md")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''기존 **팔로잉 자동 좋아요** 앱과 같은 전용 Chrome 프로필, 로그인 상태, 데이터 폴더와
단일 실행 잠금을 공유합니다. 두 앱을 동시에 실행할 수 없으므로 같은 Chrome 프로필을 함께
조작하는 상황을 막습니다.
''',
        '''이 기능은 현재 **Instagram Tools** 통합 앱의 **미팔로워 정리** 탭으로 배포됩니다.
자동 좋아요와 같은 전용 Chrome 프로필, 로그인 상태, 데이터 폴더와 작업 스레드를 공유하며,
두 작업을 동시에 실행하지 않아 같은 Chrome 프로필을 함께 조작하는 상황을 막습니다. 이
디렉터리는 기능 구현과 기존 단독 실행 진입점을 유지하지만 공식 패키지는 통합 앱으로만 생성됩니다.
''',
        "cleaner unified distribution notice",
    )
    text = replace_once(
        text,
        '''각 언팔로우 요청에서 HTTP 2xx가 돌아오면 완료로 기록합니다. Instagram이 JSON을 반환하면
실패·활동 제한 신호를 검사하고, 성공 응답을 빈 본문·일반 텍스트·HTML로 보내는 경우에는
HTTP 성공 상태를 사용합니다. 로그인·체크포인트 페이지로 이동하거나 활동 제한 신호가
확인되면 성공으로 간주하지 않습니다.
''',
        '''각 언팔로우 요청은 HTTP 2xx만으로 완료 처리하지 않습니다. 응답이 명시적으로
`following=false`를 확인해 주지 않으면 방금 요청한 계정 하나의 현재 관계를 다시 읽고,
실제로 팔로우가 해제된 경우에만 성공으로 기록합니다. POST 응답이 제한 시간 안에 도착하지
않아도 쓰기가 서버에 반영됐을 수 있으므로 같은 사후 확인을 수행합니다. 기본 엔드포인트가
관계를 바꾸지 않았을 때만 대체 웹 엔드포인트를 한 번 시도하며, 로그인·체크포인트·활동 제한
신호 또는 확인 불가능한 상태에서는 추가 작업을 중지합니다.
''',
        "cleaner verified write documentation",
    )
    usage_start = text.index("## 가장 쉬운 사용법\n")
    usage_end = text.index("## 저장 데이터\n", usage_start)
    new_usage = '''## 가장 쉬운 사용법

저장소의 Releases 또는 GitHub Actions에서 운영체제에 맞는 통합 패키지를 받습니다.

- Windows: `InstagramTools-Windows`
- Apple Silicon Mac: `InstagramTools-macOS-Apple-Silicon`
- Intel Mac: `InstagramTools-macOS-Intel`

Windows Actions 산출물은 실행 폴더를 GitHub가 한 번 ZIP으로 감싼 것이므로 한 번만 풀면
`InstagramTools.exe`가 나옵니다. macOS Actions 산출물은 GitHub ZIP을 한 번 풀어 DMG를 연 뒤
`InstagramTools.app`을 `Applications`로 옮깁니다. Releases에서는 Windows ZIP과 두 DMG를
직접 받을 수 있습니다.

현재 macOS 앱은 Developer ID 서명과 Apple 공증을 하지 않았습니다. 첫 실행이 차단되면 앱을
한 번 실행한 뒤 **시스템 설정 → 개인정보 보호 및 보안 → 보안 → 확인 없이 열기**에서 예외를
승인합니다. 출처와 파일 무결성을 신뢰할 때만 실행해야 합니다.

통합 앱의 **미팔로워 정리** 탭에서 **목록 확인**을 누르고, 첫 사용이면 열린 Chrome에서 직접
로그인합니다. 이후에는 자동 좋아요 탭과 같은 Chrome 창과 로그인 상태를 재사용합니다.

'''
    text = text[:usage_start] + new_usage + text[usage_end:]
    text = replace_once(
        text,
        '''python -m apps.non_follower_cleaner.app
''',
        '''python -m apps.instagram_tools.app
''',
        "cleaner unified development entry",
    )
    text = replace_once(
        text,
        '''pyinstaller --noconfirm --clean --windowed --onedir \\
  --name NonFollowerCleaner --paths . \\
  --collect-all playwright --copy-metadata playwright \\
  apps/non_follower_cleaner/app.py
''',
        '''pyinstaller --noconfirm --clean --windowed --onedir \\
  --name InstagramTools --paths . \\
  --collect-all playwright --copy-metadata playwright \\
  apps/instagram_tools/app.py
''',
        "cleaner unified local package",
    )
    deploy_start = text.index("## 배포 빌드\n")
    risk_start = text.index("## 중요한 위험\n", deploy_start)
    new_deploy = '''## 배포 빌드

`.github/workflows/instagram-tools.yml`이 자동 좋아요와 미팔로워 정리를 함께 담은 통합 패키지를 만듭니다.

- Pull request와 `master` 반영 시: 회귀 테스트 후 Windows와 Apple Silicon/Intel macOS 산출물 생성
- 수동 실행: Actions의 `Instagram Tools`에서 `Run workflow`
- `instagram-tools-v*` 태그 푸시 시: `InstagramTools-Windows.zip`과 두 macOS DMG를 GitHub Release로 게시

'''
    text = text[:deploy_start] + new_deploy + text[risk_start:]
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_browser()
    patch_worker()
    patch_tests()
    patch_following_readme()
    patch_cleaner_readme()


if __name__ == "__main__":
    main()
