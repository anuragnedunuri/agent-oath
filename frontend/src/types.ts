/**
 * TypeScript mirror of the API contract.
 *
 * Hand-written on purpose, mirroring backend/app/models.py field for field.
 * The two files are edited together: change one, change the other. The backend
 * test `test_serialized_shape_matches_the_spec_contract` pins the top-level key
 * sets on the Python side, so a rename there fails CI rather than silently
 * drifting from this file.
 *
 * Contract source of truth: SPEC.md section 3.
 */

export type CheckName = 'synthetic' | 'provenance' | 'registry';

export type RetrievalStatus =
  | 'ok'
  | 'paywall_or_blocked'
  | 'timeout'
  | 'js_required'
  | 'not_html'
  | 'invalid_url'
  | 'unreachable'
  | 'http_error';

export type SyntheticStatus = 'ok' | 'skipped_no_content' | 'text_too_short' | 'not_integrated';

export type TrustTier = 'high' | 'medium' | 'low';

export type SourceType =
  | 'news_wire'
  | 'newspaper'
  | 'broadcaster'
  | 'magazine'
  | 'digital_native'
  | 'institutional'
  | 'other';

/** Request body for POST /api/analyze. Exactly one field is set. */
export interface AnalyzeRequest {
  url: string | null;
  text: string | null;
}

/** One raw, traceable signal behind a displayed result (INV-4). */
export interface Signal {
  name: string;
  raw_value: string;
  interpretation: string;
}

export interface RetrievalResult {
  status: RetrievalStatus;
  /** Always non-empty, including on success (INV-2). */
  detail: string;
  checks_completed: CheckName[];
}

export interface SyntheticSection {
  section_index: number;
  excerpt_start: string;
  probability_ai: number;
}

export interface SyntheticResult {
  status: SyntheticStatus;
  /** Null whenever status is not 'ok'. Never rendered without the disclaimer. */
  probability_ai: number | null;
  model_name: string;
  model_version: string;
  /** The sections actually analyzed. Empty whenever status is not 'ok'. */
  sections: SyntheticSection[];
  total_sections: number;
  disclaimer: string;
  /** Why the check was skipped or limited. Set whenever status is not 'ok'. */
  detail: string;
}

export interface DomainAge {
  status: 'ok' | 'unavailable';
  value_years: number | null;
  source: string;
  detail: string;
}

export interface PublicationDate {
  status: 'ok' | 'not_found';
  value: string | null;
  method: string;
}

/** URL-dependent fields are null for pasted text; `signals` explains why. */
export interface ProvenanceResult {
  domain: string | null;
  domain_age: DomainAge | null;
  https: boolean | null;
  publisher_metadata_found: boolean | null;
  publication_date: PublicationDate | null;
  /** Never empty (INV-4). */
  signals: Signal[];
}

/** One source registry entry (SPEC.md section 5). */
export interface RegistryEntry {
  domain: string;
  publisher_name: string;
  trust_tier: TrustTier;
  source_type: SourceType;
  editorial_standards_public: boolean;
  editorial_standards_url: string | null;
  corrections_policy_public: boolean;
  corrections_policy_url: string | null;
  ownership_transparent: boolean;
  ownership_url: string | null;
  country: string;
  last_verified: string;
  notes: string;
}

export interface CredibilityResult {
  status: 'rated' | 'unrated';
  /** Null whenever status is 'unrated'. Never interpolated (INV-6). */
  trust_tier: TrustTier | null;
  matched_entry: RegistryEntry | null;
  unrated_explanation: string;
  registry_version: string;
}

/** The complete response for POST /api/analyze. Every input yields one (INV-2). */
export interface TrustSummary {
  input_type: 'url' | 'text';
  retrieval: RetrievalResult;
  synthetic: SyntheticResult;
  provenance: ProvenanceResult;
  credibility: CredibilityResult;
}
