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
        '''class _AmbiguousWriteTimeout(FriendshipRequestError):
    """A POST may have completed even though the local fetch timed out."""
''',
        '''class _AmbiguousWriteFailure(FriendshipRequestError):
    """A POST may have completed even though its response was not observed."""

    def __init__(
        self,
        message: str,
        *,
        confirmation: str,
        diagnostic: str,
    ) -> None:
        super().__init__(message)
        self.confirmation = confirmation
        self.diagnostic = diagnostic
''',
        "ambiguous write failure type",
    )
    text = replace_once(
        text,
        '''            except _AmbiguousWriteTimeout:
                confirmation = self._confirm_unfollow_after_write(
                    user_id,
                    attempt_number=attempt_number,
                    status_code=0,
                    confirmation="post_timeout_friendship_check",
                )
                if confirmation is not None:
                    return confirmation
                diagnostics.append(f"시도 {attempt_number}: 요청 시간 초과 후 팔로우 상태가 그대로임")
                continue
''',
        '''            except _AmbiguousWriteFailure as exc:
                confirmation = self._confirm_unfollow_after_write(
                    user_id,
                    attempt_number=attempt_number,
                    status_code=0,
                    confirmation=exc.confirmation,
                )
                if confirmation is not None:
                    return confirmation
                diagnostics.append(
                    f"시도 {attempt_number}: {exc.diagnostic} 후 팔로우 상태가 그대로임"
                )
                continue
''',
        "confirm ambiguous write failures",
    )
    text = replace_once(
        text,
        '''        if result.get("timedOut"):
            message = f"Instagram 네트워크 요청이 {self.request_timeout_seconds:g}초 안에 완료되지 않았습니다."
            if method.upper() == "POST":
                raise _AmbiguousWriteTimeout(message)
            raise FriendshipRequestError(f"{message} 작업을 중지했습니다.")
        if result.get("networkError"):
            raise FriendshipRequestError(f"Instagram 네트워크 요청에 실패했습니다: {result['networkError']}")
''',
        '''        if result.get("timedOut"):
            message = f"Instagram 네트워크 요청이 {self.request_timeout_seconds:g}초 안에 완료되지 않았습니다."
            if method.upper() == "POST":
                raise _AmbiguousWriteFailure(
                    message,
                    confirmation="post_timeout_friendship_check",
                    diagnostic="요청 시간 초과",
                )
            raise FriendshipRequestError(f"{message} 작업을 중지했습니다.")
        if result.get("networkError"):
            network_error = str(result["networkError"])
            message = f"Instagram 네트워크 요청에 실패했습니다: {network_error}"
            if method.upper() == "POST":
                raise _AmbiguousWriteFailure(
                    message,
                    confirmation="post_network_error_friendship_check",
                    diagnostic=f"네트워크 오류 ({network_error})",
                )
            raise FriendshipRequestError(message)
''',
        "treat POST network errors as ambiguous writes",
    )
    path.write_text(text, encoding="utf-8")


