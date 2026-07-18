# -*- coding: utf-8 -*-

from copy import deepcopy
from typing import NamedTuple

from scrubbing import KNOWN_SECRET_MARKER, scrub_public_data, scrub_text


def test_known_secret_replacement_precedes_policy_scan():
    secret = "sk-proj-" + "a" * 32

    scrubbed = scrub_text(
        f"exact={secret} embedded=before-{secret}-after",
        known_secrets=[secret],
    )

    assert secret not in scrubbed
    assert scrubbed.count(KNOWN_SECRET_MARKER) == 2
    assert "REDACTED:openai_api_key" not in scrubbed


def test_policy_scan_redacts_api_key_like_text_without_known_secrets():
    secret = "xai-" + "b" * 32

    scrubbed = scrub_text(f"誤って貼った値: {secret}")

    assert secret not in scrubbed
    assert "REDACTED:xai_api_key" in scrubbed


def test_confirm_only_personal_data_is_preserved():
    text = "連絡先は user@example.com です。"

    assert scrub_text(text) == text


def test_nested_containers_keep_types_and_original_is_not_modified():
    secret = "opaque-secret-value-123456789"
    original = {
        f"key-{secret}": [
            f"prefix {secret} suffix",
            {"nested": secret},
        ],
        "tuple": (secret, 42, True, None),
        "set": {secret, "safe"},
        "frozen": frozenset({secret, "safe"}),
    }
    snapshot = deepcopy(original)

    scrubbed = scrub_public_data(original, known_secrets=(secret, ""))

    assert original == snapshot
    assert scrubbed is not original
    assert isinstance(scrubbed, dict)
    assert isinstance(scrubbed["tuple"], tuple)
    assert isinstance(scrubbed["set"], set)
    assert isinstance(scrubbed["frozen"], frozenset)
    assert secret not in repr(scrubbed)
    assert f"key-{KNOWN_SECRET_MARKER}" in scrubbed
    assert scrubbed["tuple"][1:] == (42, True, None)


def test_normal_text_and_scalar_values_are_preserved():
    original = {
        "message": "通常の日本語テキストです。",
        "items": [1, 2.5, False, None],
    }

    scrubbed = scrub_public_data(original)

    assert scrubbed == original
    assert scrubbed is not original
    assert scrubbed["items"] is not original["items"]


def test_named_tuple_type_is_preserved():
    class PublicPair(NamedTuple):
        label: str
        value: str

    secret = "known-value-123456"
    original = PublicPair("safe", f"prefix-{secret}-suffix")

    scrubbed = scrub_public_data(original, known_secrets=secret)

    assert isinstance(scrubbed, PublicPair)
    assert scrubbed.label == "safe"
    assert scrubbed.value == f"prefix-{KNOWN_SECRET_MARKER}-suffix"
