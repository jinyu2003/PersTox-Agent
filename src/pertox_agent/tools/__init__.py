"""PersAgent tool layer.

Implemented groups:
  clinical_input/       - raw clinical input parsing, LLM extraction, and fusion
  patient_context/      - patient feature standardization and local resolvers
  molecular_evidence/   - ADMET, DDI, DTI, metabolism, mechanism, and pathway
  real_world_evidence/  - PersADE / FAERS-derived ADE evidence
  toxicity_attribution/ - toxicity causal-chain synthesis
  shared/               - shared data access and DrugBank client
  runtime/              - agent-facing retrieval runtime adapter
"""

