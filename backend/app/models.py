"""Pydantic request/response models for the Trust Summary API (SPEC.md section 3).

This module is the contract. It is locked before any check logic is written: the
check modules in app/checks/ must produce these exact shapes, and the TypeScript
types in frontend/src/types.ts mirror it field for field.

Several charter invariants are enforced here, in the models themselves, rather
than left to the discipline of whoever writes the check modules later:

  INV-2  a retrieval result always carries a human-readable detail
  INV-3  a probability never appears without model name, version, and the fixed
         disclaimer, and no field anywhere can express a binary verdict
  INV-4  provenance always ships the raw signals behind its numbers
  INV-6  an unrated source carries no tier and no matched entry

A malformed Trust Summary therefore cannot leave this process.
"""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

# The fixed uncertainty disclaimer (INV-3). Shown verbatim beside every score.
AI_DETECTION_DISCLAIMER = (
    "This is a probability estimate produced by a statistical model, not a verdict. "
    "AI-text detectors are unreliable on short passages, on heavily edited text, and "
    "on writing by people whose first language is not English. Read it as one signal "
    "among several, never as proof."
)

CheckName = Literal["synthetic", "provenance", "registry"]
RetrievalStatus = Literal[
    "ok",
    "paywall_or_blocked",
    "timeout",
    "js_required",
    "not_html",
    "invalid_url",
    "unreachable",
    "http_error",
]
SyntheticStatus = Literal["ok", "skipped_no_content", "text_too_short", "not_integrated"]
TrustTier = Literal["high", "medium", "low"]
SourceType = Literal[
    "news_wire",
    "newspaper",
    "broadcaster",
    "magazine",
    "digital_native",
    "institutional",
    "other",
]


class HealthResponse(BaseModel):
    """Response body for GET /health."""

    status: Literal["ok"] = "ok"
    service: str = Field(description="Service identifier.")
    version: str = Field(description="Backend version.")
    app_env: Literal["dev", "prod"] = Field(description="Environment the backend is running in.")


class AnalyzeRequest(BaseModel):
    """Request body for POST /api/analyze. Exactly one of url or text is set."""

    # Deliberately a plain str, not pydantic's HttpUrl. A malformed URL must come
    # back as a Trust Summary with retrieval status invalid_url (INV-2), not as a
    # 422 validation error the user never asked for.
    url: str | None = Field(default=None, description="URL to analyze.")
    text: str | None = Field(default=None, description="Pasted text to analyze.")

    @model_validator(mode="after")
    def _exactly_one_input(self) -> Self:
        """Reject requests that set both inputs or neither."""
        provided = [value for value in (self.url, self.text) if value is not None and value != ""]
        if len(provided) != 1:
            raise ValueError("Provide exactly one of url or text.")
        return self


class Signal(BaseModel):
    """One raw, traceable signal behind a displayed result (INV-4)."""

    name: str = Field(description="Short signal identifier, for example 'og:site_name'.")
    raw_value: str = Field(description="The raw observed value, unmodified.")
    interpretation: str = Field(description="Plain-language meaning of that raw value.")


class RetrievalResult(BaseModel):
    """What happened when the system tried to obtain the content (INV-2)."""

    status: RetrievalStatus
    detail: str = Field(
        min_length=1,
        description="Honest, human-readable explanation. Never blank, including on success.",
    )
    checks_completed: list[CheckName] = Field(
        default_factory=list,
        description="Which checks actually ran. A failed retrieval still runs some of them.",
    )


class SyntheticSection(BaseModel):
    """Per-section detection result (INV-5)."""

    section_index: int = Field(
        ge=0,
        description="Zero-based index of this section within the full document.",
    )
    excerpt_start: str = Field(
        description="Opening words of the section, so the user can locate it.",
    )
    probability_ai: float = Field(ge=0.0, le=1.0)


class SyntheticResult(BaseModel):
    """AI-text detection result, always framed as an estimate (INV-3, INV-5)."""

    status: SyntheticStatus
    probability_ai: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Mean of the analyzed sections. Null whenever status is not ok.",
    )
    model_name: str = Field(description="Detector model, always named alongside a score.")
    model_version: str = Field(description="Detector model revision or version.")
    sections: list[SyntheticSection] = Field(
        default_factory=list,
        description="The sections actually analyzed. Empty whenever status is not ok.",
    )
    total_sections: int = Field(
        default=0,
        ge=0,
        description="Sections the document was split into, so the UI can state coverage honestly.",
    )
    disclaimer: str = Field(
        default=AI_DETECTION_DISCLAIMER,
        description="Fixed uncertainty disclaimer. Always present, always identical.",
    )
    detail: str = Field(
        default="",
        description="Why the check was skipped or limited. Required when status is not ok.",
    )

    @model_validator(mode="after")
    def _score_and_disclaimer_travel_together(self) -> Self:
        """Enforce INV-3: no bare score, no score at all unless the check ran."""
        if self.disclaimer != AI_DETECTION_DISCLAIMER:
            raise ValueError("The uncertainty disclaimer is fixed and must not be altered.")

        if self.status == "ok":
            if self.probability_ai is None:
                raise ValueError("status 'ok' requires probability_ai.")
            if not self.sections:
                raise ValueError("status 'ok' requires the analyzed sections (INV-5).")
        else:
            if self.probability_ai is not None:
                raise ValueError(f"status '{self.status}' must not carry a probability.")
            if self.sections:
                raise ValueError(f"status '{self.status}' must not carry section scores.")
            if not self.detail:
                raise ValueError(f"status '{self.status}' requires an honest detail (INV-2).")

        if len(self.sections) > self.total_sections:
            raise ValueError("Analyzed sections cannot outnumber total_sections.")
        return self


