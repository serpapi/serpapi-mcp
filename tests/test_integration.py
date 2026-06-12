"""Live contract test. Skipped unless SERPAPI_KEY is set, so CI stays green
without a key. It validates the one boundary the unit tests mock: that
serpapi.search returns a SerpResults whose as_dict carries organic_results.
"""

import os

import pytest
import serpapi

KEY = os.getenv("SERPAPI_KEY")


@pytest.mark.skipif(
    not KEY, reason="set SERPAPI_KEY to run the live SerpApi contract test"
)
def test_live_search_returns_organic_results():
    results = serpapi.search({"engine": "google_light", "q": "coffee", "api_key": KEY})
    data = results.as_dict()
    assert "organic_results" in data
