"""
Tests for PromptStore — versioned prompt management.
"""
from __future__ import annotations

import pytest

from agentix.prompts.store import PromptStore


@pytest.fixture
def ps(tmp_db) -> PromptStore:
    return PromptStore(str(tmp_db))


# ---------------------------------------------------------------------------
# create + get_by_id
# ---------------------------------------------------------------------------

def test_create_returns_prompt_version(ps):
    p = ps.create("research-system", "1.0.0", "You are a researcher.")
    assert p.id.startswith("prompt_")
    assert p.name == "research-system"
    assert p.version == "1.0.0"
    assert p.content == "You are a researcher."
    assert p.status == "draft"


def test_create_auto_detects_variables(ps):
    p = ps.create("greeting", "1.0.0", "Hello {{name}}, you are {{role}}.")
    assert "name" in p.variables
    assert "role" in p.variables


def test_create_explicit_variables(ps):
    p = ps.create("tmpl", "1.0.0", "Hello {{name}}", variables=["name", "title"])
    assert p.variables == ["name", "title"]


def test_create_default_status_is_draft(ps):
    p = ps.create("x", "1.0.0", "content")
    assert p.status == "draft"


def test_get_by_id_returns_none_for_missing(ps):
    assert ps.get_by_id("prompt_nonexistent") is None


def test_create_duplicate_version_raises(ps):
    ps.create("dup", "1.0.0", "v1")
    with pytest.raises(Exception, match="(?i)unique|already exist"):
        ps.create("dup", "1.0.0", "v1-dup")


# ---------------------------------------------------------------------------
# publish + get_latest
# ---------------------------------------------------------------------------

def test_publish_changes_status_to_active(ps):
    p = ps.create("sysPrompt", "1.0.0", "content")
    ps.publish(p.id)
    updated = ps.get_by_id(p.id)
    assert updated.status == "active"


def test_get_latest_returns_active_version(ps):
    p = ps.create("multi", "1.0.0", "v1 content")
    ps.publish(p.id)
    latest = ps.get_latest("multi")
    assert latest is not None
    assert latest.version == "1.0.0"


def test_get_latest_returns_none_for_draft_only(ps):
    ps.create("draft-only", "1.0.0", "content")
    assert ps.get_latest("draft-only") is None


def test_get_latest_returns_most_recent_active(ps):
    p1 = ps.create("versioned", "1.0.0", "v1")
    p2 = ps.create("versioned", "2.0.0", "v2")
    ps.publish(p1.id)
    ps.publish(p2.id)
    latest = ps.get_latest("versioned")
    assert latest.version == "2.0.0"


def test_get_latest_not_found_returns_none(ps):
    assert ps.get_latest("does-not-exist") is None


# ---------------------------------------------------------------------------
# archive
# ---------------------------------------------------------------------------

def test_archive_changes_status(ps):
    p = ps.create("arc", "1.0.0", "content")
    ps.publish(p.id)
    ps.archive(p.id)
    updated = ps.get_by_id(p.id)
    assert updated.status == "archived"


def test_archived_not_returned_by_get_latest(ps):
    p = ps.create("arc2", "1.0.0", "content")
    ps.publish(p.id)
    ps.archive(p.id)
    assert ps.get_latest("arc2") is None


# ---------------------------------------------------------------------------
# update_content + delete
# ---------------------------------------------------------------------------

def test_update_content_for_draft(ps):
    p = ps.create("updatable", "1.0.0", "old content")
    ps.update_content(p.id, "new content {{var}}")
    updated = ps.get_by_id(p.id)
    assert updated.content == "new content {{var}}"
    assert "var" in updated.variables


def test_delete_removes_draft(ps):
    p = ps.create("deletable", "1.0.0", "content")
    ps.delete(p.id)
    assert ps.get_by_id(p.id) is None


def test_delete_does_not_remove_active(ps):
    p = ps.create("protected", "1.0.0", "content")
    ps.publish(p.id)
    ps.delete(p.id)  # should silently do nothing (WHERE status='draft')
    assert ps.get_by_id(p.id) is not None


# ---------------------------------------------------------------------------
# get_version + list_versions + list_names
# ---------------------------------------------------------------------------

def test_get_version_returns_specific_version(ps):
    ps.create("specific", "1.0.0", "v1")
    ps.create("specific", "2.0.0", "v2")
    p = ps.get_version("specific", "1.0.0")
    assert p is not None
    assert p.content == "v1"


def test_get_version_not_found(ps):
    assert ps.get_version("nothing", "9.9.9") is None


def test_list_versions_returns_all(ps):
    ps.create("lv", "1.0.0", "v1")
    ps.create("lv", "2.0.0", "v2")
    versions = ps.list_versions(name="lv")
    assert len(versions) == 2


def test_list_versions_filter_by_status(ps):
    p = ps.create("filt", "1.0.0", "v1")
    ps.create("filt", "2.0.0", "v2")
    ps.publish(p.id)
    active = ps.list_versions(name="filt", status="active")
    assert len(active) == 1
    assert active[0].version == "1.0.0"


def test_list_names_returns_distinct(ps):
    ps.create("alpha", "1.0.0", "a")
    ps.create("alpha", "2.0.0", "a2")
    ps.create("beta", "1.0.0", "b")
    names = ps.list_names()
    assert sorted(names) == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def test_render_substitutes_variables(ps):
    p = ps.create("render-test", "1.0.0", "Hello {{name}}! You are {{role}}.")
    rendered = p.render({"name": "Alice", "role": "analyst"})
    assert rendered == "Hello Alice! You are analyst."


def test_render_missing_variable_raises(ps):
    p = ps.create("render-fail", "1.0.0", "Hello {{name}}")
    with pytest.raises(KeyError, match="name"):
        p.render({})


def test_render_no_variables_noop(ps):
    p = ps.create("plain", "1.0.0", "No variables here.")
    assert p.render({}) == "No variables here."


def test_to_dict_contains_expected_fields(ps):
    p = ps.create("dict-test", "1.0.0", "hello {{x}}", tags={"env": "prod"})
    d = p.to_dict()
    assert "id" in d
    assert "content" in d
    assert "variables" in d
    assert "tags" in d
    assert d["tags"]["env"] == "prod"
