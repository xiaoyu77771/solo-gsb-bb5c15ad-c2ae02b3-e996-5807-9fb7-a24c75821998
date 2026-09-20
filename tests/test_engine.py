from app.engine import resolve
from app.templates import normalize_template, validate_template, TemplateError
import re
import pytest


def run(text, rules):
    """rules: list of (pattern, template, priority, ignorecase)."""
    return resolve(
        text,
        [r[0] for r in rules],
        [r[1] if len(r) > 1 else "***" for r in rules],
        [r[2] if len(r) > 2 else 0 for r in rules],
        [r[3] if len(r) > 3 else False for r in rules],
    )


def test_basic_single_rule_replaces_all_matches():
    res = run("call 13800001111 now 13900002222", [(r"\d{11}", "[PHONE]")])
    assert res.masked_text == "call [PHONE] now [PHONE]"
    assert [h.status for h in res.hits] == ["replaced", "replaced"]
    # Replacing 11 chars with 7 shifts the second hit left by 4.
    assert [(h.result_start, h.result_end) for h in res.hits] == [
        (5, 12),
        (17, 24),
    ]
    assert res.rule_errors == []


def test_priority_wins_overlap():
    # Rule 0 masks the whole phone; rule 1 masks digit triplets and wins
    # because of its higher priority. The broad rule 0 match is suppressed.
    res = run(
        "13800001111",
        [
            (r"\d{11}", "***********", 0),
            (r"\d{3}", "XXX", 10),
        ],
    )
    assert res.masked_text == "XXXXXXXXX11"
    low = [h for h in res.hits if h.rule_index == 0]
    high = [h for h in res.hits if h.rule_index == 1]
    assert len(high) == 3 and all(h.status == "replaced" for h in high)
    assert len(low) == 1 and low[0].status == "suppressed_overlap"
    assert low[0].suppressed_by_hit_id == high[0].hit_id
    assert low[0].suppressed_by_rule_index == 1
    assert low[0].result_start is None


def test_same_priority_earlier_start_wins():
    text = "abcdef"
    # Both matches overlap; rule 0 starts at 1, rule 1 starts at 2.
    # Overlapping winner "bc" (1..3) suppresses "cd" (2..4); the trailing
    # original text after a suppressed interval is preserved untouched.
    res = run(text, [(r"bc", "[0]"), (r"cd", "[1]")])
    assert res.masked_text == "a[0]def"
    assert res.hits[0].status == "replaced"
    assert res.hits[1].status == "suppressed_overlap"


def test_same_start_and_priority_rule_order_wins():
    res = run(
        "abc",
        [(r"abc", "A", 0), (r"a", "B", 0)],
    )
    # Both start at 0 -> rule 0 (submission order) wins.
    replaced = [h for h in res.hits if h.status == "replaced"]
    assert len(replaced) == 1
    assert replaced[0].rule_index == 0
    assert res.masked_text == "A"


def test_deterministic_repeated_runs():
    rules = [(r"\d+", "#", 0), (r"\d", "@", 5), (r"\d{3}", "%%", 3)]
    text = "xx 12345 yy 67 zz 89012"
    first = run(text, rules)
    second = run(text, rules)
    assert first.masked_text == second.masked_text
    assert [(h.hit_id, h.status, h.source_start) for h in first.hits] == [
        (h.hit_id, h.status, h.source_start) for h in second.hits
    ]


def test_invalid_regex_reported_per_rule_and_others_still_apply():
    res = run(
        "hello 12 world",
        [
            (r"([", "X"),          # invalid: unterminated group
            (r"\d+", "N"),         # valid
            (r"*foo", "Y"),        # invalid: nothing to repeat
        ],
    )
    assert res.masked_text == "hello N world"
    assert {e.rule_index: e.kind for e in res.rule_errors} == {
        0: "pattern",
        2: "pattern",
    }
    assert all(e.code == "invalid_regex" for e in res.rule_errors)
    # error positions are surfaced when available
    assert res.rule_errors[0].position is not None


def test_invalid_template_does_not_block_other_rules():
    res = run(
        "abc123",
        [
            (r"(a)bc", r"[\9]"),       # bad group ref
            (r"\d+", "NUM"),           # valid
        ],
    )
    assert res.masked_text == "abcNUM"
    err = res.rule_errors[0]
    assert err.rule_index == 0
    assert err.kind == "template"
    assert err.code == "invalid_template"
    assert [h.rule_index for h in res.hits] == [1]


def test_backreference_templates_python_and_dollar_styles():
    res = run(
        "John Smith called",
        [
            (r"(\w+) (\w+)", r"$1 ***", 0),
            (r"(?P<word>\w+)!", r"${word}", 0),  # no match, still valid
        ],
    )
    assert res.masked_text == "John *** called"
    hit = res.hits[0]
    assert hit.replacement == "John ***"


