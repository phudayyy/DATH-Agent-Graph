"""Indexes CRONQUESTIONS facts without changing their line-based event IDs."""

from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

Event = Tuple[int, str, str, str, int, int]


def event_is_valid(start: int, end: int, year: int) -> bool:
    """A fact [start, end] holds in `year` iff start <= year <= end.

    This is the left-closed convention written in the README: a fact maps to
    [1/1/start, 1/1/(end+1)) UTC and a year is asked at 1/1/year, so the end
    year counts (CronKGQA's own `discretizeKG` walks `range(start, end + 1)`,
    and this rule reproduces CRONQUESTIONS' gold answers on all 360
    simple_entity questions of the frozen set). A point fact (start == end,
    48% of data_v2) holds during that one year. start > end is empty: the 652
    reversed rows, including the single row whose end year is 0.
    The graph query (`agent_graph.cronquestions`) applies the same predicate;
    tests/test_asof.py checks that the two agree year by year.
    """
    return start <= year <= end


class KGEventIndex:
    """Small-memory indexes for event-level attribution and temporal golds."""

    def __init__(self, events: Iterable[Event]):
        self.events: Dict[int, Event] = {}
        self.by_entity: Dict[str, List[int]] = defaultdict(list)
        self.by_head_relation: Dict[Tuple[str, str], List[int]] = defaultdict(list)
        self.by_tail_relation: Dict[Tuple[str, str], List[int]] = defaultdict(list)
        for event_id, head, rel, tail, start, end in events:
            event = (int(event_id), str(head), str(rel), str(tail), int(start), int(end))
            eid, h, r, t, _, _ = event
            self.events[eid] = event
            self.by_entity[h].append(eid)
            self.by_entity[t].append(eid)
            self.by_head_relation[(h, r)].append(eid)
            self.by_tail_relation[(t, r)].append(eid)

    @classmethod
    def from_file(cls, path: Path) -> "KGEventIndex":
        def rows():
            with path.open("r", encoding="utf-8") as stream:
                for line_id, line in enumerate(stream):
                    fields = line.rstrip("\n").split("\t")
                    if len(fields) == 5:
                        h, r, t, start, end = fields
                        yield line_id, h, r, t, int(start), int(end)
        return cls(rows())

    def asof_answers(self, head: str, relation: str, year: int) -> Set[str]:
        answers = set()
        for eid in self.by_head_relation.get((head, relation), ()):
            _, _, _, tail, start, end = self.events[eid]
            if event_is_valid(start, end, year):
                answers.add(tail)
        return answers

    def retrieve_question(self, question: dict, limit: int = 64) -> List[Event]:
        """Return a deterministic, fixed-budget set of query-relevant facts.

        This retrieval path deliberately does not apply permission or as-of
        predicates. A0 and A1 call the same method with the same budget.
        """
        anchors = {str(entity) for entity in question.get("entities", [])}
        relations = {str(relation) for relation in question.get("relations", [])}
        if limit < 1:
            raise ValueError("retrieval limit must be positive")
        candidate_ids: Set[int] = set()
        for anchor in anchors:
            for event_id in self.by_entity.get(anchor, ()):
                event = self.events[event_id]
                if not relations or event[2] in relations:
                    candidate_ids.add(event_id)

        # Expand one hop from anchors so joins such as "teammate in team X"
        # can include facts for the other team members, not only the subject.
        bridge_entities: Set[str] = set()
        for event_id in tuple(candidate_ids):
            _, head, _, tail, _, _ = self.events[event_id]
            if head in anchors:
                bridge_entities.add(tail)
            if tail in anchors:
                bridge_entities.add(head)
        for bridge in bridge_entities:
            for event_id in self.by_entity.get(bridge, ()):
                event = self.events[event_id]
                if not relations or event[2] in relations:
                    candidate_ids.add(event_id)

        def rank(event_id):
            _, head, relation, tail, _, _ = self.events[event_id]
            anchor_overlap = int(head in anchors) + int(tail in anchors)
            relation_match = int(not relations or relation in relations)
            return (-relation_match, -anchor_overlap, event_id)

        selected = sorted(candidate_ids, key=rank)[:limit]
        return [self.events[event_id] for event_id in selected]

    def latest_answers(self, head: str, relation: str) -> Set[str]:
        # Reversed intervals (start > end) are empty, as in `event_is_valid`.
        candidates = [event for eid in self.by_head_relation.get((head, relation), ())
                      if (event := self.events[eid])[4] <= event[5]]
        if not candidates:
            return set()
        # For the "latest" comparison use the fact(s) with the newest
        # observed start year. Point facts (start == end) hold for one year
        # and rank like any other fact.
        latest_year = max(event[4] for event in candidates)
        return {tail for _, _, _, tail, start, _ in candidates if start == latest_year}

    def evidence_for_mentions(self, mentions: Set[str], anchors: Set[str],
                              relations: Set[str]) -> Set[int]:
        """Return relation facts directly connecting a mentioned entity to an anchor.

        This conservative attribution avoids treating every fact about an
        anchor as evidence for every answer entity mentioned in the text.
        CRONQUESTIONS' time_join prompts include the shared team/location as
        a linked entity, so the answer-to-anchor edge is directly identifiable.
        """
        relevant: Set[int] = set()
        mentions = set(mentions) - set(anchors)
        if not mentions or not relations:
            return relevant
        for entity in mentions:
            for eid in self.by_entity.get(entity, ()):
                _, head, rel, tail, _, _ = self.events[eid]
                if (rel in relations and {head, tail} & anchors
                        and {head, tail} & mentions):
                    relevant.add(eid)
        return relevant

    def evidence_for_year_mentions(self, text: str, anchors: Set[str],
                                   relations: Set[str]) -> Set[int]:
        """Attribute year-only answers to matching facts valid in that year."""
        import re
        years = {int(value) for value in re.findall(r"\b(?:1[0-9]{3}|20[0-9]{2})\b", text or "")}
        relevant: Set[int] = set()
        for anchor in anchors:
            for event_id in self.by_entity.get(anchor, ()):
                _, _, relation, _, start, end = self.events[event_id]
                if relation in relations and any(event_is_valid(start, end, year) for year in years):
                    relevant.add(event_id)
        return relevant
