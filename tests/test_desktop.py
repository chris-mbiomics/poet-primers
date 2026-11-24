"""Desktop window mode (``poet-primers serve --window``), tested without a GUI."""

from __future__ import annotations

import sys
import types
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from poet_primers import cli, desktop


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    seen: list[int] = []

    def fake_run_window(port: int) -> int:
        seen.append(port)
        return 0

    monkeypatch.setattr(desktop, "run_window", fake_run_window)
    return seen


def test_window_default_port(calls: list[int]) -> None:
    assert cli.main(["serve", "--window"]) == 0
    assert calls == [cli.WINDOW_PORT]


def test_window_explicit_port(calls: list[int]) -> None:
    assert cli.main(["serve", "--window", "--port", "9000"]) == 0
    assert calls == [9000]


@pytest.mark.parametrize(
    "extra", [["--debug"], ["--open"], ["--host", "0.0.0.0"]], ids=["debug", "open", "host"]
)
def test_window_rejects_incompatible_options(calls: list[int], extra: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["serve", "--window", *extra])
    assert exc.value.code == 2
    assert calls == []


def test_missing_pywebview(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "webview", None)
    assert desktop.run_window(0) == 1
    assert "poet-primers[desktop]" in capsys.readouterr().err


class FakeWindow:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[tuple[Any, str]] = []

    def create_file_dialog(self, kind: Any, save_filename: str) -> Any:
        self.calls.append((kind, save_filename))
        return self.result


def test_save_file(tmp_path: Path) -> None:
    target = tmp_path / "out.tsv"
    api = desktop._Api(types.SimpleNamespace(SAVE_DIALOG="save"))
    api._window = FakeWindow(str(target))
    assert api.save_file("../primers.tsv", "a\tb\n")
    assert target.read_text(encoding="utf-8") == "a\tb\n"
    assert api._window.calls == [("save", "primers.tsv")]


def test_save_file_cancelled() -> None:
    api = desktop._Api(types.SimpleNamespace(SAVE_DIALOG="save"))
    api._window = FakeWindow(None)
    assert not api.save_file("x.txt", "x")


def test_run_window_lifecycle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The window loads the page from the background server, which stops on close."""
    seen: dict[str, Any] = {}

    def create_window(title: str, url: str, **kwargs: Any) -> object:
        seen["title"], seen["url"], seen["api"] = title, url, kwargs["js_api"]
        return object()

    def start(**kwargs: Any) -> None:
        seen["start"] = kwargs
        with urllib.request.urlopen(seen["url"], timeout=5) as response:
            seen["status"] = response.status

    fake = types.SimpleNamespace(
        settings={}, SAVE_DIALOG="save", create_window=create_window, start=start
    )
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(desktop, "_data_dir", lambda: tmp_path / "store")
    monkeypatch.setattr(desktop, "_gui", lambda: None)

    assert desktop.run_window(0) == 0
    assert seen["status"] == 200
    assert seen["title"] == "Primer & Oligo Evaluation Toolkit"
    assert seen["start"] == {
        "gui": None,
        "private_mode": False,
        "storage_path": str(tmp_path / "store"),
    }
    assert fake.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is True
    assert (tmp_path / "store").is_dir()
    with pytest.raises(OSError):
        urllib.request.urlopen(seen["url"], timeout=1)


@pytest.mark.parametrize("argv", [[], ["-psn_0_12345"]], ids=["no-args", "macos-psn"])
def test_no_arguments_opens_window(calls: list[int], argv: list[str]) -> None:
    assert cli.main(argv) == 0
    assert calls == [cli.WINDOW_PORT]


def test_arguments_still_reach_cli(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyse", "GAGAATGTGGGCTCTCAAGC"]) == 0
    assert "Forward: GAGAATGTGGGCTCTCAAGC" in capsys.readouterr().out


@pytest.mark.skipif(sys.platform == "win32", reason="hides the console on Windows")
def test_console_hiding_is_noop_elsewhere() -> None:
    from poet_primers._winconsole import hide_if_owned

    assert hide_if_owned() is False
