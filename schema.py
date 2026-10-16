"""
schema.py — Consolidated Pydantic v2 schema for the CARO pipeline's
output JSON (caro/cin_pipeline.py's process_cin / views.py's clauses_view
combined_view, and api.py's envelope on top).

Follows the same conventions as schema.py (the CIN/MCA dataset schema):
every model uses `ConfigDict(populate_by_name=True, extra="forbid")` so both
the JSON key and the Python field name can populate a model, and any
unexpected key — including a field that was DELIBERATELY REMOVED from this
output (evidence_text, main_clause_ref, sub_clause_ref, free_text_details,
filing_type, no_caro_text_found, document_red_flag, the old financial_years
key) — fails validation instead of silently round-tripping. That makes this
schema double as a regression check for the 2026-09-26 field-removal/rename
pass: if any of those seven ever reappear in a future run, validation fails
loudly instead of drifting back in unnoticed.

This is a DIFFERENT dataset from schema.py (directors/charges/capital/etc.)
— only the modelling conventions are shared, not any of the field content.

Enum values below (classification labels, color codes, severity_hint,
negation_risk, source_mode) are taken directly from caro/registry.py,
caro/pipeline.py and caro/quality.py, not guessed.

Every field below carries a `description=` explaining what it means, where
it comes from in the pipeline, and any quirk worth knowing about it.
"""

from __future__ import annotations


from typing import Any, Literal, Optional
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict, model_validator

# ------------------------------------------------------------------------------
# CLASSIFICATION LABELS (change 10, 2026-09-28)
# The pipeline can emit either label set, selected by CARO_LABEL_SET:
#   v1 (schema_version "4.0") - the original seven labels
#   v2 (schema_version "4.1") - the revised seven labels (registry.
#                               CLASSIFICATION_LABELS_V2); "Human Review Required"
#                               is not a label in v2 (review is signalled by
#                               score_ready_fields.review_required_flag)
# One document must use ONE set only; Caro._one_label_set enforces that and
# that the declared schema_version matches the set actually used.
# ------------------------------------------------------------------------------
LABELS_V1 = ("Favourable Remark", "Unfavourable Remark", "Adverse Remark",
             "Clause Not Applicable", "Unable to Comment", "Neutral",
             "Human Review Required")
LABELS_V2 = ("Favourable Remark", "Qualified Remark", "Adverse Remark",
             "Not Applicable", "Auditor Unable to Verify",
             "Not Reported by Auditor", "Not Available in Source")
ClassificationLabel = Literal[
    # v1
    "Favourable Remark", "Unfavourable Remark", "Adverse Remark",
    "Clause Not Applicable", "Unable to Comment", "Neutral",
    "Human Review Required",
    # v2 (Favourable Remark and Adverse Remark are shared with v1)
    "Qualified Remark", "Not Applicable", "Auditor Unable to Verify",
    "Not Reported by Auditor", "Not Available in Source",
]
_V1_ONLY = frozenset(LABELS_V1) - frozenset(LABELS_V2)
_V2_ONLY = frozenset(LABELS_V2) - frozenset(LABELS_V1)
# ------------------------------------------------------------------------------
# CARO — one self-contained class. Replaces `class Caro(BaseModel): pass`
# in schema.py. Everything else (all sub-models) is nested INSIDE Caro, so
# nothing else needs to be added to the module.
#
# Imports needed at the top of schema.py (add Literal; the rest you already have):
#     from typing import Any, Literal, Optional
#     from datetime import datetime
#     from pydantic import BaseModel, Field, ConfigDict
#
# Must sit BEFORE `class Bir`, which has `CARO: Caro | None = None`.
# Validated by the existing DefaultResponse.model_validate(...) call — the data
# lives at m_output.bir.CARO in the JSON.
# ------------------------------------------------------------------------------

