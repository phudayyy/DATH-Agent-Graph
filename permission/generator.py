"""
Permission Generator for CRONQUESTIONS evaluation.

Generates synthetic role-based permission labels for 1500 questions at 3 tightness levels.
Deterministic: same seed + same input -> same output.
"""

import json
import random
import pickle
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Dict, List, Set, Tuple, Optional
from collections import defaultdict


@dataclass
class Role:
    """A role with its allowed event IDs."""
    role_id: str
    name: str
    allowed_event_ids: List[int]
    parent_role_id: Optional[str] = None
    description: str = ""


@dataclass
class QuestionPermission:
    """Permission labels for a single question."""
    question_id: int
    role_id: str
    allowed_event_ids: List[int]


class PermissionGenerator:
    """
    Generates deterministic permission networks for CRONQUESTIONS.

    Three tightness levels:
    - loose: ~80% events visible per role
    - medium: ~50% events visible per role
    - strict: ~20% events visible per role
    """

    # Relation groups for role assignment
    RELATION_GROUPS = {
        'sports': {'P54', 'P641', 'P1344', 'P1346', 'P2416'},  # sports-related
        'employment': {'P108', 'P118', 'P131', 'P1416', 'P39', 'P463'},  # employment/position
        'location': {'P276', 'P17', 'P131', 'P1376', 'P1382'},  # location-based
        'creative': {'P50', 'P57', 'P161', 'P162', 'P170', 'P175', 'P176', 'P177', 'P178', 'P179'},  # creative works
        'academic': {'P101', 'P102', 'P108', 'P512', 'P69', 'P800', 'P802', 'P803'},  # academic
        'political': {'P39', 'P1100', 'P1416', 'P279', 'P3320'},  # political/government
        'classification': {'P31', 'P279', 'P361', 'P364'},  # instance of, subclass of, part of
        'participation': {'P54', 'P1344', 'P1346'},  # member of sports team, participant
    }

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = random.Random(seed)

    def load_kg_events(self, kg_path: Path) -> List[Tuple[int, str, int, int, int]]:
        """
        Load KG events from full.txt: (event_id, head, relation, tail, start, end).

        Normalization (see data/AUDIT_REPORT.md): 652 events in data_v2 have
        start_year > end_year (noisy Wikidata intervals). They are kept with
        stable event_ids (index in file) but flagged by swapping to an empty
        marker: start > end means the interval never matches any as-of year
        under the left-closed convention.
        """
        events = []
        with open(kg_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                parts = line.strip().split('\t')
                if len(parts) == 5:
                    head, rel, tail, start, end = parts
                    events.append((i, head, rel, tail, int(start), int(end)))
        return events

    @staticmethod
    def is_valid_interval(start_year: int, end_year: int) -> bool:
        """An interval with start > end is empty (data noise, never matches)."""
        return start_year <= end_year

    def load_questions(self, question_path: Path) -> List[dict]:
        """Load questions from pickle file."""
        with open(question_path, 'rb') as f:
            return pickle.load(f)

    def build_relation_to_event_ids(self, events: List[Tuple]) -> Dict[str, List[int]]:
        """Map each relation to list of event IDs using it."""
        rel_to_events = defaultdict(list)
        for event_id, head, rel, tail, start, end in events:
            rel_to_events[rel].append(event_id)
        return dict(rel_to_events)

    def build_entity_to_event_ids(self, events: List[Tuple]) -> Dict[str, List[int]]:
        """Map each entity to list of event IDs it appears in."""
        ent_to_events = defaultdict(list)
        for event_id, head, rel, tail, start, end in events:
            ent_to_events[head].append(event_id)
            ent_to_events[tail].append(event_id)
        return dict(ent_to_events)

    def assign_roles_by_tightness(self, tightness: str,
                                   rel_to_events: Dict[str, List[int]],
                                   ent_to_events: Dict[str, List[int]],
                                   rng: random.Random) -> Dict[str, Role]:
        """
        Create roles and assign event permissions based on tightness level.
        Returns role_id -> Role mapping.
        Uses provided RNG for determinism.
        """
        roles = {}
        all_event_ids = set()
        for events in rel_to_events.values():
            all_event_ids.update(events)
        all_event_ids = list(all_event_ids)
        total_events = len(all_event_ids)

        if tightness == 'loose':
            # Few broad roles, each sees many events
            n_roles = 5
            target_coverage = 0.8
        elif tightness == 'medium':
            # More roles, each sees moderate events
            n_roles = 12
            target_coverage = 0.5
        elif tightness == 'strict':
            # Many narrow roles, each sees few events
            n_roles = 25
            target_coverage = 0.2
        else:
            raise ValueError(f"Unknown tightness: {tightness}")

        # Shuffle events for deterministic but random-looking assignment
        rng.shuffle(all_event_ids)

        # Create roles based on relation groups
        group_names = list(self.RELATION_GROUPS.keys())
        rng.shuffle(group_names)

        # Each role sees ~target_coverage of the KG. The role count (n_roles)
        # controls partition granularity, not per-role size: loose roles are
        # broad and overlap heavily, strict roles are narrow slices.
        events_per_role = max(1, int(total_events * target_coverage))

        for i in range(n_roles):
            group = group_names[i % len(group_names)]
            role_id = f"role_{tightness}_{i}"

            # Collect events for this role's relation group (semantic core, kept fully)
            role_event_set = set()
            for rel in sorted(self.RELATION_GROUPS.get(group, set())):
                role_event_set.update(rel_to_events.get(rel, []))
            role_events = sorted(role_event_set)

            # If the semantic core overshoots the target badly, sample it down
            # deterministically (keeps coverage roughly at the tightness level)
            max_core = max(events_per_role * 2, 1)
            if len(role_events) > max_core:
                role_events = sorted(rng.sample(role_events, max_core))

            # If below target, add random filler events
            if len(role_events) < events_per_role:
                needed = events_per_role - len(role_events)
                role_event_set = set(role_events)
                candidates = [e for e in all_event_ids if e not in role_event_set]
                rng.shuffle(candidates)
                role_events.extend(candidates[:needed])

            roles[role_id] = Role(
                role_id=role_id,
                name=f"{group}_{tightness}_{i}",
                allowed_event_ids=role_events,
                description=f"{tightness.capitalize()} role for {group} domain"
            )

        # Add a "public" role that sees everything (for baseline)
        roles['public'] = Role(
            role_id='public',
            name='public_all',
            allowed_event_ids=all_event_ids,
            description='Public role - sees all events (no filtering)'
        )

        return roles

    def assign_question_to_role(self, question: dict, roles: Dict[str, Role],
                                ent_to_events: Dict[str, List[int]],
                                rng: random.Random,
                                role_event_sets: Optional[Dict[str, Set[int]]] = None) -> str:
        """
        Assign a question to the most relevant role based on its entities/relations.
        Returns role_id.
        Uses provided RNG for deterministic tie-breaking.
        """
        # Score each role by overlap with question's entities/relations
        best_role = 'public'
        best_score = -1

        q_entities = question.get('entities', set())
        q_relations = question.get('relations', set())
        if role_event_sets is None:
            role_event_sets = {
                rid: set(role.allowed_event_ids)
                for rid, role in roles.items()
                if rid != 'public'
            }
        entity_event_sets = {
            ent: set(ent_to_events.get(ent, []))
            for ent in q_entities
        }

        # Get role IDs in deterministic order for tie-breaking
        role_ids = [rid for rid in roles.keys() if rid != 'public']
        role_ids.sort()  # deterministic order

        for role_id in role_ids:
            role = roles[role_id]

            # Score: how many of question's entities appear in role's events
            score = 0
            role_event_set = role_event_sets[role_id]

            for ent in q_entities:
                ent_events = entity_event_sets[ent]
                overlap = len(ent_events & role_event_set)
                score += overlap

            # Bonus only when the question relation belongs to this role's
            # semantic group. The previous check used `any(...)` across every
            # group, so a matching relation boosted every role equally.
            role_group = role.name.split('_', 1)[0]
            role_relations = self.RELATION_GROUPS.get(role_group, set())
            score += 10 * sum(1 for rel in q_relations if rel in role_relations)

            if score > best_score:
                best_score = score
                best_role = role_id

        return best_role

    def generate_for_tightness(self, tightness: str,
                               kg_path: Path,
                               question_path: Path,
                               question_ids: List[int]) -> Tuple[Dict[str, Role], List[QuestionPermission]]:
        """
        Generate permission labels for a specific tightness level.
        Returns (roles_dict, list_of_question_permissions).
        Deterministic: uses a fresh RNG derived from base seed + tightness.
        """
        # Create fresh RNG for this tightness to ensure determinism across calls.
        # NOTE: hash() of str is salted per-process in Python (PYTHONHASHSEED),
        # so use a stable mapping instead.
        tightness_index = {'loose': 0, 'medium': 1, 'strict': 2}[tightness]
        tightness_seed = (self.seed * 1000 + tightness_index) & 0xFFFFFFFF
        rng = random.Random(tightness_seed)

        # Load data
        events = self.load_kg_events(kg_path)
        questions = self.load_questions(question_path)

        # Build indices
        rel_to_events = self.build_relation_to_event_ids(events)
        ent_to_events = self.build_entity_to_event_ids(events)

        # Create roles for this tightness (using fresh RNG)
        roles = self.assign_roles_by_tightness(tightness, rel_to_events, ent_to_events, rng)
        role_event_sets = {
            rid: set(role.allowed_event_ids)
            for rid, role in roles.items()
            if rid != 'public'
        }

        # Assign each question to a role, grouped by uniq_id: paraphrase
        # records share one label (same entities/relations/answers).
        # Output is sorted by question_id for stable, diffable files.
        question_id_set = set(question_ids)
        seen_qids = set()
        q_permissions = []
        for q in questions:
            qid = q.get('uniq_id')
            if qid not in question_id_set or qid in seen_qids:
                continue
            seen_qids.add(qid)

            role_id = self.assign_question_to_role(
                q, roles, ent_to_events, rng, role_event_sets
            )

            # Permission labels store only the role_id (compact format);
            # allowed events live in roles_*.json, resolved via role_id.
            q_permissions.append(QuestionPermission(
                question_id=qid,
                role_id=role_id,
                allowed_event_ids=[],
            ))
        q_permissions.sort(key=lambda qp: qp.question_id)

        return roles, q_permissions

    def generate_all(self, kg_path: Path, question_path: Path,
                     question_ids: List[int], output_dir: Path) -> dict:
        """
        Generate permissions for all 3 tightness levels.
        Saves JSON files to output_dir.
        Returns summary dict.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        summary = {
            'seed': self.seed,
            'n_questions': len(question_ids),
            'tightness_levels': {}
        }

        for tightness in ['loose', 'medium', 'strict']:
            print(f"Generating {tightness} permissions...")
            roles, q_perms = self.generate_for_tightness(tightness, kg_path, question_path, question_ids)

            # Save roles
            roles_data = {rid: asdict(role) for rid, role in roles.items()}
            with open(output_dir / f'roles_{tightness}.json', 'w', encoding='utf-8') as f:
                json.dump(roles_data, f, ensure_ascii=False, indent=2)

            # Save question permissions
            perms_data = [asdict(qp) for qp in q_perms]
            with open(output_dir / f'permissions_{tightness}.jsonl', 'w', encoding='utf-8') as f:
                for qp in perms_data:
                    f.write(json.dumps(qp, ensure_ascii=False) + '\n')

            # Summary stats
            n_roles = len(roles) - 1  # exclude public
            avg_events = sum(len(r.allowed_event_ids) for r in roles.values() if r.role_id != 'public') / n_roles
            total_events = len(roles['public'].allowed_event_ids)

            summary['tightness_levels'][tightness] = {
                'n_roles': n_roles,
                'avg_events_per_role': round(avg_events),
                'total_events_in_kg': total_events,
                'coverage_pct': round(avg_events / total_events * 100, 1)
            }

            print(f"  {tightness}: {n_roles} roles, avg {avg_events:.0f} events/role ({summary['tightness_levels'][tightness]['coverage_pct']}% coverage)")

        # Save summary
        with open(output_dir / 'generation_summary.json', 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        return summary


