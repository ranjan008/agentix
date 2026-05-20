"""
Tests for eval harness — dataset, scorers, and runner.
"""
from __future__ import annotations

import pytest

from agentix.eval.dataset import DatasetStore
from agentix.eval.scorers import (
    contains, exact_match, json_keys, regex_match, word_overlap, get_scorer
)
from agentix.eval.runner import EvalRunner


# ===========================================================================
# DatasetStore
# ===========================================================================

@pytest.fixture
def ds(tmp_db):
    return DatasetStore(str(tmp_db))


def test_create_dataset(ds):
    did = ds.create_dataset("test-set", agent_id="my-agent", description="desc")
    assert did.startswith("evds_")


def test_add_case(ds):
    did = ds.create_dataset("qa")
    cid = ds.add_case(did, input="What is 2+2?", expected="4")
    assert cid.startswith("case_")


def test_get_dataset_with_cases(ds):
    did = ds.create_dataset("qa2")
    ds.add_case(did, "Q1?", "A1")
    ds.add_case(did, "Q2?", "A2")
    dataset = ds.get_dataset(did)
    assert dataset is not None
    assert dataset.name == "qa2"
    assert len(dataset.cases) == 2


def test_get_dataset_by_name(ds):
    ds.create_dataset("named-ds")
    dataset = ds.get_dataset_by_name("named-ds")
    assert dataset is not None
    assert dataset.name == "named-ds"


def test_get_dataset_not_found(ds):
    assert ds.get_dataset("evds_missing") is None


def test_add_cases_bulk(ds):
    did = ds.create_dataset("bulk")
    ids = ds.add_cases_bulk(did, [
        {"input": "Q1", "expected": "A1"},
        {"input": "Q2", "expected": "A2"},
        {"input": "Q3", "expected": "A3"},
    ])
    assert len(ids) == 3
    dataset = ds.get_dataset(did)
    assert len(dataset.cases) == 3


def test_delete_dataset_removes_cases(ds):
    did = ds.create_dataset("deletable")
    ds.add_case(did, "Q", "A")
    ds.delete_dataset(did)
    assert ds.get_dataset(did) is None


def test_list_datasets(ds):
    ds.create_dataset("ds1")
    ds.create_dataset("ds2")
    datasets = ds.list_datasets()
    assert len(datasets) == 2


def test_case_tags_and_metadata(ds):
    did = ds.create_dataset("tagged")
    ds.add_case(did, "Q", "A", tags={"difficulty": "easy"}, metadata={"topic": "math"})
    dataset = ds.get_dataset(did)
    case = dataset.cases[0]
    assert case.tags["difficulty"] == "easy"
    assert case.metadata["topic"] == "math"


# ===========================================================================
# Scorers
# ===========================================================================

def test_exact_match_pass():
    s = exact_match("hello world", "hello world")
    assert s.passed is True
    assert s.value == 1.0


def test_exact_match_fail():
    s = exact_match("hello world", "goodbye")
    assert s.passed is False
    assert s.value == 0.0


def test_exact_match_strips_whitespace():
    s = exact_match("  hello  ", "hello")
    assert s.passed is True


def test_contains_pass():
    s = contains("The answer is 42 in total", "42")
    assert s.passed is True


def test_contains_fail():
    s = contains("The answer is 42", "99")
    assert s.passed is False


def test_contains_case_insensitive():
    s = contains("Hello World", "hello world")
    assert s.passed is True


def test_regex_match_pass():
    s = regex_match("Error code 404 not found", r"error code \d+")
    assert s.passed is True


def test_regex_match_fail():
    s = regex_match("success", r"error")
    assert s.passed is False


def test_regex_match_invalid_pattern():
    s = regex_match("text", r"[invalid")
    assert s.passed is False
    assert "regex" in s.explanation.lower()


def test_word_overlap_full_match():
    s = word_overlap("the quick brown fox", "the quick brown fox")
    assert s.value == 1.0


def test_word_overlap_partial():
    s = word_overlap("quick brown fox", "slow brown fox")
    assert 0.0 < s.value < 1.0


def test_word_overlap_no_match():
    s = word_overlap("hello world", "goodbye universe")
    assert s.value == 0.0


def test_json_keys_pass():
    actual = '{"name": "Alice", "score": 95, "passed": true}'
    expected = '{"name": "", "score": 0}'
    s = json_keys(actual, expected)
    assert s.passed is True


def test_json_keys_fail():
    actual = '{"name": "Alice"}'
    expected = '["name", "score"]'
    s = json_keys(actual, expected)
    assert s.passed is False
    assert "score" in s.explanation


