"""Deterministic regex-based text desensitization engine.

Rules with higher ``priority`` win on overlap. Ties are broken by the
earlier match start, and then by the rule's submission order (0-based).
Only winning intervals are replaced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import NamedTuple

from .templates import normalize_template, validate_template


class RuleError(NamedTuple):
    """Structured error reported for a single invalid rule."""

    code: str
    message: str
    position: int | None = None
    kind: str = "pattern"  # "pattern" | "template"
    rule_index: int = -1


@dataclass
class CompiledRule:
    index: int
    pattern_str: str
    template_str: str
    priority: int
    ignorecase: bool
    regex: re.Pattern[str]
    normalized_template: str


@dataclass
class Candidate:
    rule: CompiledRule
    start: int
    end: int
    replacement: str
    groups: dict[str, str | None]
    text: str  # matched substring


@dataclass
class Hit:
    """A single rule match and how it was resolved."""

    hit_id: int
    rule_index: int
    status: str  # "replaced" | "suppressed_overlap"
    source_start: int
    source_end: int
    match_text: str
    replacement: str | None = None
    result_start: int | None = None
    result_end: int | None = None
    groups: dict[str, str | None] = field(default_factory=dict)
    suppressed_by_hit_id: int | None = None
    suppressed_by_rule_index: int | None = None


@dataclass
class Segment:
    type: str  # "text" | "replacement"
    source_start: int
    source_end: int
    result_start: int
    result_end: int
    text: str
    hit_id: int | None = None
    rule_index: int | None = None


@dataclass
class EngineResult:
    masked_text: str
    hits: list[Hit]
    segments: list[Segment]
    rule_errors: list[RuleError]


def compile_rules(
    patterns: list[str],
    templates: list[str],
    priorities: list[int],
    ignorecase: list[bool],
) -> tuple[list[CompiledRule], list[RuleError]]:
    """Compile every rule independently; one bad rule never blocks others."""
    compiled: list[CompiledRule] = []
    errors: list[RuleError] = []

    for i, (pat, tpl, prio, ic) in enumerate(
        zip(patterns, templates, priorities, ignorecase)
    ):
        flags = re.IGNORECASE if ic else 0
        try:
            regex = re.compile(pat, flags)
        except re.error as exc:
            errors.append(
                RuleError(
                    code="invalid_regex",
                    message=str(exc),
                    position=getattr(exc, "pos", None),
                    kind="pattern",
                    rule_index=i,
                )
            )
            continue

        try:
            normalized = normalize_template(tpl)
            validate_template(normalized, regex)
        except Exception as exc:  # TemplateError / re.error / IndexError
            errors.append(
                RuleError(
                    code="invalid_template",
                    message=str(exc),
                    position=getattr(exc, "position", None),
                    kind="template",
                    rule_index=i,
                )
            )
            continue

        compiled.append(
            CompiledRule(
                index=i,
                pattern_str=pat,
                template_str=tpl,
                priority=prio,
                ignorecase=ic,
                regex=regex,
                normalized_template=normalized,
            )
        )

    return compiled, errors


def _group_snapshot(match: re.Match[str]) -> dict[str, str | None]:
    return {
        name: match.group(name)
        for name in (match.re.groupindex or {})
        if match.group(name) is not None
    }


def _collect_candidates(
    text: str, rules: list[CompiledRule]
) -> tuple[list[Candidate], list[RuleError]]:
    """Run every valid rule and expand its replacement template per match."""
    candidates: list[Candidate] = []
    errors: list[RuleError] = []

    for rule in rules:
        rule_candidates: list[Candidate] = []
        failed = False
        for match in rule.regex.finditer(text):
            try:
                replacement = match.expand(rule.normalized_template)
            except re.error as exc:
                # Defensive last resort: validate_template catches statically
                # detectable problems up front.
                errors.append(
                    RuleError(
                        code="invalid_template",
                        message=str(exc),
                        position=getattr(exc, "pos", None),
                        kind="template",
                        rule_index=rule.index,
                    )
                )
                failed = True
                break
            rule_candidates.append(
                Candidate(
                    rule=rule,
                    start=match.start(),
                    end=match.end(),
                    replacement=replacement,
                    groups=_group_snapshot(match),
                    text=match.group(0),
                )
            )
        if not failed:
            candidates.extend(rule_candidates)

    return candidates, errors


def resolve(
    text: str,
    patterns: list[str],
    templates: list[str],
    priorities: list[int],
    ignorecase: list[bool],
) -> EngineResult:
    compiled, rule_errors = compile_rules(
        patterns, templates, priorities, ignorecase
    )
    candidates, runtime_errors = _collect_candidates(text, compiled)
    rule_errors.extend(runtime_errors)

    # Deterministic resolution order:
    # 1. higher priority first  2. earlier start first  3. submission order
    ordered = sorted(
        candidates,
        key=lambda c: (-c.rule.priority, c.start, c.rule.index),
    )

    winners: list[Candidate] = []
    suppressed_by: dict[int, Candidate] = {}  # id(candidate) -> winner
    for cand in ordered:
        winner = next(
            (
                w
                for w in winners
                # Half-open overlap; zero-length matches never conflict.
                if cand.start < w.end and w.start < cand.end
            ),
            None,
        )
        if winner is None:
            winners.append(cand)
        else:
            suppressed_by[id(cand)] = winner

    # Apply winners left to right. At the same source position (only possible
    # for zero-length insertions) keep the same precedence as resolution so
    # output ordering stays deterministic.
    winners_sorted = sorted(
        winners,
        key=lambda c: (c.start, -c.rule.priority, c.rule.index),
    )

    masked_parts: list[str] = []
    segments: list[Segment] = []
    seg_candidate_ids: list[int] = []
    result_span_by_cid: dict[int, tuple[int, int]] = {}
    cursor = 0
    out_pos = 0

    for cand in winners_sorted:
        start, end = cand.start, cand.end
        if start > cursor:
            piece = text[cursor:start]
            masked_parts.append(piece)
            segments.append(
                Segment("text", cursor, start, out_pos, out_pos + len(piece), piece)
            )
            out_pos += len(piece)
        piece = cand.replacement
        masked_parts.append(piece)
        result_start, result_end = out_pos, out_pos + len(piece)
        result_span_by_cid[id(cand)] = (result_start, result_end)
        segments.append(
            Segment(
                "replacement",
                start,
                end,
                result_start,
                result_end,
                piece,
                rule_index=cand.rule.index,
            )
        )
        seg_candidate_ids.append(id(cand))
        out_pos = result_end
        cursor = end

    if cursor < len(text):
        piece = text[cursor:]
        masked_parts.append(piece)
        segments.append(
            Segment("text", cursor, len(text), out_pos, out_pos + len(piece), piece)
        )

    # Stable reporting order.
    output_order = sorted(
        candidates,
        key=lambda c: (c.start, c.end, c.rule.index, -c.rule.priority),
    )

    hit_id_by_cid: dict[int, int] = {
        id(cand): hit_id for hit_id, cand in enumerate(output_order)
    }
    hits: list[Hit] = []
    for hit_id, cand in enumerate(output_order):
        if id(cand) not in suppressed_by:
            result_start, result_end = result_span_by_cid[id(cand)]
            hits.append(
                Hit(
                    hit_id=hit_id,
                    rule_index=cand.rule.index,
                    status="replaced",
                    source_start=cand.start,
                    source_end=cand.end,
                    match_text=cand.text,
                    replacement=cand.replacement,
                    result_start=result_start,
                    result_end=result_end,
                    groups=cand.groups,
                )
            )
        else:
            winner = suppressed_by[id(cand)]
            hits.append(
                Hit(
                    hit_id=hit_id,
                    rule_index=cand.rule.index,
                    status="suppressed_overlap",
                    source_start=cand.start,
                    source_end=cand.end,
                    match_text=cand.text,
                    replacement=None,
                    result_start=None,
                    result_end=None,
                    groups=cand.groups,
                    suppressed_by_hit_id=hit_id_by_cid.get(id(winner)),
                    suppressed_by_rule_index=winner.rule.index,
                )
            )

    # Attach hit ids to replacement segments.
    replacement_segments = [s for s in segments if s.type == "replacement"]
    for seg, cid in zip(replacement_segments, seg_candidate_ids):
        seg.hit_id = hit_id_by_cid[cid]

    rule_errors.sort(key=lambda e: e.rule_index)
    return EngineResult(
        masked_text="".join(masked_parts),
        hits=hits,
        segments=segments,
        rule_errors=rule_errors,
    )
