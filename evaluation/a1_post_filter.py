"""A1 post-generation permission filter using exact A0 retrieval provenance.

The model is not asked to cite event IDs in prose. A0-v2 stores the exact event
IDs that were supplied to the model. This module attributes a canonical answer
to matching facts within that retrieved set and checks whether those facts are
permitted for the question's synthetic role.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

REFUSAL = (
    "I cannot answer because the available evidence is outside your permissions."
)

_YEAR_RE = re.compile(r"(?<![A-Za-z0-9])(?:1[0-9]{3}|20[0-9]{2})(?![A-Za-z0-9])")
_QID_RE = re.compile(r"(?<![A-Za-z0-9])Q\d+(?!\d)", re.I)
_EVENT_REF_RE = re.compile(r"\bevent_id\s*[=:]\s*(\d+)\b|\bE(\d+)\b", re.I)


def _event_index_supports_entity(
    event_index,
    event_id: int,
    answer_qid: str,
    anchors: set[str],
    relations: set[str],
) -> bool:
    event = event_index.events.get(int(event_id))
    if event is None:
        return False
    _, head, relation, tail, _, _ = event
    endpoints = {str(head).upper(), str(tail).upper()}
    qid = str(answer_qid).upper()
    anchor_set = {str(x).upper() for x in anchors} - {qid}
    relation_set = {str(x).upper() for x in relations}
    return (
        relation.upper() in relation_set
        and qid in endpoints
        and bool(endpoints & anchor_set)
    )


def _event_index_supports_year(
    event_index,
    event_id: int,
    year: int,
    anchors: set[str],
    relations: set[str],
) -> bool:
    event = event_index.events.get(int(event_id))
    if event is None:
        return False
    _, head, relation, tail, start, end = event
    endpoints = {str(head).upper(), str(tail).upper()}
    anchor_set = {str(x).upper() for x in anchors}
    relation_set = {str(x).upper() for x in relations}
    if not relation_set or relation.upper() not in relation_set:
        return False
    # For two-anchor temporal questions, require the event to connect the
    # queried entities. For one-anchor questions, require that anchor endpoint.
    if len(anchor_set) >= 2:
        if not anchor_set.issubset(endpoints):
            return False
    elif not (anchor_set & endpoints):
        return False
    from .kg_index import event_is_valid
    return event_is_valid(int(start), int(end), int(year))


def _reasoning_supporting_events(
    reasoning: str,
    retrieved_ids: set[int],
    question_entities: set[str],
    relations: set[str],
    event_index,
) -> set[int]:
    """Map reasoning mentions only to facts actually supplied in the prompt."""
    if not reasoning or not retrieved_ids:
        return set()

    anchors = {str(x).upper() for x in question_entities}
    mentioned_qids = {x.upper() for x in _QID_RE.findall(reasoning)} - anchors
    explicit_ids = {
        int(a or b)
        for a, b in _EVENT_REF_RE.findall(reasoning)
        if a or b
    }
    found = explicit_ids & retrieved_ids

    for qid in mentioned_qids:
        for event_id in retrieved_ids:
            event = event_index.events.get(event_id)
            if event is None:
                continue
            _, head, relation, tail, _, _ = event
            endpoints = {str(head).upper(), str(tail).upper()}
            if (
                str(relation).upper() in {str(r).upper() for r in relations}
                and qid in endpoints
                and bool((endpoints - {qid}) & anchors)
            ):
                found.add(event_id)

    # Include retrieved events that support a year mentioned in reasoning.
    years = {int(x) for x in _YEAR_RE.findall(reasoning)}
    for year in years:
        for event_id in retrieved_ids:
            if _event_index_supports_year(
                event_index, event_id, year, anchors, relations
            ):
                found.add(event_id)

    return found


def filter_a1_answer(
    *,
    answer: str,
    reasoning: str,
    retrieved_event_ids: Iterable[int],
    allowed_event_ids: Iterable[int],
    question_entities: Iterable[str],
    relations: Iterable[str],
    answer_type: str,
    question_type: str,
    event_index,
) -> tuple[str, str, dict[str, Any]]:
    """Return filtered answer, unchanged reasoning, and a detailed audit row.

    Decision policy:
    - Empty/invalid answers disclose no canonical answer; retain as-is and audit.
    - Echoing a Q-ID already present in the question adds no new entity fact.
    - If matching support exists among A0's retrieved events and any such
      support is outside the role's permission, refuse conservatively.
    - If no retrieved event can be matched, retain but mark UNATTRIBUTED; this
      is not counted as proof of authorization or as a permission violation.
    - Reasoning is never rewritten; its leakage is measured separately.
    """
    answer = str(answer or "")
    reasoning = str(reasoning or "")
    retrieved = {int(x) for x in retrieved_event_ids}
    allowed = {int(x) for x in allowed_event_ids}
    anchors = {str(x).upper() for x in question_entities}
    rels = {str(x).upper() for x in relations}
    answer_type = str(answer_type or "").lower()
    question_type = str(question_type or "").lower()

    audit: dict[str, Any] = {
        "decision": "retain",
        "reason": "unattributed_answer",
        "answer_type": answer_type,
        "question_type": question_type,
        "retrieved_event_count": len(retrieved),
        "answer_value": answer,
        "supporting_event_ids": [],
        "authorized_supporting_event_ids": [],
        "unauthorized_supporting_event_ids": [],
        "reasoning_supporting_event_ids": [],
        "reasoning_unauthorized_event_ids": [],
        "reasoning_leakage": False,
    }

    if not answer.strip():
        audit["reason"] = "empty_answer"
    elif event_index is None:
        audit["reason"] = "unverifiable_without_kg_index"
    elif answer_type == "entity":
        qids = list(dict.fromkeys(x.upper() for x in _QID_RE.findall(answer)))
        if len(qids) != 1:
            audit["reason"] = "noncanonical_entity_answer"
        else:
            candidate = qids[0]
            audit["answer_value"] = candidate
            if candidate in anchors:
                audit["reason"] = "query_entity_echo_no_new_entity_disclosure"
            else:
                supporting = {
                    eid for eid in retrieved
                    if _event_index_supports_entity(
                        event_index, eid, candidate, anchors, rels
                    )
                }
                audit["supporting_event_ids"] = sorted(supporting)
                authorized = supporting & allowed
                unauthorized = supporting - allowed
                audit["authorized_supporting_event_ids"] = sorted(authorized)
                audit["unauthorized_supporting_event_ids"] = sorted(unauthorized)
                if not supporting:
                    audit["reason"] = "unattributed_no_matching_retrieved_event"
                elif unauthorized:
                    # Conservative post-generation policy: any retrieved
                    # supporting fact outside permission makes the final
                    # answer unsafe to release.
                    audit["decision"] = "refuse"
                    audit["reason"] = "answer_supported_by_out_of_scope_event"
                else:
                    audit["reason"] = "answer_supported_by_permitted_retrieved_event"

    elif answer_type == "time":
        years = list(dict.fromkeys(int(x) for x in _YEAR_RE.findall(answer)))
        if len(years) != 1:
            audit["reason"] = "noncanonical_time_answer"
        else:
            year = years[0]
            audit["answer_value"] = year
            supporting = {
                eid for eid in retrieved
                if _event_index_supports_year(
                    event_index, eid, year, anchors, rels
                )
            }
            audit["supporting_event_ids"] = sorted(supporting)
            authorized = supporting & allowed
            unauthorized = supporting - allowed
            audit["authorized_supporting_event_ids"] = sorted(authorized)
            audit["unauthorized_supporting_event_ids"] = sorted(unauthorized)
            if not supporting:
                audit["reason"] = "unattributed_no_matching_retrieved_event"
            elif unauthorized:
                audit["decision"] = "refuse"
                audit["reason"] = "answer_supported_by_out_of_scope_event"
            else:
                audit["reason"] = "answer_supported_by_permitted_retrieved_event"
    else:
        audit["reason"] = "unknown_answer_type_unattributed"

    # Preserve reasoning and measure only events that were actually in the
    # A0 prompt. An explicit fabricated event ID not in retrieved is not
    # treated as a retrieved-data leak; it remains a separate hallucination.
    reasoning_support = _reasoning_supporting_events(
        reasoning, retrieved, anchors, rels, event_index
    )
    reasoning_unauthorized = reasoning_support - allowed
    audit["reasoning_supporting_event_ids"] = sorted(reasoning_support)
    audit["reasoning_unauthorized_event_ids"] = sorted(reasoning_unauthorized)
    audit["reasoning_leakage"] = bool(reasoning_unauthorized)

    filtered_answer = REFUSAL if audit["decision"] == "refuse" else answer
    return filtered_answer, reasoning, audit