def patch_app() -> None:
    path = Path("apps/instagram_tools/app.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''        self.running_kind = ""
        self.closing = False
''',
        '''        self.running_kind = ""
        self.stop_requested_kind = ""
        self.closing = False
''',
        "initialize stop request state",
    )
    text = replace_once(
        text,
        '''    def _begin_operation(self, kind: str, log_message: str) -> None:
        self.running_kind = kind
''',
        '''    def _begin_operation(self, kind: str, log_message: str) -> None:
        self.stop_requested_kind = ""
        self.running_kind = kind
''',
        "reset stop request at operation start",
    )
    text = replace_once(
        text,
        '''        self.worker.stop_current()
        self.global_status_var.set("중지 요청됨")
''',
        '''        self.stop_requested_kind = self.running_kind
        self.worker.stop_current()
        self.global_status_var.set("중지 요청됨")
''',
        "record stop request independently",
    )
    text = replace_once(
        text,
        '''                elif kind == "status":
                    self.global_status_var.set(str(payload))
                elif kind == "auto_status" and isinstance(payload, dict):
                    self._apply_auto_status(payload)
                elif kind == "cleaner_progress" and isinstance(payload, dict):
                    self._apply_cleaner_progress(payload)
                elif kind == "scan_result" and isinstance(payload, ScanResult):
                    self._apply_scan_result(payload)
                elif kind == "unfollow_result" and isinstance(payload, UnfollowSummary):
                    self._apply_unfollow_result(payload)
                elif kind == "unfollow_error" and isinstance(payload, tuple):
                    message, summary = payload
                    if isinstance(summary, UnfollowSummary):
                        self._apply_unfollow_result(summary)
''',
        '''                elif kind == "status":
                    if not self.stop_requested_kind:
                        self.global_status_var.set(str(payload))
                elif kind == "auto_status" and isinstance(payload, dict):
                    self._apply_auto_status(payload)
                    self._restore_pending_stop_status()
                elif kind == "cleaner_progress" and isinstance(payload, dict):
                    self._apply_cleaner_progress(payload)
                    self._restore_pending_stop_status()
                elif kind == "scan_result" and isinstance(payload, ScanResult):
                    self._apply_scan_result(payload)
                    self._restore_pending_stop_status()
                elif kind == "unfollow_result" and isinstance(payload, UnfollowSummary):
                    self._apply_unfollow_result(payload)
                    self._restore_pending_stop_status()
                elif kind == "unfollow_error" and isinstance(payload, tuple):
                    self.stop_requested_kind = ""
                    message, summary = payload
                    if isinstance(summary, UnfollowSummary):
                        self._apply_unfollow_result(summary)
''',
        "preserve pending stop across queued events",
    )
    text = replace_once(
        text,
        '''                elif kind == "error":
                    self._append_log(str(payload))
                    if self.running_kind == "auto_like":
''',
        '''                elif kind == "error":
                    self._append_log(str(payload))
                    self.stop_requested_kind = ""
                    if self.running_kind == "auto_like":
''',
        "let operation errors override stop request",
    )
    text = replace_once(
        text,
        '''                elif kind == "operation_finished":
                    self._finalize_finished_tab_status(payload)
                    self.running_kind = ""
                    self.global_status_var.set("대기 중 · Chrome 창을 다음 작업에 재사용합니다.")
''',
        '''                elif kind == "operation_finished":
                    self._finalize_finished_tab_status(payload)
                    self.running_kind = ""
                    self.stop_requested_kind = ""
                    self.global_status_var.set("대기 중 · Chrome 창을 다음 작업에 재사용합니다.")
''',
        "clear stop request on completion",
    )
    text = replace_once(
        text,
        '''    def _finalize_finished_tab_status(self, finished_kind: object) -> None:
        if finished_kind == "auto_like" and self.auto_status_var.get() == "중지 요청됨":
            self.auto_status_var.set("사용자 요청으로 중지")
        elif finished_kind in {"scan", "unfollow"} and self.cleaner_status_var.get() == "중지 요청됨":
            self.cleaner_status_var.set("사용자 요청으로 중지")

    def _apply_auto_status(self, status: dict[str, object]) -> None:
''',
        '''    def _restore_pending_stop_status(self) -> None:
        if self.stop_requested_kind == "auto_like":
            self.auto_status_var.set("중지 요청됨")
        elif self.stop_requested_kind in {"scan", "unfollow"}:
            self.cleaner_status_var.set("중지 요청됨")

    def _finalize_finished_tab_status(self, finished_kind: object) -> None:
        if self.stop_requested_kind != finished_kind:
            return
        if finished_kind == "auto_like":
            self.auto_status_var.set("사용자 요청으로 중지")
        elif finished_kind in {"scan", "unfollow"}:
            self.cleaner_status_var.set("사용자 요청으로 중지")

    def _apply_auto_status(self, status: dict[str, object]) -> None:
''',
        "finalize using independent stop state",
    )
    path.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    main_path = Path("tests/regression/test_instagram_tools.py")
    main_text = main_path.read_text(encoding="utf-8")
    main_text = replace_once(
        main_text,
        '''        app.auto_status_var = FakeStringVar("중지 요청됨")
        app.cleaner_status_var = FakeStringVar("목록 확인 완료")

        app._finalize_finished_tab_status("auto_like")
''',
        '''        app.auto_status_var = FakeStringVar("중지 요청됨")
        app.cleaner_status_var = FakeStringVar("목록 확인 완료")
        app.stop_requested_kind = "auto_like"

        app._finalize_finished_tab_status("auto_like")
''',
        "auto stop state test",
    )
    main_text = replace_once(
        main_text,
        '''        app.auto_status_var.set("오류로 중지")
        app.cleaner_status_var.set("중지 요청됨")
        app._finalize_finished_tab_status("scan")
''',
        '''        app.auto_status_var.set("오류로 중지")
        app.cleaner_status_var.set("중지 요청됨")
        app.stop_requested_kind = "scan"
        app._finalize_finished_tab_status("scan")
''',
        "cleaner stop state test",
    )
    main_path.write_text(main_text, encoding="utf-8")

    final_path = Path("tests/regression/test_instagram_tools_final_review.py")
    final_text = final_path.read_text(encoding="utf-8")
    final_text = replace_once(
        final_text,
        '''        self.assertTrue(str(calls[1]["path"]).startswith("/api/v1/friendships/show/2/"))


class FailingUnfollowWorker(InstagramAutomationWorker):
''',
        '''        self.assertTrue(str(calls[1]["path"]).startswith("/api/v1/friendships/show/2/"))

    def test_non_timeout_post_network_error_is_verified_before_retry(self) -> None:
        session = FakeSession(
            [
                {"networkError": "TypeError: Failed to fetch", "timedOut": False},
                response(
                    SHOW_URL,
                    text='{"status":"ok","following":false}',
                    content_type="application/json",
                ),
            ]
        )
        backend = VerifiedFriendshipBackend(session)

        payload = backend.unfollow("2")

        self.assertEqual(
            payload["_transport"]["confirmation"],
            "post_network_error_friendship_check",
        )
        self.assertIs(payload["friendship_status"]["following"], False)
        calls = [arguments for _script, arguments in session.page.evaluate_calls]
        self.assertEqual([call["method"] for call in calls], ["POST", "GET"])


class FailingUnfollowWorker(InstagramAutomationWorker):
''',
        "network error regression",
    )
    final_text = replace_once(
        final_text,
        '''        app.running_kind = running_kind
        app.auto_status_var = FakeStringVar("대기 중")
''',
        '''        app.running_kind = running_kind
        app.stop_requested_kind = ""
        app.auto_status_var = FakeStringVar("대기 중")
''',
        "profile error state stop field",
    )
    final_text = replace_once(
        final_text,
        '''

if __name__ == "__main__":
    unittest.main()
''',
        '''

class QueuedProgressAfterStopRegressionTestCase(unittest.TestCase):
    @staticmethod
    def app_state(running_kind: str) -> InstagramToolsApp:
        app = object.__new__(InstagramToolsApp)
        app.events = queue.Queue()
        app.running_kind = running_kind
        app.stop_requested_kind = running_kind
        app.global_status_var = FakeStringVar("중지 요청됨")
        app.auto_status_var = FakeStringVar("중지 요청됨")
        app.auto_likes_var = FakeStringVar("0개")
        app.auto_last_scan_var = FakeStringVar("아직 없음")
        app.cleaner_status_var = FakeStringVar("중지 요청됨")
        app.closing = True
        app._refresh_controls = Mock()
        return app

    def test_queued_auto_progress_cannot_erase_stop_completion(self) -> None:
        app = self.app_state("auto_like")
        app.events.put(
            (
                "auto_status",
                {
                    "message": "피드 확인 중",
                    "session_likes": 2,
                    "last_scan_at": "",
                },
            )
        )

        app._drain_events()

        self.assertEqual(app.auto_status_var.get(), "중지 요청됨")
        self.assertEqual(app.auto_likes_var.get(), "2개")

        app.events.put(("operation_finished", "auto_like"))
        app._drain_events()

        self.assertEqual(app.auto_status_var.get(), "사용자 요청으로 중지")
        self.assertEqual(app.running_kind, "")
        self.assertEqual(app.stop_requested_kind, "")

    def test_queued_cleaner_progress_cannot_erase_stop_completion(self) -> None:
        app = self.app_state("scan")
        app.events.put(("cleaner_progress", {"phase": "followers", "collected": 25}))

        app._drain_events()

        self.assertEqual(app.cleaner_status_var.get(), "중지 요청됨")

        app.events.put(("operation_finished", "scan"))
        app._drain_events()

        self.assertEqual(app.cleaner_status_var.get(), "사용자 요청으로 중지")
        self.assertEqual(app.running_kind, "")
        self.assertEqual(app.stop_requested_kind, "")


if __name__ == "__main__":
    unittest.main()
''',
        "queued progress stop regressions",
    )
    final_path.write_text(final_text, encoding="utf-8")


def main() -> None:
    patch_browser()
    patch_app()
    patch_tests()


if __name__ == "__main__":
    main()