def test_named_group_and_dollar_ampersand():
    res = run(
        "key=secret",
        [(r"key=(?P<v>\w+)", r"key=<\g<v>>")],
    )
    assert res.masked_text == "key=<secret>"
    res2 = run("abc", [(r"b", r"[$&]")])
    assert res2.masked_text == "a[b]c"


def test_multi_digit_dollar_reference_uses_braces():
    # 12 groups; $12 must reference group 12, not group 1 + literal 2.
    pat = r"(a)(b)(c)(d)(e)(f)(g)(h)(i)(j)(k)(l)"
    res = run("abcdefghijkl", [(pat, "$12")])
    assert res.masked_text == "l"


def test_segments_cover_full_text_and_align_offsets():
    text = "ab12cd"
    res = run(text, [(r"\d+", "###")])
    # Concatenation reconstructs the masked text.
    assert "".join(s.text for s in res.segments) == res.masked_text
    text_segs = [s for s in res.segments if s.type == "text"]
    rep_segs = [s for s in res.segments if s.type == "replacement"]
    assert [s.text for s in text_segs] == ["ab", "cd"]
    seg = rep_segs[0]
    assert (seg.source_start, seg.source_end) == (2, 4)
    assert (seg.result_start, seg.result_end) == (2, 5)
    hit = res.hits[0]
    assert (seg.result_start, seg.result_end) == (
        hit.result_start,
        hit.result_end,
    )
    assert seg.hit_id == hit.hit_id


def test_case_insensitive_flag_per_rule():
    res = run("ABC abc", [(r"abc", "x", 0, True)])
    assert res.masked_text == "x x"


def test_empty_text():
    res = run("", [(r".+", "x")])
    assert res.masked_text == ""
    assert res.hits == []
    assert res.segments == []


def test_no_matches_returns_original_text():
    res = run("plain text", [(r"\d+", "N")])
    assert res.masked_text == "plain text"
    assert res.hits == []
    assert len(res.segments) == 1
    assert res.segments[0].type == "text"


def test_zero_width_matches_do_not_conflict_and_order_is_deterministic():
    # a* yields zero-width matches (1,1) and (3,3) that must not suppress
    # nor be suppressed by the positive-length matches.
    res = run("aba", [(r"a*", "Z")])
    assert res.masked_text == "ZZbZZ"
    assert all(h.status == "replaced" for h in res.hits)
    zero = [h for h in res.hits if h.source_start == h.source_end]
    assert len(zero) == 2
    assert all(h.result_end - h.result_start == 1 for h in zero)


def test_replacement_longer_and_shorter_offsets():
    res = run("a1b", [(r"1", "XXXX")])
    hit = res.hits[0]
    assert hit.result_start == 1 and hit.result_end == 5
    tail = [s for s in res.segments if s.text == "b"][0]
    assert (tail.source_start, tail.result_start) == (2, 5)

    res2 = run("a123b", [(r"123", "")])
    assert res2.masked_text == "ab"
    hit2 = res2.hits[0]
    assert hit2.result_start == hit2.result_end == 1


def test_rule_match_counts():
    res = run(
        "12 34 56",
        [(r"\d", "x", 0), (r"\d{2}", "Y", 10), ("(", "z")],
    )
    r0 = [h for h in res.hits if h.rule_index == 0]
    r1 = [h for h in res.hits if h.rule_index == 1]
    assert len(r0) == 6 and all(h.status == "suppressed_overlap" for h in r0)
    assert len(r1) == 3 and all(h.status == "replaced" for h in r1)
    assert res.masked_text == "Y Y Y"
    assert {e.rule_index for e in res.rule_errors} == {2}


# ---- template unit tests ----

def test_normalize_template_variants():
    assert normalize_template(r"\1") == r"\1"
    assert normalize_template("$1") == r"\g<1>"
    assert normalize_template("${name}") == r"\g<name>"
    assert normalize_template("$<name>") == r"\g<name>"
    assert normalize_template("$&") == r"\g<0>"
    assert normalize_template("price is $5") == r"price is \g<5>"
    assert normalize_template("100$") == "100$"
    assert normalize_template(r"a\nb") == r"a\nb"


def test_validate_template_accepts_valid_refs():
    p = re.compile(r"(?P<x>a)(b)")
    validate_template(normalize_template(r"\1-\2-\g<x>-$1"), p)
    validate_template(normalize_template("$&"), p)


def test_validate_template_rejects_missing_numeric_and_named():
    p = re.compile(r"(a)")
    with pytest.raises(TemplateError):
        validate_template(normalize_template(r"\2"), p)
    with pytest.raises(TemplateError):
        validate_template(normalize_template("${missing}"), p)
