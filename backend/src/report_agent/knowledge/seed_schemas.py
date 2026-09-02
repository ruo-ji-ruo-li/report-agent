from typing import Literal

from pydantic import BaseModel, Field


class RangeYaml(BaseModel):
    sex: Literal["male", "female", "any"] = "any"
    age_min: float = 0
    age_max: float = 150
    low: float | None = None
    high: float | None = None
    critical_low: float | None = None
    critical_high: float | None = None
    unit: str | None = None
    source_note: str | None = None


class SuggestYaml(BaseModel):
    condition: str
    strength: str = "medium"  # strong / medium / weak
    note: str | None = None
    description: str | None = None


class InterventionYaml(BaseModel):
    level: Literal["lifestyle", "recheck", "specialist", "urgent"]
    text: str
    timeframe: str | None = None
    departments: list[str] = Field(default_factory=list)


class IndicatorSeed(BaseModel):
    code: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    unit: str | None = None
    unit_conversions: dict[str, float] = Field(default_factory=dict)  # {from_unit: 乘数}
    category: str | None = None
    description: str | None = None
    ranges: list[RangeYaml] = Field(default_factory=list)
    high_suggests: list[SuggestYaml] = Field(default_factory=list)
    low_suggests: list[SuggestYaml] = Field(default_factory=list)
    clusters: list[str] = Field(default_factory=list)
    interventions: list[InterventionYaml] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    narrative: str = ""


class PatternSeed(BaseModel):
    name: str
    description: str = ""
    criteria: list[dict] = Field(default_factory=list)  # [{indicator_code, direction}]
