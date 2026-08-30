from __future__ import annotations

import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import apps.instagram_tools.app as app_module
from apps.following_auto_liker.storage import Storage, StoragePaths


class _FakeThread:
    @staticmethod
    def is_alive() -> bool:
        return False


class _FakeWorker:
    def __init__(self, _storage: Storage, _events) -> None:
        self.thread = _FakeThread()

    def start(self) -> None:
        return None

    def submit(self, _kind: str, **_payload) -> bool:
        return True

    def stop_current(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


def _assert_fully_visible(app: app_module.InstagramToolsApp, widget, label: str) -> None:
    app.update_idletasks()
    assert widget.winfo_ismapped(), f"{label} is not mapped"
    top = widget.winfo_rooty() - app.winfo_rooty()
    height = widget.winfo_height()
    assert height > 1, f"{label} collapsed to {height}px"
    assert top >= 0, f"{label} starts above the window: {top}px"
    assert top + height <= app.winfo_height(), (
        f"{label} extends below the window: {top + height}px > {app.winfo_height()}px"
    )


def _assert_tree_rows_visible(
    app: app_module.InstagramToolsApp,
    count: int = 4,
) -> None:
    item_ids: list[str] = []
    for index in range(count):
        item_id = f"layout-row-{index}"
        app.tree.insert(
            "",
            "end",
            iid=item_id,
            values=(f"@user{index}", f"User {index}", "아니오", "아니오"),
        )
        item_ids.append(item_id)
    app.tree.selection_set(item_ids)
    app.update_idletasks()

    for item_id in item_ids:
        bounds = app.tree.bbox(item_id)
        assert bounds, f"{item_id} is not visible in the account list"
        _x, top, _width, height = bounds
        assert top + height <= app.tree.winfo_height(), (
            f"{item_id} extends below the tree: {top + height}px > {app.tree.winfo_height()}px"
        )


def main() -> None:
    original_worker = app_module.InstagramAutomationWorker
    app_module.InstagramAutomationWorker = _FakeWorker
    app = None
    try:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            storage = Storage(
                StoragePaths(
                    root=root,
                    config=root / "config.json",
                    chrome_profile=root / "chrome-profile",
                    log=root / "app.log",
                    instance_lock=root / "app.lock",
                )
            )
            app = app_module.InstagramToolsApp(storage=storage)
            app.update_idletasks()

            expected_screen_height = int(os.environ.get("EXPECTED_SCREEN_HEIGHT", "768"))
            expected_compact = os.environ.get("EXPECTED_COMPACT", "1") == "1"
            expected_window_height = app_module.window_height_for_screen(expected_screen_height)

            assert app.winfo_screenheight() == expected_screen_height
            assert app.compact_ui is expected_compact
            assert abs(app.winfo_height() - expected_window_height) <= 2

            app.notebook.select(0)
            app.update_idletasks()
            for widget, label in (
                (app.notebook, "notebook"),
                (app.auto_status_frame, "auto status"),
                (app.log_frame, "shared log"),
                (app.bottom_bar, "bottom controls"),
                (app.clear_browser_button, "profile reset button"),
                (app.open_data_button, "data folder button"),
            ):
                _assert_fully_visible(app, widget, label)

            app.notebook.select(1)
            app.update_idletasks()
            for widget, label in (
                (app.cleaner_status_frame, "cleaner status"),
                (app.cleaner_list_frame, "cleaner account list"),
                (app.tree, "cleaner tree"),
                (app.log_frame, "shared log on cleaner tab"),
                (app.bottom_bar, "bottom controls on cleaner tab"),
            ):
                _assert_fully_visible(app, widget, label)
            _assert_tree_rows_visible(app)
    finally:
        if app is not None:
            logger = app.logger
            app.destroy()
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
        app_module.InstagramAutomationWorker = original_worker
        logging.shutdown()


if __name__ == "__main__":
    main()
