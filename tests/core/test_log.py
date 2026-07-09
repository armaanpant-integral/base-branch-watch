from __future__ import annotations

from base_branch_watch.core import log


def test_append_writes_line_with_newline(bbw_config_dir):
    log.append("hello")

    assert log.log_path().read_text() == "hello\n"


def test_append_accumulates_in_order(bbw_config_dir):
    log.append("first")
    log.append("second")

    assert log.log_path().read_text() == "first\nsecond\n"


def test_rotate_if_needed_truncates_at_day_boundary(bbw_config_dir):
    log.log_path().write_text("stale content from yesterday\n")
    marker = bbw_config_dir / log.DAY_MARKER_FILENAME
    marker.write_text("2020-01-01")

    log.rotate_if_needed(today="2020-01-02")

    assert log.log_path().read_text() == ""
    assert marker.read_text().strip() == "2020-01-02"


def test_rotate_if_needed_same_day_does_not_retruncate(bbw_config_dir):
    log.rotate_if_needed(today="2020-01-02")
    with open(log.log_path(), "a") as f:
        f.write("fresh line\n")

    log.rotate_if_needed(today="2020-01-02")

    assert log.log_path().read_text() == "fresh line\n"
