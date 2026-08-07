"""Revert/reopen evidence analysis lifted from scripts/avm_scoreboard.py."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

WINDOW_SECONDS = 7 * 24 * 60 * 60


def list_from_any(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def raw_entity_records(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("entities", "entity_timeline", "entity_timelines", "timelines"):
            records = list_from_any(data.get(key))
            if records:
                return records
    return []


def parse_pr_number(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def title_or_body_reverts_pr(record: dict[str, Any], target: int) -> bool:
    text = " ".join(f"{record.get('title') or ''}\n{record.get('body') or ''}".lower().split())
    return "revert" in text and re.search(rf"(?:#|pull/|pull request |pr ){target}(?!\d)", text) is not None


def timestamp_order(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def merge_seconds(fact: dict[str, Any]) -> float | None:
    for value in (fact.get("merged_at"), fact.get("mergedAt"), fact.get("comment_created_at")):
        parsed = timestamp_order(value)
        if parsed is not None:
            return parsed
    return None


def evidence_seconds(raw: dict[str, Any]) -> float | None:
    for key in ("merged_at", "mergedAt", "committed_at", "committedAt", "reopened_at", "reopenedAt", "updated_at", "updatedAt"):
        parsed = timestamp_order(raw.get(key))
        if parsed is not None:
            return parsed
    return None


def evidence_in_window(fact: dict[str, Any], raw: dict[str, Any]) -> bool:
    merged, evidence = merge_seconds(fact), evidence_seconds(raw)
    return merged is None or evidence is None or merged <= evidence <= merged + WINDOW_SECONDS


def pr_records(data: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(data, dict):
        for key in ("recent_merged_prs", "merged_prs", "pull_requests", "prs"):
            records.extend(row for row in list_from_any(data.get(key)) if isinstance(row, dict))
    for entity in raw_entity_records(data):
        if isinstance(entity, dict):
            if isinstance(entity.get("pr"), dict):
                records.append(entity["pr"])
            if entity.get("kind") == "pr" or "pr_number" in entity:
                records.append(entity)
    return records


def issue_reopened(entity: dict[str, Any]) -> bool:
    issue = entity.get("parent_issue") if isinstance(entity.get("parent_issue"), dict) else entity.get("issue")
    candidates = [entity, issue] if isinstance(issue, dict) else [entity]
    return any(
        str(row.get("state_reason") or row.get("stateReason") or "").upper() == "REOPENED"
        or row.get("reopened") is True or row.get("issue_reopened") is True
        for row in candidates
    )


def revert_commits(fact: dict[str, Any], data: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for owner in (fact, data if isinstance(data, dict) else {}):
        scan = owner.get("no_revert_reopen_scan")
        if isinstance(scan, dict):
            records.extend(row for row in list_from_any(scan.get("revert_commits")) if isinstance(row, dict))
        for key in ("revert_commits", "recent_revert_commits"):
            records.extend(row for row in list_from_any(owner.get(key)) if isinstance(row, dict))
    return records


def commit_reverts_pr(commit: dict[str, Any], target: int) -> bool:
    explicit = parse_pr_number(commit.get("reverted_pr") or commit.get("reverted_pr_number") or commit.get("target_pr"))
    return explicit == target if explicit is not None else title_or_body_reverts_pr(
        {"title": commit.get("message_head") or commit.get("subject"), "body": commit.get("message_body") or commit.get("message")}, target
    )


def revert_reopen_evidence(fact: dict[str, Any], data: Any) -> list[dict[str, Any]]:
    target = parse_pr_number(fact.get("pr_number") or fact.get("pr"))
    if target is None:
        return []
    pairs: list[dict[str, Any]] = []
    for pr in pr_records(data):
        number = parse_pr_number(pr.get("number") or pr.get("pr_number"))
        if number and number != target and title_or_body_reverts_pr(pr, target) and evidence_in_window(fact, pr):
            pairs.append({"reverted_pr": target, "revert_pr": number, "evidence": "explicit-revert-pr"})
    for commit in revert_commits(fact, data):
        if commit_reverts_pr(commit, target) and evidence_in_window(fact, commit):
            identity = str(commit.get("sha") or commit.get("oid") or commit.get("id") or "")
            pairs.append({"reverted_pr": target, "revert_commit": identity, "evidence": "revert-commit"})
    for entity in raw_entity_records(data):
        if isinstance(entity, dict) and parse_pr_number(entity.get("pr_number")) == target and issue_reopened(entity) and evidence_in_window(fact, entity):
            pairs.append({"reverted_pr": target, "issue_number": parse_pr_number(entity.get("issue_number")), "evidence": "issue-reopened"})
    return pairs


def false_consensus_evidence(data: Any) -> list[dict[str, Any]]:
    facts = []
    if isinstance(data, dict):
        for key in ("avm_facts", "autonomy_facts", "autonomy_results", "autonomy_ledger", "competence_facts"):
            facts.extend(row for row in list_from_any(data.get(key)) if isinstance(row, dict))
    pairs, seen = [], set()
    for fact in facts:
        for pair in revert_reopen_evidence(fact, data):
            identity = (pair.get("reverted_pr"), pair.get("revert_pr"), pair.get("revert_commit"), pair.get("issue_number"))
            if identity not in seen:
                seen.add(identity)
                pairs.append(pair)
    return pairs
