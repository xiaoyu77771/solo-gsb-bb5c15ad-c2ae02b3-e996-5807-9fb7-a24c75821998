"""FastAPI application: text desensitization service."""

from __future__ import annotations

import os

from fastapi import FastAPI

from .engine import resolve
from .schemas import (
    DesensitizeRequest,
    DesensitizeResponse,
    HealthResponse,
    HitOut,
    RuleErrorOut,
    RuleReport,
    SegmentOut,
    Summary,
)

app = FastAPI(
    title="文本脱敏 API",
    description=(
        "提交原文与多条正则脱敏规则，服务对重叠命中按 "
        "优先级 → 起始位置 → 规则顺序 确定性消解，仅替换胜出区间。"
    ),
    version="1.0.0",
)


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post(
    "/api/v1/desensitize",
    response_model=DesensitizeResponse,
    tags=["desensitize"],
)
def desensitize(request: DesensitizeRequest) -> DesensitizeResponse:
    text = request.text
    rules = request.rules

    result = resolve(
        text=text,
        patterns=[r.pattern for r in rules],
        templates=[r.template for r in rules],
        priorities=[r.priority for r in rules],
        ignorecase=[r.ignorecase for r in rules],
    )

    invalid_indexes = {e.rule_index for e in result.rule_errors}
    hits_out = [
        HitOut(
            hit_id=h.hit_id,
            rule_index=h.rule_index,
            status=h.status,  # type: ignore[arg-type]
            source_start=h.source_start,
            source_end=h.source_end,
            match_text=h.match_text,
            replacement=h.replacement,
            result_start=h.result_start,
            result_end=h.result_end,
            groups=h.groups,
            suppressed_by_hit_id=h.suppressed_by_hit_id,
            suppressed_by_rule_index=h.suppressed_by_rule_index,
        )
        for h in result.hits
    ]

    rule_reports: list[RuleReport] = []
    for i, rule in enumerate(rules):
        rule_hits = [h for h in result.hits if h.rule_index == i]
        rule_reports.append(
            RuleReport(
                rule_index=i,
                pattern=rule.pattern,
                priority=rule.priority,
                valid=i not in invalid_indexes,
                total_matches=len(rule_hits),
                replaced_matches=sum(
                    1 for h in rule_hits if h.status == "replaced"
                ),
                suppressed_matches=sum(
                    1 for h in rule_hits if h.status == "suppressed_overlap"
                ),
            )
        )

    segments_out = [
        SegmentOut(
            type=s.type,  # type: ignore[arg-type]
            source_start=s.source_start,
            source_end=s.source_end,
            result_start=s.result_start,
            result_end=s.result_end,
            text=s.text,
            hit_id=s.hit_id,
            rule_index=s.rule_index,
        )
        for s in result.segments
    ]

    summary = Summary(
        rules_total=len(rules),
        rules_valid=len(rules) - len(invalid_indexes),
        rules_invalid=len(invalid_indexes),
        hits_total=len(hits_out),
        hits_replaced=sum(1 for h in hits_out if h.status == "replaced"),
        hits_suppressed=sum(
            1 for h in hits_out if h.status == "suppressed_overlap"
        ),
    )

    return DesensitizeResponse(
        original_text=text,
        masked_text=result.masked_text,
        summary=summary,
        rule_errors=[
            RuleErrorOut(
                rule_index=e.rule_index,
                kind=e.kind,  # type: ignore[arg-type]
                code=e.code,
                message=e.message,
                position=e.position,
            )
            for e in result.rule_errors
        ],
        rule_reports=rule_reports,
        hits=hits_out,
        segments=segments_out,
    )


def main() -> None:  # pragma: no cover - container entrypoint
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
