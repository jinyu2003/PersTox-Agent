"""Pydantic schemas used across the PersAgent pipeline."""

from __future__ import annotations

import copy
import builtins
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional


try:  # pragma: no cover - exercised only when pydantic is installed.
    from pydantic import BaseModel as _PydanticBaseModel
    from pydantic import Field

    try:
        from pydantic import ConfigDict
    except Exception:  # pragma: no cover - pydantic v1 compatibility.
        ConfigDict = None  # type: ignore[assignment]

    _HAS_PYDANTIC = True
except ImportError:  # Lightweight fallback for local dry-runs without deps.
    _HAS_PYDANTIC = False
    ConfigDict = None  # type: ignore[assignment]

    _MISSING = object()

    class _FallbackField:
        def __init__(self, default: Any = _MISSING, default_factory: Any = None):
            self.default = default
            self.default_factory = default_factory

    def Field(  # type: ignore[override]
        default: Any = _MISSING,
        *,
        default_factory: Any = None,
        description: str | None = None,
        ge: float | None = None,
        le: float | None = None,
        **_: Any,
    ) -> Any:
        return _FallbackField(default=default, default_factory=default_factory)

    class _PydanticBaseModel:  # type: ignore[no-redef]
        """Tiny BaseModel substitute so the demo can run before installation."""

        def __init__(self, **data: Any):
            annotations: Dict[str, Any] = {}
            for cls in reversed(type(self).mro()):
                annotations.update(getattr(cls, "__annotations__", {}))

            for name in annotations:
                if name.startswith("_") or name == "model_config":
                    continue
                if name in data:
                    value = data.pop(name)
                else:
                    default = getattr(type(self), name, _MISSING)
                    if isinstance(default, _FallbackField):
                        if default.default_factory is not None:
                            value = default.default_factory()
                        elif default.default is not _MISSING and default.default is not ...:
                            value = copy.deepcopy(default.default)
                        else:
                            raise ValueError(f"Missing required field: {name}")
                    elif default is not _MISSING:
                        value = copy.deepcopy(default)
                    else:
                        raise ValueError(f"Missing required field: {name}")
                setattr(self, name, value)

            for name, value in data.items():
                setattr(self, name, value)

        def model_dump(self, mode: str = "python", **_: Any) -> Dict[str, Any]:
            return {
                key: _fallback_dump(value, mode=mode)
                for key, value in self.__dict__.items()
                if not key.startswith("_")
            }

        def dict(self, **_: Any) -> Dict[str, Any]:
            return self.model_dump()


def _fallback_dump(value: Any, mode: str = "python") -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode=mode)
    if isinstance(value, dict):
        return {key: _fallback_dump(item, mode=mode) for key, item in value.items()}
    if isinstance(value, list):
        return [_fallback_dump(item, mode=mode) for item in value]
    if isinstance(value, datetime) and mode == "json":
        return value.isoformat()
    return value

