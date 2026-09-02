import pytest
from pydantic import ValidationError

from report_agent.knowledge.seed_schemas import IndicatorSeed


def test_indicator_seed_valid():
    seed = IndicatorSeed.model_validate({
        "code": "GLU", "name": "空腹血糖", "aliases": ["血糖"],
        "unit": "mmol/L", "unit_conversions": {"mg/dL": 0.0555},
        "ranges": [{"sex": "any", "age_min": 18, "age_max": 100,
                    "low": 3.9, "high": 6.1, "critical_low": 2.8, "critical_high": 22.0}],
        "high_suggests": [{"condition": "糖尿病风险", "strength": "strong"}],
        "interventions": [{"level": "recheck", "text": "复查空腹血糖", "timeframe": "2-4周",
                           "departments": ["内分泌科"]}],
        "narrative": "空腹血糖反映糖代谢。",
    })
    assert seed.unit_conversions == {"mg/dL": 0.0555}


def test_indicator_seed_invalid_level():
    with pytest.raises(ValidationError):
        IndicatorSeed.model_validate({
            "code": "X", "name": "x", "interventions": [{"level": "nonsense", "text": "t"}],
        })
