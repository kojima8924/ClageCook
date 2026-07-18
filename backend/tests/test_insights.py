import pytest

import insights

from insights import analyze_insights


def answer(source, text):
    return {"source": source, "text": text}


def signal(result, source, signal_type):
    return next(
        item
        for item in result["caution_signals"][source]
        if item["type"] == signal_type
    )


def test_identical_answers_have_full_lexical_overlap():
    result = analyze_insights(
        [
            answer("claude", "Local storage improves privacy."),
            answer("grok", "Local storage improves privacy."),
        ]
    )

    assert result["is_comparable"] is True
    assert result["agreement_score"] == 1.0
    assert result["provider_similarities"] == {"claude": 1.0, "grok": 1.0}
    assert {"local", "storage", "improves", "privacy"}.issubset(
        result["shared_terms"]
    )
    assert result["distinctive_terms"] == {"claude": [], "grok": []}


def test_three_answers_report_per_provider_average_without_claiming_truth():
    result = analyze_insights(
        [
            answer("a", "shared technical proposal"),
            answer("b", "shared technical proposal"),
            answer("c", "xyz uvw jkq"),
        ]
    )

    assert result["agreement_score"] == 0.3333
    assert result["provider_similarities"] == {"a": 0.5, "b": 0.5, "c": 0.0}
    assert result["comparison_count"] == 3
    assert "事実の正しさ" in result["method"]["limitations"]
    assert "信頼度" in result["method"]["limitations"]


def test_japanese_shared_and_distinctive_terms_are_extracted():
    result = analyze_insights(
        [
            answer("gemini", "安全性を高めるため、暗号化と監査ログを追加します。"),
            answer("grok", "安全性を高める設計では、暗号化と権限制御を使います。"),
        ]
    )

    assert 0.0 < result["agreement_score"] < 1.0
    assert "安全性" in result["shared_terms"]
    assert "暗号化" in result["shared_terms"]
    assert "監査" in result["distinctive_terms"]["gemini"]
    assert "権限制御" in result["distinctive_terms"]["grok"]


def test_nfkc_and_casefold_make_equivalent_text_identical():
    result = analyze_insights(
        [answer("left", "ＡＰＩ ３０％"), answer("right", "api 30%")]
    )

    assert result["agreement_score"] == 1.0
    assert signal(result, "left", "numeric_expression")["matches"] == ["30%"]
    assert signal(result, "right", "numeric_expression")["matches"] == ["30%"]


def test_caution_rules_expose_matches_and_do_not_label_them_as_facts():
    result = analyze_insights(
        [
            answer(
                "jp",
                "必ず成功しますが、おそらく改善の可能性は30.5%です。",
            ),
            answer(
                "en",
                "This will definitely work, but it might cost USD 1,200.",
            ),
        ]
    )

    absolute_jp = signal(result, "jp", "absolute_language")
    uncertainty_jp = signal(result, "jp", "uncertainty_language")
    numeric_jp = signal(result, "jp", "numeric_expression")
    assert absolute_jp["matches"] == ["必ず"]
    assert uncertainty_jp["matches"] == ["可能性", "おそらく"]
    assert numeric_jp["matches"] == ["30.5%"]
    assert "正否は判定していません" in absolute_jp["description"]

    assert signal(result, "en", "absolute_language")["matches"] == ["definitely"]
    assert signal(result, "en", "uncertainty_language")["matches"] == ["might"]
    assert signal(result, "en", "numeric_expression")["matches"] == ["1,200"]


def test_word_boundaries_avoid_substring_false_positive():
    result = analyze_insights([answer("one", "Maybe a mustard color remains.")])

    # "may" in "maybe" and "must" in "mustard" must not trigger rules.
    assert result["caution_signals"]["one"] == []


def test_empty_and_single_answer_explicitly_report_not_comparable():
    empty = analyze_insights([])
    assert empty["is_comparable"] is False
    assert empty["agreement_score"] == 0.0
    assert empty["comparison_count"] == 0

    single = analyze_insights([answer("solo", "Only one answer")])
    assert single["is_comparable"] is False
    assert single["agreement_score"] == 0.0
    assert single["provider_similarities"] == {"solo": 0.0}


def test_agreement_score_averages_unrounded_pairwise_values(monkeypatch):
    values = iter((0.00004, 0.00004, 0.00014))
    monkeypatch.setattr(insights, "_text_similarity", lambda *_args: next(values))

    result = analyze_insights(
        [
            answer("a", "alpha"),
            answer("b", "bravo"),
            answer("c", "charlie"),
        ]
    )

    assert [item["similarity"] for item in result["pairwise_similarities"]] == [
        0.0,
        0.0,
        0.0001,
    ]
    assert result["agreement_score"] == 0.0001


def test_blank_answers_are_ignored_transparently():
    result = analyze_insights(
        [answer("blank", " \n\t "), answer("valid", "有効な回答")]
    )

    assert result["answer_count"] == 1
    assert result["ignored_sources"] == ["blank"]
    assert list(result["provider_similarities"]) == ["valid"]


def test_identical_punctuation_does_not_create_artificial_agreement():
    result = analyze_insights([answer("a", "!!!"), answer("b", "!!!")])

    assert result["is_comparable"] is True
    assert result["agreement_score"] == 0.0


@pytest.mark.parametrize(
    "answers,exception",
    [
        ([answer("same", "one"), answer("same", "two")], ValueError),
        ([{"source": "ok", "text": None}], ValueError),
        ([{"source": "", "text": "text"}], ValueError),
        (["not a mapping"], TypeError),
        ("not an answer list", TypeError),
    ],
)
def test_invalid_input_is_rejected(answers, exception):
    with pytest.raises(exception):
        analyze_insights(answers)


@pytest.mark.parametrize("max_terms", [-1, 1.5, True])
def test_invalid_term_limit_is_rejected(max_terms):
    with pytest.raises(ValueError):
        analyze_insights([], max_terms=max_terms)


def test_term_limit_zero_preserves_scores_but_returns_no_terms():
    result = analyze_insights(
        [answer("a", "shared apple"), answer("b", "shared banana")],
        max_terms=0,
    )

    assert result["shared_terms"] == []
    assert result["distinctive_terms"] == {"a": [], "b": []}
    assert 0.0 <= result["agreement_score"] <= 1.0


def test_result_is_deterministic_independent_of_input_order():
    forward = [
        answer("zeta", "安全性とprivacy 99%"),
        answer("alpha", "privacy and local storage"),
        answer("middle", "おそらく安全性を改善"),
    ]
    reverse = list(reversed(forward))

    assert analyze_insights(forward) == analyze_insights(reverse)


@pytest.mark.parametrize(
    "left,right",
    [
        ("", ""),
        ("!!!", "???"),
        ("a", "a"),
        ("𠮷野家", "𠮷野家"),
        ("café", "CAFE\u0301"),
        ("alpha beta", "completely different"),
    ],
)
def test_score_is_always_bounded_for_unicode_edges(left, right):
    result = analyze_insights([answer("left", left), answer("right", right)])

    assert 0.0 <= result["agreement_score"] <= 1.0
    assert all(
        0.0 <= score <= 1.0
        for score in result["provider_similarities"].values()
    )
