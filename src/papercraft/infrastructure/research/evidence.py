"""Helpers for maintaining the auditable Claim -> Evidence graph."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from papercraft.domain import Claim, ClaimStatus, Evidence, Locator, SourceFragment, new_id

_FACTUAL_CUE = re.compile(
    r"(?:\d|%|\b(?:is|are|was|were|has|have|increased|decreased|reported|"
    r"shows?|demonstrates?|составля(?:ет|ли)|увелич(?:ился|илась|илось|ились|ение)|"
    r"сниз(?:ился|илась|илось|ились|жение)|показал(?:а|о|и)?|выявил(?:а|о|и)?|"
    r"установил(?:а|о|и)?|согласно)\b)",
    flags=re.IGNORECASE,
)


def final_text_claims(
    project_id: str,
    text: str,
    *,
    section_id: str | None = None,
    block_id: str | None = None,
) -> list[Claim]:
    """Extract conservative factual claims from final prose lacking bindings.

    This is a release guard, not an evidence inference mechanism. A newly
    discovered assertion must re-enter research and receive verified evidence.
    """

    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    claims: list[Claim] = []
    seen: set[str] = set()
    for sentence in sentences:
        normalized = " ".join(sentence.split())
        key = normalized.casefold().rstrip(".!?")
        if len(normalized) < 12 or key in seen or not _FACTUAL_CUE.search(normalized):
            continue
        seen.add(key)
        claims.append(
            Claim(
                project_id=project_id,
                text=normalized,
                section_id=section_id,
                metadata={
                    "origin": "final_text",
                    "block_id": block_id or "",
                    "requires_verified_evidence": True,
                },
            )
        )
    return claims


@dataclass(frozen=True, slots=True)
class EvidenceCoverage:
    total_checkable: int
    supported: int
    disputed: int
    unsupported: int
    pending: int

    @property
    def ratio(self) -> float:
        return 1.0 if self.total_checkable == 0 else self.supported / self.total_checkable


def link_claim_evidence(claim: Claim, evidence: Evidence) -> Claim:
    if evidence.claim_id != claim.id:
        raise ValueError("evidence.claim_id must match claim.id")
    evidence_ids = list(dict.fromkeys([*claim.evidence_ids, evidence.id]))
    return claim.model_copy(update={"evidence_ids": evidence_ids})


def evidence_from_fragment(
    claim: Claim,
    fragment: SourceFragment,
    *,
    excerpt: str | None = None,
    supports: bool = True,
    confidence: float = 1.0,
    verified: bool = False,
) -> Evidence:
    return Evidence(
        claim_id=claim.id,
        source_id=fragment.source_id,
        locator=fragment.locator.model_copy(deep=True),
        excerpt=(excerpt if excerpt is not None else fragment.content)[:4_000],
        supports=supports,
        confidence=confidence,
        verified=verified,
        metadata={"fragment_id": fragment.id},
    )


class EvidenceGraph:
    """In-memory invariant keeper used before repository persistence."""

    def __init__(
        self,
        project_id: str,
        *,
        claims: Iterable[Claim] = (),
        evidence: Iterable[Evidence] = (),
    ) -> None:
        self.project_id = project_id
        self.claims = {claim.id: claim.model_copy(deep=True) for claim in claims}
        self.evidence = {item.id: item.model_copy(deep=True) for item in evidence}
        for claim in self.claims.values():
            if claim.project_id != project_id:
                raise ValueError("claim belongs to another project")
        for item in self.evidence.values():
            if item.claim_id not in self.claims:
                raise ValueError(f"evidence refers to unknown claim: {item.claim_id}")
            self.claims[item.claim_id] = link_claim_evidence(self.claims[item.claim_id], item)
        self.recompute_all()

    def create_claim(
        self,
        text: str,
        *,
        section_id: str | None = None,
        checkable: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> Claim:
        claim = Claim(
            id=new_id(),
            project_id=self.project_id,
            text=text,
            section_id=section_id,
            checkable=checkable,
            metadata=metadata or {},
        )
        self.claims[claim.id] = claim
        return claim.model_copy(deep=True)

    def add_evidence(
        self,
        claim_id: str,
        source_id: str,
        locator: Locator,
        *,
        excerpt: str = "",
        supports: bool = True,
        confidence: float = 1.0,
        verified: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> Evidence:
        if claim_id not in self.claims:
            raise KeyError(f"Unknown claim: {claim_id}")
        item = Evidence(
            id=new_id(),
            claim_id=claim_id,
            source_id=source_id,
            locator=locator.model_copy(deep=True),
            excerpt=excerpt[:4_000],
            supports=supports,
            confidence=confidence,
            verified=verified,
            metadata=metadata or {},
        )
        self.evidence[item.id] = item
        self.claims[claim_id] = link_claim_evidence(self.claims[claim_id], item)
        self._recompute(claim_id)
        return item.model_copy(deep=True)

    def add_fragment_evidence(
        self,
        claim_id: str,
        source_fragment: SourceFragment,
        **kwargs: Any,
    ) -> Evidence:
        return self.add_evidence(
            claim_id,
            source_fragment.source_id,
            source_fragment.locator,
            excerpt=kwargs.pop("excerpt", source_fragment.content),
            metadata={"fragment_id": source_fragment.id, **kwargs.pop("metadata", {})},
            **kwargs,
        )

    def set_verified(self, evidence_id: str, verified: bool = True) -> Evidence:
        item = self.evidence[evidence_id].model_copy(update={"verified": verified})
        self.evidence[evidence_id] = item
        self._recompute(item.claim_id)
        return item.model_copy(deep=True)

    def evidence_for(self, claim_id: str, *, verified_only: bool = False) -> list[Evidence]:
        items = [self.evidence[item_id] for item_id in self.claims[claim_id].evidence_ids]
        if verified_only:
            items = [item for item in items if item.verified]
        return [item.model_copy(deep=True) for item in items]

    def mark_unsupported(self, claim_id: str) -> Claim:
        claim = self.claims[claim_id].model_copy(update={"status": ClaimStatus.UNSUPPORTED})
        self.claims[claim_id] = claim
        return claim.model_copy(deep=True)

    def _recompute(self, claim_id: str) -> None:
        claim = self.claims[claim_id]
        verified = [self.evidence[item_id] for item_id in claim.evidence_ids if self.evidence[item_id].verified]
        if any(not item.supports for item in verified):
            status = ClaimStatus.DISPUTED
        elif any(item.supports for item in verified):
            status = ClaimStatus.SUPPORTED
        elif claim.status != ClaimStatus.UNSUPPORTED:
            status = ClaimStatus.PENDING
        else:
            status = claim.status
        self.claims[claim_id] = claim.model_copy(update={"status": status})

    def recompute_all(self) -> None:
        for claim_id in self.claims:
            self._recompute(claim_id)

    def coverage(self) -> EvidenceCoverage:
        checkable = [claim for claim in self.claims.values() if claim.checkable]
        counts = {status: 0 for status in ClaimStatus}
        for claim in checkable:
            counts[claim.status] += 1
        return EvidenceCoverage(
            total_checkable=len(checkable),
            supported=counts[ClaimStatus.SUPPORTED],
            disputed=counts[ClaimStatus.DISPUTED],
            unsupported=counts[ClaimStatus.UNSUPPORTED],
            pending=counts[ClaimStatus.PENDING],
        )

    def unresolved_claims(self) -> list[Claim]:
        return [
            claim.model_copy(deep=True)
            for claim in self.claims.values()
            if claim.checkable and claim.status != ClaimStatus.SUPPORTED
        ]


__all__ = [
    "EvidenceCoverage",
    "EvidenceGraph",
    "evidence_from_fragment",
    "link_claim_evidence",
]
