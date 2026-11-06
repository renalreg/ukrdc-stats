"""
Functions to calculate patient cohort statistics.
Pydantic classes to return nice plotly friendly output.

The DemographicStatsCalculator available prior to version 3.0.0 has been
removed; demographics are now served by the KRT and CKD calculators.
"""

from ukrdc_stats.calculators.ckd import PrevalentCKDCalculator
from ukrdc_stats.calculators.krt import KRTStatsCalculator

__all__ = ["KRTStatsCalculator", "PrevalentCKDCalculator"]
