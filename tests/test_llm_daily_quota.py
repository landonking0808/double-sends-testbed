import pytest

from dst.llm.base import _daily_quota_429


def test_daily_quota_bodies():
    assert _daily_quota_429('{"errors":[{"message":"AiError: you have used up your daily free allocation of 10,000 neurons"}]}')
    assert _daily_quota_429('{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}')
    assert not _daily_quota_429('{"quotaId": "GenerateContentInputTokensPerModelPerMinute-FreeTier"}')
    assert not _daily_quota_429("rate limited")


def test_daily_quota_429_stops_without_retry(tmp_path):
    from tests.test_llm_client import make_gemini, MSG
    from dst.llm import QuotaExhausted
    import httpx
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(429, json={"error": {"details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}})

    c, _ = make_gemini(tmp_path, handler)
    with pytest.raises(QuotaExhausted):
        c.complete(MSG, model="m", temperature=0.7, seed=1)
    assert len(seen) == 1