class DomainAge(BaseModel):
    """Registration age of the domain, or an honest statement that it is unknown."""

    status: Literal["ok", "unavailable"]
    value_years: float | None = Field(default=None, ge=0.0)
    source: str = Field(description="Where the value came from, for example 'RDAP'.")
    detail: str = Field(default="", description="Why the value is unavailable, when it is.")

    @model_validator(mode="after")
    def _never_fake_an_age(self) -> Self:
        """A domain age is either measured or absent. It is never estimated."""
        if self.status == "ok" and self.value_years is None:
            raise ValueError("status 'ok' requires value_years.")
        if self.status == "unavailable":
            if self.value_years is not None:
                raise ValueError("status 'unavailable' must not carry a value.")
            if not self.detail:
                raise ValueError("status 'unavailable' requires a detail explaining why.")
        return self


class PublicationDate(BaseModel):
    """Extracted publication date, with the method that found it."""

    status: Literal["ok", "not_found"]
    value: str | None = Field(default=None, description="ISO 8601 date, for example 2026-05-01.")
    method: str = Field(description="Extraction method, for example 'htmldate'.")

    @model_validator(mode="after")
    def _value_matches_status(self) -> Self:
        """A date is either found or not. There is no partial state."""
        if self.status == "ok" and not self.value:
            raise ValueError("status 'ok' requires a value.")
        if self.status == "not_found" and self.value is not None:
            raise ValueError("status 'not_found' must not carry a value.")
        return self


class ProvenanceResult(BaseModel):
    """Domain-level and page-level provenance signals (INV-4).

    The URL-dependent fields are nullable because pasted text has no domain,
    no scheme, and no page metadata. In that case they are null and `signals`
    explains why. Which checks ran is reported by `retrieval.checks_completed`.
    """

    domain: str | None = Field(
        default=None,
        description="Registrable domain. Null for pasted text.",
    )
    domain_age: DomainAge | None = None
    https: bool | None = None
    publisher_metadata_found: bool | None = None
    publication_date: PublicationDate | None = None
    signals: list[Signal] = Field(
        min_length=1,
        description="Raw signals behind every field above. Never empty (INV-4).",
    )


class RegistryEntry(BaseModel):
    """One source registry entry (SPEC.md section 5). Structured fields only."""

    domain: str
    publisher_name: str
    trust_tier: TrustTier
    source_type: SourceType
    editorial_standards_public: bool
    corrections_policy_public: bool
    ownership_transparent: bool
    country: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2 code.")
    last_verified: str = Field(description="ISO 8601 date this entry was last checked.")
    notes: str = Field(default="", description="Optional and short. Not an essay field.")

    @staticmethod
    def derive_tier(
        editorial_standards_public: bool,
        corrections_policy_public: bool,
        ownership_transparent: bool,
    ) -> TrustTier:
        """Apply the tier rule from SPEC.md section 5: 3 true = high, 2 = medium, else low."""
        satisfied = sum(
            (editorial_standards_public, corrections_policy_public, ownership_transparent)
        )
        if satisfied == 3:
            return "high"
        if satisfied == 2:
            return "medium"
        return "low"

    @model_validator(mode="after")
    def _tier_matches_the_booleans(self) -> Self:
        """The tier is mechanical. A hand-edited tier that contradicts the booleans is a bug."""
        expected = self.derive_tier(
            self.editorial_standards_public,
            self.corrections_policy_public,
            self.ownership_transparent,
        )
        if self.trust_tier != expected:
            raise ValueError(
                f"trust_tier '{self.trust_tier}' contradicts the criteria booleans, "
                f"which derive '{expected}'."
            )
        return self


class CredibilityResult(BaseModel):
    """Registry lookup result. Unknown means unrated, never interpolated (INV-6)."""

    status: Literal["rated", "unrated"]
    trust_tier: TrustTier | None = None
    matched_entry: RegistryEntry | None = None
    unrated_explanation: str = Field(
        default="",
        description="Why this source has no rating. Required whenever status is unrated.",
    )
    registry_version: str = Field(description="Registry version the lookup ran against.")

    @model_validator(mode="after")
    def _unknown_means_unrated(self) -> Self:
        """Enforce INV-6: no tier without a matching entry to justify it."""
        if self.status == "rated":
            if self.matched_entry is None:
                raise ValueError("status 'rated' requires the matched registry entry (INV-4).")
            if self.trust_tier != self.matched_entry.trust_tier:
                raise ValueError("trust_tier must equal the matched entry's tier.")
            if self.unrated_explanation:
                raise ValueError("status 'rated' must not carry an unrated explanation.")
        else:
            if self.trust_tier is not None:
                raise ValueError("status 'unrated' must not carry a trust tier (INV-6).")
            if self.matched_entry is not None:
                raise ValueError("status 'unrated' must not carry a matched entry.")
            if not self.unrated_explanation:
                raise ValueError("status 'unrated' requires an honest explanation (INV-6).")
        return self


class TrustSummary(BaseModel):
    """The complete response for POST /api/analyze. Every input yields one (INV-2)."""

    input_type: Literal["url", "text"]
    retrieval: RetrievalResult
    synthetic: SyntheticResult
    provenance: ProvenanceResult
    credibility: CredibilityResult