class _StrictModel(BaseModel):
    """Item 11 (2026-09-29): the convention the module docstring always
    promised -- JSON key or field name both populate a model, and any key the
    schema does not list FAILS validation instead of being silently dropped.
    Before this base existed the nested models had no model_config at all, so
    pydantic's default (extra="ignore") applied and removed fields could creep
    back unnoticed. caro/output_check.py runs this check on every response
    (CARO_STRICT_SCHEMA=enforce | warn | off)."""
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class Caro(_StrictModel):

    # ---------- shared shapes ----------

    class DisputedDue(_StrictModel):
        # Rows are written by the LLM and the pipeline never type-checks them
        # (llm.py declares disputed_dues_table as a bare array), so any scalar
        # is accepted for each value.
        nature_of_dues: str | int | float | None = None
        forum: str | int | float | None = None
        period: str | int | float | None = None
        amount: str | int | float | None = None

    class NormalizedFields(_StrictModel):
        # normalized_fields content is written by the LLM; pipeline.py only
        # fills MISSING keys (setdefault) and never coerces types, and llm.py's
        # structured-output schema leaves sub-clause normalized_fields as a bare
        # object. So these are deliberately tolerant.
        summary_label: str | None = None
        positive_flag: bool | str | int | float | None = None
        adverse_flag: bool | str | int | float | None = None
        disputed_dues_table: list["Caro.DisputedDue | Any"] | None = []
        amount_total: float | int | str | None = None
        counterparties: list[Any] | None = []
        sub_findings: list[Any] | None = []
        source_text: str | None = None

    class ScoreReadyFields(_StrictModel):
        # Model-written and only setdefault()-filled for clauses with no
        # sub-clauses, so a null / odd value can reach the output. color_code is
        # always computed by the pipeline, so it stays strict.
        red_flag: bool | str | None = None
        coverage_gap: bool | None = None
        review_required_flag: bool | None = None
        severity_hint: str | None = None      # normally "high" | "medium" | "low" | null
        color_code: Literal["red", "amber", "yellow", "green"] | None = None

    class ClauseQualityFields(_StrictModel):
        source_mode: Literal["native", "ocr"]
        page_confidence: float
        clause_confidence: float
        support_score: float
        negation_risk: Literal["low", "medium", "high"]
        contradiction_flag: bool
        review_required: bool

    # ---------- result[].CARO_clauses ----------

    class SubClause(_StrictModel):
        sub_clause_id: str
        sub_clause_title: str | None = None
        classification: ClassificationLabel      # v1 or v2 set, see LABELS_V1 / LABELS_V2
        explanation_text: str | int | float | None = None
        normalized_fields: "Caro.NormalizedFields"
        comment: Any = None

    class MainClause(_StrictModel):
        main_clause_id: str
        main_clause_title: str
        classification: ClassificationLabel      # v1 or v2 set, see LABELS_V1 / LABELS_V2
        explanation_text: str | int | float | None = None
        normalized_fields: "Caro.NormalizedFields"
        sub_clauses: list["Caro.SubClause"] = []
        comment: Any = None

    # ---------- result[].CARO_ews ----------

    class SubClauseEws(_StrictModel):
        sub_clause_id: str
        score_ready_fields: "Caro.ScoreReadyFields"

    class MainClauseEws(_StrictModel):
        main_clause_id: str
        score_ready_fields: "Caro.ScoreReadyFields"
        sub_clause_ews: list["Caro.SubClauseEws"] = []

    # ---------- result[] (one per financial-year filing) ----------

    class ResultEntry(_StrictModel):
        filing_id: str
        source_pdf: str | None = None
        Note: str | None = None
        Remarks: str | None = None
        financial_year: str | None = None
        CARO_clauses: list["Caro.MainClause"] = []
        CARO_ews: list["Caro.MainClauseEws"] = []
        worst_color_code: Literal["red", "amber", "yellow", "green"] | None = None
        red_flag_clause_ids: list[str] = []

    # ---------- metadata.filings[] ----------

    class SubClauseQuality(_StrictModel):
        sub_clause_id: str
        quality: "Caro.ClauseQualityFields"

    class MainClauseQuality(_StrictModel):
        main_clause_id: str
        quality: "Caro.ClauseQualityFields"
        sub_clause_quality: list["Caro.SubClauseQuality"] = []

    class FilingMeta(_StrictModel):
        schema_version: Literal["4.0", "4.1"] | None = None   # 4.1 = label set v2
        document_id: str | None = None
        company_id: str | None = None
        company_name: str | None = None
        source_format: str | None = None
        ocr_page_count: int | None = None
        caro_order_year: int | None = None
        numbering_basis: str | None = None
        page_range: str | list[int] | None = None
        statement_type: str | None = None
        caro_applicable: bool | None = None
        caro_section_detected: bool | None = None
        caro_applicability_source: Literal[
            "form_aoc4_XBRL/XML", "XBRL_pdf", "form_aoc4/Scanned_pdf"
        ] | None = None
        caro_exempt_reason: str | None = None
        report_qualified: bool | None = None
        qualification_details: str | None = None
        clause_source: str | None = None
        caro_text_source_file: str | None = None
        caro_text_source_kind: Literal["xml", "xml_mode_b", "aoc4_attachment"] | None = None
        clause_count: int | None = None
        CARO_quality: list["Caro.MainClauseQuality"] = []
        filing_id: str
        source_pdf: str | None = None
        source_path: str | None = None
        status: str
        error: str | None = None
        financial_year: str | None = None

    # ---------- metadata ----------

    class ExtractionLogEntry(_StrictModel):
        financial_year: str | None = None     # null when the year could not be read
        extracted_from: str | None = None

    class ReceivedInputs(_StrictModel):
        cin: str
        old_main_json: bool
        new_main_json: bool
        trigger_json: bool
        note: str | None = None

    class Metadata(_StrictModel):
        cin: str
        error: str | None = None
        filing_types_found: list[str] = []
        years_found: int
        years_succeeded: int
        years_available: int | None = None
        max_years: int | None = None
        duplicate_year_filings_skipped: int | None = None
        Caro_fy_extraction_logs: list["Caro.ExtractionLogEntry"] = []
        filings: list["Caro.FilingMeta"] = []
        section: str | None = None
        processing_seconds: float | None = None
        from_cache: bool | None = None
        received_inputs: "Caro.ReceivedInputs | None" = None
        # only under CARO_STRICT_SCHEMA=warn, when the check found problems (item 11)
        schema_check: dict | None = None

    # ---------- top-level fields of Caro itself ----------

    status: Literal["Success", "Failed"]
    result: list["Caro.ResultEntry"] = []
    metadata: "Caro.Metadata"
    last_updated_datetime: datetime | None = None

    @model_validator(mode="after")
    def _one_label_set(self):
        """Change 10: a document may not mix the v1 and v2 label sets, and its
        filings' schema_version must match the set used (4.0 = v1, 4.1 = v2)."""
        used = set()
        for e in self.result:
            for c in e.CARO_clauses:
                used.add(c.classification)
                used.update(s.classification for s in c.sub_clauses)
        v1, v2 = bool(used & _V1_ONLY), bool(used & _V2_ONLY)
        if v1 and v2:
            raise ValueError("CARO_clauses mix label sets v1 and v2: "
                             f"{sorted(used & _V1_ONLY)} / {sorted(used & _V2_ONLY)}")
        versions = {f.schema_version for f in self.metadata.filings if f.schema_version}
        if v2 and "4.0" in versions:
            raise ValueError("label set v2 used but a filing declares schema_version 4.0")
        if v1 and "4.1" in versions:
            raise ValueError("label set v1 used but a filing declares schema_version 4.1")
        return self