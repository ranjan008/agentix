"""Tests for HITL CheckpointStore."""
from __future__ import annotations

import time

import pytest

from agentix.hitl.checkpoint import Checkpoint, CheckpointStore


@pytest.fixture
def cp_store(tmp_db):
    return CheckpointStore(str(tmp_db))


@pytest.fixture
def sample_cp():
    return Checkpoint(
        trigger_id="trig_abc123",
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ],
        pending_calls=[{"id": "tu_1", "name": "file_write", "input": {"path": "out.txt", "content": "data"}}],
        iteration=3,
    )


# --- save / load ---

def test_save_and_load(cp_store, sample_cp):
    cp_store.save(sample_cp)
    loaded = cp_store.load("trig_abc123")
    assert loaded is not None
    assert loaded.trigger_id == "trig_abc123"
    assert loaded.iteration == 3
    assert len(loaded.messages) == 2
    assert loaded.pending_calls[0]["name"] == "file_write"


def test_load_nonexistent_returns_none(cp_store):
    assert cp_store.load("does_not_exist") is None


def test_save_overwrites_existing(cp_store, sample_cp):
    cp_store.save(sample_cp)
    sample_cp.iteration = 7
    cp_store.save(sample_cp)
    loaded = cp_store.load("trig_abc123")
    assert loaded.iteration == 7


def test_messages_preserved_exactly(cp_store, sample_cp):
    cp_store.save(sample_cp)
    loaded = cp_store.load("trig_abc123")
    assert loaded.messages == sample_cp.messages


# --- delete ---

def test_delete_removes_checkpoint(cp_store, sample_cp):
    cp_store.save(sample_cp)
    cp_store.delete("trig_abc123")
    assert cp_store.load("trig_abc123") is None


def test_delete_nonexistent_is_noop(cp_store):
    cp_store.delete("no_such_trigger")  # should not raise


# --- expiry ---

def test_expired_checkpoint_returns_none(cp_store, sample_cp):
    sample_cp.expires_at = time.time() - 1  # already expired
    cp_store.save(sample_cp)
    assert cp_store.load("trig_abc123") is None


def test_non_expired_checkpoint_returned(cp_store, sample_cp):
    sample_cp.expires_at = time.time() + 3600
    cp_store.save(sample_cp)
    assert cp_store.load("trig_abc123") is not None


def test_purge_expired(cp_store):
    cp1 = Checkpoint("t1", [], [], expires_at=time.time() - 1)
    cp2 = Checkpoint("t2", [], [], expires_at=time.time() + 3600)
    cp_store.save(cp1)
    cp_store.save(cp2)
    removed = cp_store.purge_expired()
    assert removed == 1
    assert cp_store.load("t1") is None
    assert cp_store.load("t2") is not None


# --- to_dict round-trip ---

def test_to_dict_contains_all_fields(sample_cp):
    d = sample_cp.to_dict()
    assert set(d.keys()) >= {"trigger_id", "iteration", "messages", "pending_calls", "created_at"}
