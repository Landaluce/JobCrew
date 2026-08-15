from job_automation.identity import canonical_url, job_id


def test_tracking_parameters_do_not_change_job_identity() -> None:
    base = {"url": "https://jobs.example.com/opening/42"}
    tracked = {"url": "https://jobs.example.com/opening/42?utm_source=search#details"}
    assert canonical_url(tracked["url"]) == base["url"]
    assert job_id(base) == job_id(tracked)


def test_identity_falls_back_to_job_details_without_url() -> None:
    assert job_id({"company": "Acme", "title": "Engineer", "location": "Remote"})