def test_json_keys_extracts_from_text():
    actual = 'Here is the result: {"answer": "42", "confidence": 0.9}'
    expected = '["answer"]'
    s = json_keys(actual, expected)
    assert s.passed is True


def test_get_scorer_returns_callable():
    fn = get_scorer("contains")
    assert callable(fn)


def test_get_scorer_unknown_raises():
    with pytest.raises(ValueError, match="Unknown scorer"):
        get_scorer("not_a_real_scorer")


# ===========================================================================
# EvalRunner
# ===========================================================================

class _MockLLMResponse:
    def __init__(self, content):
        self.content = content
        self.stop_reason = "end_turn"
        self.usage = {"input_tokens": 5, "output_tokens": 10}


class MockLLM:
    def __init__(self, responses):
        self._responses = list(responses)
    async def complete(self, messages, **kwargs):
        if self._responses:
            return _MockLLMResponse(self._responses.pop(0))
        return _MockLLMResponse("")


@pytest.fixture
def runner(tmp_db):
    return EvalRunner(db_path=str(tmp_db), llm=None)


@pytest.fixture
def ds_with_cases(tmp_db):
    ds = DatasetStore(str(tmp_db))
    did = ds.create_dataset("runner-test")
    ds.add_cases_bulk(did, [
        {"input": "What is 2+2?", "expected": "4"},
        {"input": "Capital of France?", "expected": "Paris"},
    ])
    return ds.get_dataset(did)


@pytest.mark.asyncio
async def test_runner_creates_run_record(tmp_db, ds_with_cases):
    llm = MockLLM(["The answer is 4.", "Paris is the capital."])
    runner = EvalRunner(db_path=str(tmp_db), llm=llm)
    run_id = await runner.run(ds_with_cases, scorers=["contains"])
    assert run_id.startswith("evrun_")


@pytest.mark.asyncio
async def test_runner_scores_results(tmp_db, ds_with_cases):
    llm = MockLLM(["The answer is 4", "Paris"])
    runner = EvalRunner(db_path=str(tmp_db), llm=llm)
    run_id = await runner.run(ds_with_cases, scorers=["contains"])
    report = runner.get_report(run_id)
    assert report is not None
    assert report["total_cases"] == 2
    assert report["passed_cases"] == 2
    assert report["pass_rate"] == 1.0


@pytest.mark.asyncio
async def test_runner_partial_pass(tmp_db, ds_with_cases):
    llm = MockLLM(["4", "Berlin"])  # Paris case will fail
    runner = EvalRunner(db_path=str(tmp_db), llm=llm)
    run_id = await runner.run(ds_with_cases, scorers=["contains"])
    report = runner.get_report(run_id)
    assert report["passed_cases"] == 1
    assert report["failed_cases"] == 1
    assert report["pass_rate"] == 0.5


@pytest.mark.asyncio
async def test_runner_no_llm_marks_error(tmp_db, ds_with_cases):
    runner = EvalRunner(db_path=str(tmp_db), llm=None)
    run_id = await runner.run(ds_with_cases, scorers=["contains"])
    report = runner.get_report(run_id)
    # All fail since no LLM
    assert report["passed_cases"] == 0


@pytest.mark.asyncio
async def test_runner_multiple_scorers(tmp_db, ds_with_cases):
    llm = MockLLM(["4", "Paris is the capital of France"])
    runner = EvalRunner(db_path=str(tmp_db), llm=llm)
    run_id = await runner.run(ds_with_cases, scorers=["contains", "word_overlap"])
    report = runner.get_report(run_id)
    # All results should have both scorer scores
    for result in report["results"]:
        assert "contains" in result["scores"]
        assert "word_overlap" in result["scores"]


@pytest.mark.asyncio
async def test_runner_report_contains_expected_fields(tmp_db, ds_with_cases):
    runner = EvalRunner(db_path=str(tmp_db))
    run_id = await runner.run(ds_with_cases, scorers=["exact_match"])
    report = runner.get_report(run_id)
    assert "run_id" not in report  # it's the id field
    assert "id" in report
    assert "status" in report
    assert "results" in report


def test_get_report_not_found(runner):
    assert runner.get_report("evrun_missing") is None


@pytest.mark.asyncio
async def test_list_runs(tmp_db, ds_with_cases):
    runner = EvalRunner(db_path=str(tmp_db))
    await runner.run(ds_with_cases)
    await runner.run(ds_with_cases)
    runs = runner.list_runs()
    assert len(runs) == 2
