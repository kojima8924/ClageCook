# -*- coding: utf-8 -*-
"""回答間の語彙的な合意・相違を、完全ローカルで可視化する。

このモジュールは意味理解、事実確認、回答品質の採点を行わない。返すスコアは
Unicode正規化後の語彙と文字3-gramの重なりだけを表す。外部APIや乱数を使わず、
同じ入力からは常に同じ結果を返す。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from itertools import combinations
from typing import Any


_DEFAULT_MAX_TERMS = 12

_ENGLISH_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "can",
        "did",
        "do",
        "does",
        "for",
        "from",
        "he",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "no",
        "not",
        "of",
        "on",
        "or",
        "she",
        "should",
        "that",
        "the",
        "then",
        "these",
        "they",
        "this",
        "those",
        "to",
        "was",
        "we",
        "were",
        "will",
        "with",
        "would",
        "you",
    }
)

_JAPANESE_STOP_WORDS = frozenset(
    {
        "あれ",
        "ある",
        "いる",
        "これ",
        "こと",
        "しかし",
        "する",
        "それ",
        "そして",
        "ため",
        "です",
        "など",
        "なる",
        "ので",
        "ます",
        "また",
        "まで",
        "もの",
        "よう",
        "より",
        "られる",
        "れる",
    }
)

_ABSOLUTE_TERMS = (
    "必ず",
    "絶対",
    "間違いなく",
    "確実に",
    "完全に",
    "に違いない",
    "always",
    "never",
    "definitely",
    "certainly",
    "guaranteed",
    "must",
)

_UNCERTAINTY_TERMS = (
    "かもしれない",
    "可能性",
    "推測",
    "不明",
    "わからない",
    "おそらく",
    "恐らく",
    "たぶん",
    "と思われる",
    "may",
    "might",
    "could",
    "possibly",
    "probably",
    "uncertain",
    "unknown",
    "estimate",
    "assume",
    "likely",
)

_NUMBER_PATTERN = re.compile(
    r"[-+]?\d+(?:[,.]\d+)*(?:\s?(?:%|％|円|ドル|usd|eur|gbp|jpy))?",
    re.IGNORECASE,
)

_CAUTION_DESCRIPTIONS = {
    "absolute_language": (
        "登録済みの強い断定表現を検出しました。内容の正否は判定していません。"
    ),
    "uncertainty_language": (
        "登録済みの不確実性表現を検出しました。不確実さの大きさは判定していません。"
    ),
    "numeric_expression": (
        "数字を含む表現を検出しました。単位、根拠、計算の正しさは判定していません。"
    ),
}


def analyze_insights(
    answers: Iterable[Mapping[str, object]],
    *,
    max_terms: int = _DEFAULT_MAX_TERMS,
) -> dict[str, Any]:
    """成功した回答の ``source`` / ``text`` から語彙インサイトを返す。

    ``source`` は空でない一意な文字列、``text`` は文字列でなければならない。
    空白だけの回答は入力件数には含めず ``ignored_sources`` に記録する。比較対象が
    2件未満の場合、比較不能であることを ``is_comparable`` で示し、数値フィールドの
    ``agreement_score`` は0.0を返す。
    """

    if isinstance(answers, (str, bytes)):
        raise TypeError("answers must be an iterable of mappings")
    if not isinstance(max_terms, int) or isinstance(max_terms, bool) or max_terms < 0:
        raise ValueError("max_terms must be a non-negative integer")

    prepared: list[tuple[str, str, str]] = []
    ignored_sources: list[str] = []
    seen_sources: set[str] = set()

    for index, answer in enumerate(answers):
        if not isinstance(answer, Mapping):
            raise TypeError(f"answers[{index}] must be a mapping")
        source = answer.get("source")
        text = answer.get("text")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"answers[{index}].source must be a non-empty string")
        source = source.strip()
        if source in seen_sources:
            raise ValueError(f"duplicate source: {source}")
        seen_sources.add(source)
        if not isinstance(text, str):
            raise ValueError(f"answers[{index}].text must be a string")

        normalized = _normalize(text)
        if not normalized:
            ignored_sources.append(source)
            continue
        prepared.append((source, text, normalized))

    # 入力順が結果の並びに影響しないよう、sourceで一意に固定する。
    prepared.sort(key=lambda item: item[0])
    ignored_sources.sort()

    sources = [source for source, _, _ in prepared]
    features = {
        source: _similarity_features(normalized)
        for source, _, normalized in prepared
    }
    term_counts = {
        source: _term_counts(normalized) for source, _, normalized in prepared
    }

    pairwise: list[dict[str, object]] = []
    similarity_totals = {source: 0.0 for source in sources}
    comparison_totals = {source: 0 for source in sources}
    normalized_by_source = {
        source: normalized for source, _, normalized in prepared
    }

    for left, right in combinations(sources, 2):
        similarity = _text_similarity(
            normalized_by_source[left],
            normalized_by_source[right],
            features[left],
            features[right],
        )
        pairwise.append(
            {
                "sources": [left, right],
                "similarity": _rounded(similarity),
            }
        )
        similarity_totals[left] += similarity
        similarity_totals[right] += similarity
        comparison_totals[left] += 1
        comparison_totals[right] += 1

    agreement_score = (
        sum(float(item["similarity"]) for item in pairwise) / len(pairwise)
        if pairwise
        else 0.0
    )
    provider_similarities = {
        source: _rounded(similarity_totals[source] / comparison_totals[source])
        if comparison_totals[source]
        else 0.0
        for source in sources
    }

    document_frequency = Counter(
        term for counts in term_counts.values() for term in counts
    )
    total_frequency = Counter()
    for counts in term_counts.values():
        total_frequency.update(counts)

    shared_candidates = [
        term for term, count in document_frequency.items() if count >= 2
    ]
    shared_terms = sorted(
        shared_candidates,
        key=lambda term: (
            -document_frequency[term],
            -total_frequency[term],
            -len(term),
            term,
        ),
    )[:max_terms]

    distinctive_terms = {
        source: sorted(
            (
                term
                for term in counts
                if document_frequency[term] == 1
            ),
            key=lambda term: (-counts[term], -len(term), term),
        )[:max_terms]
        for source, counts in term_counts.items()
    }
    caution_signals = {
        source: _caution_signals(normalized)
        for source, _, normalized in prepared
    }

    return {
        "analysis_kind": "deterministic_lexical_overlap_v1",
        "is_comparable": len(sources) >= 2,
        "answer_count": len(sources),
        "comparison_count": len(pairwise),
        "agreement_score": _rounded(agreement_score),
        "provider_similarities": provider_similarities,
        "pairwise_similarities": pairwise,
        "shared_terms": shared_terms,
        "distinctive_terms": distinctive_terms,
        "caution_signals": caution_signals,
        "ignored_sources": ignored_sources,
        "method": {
            "score_basis": (
                "NFKC/casefold後の語彙とUnicode文字3-gram集合に対する"
                "Sørensen-Dice係数。agreement_scoreは全回答ペアの単純平均。"
            ),
            "provider_basis": (
                "各sourceと、それ以外の全回答とのpairwise similarityの単純平均。"
            ),
            "term_basis": (
                "英単語・日本語script-runから一般的な機能語と純粋な数値を除外。"
                "sharedは2回答以上、distinctiveは1回答だけに現れる語。"
            ),
            "caution_basis": (
                "公開された固定語彙と数字パターンの出現検査。文脈や妥当性は判定しない。"
            ),
            "limitations": (
                "意味的一致、事実の正しさ、品質、信頼度、モデルの確信度を表さない。"
            ),
        },
    }


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(normalized.split())


def _rounded(value: float) -> float:
    # JSON表示とテストを安定させる。floatの計算順はsource sortで固定済み。
    return round(max(0.0, min(1.0, value)), 4)


def _text_similarity(
    left_text: str,
    right_text: str,
    left_features: frozenset[str],
    right_features: frozenset[str],
) -> float:
    if not left_features or not right_features:
        return 0.0
    if left_text == right_text:
        return 1.0
    return (2.0 * len(left_features & right_features)) / (
        len(left_features) + len(right_features)
    )


def _similarity_features(text: str) -> frozenset[str]:
    terms = {f"term:{term}" for term in _term_counts(text)}
    grams = {f"gram:{gram}" for gram in _character_grams(text)}
    return frozenset(terms | grams)


def _character_grams(text: str) -> set[str]:
    """句読点・空白を境界としたUnicode英数字runから3-gramを作る。"""

    runs: list[str] = []
    current: list[str] = []
    for character in text:
        category = unicodedata.category(character)
        if category.startswith(("L", "N")):
            current.append(character)
        elif current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))

    grams: set[str] = set()
    for run in runs:
        if len(run) < 3:
            grams.add(run)
            continue
        grams.update(run[index : index + 3] for index in range(len(run) - 2))
    return grams


def _term_counts(text: str) -> Counter[str]:
    terms: list[str] = []
    current: list[str] = []
    current_script: str | None = None

    def flush() -> None:
        nonlocal current, current_script
        if not current or current_script is None:
            current = []
            current_script = None
            return
        term = "".join(current).strip("-'")
        if _is_meaningful_term(term, current_script):
            terms.append(term)
        current = []
        current_script = None

    for character in text:
        script = _script(character)
        if script is None:
            # ASCII apostrophe/hyphen is kept only inside non-Japanese words.
            if character in {"'", "-"} and current_script == "word":
                current.append(character)
            else:
                flush()
            continue
        if script != current_script:
            flush()
            current_script = script
        current.append(character)
    flush()
    return Counter(terms)


def _script(character: str) -> str | None:
    codepoint = ord(character)
    if _is_han(codepoint):
        return "han"
    if 0x3040 <= codepoint <= 0x309F:
        return "hiragana"
    if (0x30A0 <= codepoint <= 0x30FF) or (0x31F0 <= codepoint <= 0x31FF):
        return "katakana"
    category = unicodedata.category(character)
    if category.startswith(("L", "N")):
        return "word"
    return None


def _is_han(codepoint: int) -> bool:
    return any(
        start <= codepoint <= end
        for start, end in (
            (0x3400, 0x4DBF),
            (0x4E00, 0x9FFF),
            (0xF900, 0xFAFF),
            (0x20000, 0x2FA1F),
        )
    )


def _is_meaningful_term(term: str, script: str) -> bool:
    if not term or term.isdecimal():
        return False
    if script == "word":
        return len(term) >= 2 and term not in _ENGLISH_STOP_WORDS
    if script == "hiragana":
        return len(term) >= 3 and term not in _JAPANESE_STOP_WORDS
    return len(term) >= 2


def _caution_signals(text: str) -> list[dict[str, object]]:
    signals: list[dict[str, object]] = []
    for signal_type, terms in (
        ("absolute_language", _ABSOLUTE_TERMS),
        ("uncertainty_language", _UNCERTAINTY_TERMS),
    ):
        matches: list[str] = []
        count = 0
        for term in terms:
            term_count = _phrase_count(text, term)
            if term_count:
                matches.append(term)
                count += term_count
        if count:
            signals.append(
                {
                    "type": signal_type,
                    "count": count,
                    "matches": matches,
                    "description": _CAUTION_DESCRIPTIONS[signal_type],
                }
            )

    numeric_matches = _ordered_unique(
        match.group(0).strip() for match in _NUMBER_PATTERN.finditer(text)
    )
    if numeric_matches:
        signals.append(
            {
                "type": "numeric_expression",
                "count": sum(1 for _ in _NUMBER_PATTERN.finditer(text)),
                "matches": numeric_matches,
                "description": _CAUTION_DESCRIPTIONS["numeric_expression"],
            }
        )
    return signals


def _phrase_count(text: str, phrase: str) -> int:
    if phrase.isascii():
        pattern = re.compile(
            rf"(?<![a-z0-9_]){re.escape(phrase)}(?![a-z0-9_])",
            re.IGNORECASE,
        )
        return len(pattern.findall(text))
    return text.count(phrase)


def _ordered_unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
