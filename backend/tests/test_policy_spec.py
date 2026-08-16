# -*- coding: utf-8 -*-
"""ローカル秘密スキャナの実装が単一仕様 docs/policy_rules.json と一致することを固定する。

Direct BYOK (Flutter) 側にも同じ仕様と突き合わせるテストがあり、片方だけに
ruleが増える・減る状態を検知する。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import policy
from policy import scan_text


SPEC_PATH = Path(__file__).resolve().parents[2] / "docs" / "policy_rules.json"
SPEC = json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def test_version_matches_the_specification():
    assert scan_text("")["version"] == SPEC["version"]


def test_rule_ids_and_order_match_the_specification():
    assert [rule.rule_id for rule in policy._RULES] == [
        rule["rule_id"] for rule in SPEC["rules"]
    ]


@pytest.mark.parametrize("index", range(len(SPEC["rules"])))
def test_rule_definition_matches_the_specification(index):
    expected = SPEC["rules"][index]
    actual = policy._RULES[index]

    assert actual.label == expected["label"]
    assert actual.severity == expected["severity"]
    assert actual.secret_group == expected["secret_group"]
    assert actual.pattern.pattern == expected["pattern"]
    ignore_case = bool(actual.pattern.flags & re.IGNORECASE)
    assert ignore_case == ("i" in expected["flags"])


@pytest.mark.parametrize(
    "sample", SPEC["samples"], ids=[item["name"] for item in SPEC["samples"]]
)
def test_samples_produce_the_specified_result(sample):
    result = scan_text(sample["text"])

    assert result["action"] == sample["action"]
    assert result["redacted_text"] == sample["redacted_text"]
    assert [
        {"rule_id": item["rule_id"], "start": item["start"], "end": item["end"]}
        for item in result["findings"]
    ] == sample["findings"]
