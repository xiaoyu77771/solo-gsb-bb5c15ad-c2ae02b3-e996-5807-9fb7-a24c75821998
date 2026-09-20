from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_desensitize_endpoint_full_contract():
    payload = {
        "text": "联系我: 13800001111, 邮箱 a@b.com",
        "rules": [
            {"pattern": r"\d{11}", "template": "[PHONE]", "priority": 5},
            {"pattern": r"\d{3}", "template": "XXX", "priority": 10},
            {"pattern": r"([", "template": "X"},  # invalid regex
            {"pattern": r"(?P<u>\w+)@(?P<d>\w+)", "template": "${u}@***"},
            {"pattern": r"(a)", "template": r"\9"},  # invalid template
        ],
    }
    r = client.post("/api/v1/desensitize", json=payload)
    assert r.status_code == 200
    body = r.json()

    # Invalid rules are reported individually but never block the request.
    errors = body["rule_errors"]
    assert {e["rule_index"]: e["kind"] for e in errors} == {
        2: "pattern",
        4: "template",
    }

    # Phone triplets win over the full phone; \w+ stops at the dot in the
    # domain, so ".com" is untouched.
    assert body["masked_text"] == "联系我: XXXXXXXXX11, 邮箱 a@***.com"

    summary = body["summary"]
    assert summary["rules_total"] == 5
    assert summary["rules_invalid"] == 2
    assert summary["rules_valid"] == 3
    assert summary["hits_replaced"] >= 1
    assert summary["hits_total"] == summary["hits_replaced"] + summary[
        "hits_suppressed"
    ]

    # Every hit carries source/result interval information.
    for hit in body["hits"]:
        assert {"hit_id", "rule_index", "status", "source_start", "source_end"} <= set(
            hit
        )
        if hit["status"] == "replaced":
            assert body["masked_text"][
                hit["result_start"] : hit["result_end"]
            ] == hit["replacement"]
        else:
            assert hit["replacement"] is None
            assert hit["result_start"] is None
            winner = body["hits"][hit["suppressed_by_hit_id"]]
            assert winner["status"] == "replaced"

    # Segments reconstruct the masked text and map source -> result.
    assert "".join(s["text"] for s in body["segments"]) == body["masked_text"]
    for seg in body["segments"]:
        if seg["type"] == "text":
            assert (
                body["original_text"][seg["source_start"] : seg["source_end"]]
                == seg["text"]
            )

    # Rule reports cover every submitted rule, including invalid ones.
    assert [rr["rule_index"] for rr in body["rule_reports"]] == [0, 1, 2, 3, 4]
    assert body["rule_reports"][2]["valid"] is False


def test_default_template_is_stars():
    r = client.post(
        "/api/v1/desensitize",
        json={"text": "abc123", "rules": [{"pattern": r"\d+"}]},
    )
    assert r.status_code == 200
    assert r.json()["masked_text"] == "abc***"


def test_empty_rules_rejected():
    r = client.post(
        "/api/v1/desensitize", json={"text": "x", "rules": []}
    )
    assert r.status_code == 422


def test_openapi_available():
    r = client.get("/openapi.json")
    assert r.status_code == 200
