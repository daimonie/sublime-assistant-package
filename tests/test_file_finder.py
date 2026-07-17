"""Unit tests for assistant.file_finder — the @filename / read_file resolver."""
from __future__ import annotations

from assistant import file_finder

from fakes import FakeView, FakeWindow


def test_finds_file_by_walking_up_from_active_files_directory(tmp_path):
    root = tmp_path / "project"
    sub = root / "src" / "nested"
    sub.mkdir(parents=True)
    (root / "docker-compose.yml").write_text("services: {}\n")
    active_file = sub / "main.py"
    active_file.write_text("print('hi')\n")

    active_view = FakeView(file_name=str(active_file))
    window = FakeWindow(active_view=active_view)

    result = file_finder.find(window, "docker-compose.yml")
    assert result == "services: {}\n"


def test_prefers_active_dir_match_over_open_tabs(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "config.yml").write_text("from active dir\n")
    active_file = root / "main.py"
    active_file.write_text("")

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other_config = other_dir / "config.yml"
    other_config.write_text("from other tab\n")

    active_view = FakeView(file_name=str(active_file))
    tab_view = FakeView(file_name=str(other_config))
    window = FakeWindow(active_view=active_view, views=[active_view, tab_view])

    result = file_finder.find(window, "config.yml")
    assert result == "from active dir\n"


def test_falls_back_to_open_tab_when_not_found_near_active_file(tmp_path):
    active_file = tmp_path / "main.py"
    active_file.write_text("")
    other_file = tmp_path / "elsewhere" / "helper.py"
    other_file.parent.mkdir()
    other_file.write_text("def helper(): pass\n")

    active_view = FakeView(file_name=str(active_file))
    tab_view = FakeView(file_name=str(other_file))
    window = FakeWindow(active_view=active_view, views=[active_view, tab_view])

    result = file_finder.find(window, "helper.py")
    assert result == "def helper(): pass\n"


def test_open_tab_ranking_prefers_longest_common_prefix_with_active_path(tmp_path):
    active_file = tmp_path / "projA" / "src" / "main.py"
    active_file.parent.mkdir(parents=True)
    active_file.write_text("")

    close_match = tmp_path / "projA" / "utils.py"
    close_match.write_text("# close\n")
    far_match_dir = tmp_path / "projB"
    far_match_dir.mkdir()
    far_match = far_match_dir / "utils.py"
    far_match.write_text("# far\n")

    active_view = FakeView(file_name=str(active_file))
    tab_far = FakeView(file_name=str(far_match))
    tab_close = FakeView(file_name=str(close_match))
    window = FakeWindow(active_view=active_view, views=[active_view, tab_far, tab_close])

    result = file_finder.find(window, "utils.py")
    assert result == "# close\n"


def test_inline_buffer_matched_by_tab_name_returns_immediately(tmp_path):
    active_file = tmp_path / "main.py"
    active_file.write_text("")
    active_view = FakeView(file_name=str(active_file))
    scratch_view = FakeView(file_name=None, name="scratch.txt", content="unsaved buffer content")
    window = FakeWindow(active_view=active_view, views=[active_view, scratch_view])

    result = file_finder.find(window, "scratch.txt")
    assert result == "unsaved buffer content"


def test_recursive_project_folder_walk_as_last_resort(tmp_path):
    folder = tmp_path / "myproject"
    nested = folder / "a" / "b"
    nested.mkdir(parents=True)
    target = nested / "deep.txt"
    target.write_text("deep content\n")

    window = FakeWindow(active_view=None, views=[], folders=[str(folder)])

    result = file_finder.find(window, "deep.txt")
    assert result == "deep content\n"


def test_recursive_walk_skips_ignored_directories(tmp_path):
    folder = tmp_path / "myproject"
    ignored = folder / "node_modules"
    ignored.mkdir(parents=True)
    (ignored / "target.txt").write_text("should not be found\n")

    window = FakeWindow(active_view=None, views=[], folders=[str(folder)])

    result = file_finder.find(window, "target.txt")
    assert result is None


def test_returns_none_when_file_not_found_anywhere(tmp_path):
    window = FakeWindow(active_view=None, views=[], folders=[str(tmp_path)])
    result = file_finder.find(window, "does_not_exist.txt")
    assert result is None


def test_returns_none_when_no_active_view_or_folders():
    window = FakeWindow()
    result = file_finder.find(window, "anything.txt")
    assert result is None


def test_filename_is_stripped_and_basenamed(tmp_path):
    folder = tmp_path / "myproject"
    folder.mkdir()
    (folder / "readme.md").write_text("hi\n")
    window = FakeWindow(active_view=None, views=[], folders=[str(folder)])

    result = file_finder.find(window, "  sub/dir/readme.md  ")
    assert result == "hi\n"


def test_unreadable_file_returns_none_instead_of_raising(tmp_path):
    folder = tmp_path / "myproject"
    folder.mkdir()
    bad_file = folder / "binary.dat"
    bad_file.write_bytes(b"\xff\xfe\x00\x01not-utf8\x80")
    window = FakeWindow(active_view=None, views=[], folders=[str(folder)])

    result = file_finder.find(window, "binary.dat")
    assert result is None
