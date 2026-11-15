import json

from rde.services.quality_agent import QualityAgent, RunStats, parse_llm_json, rule_verdict


def _stats(clean=100, rejected=5, frames=1000, video_frames=50):
    return RunStats("r2", "lerobot/pusht", frames, clean, rejected, video_frames, {"duplicate_frame": 3})


class FakeStats:
    def __init__(self, stats):
        self.stats = stats

    def run_stats(self, dataset, run_id):
        return self.stats


class FakeBedrock:
    def __init__(self, text=None, fail=False):
        self.text, self.fail = text, fail

    def converse(self, **kwargs):
        if self.fail:
            raise RuntimeError("throttled")
        return {"output": {"message": {"content": [{"text": self.text}]}}}


def test_rules_fail_on_high_reject_rate_or_no_data():
    assert rule_verdict(_stats()).verdict == "ok"
    assert rule_verdict(_stats(clean=5, rejected=5)).verdict == "fail"
    assert rule_verdict(_stats(frames=0)).verdict == "fail"


def test_parse_llm_json_tolerates_surrounding_text():
    v = parse_llm_json('Here: {"verdict": "warn", "summary": "s", "findings": ["a"]} done')
    assert (v.verdict, v.findings, v.source) == ("warn", ["a"], "llm")


def test_agent_uses_llm_verdict():
    text = json.dumps({"verdict": "warn", "summary": "dup spike", "findings": ["duplicates up"]})
    report = QualityAgent(FakeStats(_stats()), FakeBedrock(text), "m").review("lerobot/pusht", "r2")
    assert report["verdict"] == "warn" and report["source"] == "llm"


def test_llm_cannot_pass_a_run_that_breaks_hard_rules():
    text = json.dumps({"verdict": "ok", "summary": "fine", "findings": []})
    report = QualityAgent(FakeStats(_stats(clean=1, rejected=9)), FakeBedrock(text), "m").review("d", "r")
    assert report["verdict"] == "fail"


def test_agent_falls_back_to_rules_when_llm_fails():
    report = QualityAgent(FakeStats(_stats()), FakeBedrock(fail=True), "m").review("d", "r")
    assert report["verdict"] == "ok" and report["source"] == "rules"
