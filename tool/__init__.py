"""PersTox-Agent Agent tools (doc/工具实现.pdf).

Two implemented groups (evidence/personalization group deferred):
  mechanism_admet/ - admetsar_predict, dti_query, pathway_enrich,
                     mechanism_query, drugbank_metabolism_query, ddi_query
  ade_profile/     - persade_drug_profile, persade_contextual_retrieval

Every tool exposes `run(payload: dict) -> dict` plus a CLI. Shared data-access
helpers live in tool/common.py. Raw sources stay read-only; derived caches go to
data/cache/.
"""
