"""
Test 3: As-of temporal query with left-closed boundary convention.
Required by spec - 15 points in A3 criteria.
Tests fake entity with 3 valid-time intervals, checks 3 time points + boundaries.
"""

import pytest
from evaluation.kg_index import KGEventIndex
from evaluation.temporal_error import (
    check_temporal_error, compute_temporal_error_rate,
    left_closed_boundary_check, TemporalErrorResult
)


class TestAsOfTemporal:
    """Tests for as-of temporal filtering with left-closed boundary."""

    def test_left_closed_boundary_basic(self):
        """Test left-closed interval: [start, end)"""
        # Event valid from 2000 to 2010
        # At 2000: valid (start <= year < end)
        assert left_closed_boundary_check(2000, 2010, 2000) == True
        # At 2005: valid
        assert left_closed_boundary_check(2000, 2010, 2005) == True
        # At 2009: valid
        assert left_closed_boundary_check(2000, 2010, 2009) == True
        # At 2010: NOT valid (end is exclusive)
        assert left_closed_boundary_check(2000, 2010, 2010) == False
        # At 2011: NOT valid
        assert left_closed_boundary_check(2000, 2010, 2011) == False
        # Before start: NOT valid
        assert left_closed_boundary_check(2000, 2010, 1999) == False

    def test_left_closed_boundary_no_end(self):
        """Event with no end year (ongoing) - end treated as infinity."""
        # Event from 2000 onwards
        assert left_closed_boundary_check(2000, None, 1999) == False
        assert left_closed_boundary_check(2000, None, 2000) == True
        assert left_closed_boundary_check(2000, None, 2020) == True
        assert left_closed_boundary_check(2000, 0, 2000) == True  # 0 as no-end

    def test_three_intervals_entity(self):
        """
        Fake entity with 3 valid-time intervals.
        Test 3 time points and boundaries.
        """
        # Entity has 3 non-overlapping intervals:
        # Interval 1: [1990, 2000) - e.g., position held 1990-1999
        # Interval 2: [2000, 2010) - e.g., position held 2000-2009
        # Interval 3: [2010, None) - e.g., position held 2010-present

        index = KGEventIndex([
            (0, "Qperson", "P39", "Qrole1", 1990, 2000),
            (1, "Qperson", "P39", "Qrole2", 2000, 2010),
            (2, "Qperson", "P39", "Qrole3", 2010, 0),
            # A malformed interval is retained by event ID but never matches.
            (3, "Qperson", "P39", "Qbad", 2020, 2015),
        ])

        # Test at 3 time points - one in each interval
        assert index.asof_answers("Qperson", "P39", 1995) == {"Qrole1"}
        assert index.asof_answers("Qperson", "P39", 2005) == {"Qrole2"}
        assert index.asof_answers("Qperson", "P39", 2015) == {"Qrole3"}

        # Test boundaries between intervals
        # At 2000: should be in interval 2 (left-closed: interval 1 ends at 2000, interval 2 starts at 2000)
        assert index.asof_answers("Qperson", "P39", 1990) == {"Qrole1"}
        assert index.asof_answers("Qperson", "P39", 2000) == {"Qrole2"}

        # At 2010: should be in interval 3
        assert index.asof_answers("Qperson", "P39", 2010) == {"Qrole3"}
        assert index.asof_answers("Qperson", "P39", 2020) == {"Qrole3"}

    def test_correct_at_asof_no_temporal_error(self):
        """Answer correct at as-of time -> no temporal error."""
        result = check_temporal_error(
            predicted="Q123",
            gold_at_asof={"Q123", "Q456"},
            gold_latest={"Q123", "Q789"},
            as_of_year=2005
        )
        assert result.has_temporal_error == False
        assert result.error_type == 'correct'

    def test_temporal_error_correct_latest_wrong_asof(self):
        """
        TEMPORAL ERROR: Answer correct for latest state but WRONG for as-of time.
        This is the key metric we measure.
        """
        # At as-of 2005, correct answer was Q123
        # But at latest (2020), correct answer is Q789
        # Model predicts Q789 -> temporal error
        result = check_temporal_error(
            predicted="Q789",  # Latest answer
            gold_at_asof={"Q123"},  # As-of 2005 answer
            gold_latest={"Q789"},  # Latest answer
            as_of_year=2005
        )
        assert result.has_temporal_error == True
        assert result.error_type == 'temporal_error'

    def test_other_error_wrong_at_both(self):
        """Wrong at both as-of and latest -> other error, not temporal error."""
        result = check_temporal_error(
            predicted="Q999",  # Wrong at both times
            gold_at_asof={"Q123"},
            gold_latest={"Q789"},
            as_of_year=2005
        )
        assert result.has_temporal_error == False
        assert result.error_type == 'other_error'

    def test_compute_temporal_error_rate(self):
        """Test aggregate temporal error rate."""
        results = [
            TemporalErrorResult(1, False, 2005, "Q123", "Q123", "Q123", 'correct'),
            TemporalErrorResult(2, True, 2005, "Q789", "Q123", "Q789", 'temporal_error'),
            TemporalErrorResult(3, True, 2005, "Q789", "Q123", "Q789", 'temporal_error'),
            TemporalErrorResult(4, False, 2005, "Q999", "Q123", "Q789", 'other_error'),
            TemporalErrorResult(5, False, 0, "Q123", "Q123", "Q123", 'correct'),  # No as-of
        ]

        rates = compute_temporal_error_rate(results)
        # Only 4 questions have as-of time (qid 5 has as_of_year=0)
        assert rates['with_asof'] == 4
        assert rates['temporal_error_count'] == 2
        assert rates['temporal_error_rate'] == 0.5  # 2/4
        assert rates['correct_count'] == 1
        assert rates['correct_rate'] == 0.25  # 1/4
        assert rates['other_error_count'] == 1
        assert rates['other_error_rate'] == 0.25  # 1/4

    def test_qid_extraction_from_text(self):
        """Temporal error check should handle Q-ids embedded in text."""
        result = check_temporal_error(
            predicted="The answer is Q789 based on latest data",
            gold_at_asof={"Q123"},
            gold_latest={"Q789"},
            as_of_year=2005
        )
        assert result.has_temporal_error == True
        assert result.error_type == 'temporal_error'


class TestAsOfBoundaryConvention:
    """Verify the left-closed boundary convention is correctly documented and used."""

    def test_boundary_convention_documentation(self):
        """
        The spec requires: 'quy ước nửa khoảng đóng bên trái (left-closed) ghi trong README'
        This test verifies the convention is implemented correctly.
        """
        # Left-closed means: [start_year, end_year)
        # At exactly start_year: INCLUDED
        # At exactly end_year: EXCLUDED

        # Example: Person held position from 2000 to 2010
        # Query as-of 2000: should see the position (start_year <= 2000 < end_year) -> True
        # Query as-of 2010: should NOT see the position (2010 < 2010 is False) -> False

        assert left_closed_boundary_check(2000, 2010, 2000) == True   # At start
        assert left_closed_boundary_check(2000, 2010, 2010) == False  # At end

        # This matches SQL:2011 valid time semantics (per SIGMOD Record reference in spec)
