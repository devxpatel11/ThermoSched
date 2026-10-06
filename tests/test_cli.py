from __future__ import annotations

import pytest

from thermosched.cli import main


def test_initial_cli_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "Linux user-space CPU thermal pacing" in capsys.readouterr().out


def test_cli_reports_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert "thermosched 0.1.0" in capsys.readouterr().out
