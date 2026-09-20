"""Pydantic request/response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RedactionRule(BaseModel):
    pattern: str = Field(..., description="Python 正则表达式")
    template: str = Field(
        "***",
        description=(
            "替换模板，支持 \\1、\\g<name> 以及 $1、${name}、$& 回引；"
            "默认 ***"
        ),
    )
    priority: int = Field(
        0, description="优先级，数值越大越优先；同值时按规则提交顺序"
    )
    ignorecase: bool = Field(False, description="是否忽略大小写 (re.IGNORECASE)")


class DesensitizeRequest(BaseModel):
    text: str = Field("", description="待脱敏原文")
    rules: list[RedactionRule] = Field(
        ..., min_length=1, description="正则脱敏规则（至少一条）"
    )


class RuleErrorOut(BaseModel):
    rule_index: int
    kind: Literal["pattern", "template"]
    code: str
    message: str
    position: int | None = None


class RuleReport(BaseModel):
    rule_index: int
    pattern: str
    priority: int
    valid: bool
    total_matches: int
    replaced_matches: int
    suppressed_matches: int


class HitOut(BaseModel):
    hit_id: int
    rule_index: int
    status: Literal["replaced", "suppressed_overlap"]
    source_start: int
    source_end: int
    match_text: str
    replacement: str | None = None
    result_start: int | None = None
    result_end: int | None = None
    groups: dict[str, str] = Field(default_factory=dict)
    suppressed_by_hit_id: int | None = None
    suppressed_by_rule_index: int | None = None


class SegmentOut(BaseModel):
    type: Literal["text", "replacement"]
    source_start: int
    source_end: int
    result_start: int
    result_end: int
    text: str
    hit_id: int | None = None
    rule_index: int | None = None


class Summary(BaseModel):
    rules_total: int
    rules_valid: int
    rules_invalid: int
    hits_total: int
    hits_replaced: int
    hits_suppressed: int


class DesensitizeResponse(BaseModel):
    original_text: str
    masked_text: str
    summary: Summary
    rule_errors: list[RuleErrorOut]
    rule_reports: list[RuleReport]
    hits: list[HitOut]
    segments: list[SegmentOut]


class HealthResponse(BaseModel):
    status: str
