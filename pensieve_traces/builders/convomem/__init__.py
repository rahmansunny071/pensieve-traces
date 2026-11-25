"""ConvoMem dataset builder: step 1 finds candidate memory facts in conversations, step 2 turns
them into evidence items (question, answer, evidence) for the six ConvoMem categories, step 3
(``dataset`` and ``topics``, driven by ``pensieve-traces build convomem``) writes the dataset
layout convomem-arena reads, and ``check`` (``check_dataset``, behind
``pensieve-traces check-convomem``) checks that layout's structure."""

from pensieve_traces.builders.convomem.check import Problem, check_dataset
from pensieve_traces.builders.convomem.extract import (
    CACHE_FORMAT_VERSION,
    Candidate,
    ExtractStats,
    extract_candidates,
    prompt_hashes,
)
from pensieve_traces.builders.convomem.items import (
    CATEGORIES,
    EvidenceItem,
    EvidenceMessage,
    Stats,
    build_items,
    item_prompt_hashes,
)

__all__ = [
    "CACHE_FORMAT_VERSION",
    "CATEGORIES",
    "Candidate",
    "EvidenceItem",
    "EvidenceMessage",
    "ExtractStats",
    "Problem",
    "Stats",
    "build_items",
    "check_dataset",
    "extract_candidates",
    "item_prompt_hashes",
    "prompt_hashes",
]