#8个器官系统
ORGAN_SYSTEMS: List[str] = [
    "liver",
    "heart",
    "kidney",
    "hematologic",
    "immune",
    "skin",
    "neurologic",
    "gastrointestinal",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrictBaseModel(_PydanticBaseModel):
    if _HAS_PYDANTIC and ConfigDict is not None:
        model_config = ConfigDict(extra="forbid", validate_assignment=True)
    elif _HAS_PYDANTIC:
        class Config:
            extra = "forbid"
            validate_assignment = True


class PatientInfo(StrictBaseModel):
    patient_id: str = Field(default="anonymous")
    age: int = Field(..., ge=0)
    sex: Literal["female", "male", "other", "unknown"] = "unknown"
    weight_kg: Optional[float] = Field(default=None, ge=0)
    alt_u_l: Optional[float] = Field(default=None, ge=0)
    ast_u_l: Optional[float] = Field(default=None, ge=0)
    bilirubin_mg_dl: Optional[float] = Field(default=None, ge=0)
    child_pugh: Optional[Literal["A", "B", "C"]] = None
    creatinine_mg_dl: Optional[float] = Field(default=None, ge=0)
    egfr_ml_min: Optional[float] = Field(default=None, ge=0)
    genotypes: Dict[str, str] = Field(default_factory=dict)
    hla_types: List[str] = Field(default_factory=list)
    medical_history: List[str] = Field(default_factory=list)
    concomitant_medications: List[str] = Field(default_factory=list)
    organ_function: Dict[str, Any] = Field(default_factory=dict)
    exposure: Dict[str, Any] = Field(default_factory=dict)
    pregnancy_status: Optional[Literal["pregnant", "not_pregnant", "unknown"]] = None
    missing_modalities: List[str] = Field(default_factory=list)


class ResolvedConcept(StrictBaseModel):
    """A diagnosis/indication resolved to a standard vocabulary id."""

    input: str
    name: Optional[str] = None
    umls_cui: Optional[str] = None
    mesh_id: Optional[str] = None
    icd10: Optional[str] = None
    matched: bool = False


class ResolvedDrug(StrictBaseModel):
    """A comedication name resolved to canonical drug identifiers."""

    input: str
    name: Optional[str] = None
    drugbank_id: Optional[str] = None
    inchi_key: Optional[str] = None
    matched: bool = False


class PgxPhenotype(StrictBaseModel):
    """Pharmacogenomic genotype mapped to a metabolizer/risk phenotype.

    Phenotype mapping is rule-based with limited gene coverage (no standard
    allele-function table is shipped); ``actionable`` is cross-checked against
    the normalized CPIC gene list.
    """

    gene: str
    diplotype: str
    phenotype: str
    actionable: bool = False
    source: str = "rule-based"


class OrganFunctionClass(StrictBaseModel):
    """A single organ-function stratification with its deciding basis."""

    klass: str
    basis: str


class PatientFeatures(StrictBaseModel):
    """Stage 2 Step 1 output: standardized, retrievable patient profile.

    Single source of truth for all downstream personalized-toxicity rules.
    """

    patient_id: str = Field(default="anonymous")
    age: int = Field(..., ge=0)
    age_group: str = "unknown"
    elderly: bool = False
    sex: Literal["Female", "Male", "Other", "Unknown"] = "Unknown"
    indication_umls: List[ResolvedConcept] = Field(default_factory=list)
    comedication_ids: List[ResolvedDrug] = Field(default_factory=list)
    pgx_phenotypes: List[PgxPhenotype] = Field(default_factory=list)
    organ_function_classes: Dict[str, OrganFunctionClass] = Field(default_factory=dict)
    exposure_context: Dict[str, Optional[str]] = Field(default_factory=dict)
    unresolved: List[str] = Field(default_factory=list)


class DrugInfo(StrictBaseModel):
    name: str = "unknown"
    drugbank_id: Optional[str] = None
    inchi_key: Optional[str] = None
    smiles: Optional[str] = None
    target_description: Optional[str] = None
    dose: str = "unspecified"
    route: str = "unspecified"
    frequency: Optional[str] = None
    form: Optional[str] = None
    known_toxicities: List[str] = Field(default_factory=list)


class EvidenceCitation(StrictBaseModel):
    source: str
    version: str = "simulated"
    year: int = 2026
    pmid: Optional[str] = None
    url: Optional[str] = None
    evidence_level: Literal["P1", "P2", "P3", "P4", "P5", "DrugCard", "ADMET"]
    summary: str


class EvidenceItem(StrictBaseModel):
    tool_name: str
    evidence_level: Literal["P1", "P2", "P3", "P4", "P5", "DrugCard", "ADMET"]
    finding: str
    strength: Literal["low", "moderate", "high"]
    payload: Dict[str, Any] = Field(default_factory=dict)
    citations: List[EvidenceCitation] = Field(default_factory=list)


class EvidencePackage(StrictBaseModel):
    query_id: str
    query_purpose: str
    drug_id: str
    patient_id: str
    generated_at: datetime = Field(default_factory=utc_now)
    tool_results: Dict[str, Any] = Field(default_factory=dict)
    evidence_items: List[EvidenceItem] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    attribution_chain: List[str] = Field(default_factory=list)


class DrugOutput(StrictBaseModel):
    name: str
    smiles: Optional[str] = None
    drugbank_id: Optional[str] = None


class StructuralAlert(StrictBaseModel):
    alert: str
    smarts: str
    atoms: List[int] = Field(default_factory=list)
    contribution: float = Field(..., ge=0, le=1)


class PropertyAttribution(StrictBaseModel):
    feature: str
    value: float
    contribution: float = Field(..., ge=0, le=1)


MechanismNodeType = Literal[
    "drug_original",
    "metabolism",
    "active_or_toxic_species",
    "target_binding",
    "pathway_perturbation",
    "organ_toxicity_phenotype",
]


class MechanismEvidence(StrictBaseModel):
    source: str
    tier: int = Field(..., ge=1)
    ref: str


class MechanismChainNode(StrictBaseModel):
    order: int = Field(..., ge=1)
    node_type: MechanismNodeType
    label: str
    role: Optional[str] = None
    description: str = ""
    confidence: float = Field(..., ge=0, le=1)
    phase: List[Literal["I", "II", "other"]] = Field(default_factory=list)
    enzymes: List[str] = Field(default_factory=list)
    species_type: Optional[
        Literal[
            "parent_active_exposure",
            "active_metabolite",
            "toxic_metabolite",
            "reactive_metabolite",
            "inactive_metabolite_with_exposure_relevance",
            "unknown_active_or_toxic_species",
        ]
    ] = None
    binding_role: Optional[
        Literal["on_target", "off_target", "toxic_covalent_binding", "unknown"]
    ] = None
    target_type: Optional[str] = None
    organ_system: Optional[str] = None
    soc: Optional[str] = None
    evidence: List[MechanismEvidence] = Field(default_factory=list)


class MechanismChain(StrictBaseModel):
    chain_id: str
    organ_system: str
    soc: str
    summary: str
    chain_complete: bool = True
    missing_nodes: List[MechanismNodeType] = Field(default_factory=list)
    chain_score: float = Field(..., ge=0, le=1)
    chain_confidence: float = Field(..., ge=0, le=1)
    nodes: List[MechanismChainNode]
    evidence: List[MechanismEvidence] = Field(default_factory=list)


class MechanismChainModifier(StrictBaseModel):
    chain_id: str
    affected_node: MechanismNodeType
    factor: str
    direction: Literal["increase", "decrease"]
    magnitude: float = Field(..., ge=0)
    effect: str
    rule_id: str
    evidence: MechanismEvidence


class ToxicityAttribution(StrictBaseModel):
    structural: List[StructuralAlert] = Field(default_factory=list)
    property: List[PropertyAttribution] = Field(default_factory=list)
    admet_endpoint: List[Dict[str, Any]] = Field(default_factory=list)
    target_pathway: List[Dict[str, Any]] = Field(default_factory=list)
    mechanism_summary: Optional[str] = None
    attribution_explanation: Optional[str] = None
    attribution_narrative: Optional[str] = None
    attribution_generation_method: Literal["live_llm", "deterministic_fallback"] = "deterministic_fallback"
    molecular_attribution: List[Dict[str, Any]] = Field(default_factory=list)
    attribution_limitations: List[str] = Field(default_factory=list)
    mechanism_chains: List[MechanismChain] = Field(default_factory=list, exclude=True)

    @builtins.property
    def mechanism(self) -> str:
        return self.mechanism_summary or ""



class GeneralToxicityEvidence(StrictBaseModel):
    source: str
    tier: int = Field(..., ge=1)
    ref: str


class GeneralToxicityItem(StrictBaseModel):
    soc: str
    baseline_risk_level: Optional[Literal["high", "moderate", "low", "unknown"]] = None
    baseline_probability: Optional[float] = Field(default=None, ge=0, le=1)
    uncertainty: Optional[float] = Field(default=None, ge=0, le=1)
    ctcae_grade_predicted: Optional[int] = Field(default=None, ge=1, le=5)
    attribution: ToxicityAttribution
    evidence: List[GeneralToxicityEvidence] = Field(default_factory=list)

    @property
    def risk_level(self) -> Optional[str]:
        return self.baseline_risk_level

    @property
    def probability(self) -> Optional[float]:
        return self.baseline_probability


class GeneralToxicityOutput(StrictBaseModel):
    drug: DrugOutput
    general_toxicity: List[GeneralToxicityItem]


class UniversalToxicityReport(GeneralToxicityOutput):
    """Stage 1 output schema requested by the project."""


class BaselineRisk(StrictBaseModel):
    risk_level: Optional[Literal["high", "moderate", "low", "unknown"]] = None
    probability: Optional[float] = Field(default=None, ge=0, le=1)


class PatientFactorEvidence(StrictBaseModel):
    source: str
    tier: int = Field(..., ge=1)
    grade: Optional[str] = None


class PatientAttribution(StrictBaseModel):
    factor_type: Literal["PGx", "comorbidity", "comedication", "organ_function"]
    factor: str
    direction: Literal["up", "down"]
    magnitude: float = Field(..., ge=0)
    rule_id: str
    evidence: PatientFactorEvidence
    affected_node: Optional[MechanismNodeType] = None
    effect: Optional[str] = None


class ClinicalRecommendationOutput(StrictBaseModel):
    action: Literal["monitor", "dose_adjust", "avoid"]
    text: str
    ctcae_aligned: bool = True


class PersonalizedToxicityItem(StrictBaseModel):
    soc: str
    baseline: BaselineRisk
    personalized_risk_level: Optional[Literal["high", "moderate", "low"]] = None
    personalized_probability: Optional[float] = Field(default=None, ge=0, le=1)
    risk_shift: Optional[float] = None
    ctcae_grade_predicted: Optional[int] = Field(default=None, ge=1, le=5)
    patient_attribution: List[PatientAttribution] = Field(default_factory=list)
    mechanism_chain_modifiers: List[MechanismChainModifier] = Field(default_factory=list)
    clinical_recommendation: Optional[ClinicalRecommendationOutput] = None


class PersonalizedToxicityReport(StrictBaseModel):
    """Stage 2 output schema requested by the project."""

    drug: DrugOutput
    patient_id: str
    personalized_toxicity: List[PersonalizedToxicityItem]


class VerificationIssue(StrictBaseModel):
    layer: int = Field(..., ge=1, le=4)
    severity: Literal["INFO", "WARN", "ERROR", "BLOCKER"]
    code: str
    message: str
    recommendation: str
    related_field: Optional[str] = None


class VerificationReport(StrictBaseModel):
    status: Literal["PASS", "FLAGGED", "BLOCKED"]
    issues: List[VerificationIssue] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=utc_now)
    summary: str

