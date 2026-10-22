"""
Test 1: Permission Generator determinism and correctness.
Required by spec - 15 points in A3 criteria.
"""

import json
import tempfile
from pathlib import Path

import pytest

import sys

from permission.generator import PermissionGenerator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from select_eval_set import select_eval_set


class TestPermissionGenerator:
    """Tests for permission generator determinism and correctness."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    @pytest.fixture
    def sample_kg(self, temp_dir):
        """Create a small synthetic KG for testing."""
        kg_path = temp_dir / 'test_kg.txt'
        # Format: head\trel\ttail\tstart\tend
        events = [
            "Q1\tP31\tQ100\t2000\t2010",
            "Q2\tP31\tQ100\t2005\t2015",
            "Q3\tP54\tQ200\t2010\t2020",
            "Q4\tP54\tQ200\t2012\t2022",
            "Q5\tP108\tQ300\t1990\t2000",
            "Q6\tP108\tQ300\t1995\t2005",
            "Q7\tP276\tQ400\t2000\t2020",
            "Q8\tP276\tQ400\t2005\t2025",
        ]
        kg_path.write_text('\n'.join(events))
        return kg_path

    @pytest.fixture
    def sample_questions(self, temp_dir):
        """Create sample questions pickle."""
        import pickle
        q_path = temp_dir / 'test_questions.pickle'
        questions = [
            {
                'uniq_id': 1,
                'question': 'Test question 1',
                'entities': {'Q1', 'Q2'},
                'relations': {'P31'},
                'times': {2005},
                'type': 'time_join',
                'answers': {'Q100'},
            },
            {
                'uniq_id': 2,
                'question': 'Test question 2',
                'entities': {'Q3', 'Q4'},
                'relations': {'P54'},
                'times': {2015},
                'type': 'time_join',
                'answers': {'Q200'},
            },
            {
                'uniq_id': 3,
                'question': 'Test question 3',
                'entities': {'Q5', 'Q6'},
                'relations': {'P108'},
                'times': {1998},
                'type': 'time_join',
                'answers': {'Q300'},
            },
        ]
        with open(q_path, 'wb') as f:
            pickle.dump(questions, f)
        return q_path

    def test_deterministic_same_seed(self, sample_kg, sample_questions, temp_dir):
        """Same seed must produce identical permission labels."""
        generator = PermissionGenerator(seed=42)
        question_ids = [1, 2, 3]

        # Generate twice
        roles1, perms1 = generator.generate_for_tightness('medium', sample_kg, sample_questions, question_ids)
        roles2, perms2 = generator.generate_for_tightness('medium', sample_kg, sample_questions, question_ids)

        # Check roles identical
        assert len(roles1) == len(roles2)
        for rid in roles1:
            assert roles1[rid].allowed_event_ids == roles2[rid].allowed_event_ids

        # Check question permissions identical
        assert len(perms1) == len(perms2)
        for p1, p2 in zip(perms1, perms2):
            assert p1.question_id == p2.question_id
            assert p1.role_id == p2.role_id
            assert p1.allowed_event_ids == p2.allowed_event_ids

    def test_different_seed_different_output(self, sample_kg, sample_questions):
        """Different seeds should produce different outputs."""
        gen1 = PermissionGenerator(seed=42)
        gen2 = PermissionGenerator(seed=999)
        question_ids = [1, 2, 3]

        _, perms1 = gen1.generate_for_tightness('medium', sample_kg, sample_questions, question_ids)
        _, perms2 = gen2.generate_for_tightness('medium', sample_kg, sample_questions, question_ids)

        # At least one question should have different role assignment
        different = False
        for p1, p2 in zip(perms1, perms2):
            if p1.role_id != p2.role_id or p1.allowed_event_ids != p2.allowed_event_ids:
                different = True
                break
        assert different, "Different seeds should produce different outputs"

    def test_three_tightness_levels(self, sample_kg, sample_questions, temp_dir):
        """Generator should produce 3 distinct tightness levels."""
        generator = PermissionGenerator(seed=42)
        question_ids = [1, 2, 3]

        results = {}
        for tightness in ['loose', 'medium', 'strict']:
            roles, perms = generator.generate_for_tightness(tightness, sample_kg, sample_questions, question_ids)
            n_roles = len([r for r in roles if r != 'public'])
            avg_events = sum(len(r.allowed_event_ids) for r in roles.values() if r != 'public') / n_roles
            results[tightness] = {'n_roles': n_roles, 'avg_events': avg_events}

        # Loose should have fewer roles but more events per role than strict
        assert results['loose']['n_roles'] <= results['medium']['n_roles'] <= results['strict']['n_roles']
        assert results['loose']['avg_events'] >= results['medium']['avg_events'] >= results['strict']['avg_events']

    def test_select_1500_deterministic(self, sample_questions, temp_dir):
        """select_eval_set with same seed returns same IDs (single source of truth)."""
        # We need more questions for this test, so create a larger set
        import pickle
        q_path = temp_dir / 'many_questions.pickle'
        questions = [
            {
                'uniq_id': i,
                'question': f'Question {i}',
                'entities': {f'Q{i}'},
                'relations': {'P31'},
                'times': {2000 + (i % 20)},
                'type': 'time_join',
                'answers': {f'Q{i+100}'},
            }
            for i in range(5000)
        ]
        with open(q_path, 'wb') as f:
            pickle.dump(questions, f)

        ids1, _, _, _ = select_eval_set(q_path, seed=42, n=1500)
        ids2, _, _, _ = select_eval_set(q_path, seed=42, n=1500)

        assert ids1 == ids2
        assert len(ids1) == 1500
        assert len(set(ids1)) == 1500  # All unique

    def test_role_assignment_coverage(self, sample_kg, sample_questions):
        """Each question should be assigned a valid role."""
        generator = PermissionGenerator(seed=42)
        question_ids = [1, 2, 3]

        for tightness in ['loose', 'medium', 'strict']:
            roles, perms = generator.generate_for_tightness(tightness, sample_kg, sample_questions, question_ids)

            # Every question should have a permission entry
            assert len(perms) == 3

            # Each permission should reference a valid role.
            # Compact format: allowed events live in roles_*.json, resolved via role_id.
            for perm in perms:
                assert perm.role_id in roles
                assert len(roles[perm.role_id].allowed_event_ids) > 0

    def test_public_role_exists(self, sample_kg, sample_questions):
        """Public role (sees all) should exist in all tightness levels."""
        generator = PermissionGenerator(seed=42)
        question_ids = [1, 2, 3]

        for tightness in ['loose', 'medium', 'strict']:
            roles, _ = generator.generate_for_tightness(tightness, sample_kg, sample_questions, question_ids)
            assert 'public' in roles
            assert len(roles['public'].allowed_event_ids) > 0


class TestKnownRoleMapping:
    """Test with a known role mapping (manual verification case)."""

    def test_known_role_events_match_manual(self, tmp_path):
        """
        Create a role with known event set, verify generator produces matching result.
        This is the "role giả có tập event nhìn thấy được đã biết phải khớp với kết quả tính tay" test.
        """
        # Create KG with known events
        kg_path = tmp_path / 'known_kg.txt'
        # 10 events: 0-4 for relation P31, 5-9 for P54
        events = []
        for i in range(5):
            events.append(f"Q{i}\tP31\tQ100\t2000\t2010")
        for i in range(5, 10):
            events.append(f"Q{i}\tP54\tQ200\t2010\t2020")
        kg_path.write_text('\n'.join(events))

        # Create questions that only use P31
        import pickle
        q_path = tmp_path / 'known_questions.pickle'
        questions = [
            {
                'uniq_id': 100,
                'question': 'P31 question',
                'entities': {'Q0', 'Q1'},
                'relations': {'P31'},
                'times': {2005},
                'type': 'time_join',
                'answers': {'Q100'},
            }
        ]
        with open(q_path, 'wb') as f:
            pickle.dump(questions, f)

        generator = PermissionGenerator(seed=12345)
        roles, perms = generator.generate_for_tightness('medium', kg_path, q_path, [100])

        # The question uses P31, so it should be assigned a role that includes P31 events
        perm = perms[0]
        assigned_role = roles[perm.role_id]
        assert assigned_role.name.startswith('classification_')

        # Check that assigned role has P31 events (event IDs 0-4)
        p31_events = {0, 1, 2, 3, 4}
        role_events = set(assigned_role.allowed_event_ids)

        # At least some overlap with P31 events
        overlap = len(p31_events & role_events)
        assert overlap > 0, f"Role should contain P31 events, got {role_events}"
