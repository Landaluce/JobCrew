"""Tests for resilient parsing of LLM shortlist output."""

from job_automation.shortlist import (
    extract_jobs_from_text,
    normalize_job_entry,
    recover_jobs_from_text,
    well_formed_job,
)

JOBS = [
    {
        "title": "Backend Engineer", "company": "Acme", "location": "Remote",
        "url": "https://boards.greenhouse.io/acme/jobs/42", "score": 91, "rationale": "Python match",
    },
    {
        "title": "Data Engineer", "company": "Globex", "location": "Berlin",
        "url": "https://jobs.lever.co/globex/role/7", "score": 78.5, "rationale": "SQL",
    },
]


def test_parses_plain_json_envelope() -> None:
    text = '{"jobs": ' + str(JOBS).replace("'", '"') + "}"
    jobs = extract_jobs_from_text(text)
    assert len(jobs) == 2
    assert jobs[0]["company"] == "Acme"


def test_parses_json_inside_markdown_fence() -> None:
    import json

    payload = json.dumps({"jobs": JOBS})
    text = "Here are the results:\n```json\n" + payload + "\n```\nHope this helps."
    jobs = extract_jobs_from_text(text)
    assert len(jobs) == 2
    assert jobs[1]["url"].startswith("https://jobs.lever.co")


def test_parses_json_with_prose_around_it() -> None:
    import json

    payload = json.dumps({"jobs": JOBS})
    jobs = extract_jobs_from_text(f"I ranked the roles.\n{payload}\nBest matches above.")
    assert len(jobs) == 2


def test_parses_bare_json_array() -> None:
    import json

    jobs = extract_jobs_from_text(json.dumps(JOBS))
    assert len(jobs) == 2


def test_tolerates_alternate_envelope_keys() -> None:
    import json

    text = json.dumps({"job_list": JOBS})
    assert len(extract_jobs_from_text(text)) == 2


def test_nested_braces_and_quoted_braces_do_not_confuse_parser() -> None:
    import json

    tricky = [
        {
            "title": "Engineer {Senior}", "company": "Acme {Labs}",
            "url": "https://a.example.com/jobs/1?q={x}", "rationale": "brace { in text",
        },
    ]
    jobs = extract_jobs_from_text(json.dumps({"jobs": tricky}))
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Engineer {Senior}"


def test_garbage_text_returns_empty_list() -> None:
    assert extract_jobs_from_text("no json here at all") == []
    assert extract_jobs_from_text("") == []
    assert extract_jobs_from_text(None) == []  # type: ignore[arg-type]
    assert extract_jobs_from_text("https://example.com/jobs/1") == []


def test_normalize_job_entry_coerces_fields() -> None:
    cleaned = normalize_job_entry(
        {"title": "  Engineer  ", "url": None, "score": "150", "rationale": 42, "company": 12}
    )
    assert cleaned["title"] == "Engineer"
    assert cleaned["url"] == ""
    assert cleaned["score"] == 100.0  # clamped
    assert cleaned["rationale"] == "42"
    assert cleaned["company"] == "12"


def test_normalize_job_entry_clamps_low_scores() -> None:
    assert normalize_job_entry({"score": "-5"})["score"] == 0.0
    assert normalize_job_entry({"score": "n/a"})["score"] == 0.0
    assert normalize_job_entry({})["score"] == 0.0


def test_well_formed_job_requires_url_and_identity() -> None:
    assert well_formed_job({"url": "https://x.example.com/jobs/1", "title": "Dev", "company": "Acme"})
    assert not well_formed_job({"url": "", "title": "Dev"})
    assert not well_formed_job({"title": "Dev", "company": "Acme"})
    assert not well_formed_job({"url": "https://example.com", "company": "Acme"})
    assert not well_formed_job({"url": "n/a", "title": "Dev"})


def test_recover_jobs_filters_placeholders_and_cleans_fields() -> None:
    import json

    payload = {"jobs": [
        {"title": "Real", "company": "Acme", "url": "https://boards.greenhouse.io/acme/jobs/1", "score": "95"},
        {"title": "Placeholder", "company": "X", "url": "https://example.com"},
    ]}
    recovered = recover_jobs_from_text(json.dumps(payload))
    assert len(recovered) == 1
    assert recovered[0]["score"] == 95.0
