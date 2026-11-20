import json
import math
from pathlib import Path

import pandas as pd

from app.services.volume_profile import calculate_volume_profile


def test_canonical_api_volume_profile_matches_existing_v2_reference_cases():
    fixtures=json.loads((Path(__file__).parent/'fixtures/volume_profile_v2_cases.json').read_text())
    for case in fixtures['profile_cases']:
        actual=calculate_volume_profile(pd.DataFrame(case['bars']),rows=case['rows'],minimum_tick=case['mintick'],value_area_percent=case['value_area_percent'])
        assert actual['available'] is case['expected']['available']
        for field in ('poc','vah','val','delta_percent'):
            expected=case['expected'][field]
            assert actual[field] is None if expected is None else math.isclose(actual[field],expected,abs_tol=1e-12)
