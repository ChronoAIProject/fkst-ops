import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "providers"))

from revert_reopen_evidence import false_consensus_evidence


def test_explicit_revert_pr_is_reported():
    data = {
        "avm_facts": [{"pr_number": 12, "merged_at": "2026-08-01T00:00:00Z"}],
        "recent_merged_prs": [{"number": 13, "title": "Revert PR #12", "merged_at": "2026-08-02T00:00:00Z"}],
    }
    assert false_consensus_evidence(data) == [{"reverted_pr": 12, "revert_pr": 13, "evidence": "explicit-revert-pr"}]
