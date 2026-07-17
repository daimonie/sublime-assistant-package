"""Unit tests for assistant.loop_runner — /loop and /research marker protocol."""
from __future__ import annotations

from assistant import loop_runner


def test_build_iteration_prompt_contains_goal_and_counts():
    prompt = loop_runner.build_iteration_prompt("ship the widget", 3, 8)
    assert "ship the widget" in prompt
    assert "iteration 3 of at most 8" in prompt
    assert "LOOP_STATUS" in prompt


def test_is_goal_complete_detects_marker():
    reply = "Did the work.\n\n<!-- LOOP_STATUS: complete -->"
    assert loop_runner.is_goal_complete(reply)


def test_is_goal_complete_is_case_and_whitespace_tolerant():
    reply = "Done.\n<!--LOOP_STATUS:   Complete  -->"
    assert loop_runner.is_goal_complete(reply)


def test_is_goal_complete_false_for_continue_marker():
    reply = "Made progress.\n<!-- LOOP_STATUS: continue: fetch the next page -->"
    assert not loop_runner.is_goal_complete(reply)


def test_is_goal_complete_false_when_no_marker():
    reply = "I did some things but forgot the marker."
    assert not loop_runner.is_goal_complete(reply)


def test_has_status_marker_true_for_either_form():
    assert loop_runner.has_status_marker("text\n<!-- LOOP_STATUS: complete -->")
    assert loop_runner.has_status_marker("text\n<!-- LOOP_STATUS: continue: do X -->")


def test_has_status_marker_false_when_absent():
    assert not loop_runner.has_status_marker("just some text, no marker at all")


def test_extract_next_step_returns_captured_text():
    reply = "Progress notes.\n<!-- LOOP_STATUS: continue: fetch the pricing page next -->"
    assert loop_runner.extract_next_step(reply) == "fetch the pricing page next"


def test_extract_next_step_empty_when_absent_or_complete():
    assert loop_runner.extract_next_step("no marker here") == ""
    assert loop_runner.extract_next_step("<!-- LOOP_STATUS: complete -->") == ""


def test_build_research_goal_contains_topic_and_mentions_sources():
    goal = loop_runner.build_research_goal("rust async runtimes")
    assert "rust async runtimes" in goal
    assert "fetch_url" in goal
    assert "cite" in goal.lower()
