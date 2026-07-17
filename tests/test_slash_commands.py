"""Unit tests for assistant.slash_commands — slash command parsing/expansion."""
from __future__ import annotations

from assistant import slash_commands


def test_bare_command_expands_to_template():
    display, api_query, cmd = slash_commands.parse("/explain")
    assert display == "/explain"
    assert cmd == "/explain"
    assert api_query == slash_commands.TEMPLATES["/explain"]


def test_command_with_trailing_text_appends_after_template():
    display, api_query, cmd = slash_commands.parse("/fix there's a race condition")
    assert cmd == "/fix"
    assert display == "/fix there's a race condition"
    assert api_query == slash_commands.TEMPLATES["/fix"] + "there's a race condition"


def test_special_command_is_returned_unexpanded():
    display, api_query, cmd = slash_commands.parse("/init")
    assert cmd == "/init"
    assert display == "/init"
    assert api_query == "/init"


def test_non_command_text_passes_through():
    display, api_query, cmd = slash_commands.parse("just a normal question")
    assert cmd == ""
    assert display == api_query == "just a normal question"


def test_command_prefix_without_space_does_not_match():
    """'/fixture' should not be treated as '/fix' + 'ture'."""
    display, api_query, cmd = slash_commands.parse("/fixture")
    assert cmd == ""


def test_research_with_topic_is_returned_unexpanded():
    """/research is a SPECIAL command (drives the /loop engine), not a TEMPLATES entry."""
    display, api_query, cmd = slash_commands.parse("/research rust async runtimes")
    assert cmd == "/research"
    assert display == api_query == "/research rust async runtimes"


def test_goal_and_loop_are_special_with_trailing_text():
    for cmd_name in ("/goal", "/loop"):
        display, api_query, cmd = slash_commands.parse(f"{cmd_name} ship the widget")
        assert cmd == cmd_name
        assert display == api_query == f"{cmd_name} ship the widget"


def test_loop_without_argument_matches_bare_command():
    display, api_query, cmd = slash_commands.parse("/loop")
    assert cmd == "/loop"
    assert display == api_query == "/loop"
