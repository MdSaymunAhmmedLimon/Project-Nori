"""Deterministic HTTP responses for workflow tests; never used by the app."""

import io
import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.core.cache import cache


def mock_routing(test_case):
    cache.clear()

    def response(request, **kwargs):
        url = urlsplit(request.full_url)
        points = [list(map(float, point.split(','))) for point in url.path.rsplit('/', 1)[1].split(';')]
        if '/table/' in url.path:
            sources = parse_qs(url.query)['sources'][0].split(';')
            payload = {'code': 'Ok', 'distances': [[1200.0] for _ in sources]}
        else:
            payload = {'code': 'Ok', 'routes': [{'distance': 5700.0, 'duration': 840.0,
                        'geometry': {'type': 'LineString', 'coordinates': points}}]}
        return io.BytesIO(json.dumps(payload).encode())

    mock = patch('rides.routing.urlopen', side_effect=response)
    test_case.addCleanup(mock.stop)
    test_case.addCleanup(cache.clear)
    return mock.start()
