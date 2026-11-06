"""
Backwards-compatible stats calculators consumed by the UKRDC API.
"""

from ukrdc_stats.calculators.ckd import PrevalentCKDCalculator
from ukrdc_stats.calculators.krt import KRTStatsCalculator

__all__ = ["KRTStatsCalculator", "PrevalentCKDCalculator"]
