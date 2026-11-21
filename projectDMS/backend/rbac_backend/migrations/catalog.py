"""Migration catalog.

Append new migrations here in lexical version order.  Existing migrations must
remain immutable after they have been run in any shared environment.
"""

from __future__ import annotations

from .runner import Migration
from .v20260629_0001_startup_index_baseline import DESCRIPTION as INDEX_DESCRIPTION
from .v20260629_0001_startup_index_baseline import NAME as INDEX_NAME
from .v20260629_0001_startup_index_baseline import VERSION as INDEX_VERSION
from .v20260629_0001_startup_index_baseline import upgrade as upgrade_index_baseline
from .v20260629_0002_seed_catalog_version import DESCRIPTION as SEED_DESCRIPTION
from .v20260629_0002_seed_catalog_version import NAME as SEED_NAME
from .v20260629_0002_seed_catalog_version import VERSION as SEED_VERSION
from .v20260629_0002_seed_catalog_version import upgrade as upgrade_seed_catalog
from .v20260630_0001_task_assignment_fields import DESCRIPTION as TASK_FIELDS_DESCRIPTION
from .v20260630_0001_task_assignment_fields import NAME as TASK_FIELDS_NAME
from .v20260630_0001_task_assignment_fields import VERSION as TASK_FIELDS_VERSION
from .v20260630_0001_task_assignment_fields import upgrade as upgrade_task_fields
from .v20260630_0002_insurance_indexes import DESCRIPTION as INSURANCE_IDX_DESCRIPTION
from .v20260630_0002_insurance_indexes import NAME as INSURANCE_IDX_NAME
from .v20260630_0002_insurance_indexes import VERSION as INSURANCE_IDX_VERSION
from .v20260630_0002_insurance_indexes import upgrade as upgrade_insurance_indexes
from .v20260705_0001_arbitration_hardening_indexes import DESCRIPTION as ARB_HARDENING_DESCRIPTION
from .v20260705_0001_arbitration_hardening_indexes import NAME as ARB_HARDENING_NAME
from .v20260705_0001_arbitration_hardening_indexes import VERSION as ARB_HARDENING_VERSION
from .v20260705_0001_arbitration_hardening_indexes import upgrade as upgrade_arbitration_hardening
from .v20260705_0002_arbitration_jurisdiction_matrix import DESCRIPTION as ARB_JURISDICTION_DESCRIPTION
from .v20260705_0002_arbitration_jurisdiction_matrix import NAME as ARB_JURISDICTION_NAME
from .v20260705_0002_arbitration_jurisdiction_matrix import VERSION as ARB_JURISDICTION_VERSION
from .v20260705_0002_arbitration_jurisdiction_matrix import upgrade as upgrade_arbitration_jurisdiction
from .v20260705_0003_arbitration_expert_alignment import DESCRIPTION as ARB_EXPERT_DESCRIPTION
from .v20260705_0003_arbitration_expert_alignment import NAME as ARB_EXPERT_NAME
from .v20260705_0003_arbitration_expert_alignment import VERSION as ARB_EXPERT_VERSION
from .v20260705_0003_arbitration_expert_alignment import upgrade as upgrade_arbitration_expert_alignment
from .v20260721_0001_arbitration_phase0_containment import DESCRIPTION as ARB_PHASE0_DESCRIPTION
from .v20260721_0001_arbitration_phase0_containment import NAME as ARB_PHASE0_NAME
from .v20260721_0001_arbitration_phase0_containment import VERSION as ARB_PHASE0_VERSION
from .v20260721_0001_arbitration_phase0_containment import upgrade as upgrade_arbitration_phase0
from .v20260721_0002_arbitration_workflow_foundation import DESCRIPTION as ARB_WORKFLOW_DESCRIPTION
from .v20260721_0002_arbitration_workflow_foundation import NAME as ARB_WORKFLOW_NAME
from .v20260721_0002_arbitration_workflow_foundation import VERSION as ARB_WORKFLOW_VERSION
from .v20260721_0002_arbitration_workflow_foundation import upgrade as upgrade_arbitration_workflow
from .v20260722_0001_langgraph_checkpoint_ttl_compatibility import DESCRIPTION as LANGGRAPH_TTL_DESCRIPTION
from .v20260722_0001_langgraph_checkpoint_ttl_compatibility import NAME as LANGGRAPH_TTL_NAME
from .v20260722_0001_langgraph_checkpoint_ttl_compatibility import VERSION as LANGGRAPH_TTL_VERSION
from .v20260722_0001_langgraph_checkpoint_ttl_compatibility import upgrade as upgrade_langgraph_ttl
from .v20260722_0002_arbitration_phase6_scope_index import DESCRIPTION as ARB_PHASE6_SCOPE_DESCRIPTION
from .v20260722_0002_arbitration_phase6_scope_index import NAME as ARB_PHASE6_SCOPE_NAME
from .v20260722_0002_arbitration_phase6_scope_index import VERSION as ARB_PHASE6_SCOPE_VERSION
from .v20260722_0002_arbitration_phase6_scope_index import upgrade as upgrade_arbitration_phase6_scope
from .v20260722_0003_arbitration_filing_export_effects import DESCRIPTION as ARB_EXPORT_EFFECT_DESCRIPTION
from .v20260722_0003_arbitration_filing_export_effects import NAME as ARB_EXPORT_EFFECT_NAME
from .v20260722_0003_arbitration_filing_export_effects import VERSION as ARB_EXPORT_EFFECT_VERSION
from .v20260722_0003_arbitration_filing_export_effects import upgrade as upgrade_arbitration_export_effects
from .v20260722_0004_arbitration_acceptance_governance import DESCRIPTION as ARB_ACCEPTANCE_DESCRIPTION
from .v20260722_0004_arbitration_acceptance_governance import NAME as ARB_ACCEPTANCE_NAME
from .v20260722_0004_arbitration_acceptance_governance import VERSION as ARB_ACCEPTANCE_VERSION
from .v20260722_0004_arbitration_acceptance_governance import upgrade as upgrade_arbitration_acceptance
from .v20260723_0001_arbitration_effect_recovery import DESCRIPTION as ARB_EFFECT_RECOVERY_DESCRIPTION
from .v20260723_0001_arbitration_effect_recovery import NAME as ARB_EFFECT_RECOVERY_NAME
from .v20260723_0001_arbitration_effect_recovery import VERSION as ARB_EFFECT_RECOVERY_VERSION
from .v20260723_0001_arbitration_effect_recovery import upgrade as upgrade_arbitration_effect_recovery
from .v20260811_0001_key_date_eot_revision_workflow import DESCRIPTION as KEY_DATE_EOT_DESCRIPTION
from .v20260811_0001_key_date_eot_revision_workflow import NAME as KEY_DATE_EOT_NAME
from .v20260811_0001_key_date_eot_revision_workflow import VERSION as KEY_DATE_EOT_VERSION
from .v20260811_0001_key_date_eot_revision_workflow import upgrade as upgrade_key_date_eot_revision
from .v20260813_0001_key_date_eot_attribution import DESCRIPTION as KEY_DATE_ATTRIBUTION_DESCRIPTION
from .v20260813_0001_key_date_eot_attribution import NAME as KEY_DATE_ATTRIBUTION_NAME
from .v20260813_0001_key_date_eot_attribution import VERSION as KEY_DATE_ATTRIBUTION_VERSION
from .v20260814_0001_document_extraction_indexes import (
    DESCRIPTION as DOC_EXTRACTION_IDX_DESCRIPTION,
)
from .v20260814_0001_document_extraction_indexes import NAME as DOC_EXTRACTION_IDX_NAME
from .v20260814_0001_document_extraction_indexes import (
    VERSION as DOC_EXTRACTION_IDX_VERSION,
)
from .v20260814_0001_document_extraction_indexes import (
    upgrade as upgrade_document_extraction_indexes,
)
from .v20260820_0001_entity_document_links import (
    DESCRIPTION as ENTITY_DOCUMENT_LINKS_DESCRIPTION,
)
from .v20260820_0001_entity_document_links import NAME as ENTITY_DOCUMENT_LINKS_NAME
from .v20260820_0001_entity_document_links import VERSION as ENTITY_DOCUMENT_LINKS_VERSION
from .v20260820_0001_entity_document_links import upgrade as upgrade_entity_document_links
from .v20260813_0001_key_date_eot_attribution import upgrade as upgrade_key_date_attribution
from .v20260906_0001_permission_name_unique import (
    DESCRIPTION as PERMISSION_NAME_UNIQUE_DESCRIPTION,
)
from .v20260906_0001_permission_name_unique import NAME as PERMISSION_NAME_UNIQUE_NAME
from .v20260906_0001_permission_name_unique import VERSION as PERMISSION_NAME_UNIQUE_VERSION
from .v20260906_0001_permission_name_unique import (
    upgrade as upgrade_permission_name_unique,
)
from .v20261114_0001_skill_registry import DESCRIPTION as SKILL_REGISTRY_DESCRIPTION
from .v20261114_0001_skill_registry import NAME as SKILL_REGISTRY_NAME
from .v20261114_0001_skill_registry import VERSION as SKILL_REGISTRY_VERSION
from .v20261114_0001_skill_registry import upgrade as upgrade_skill_registry
from .v20261114_0002_seed_system_skills import DESCRIPTION as SEED_SYSTEM_SKILLS_DESCRIPTION
from .v20261114_0002_seed_system_skills import NAME as SEED_SYSTEM_SKILLS_NAME
from .v20261114_0002_seed_system_skills import VERSION as SEED_SYSTEM_SKILLS_VERSION
from .v20261114_0002_seed_system_skills import upgrade as upgrade_seed_system_skills
from .v20261114_0003_letter_bench_indexes import DESCRIPTION as LETTER_BENCH_INDEXES_DESCRIPTION
from .v20261114_0003_letter_bench_indexes import NAME as LETTER_BENCH_INDEXES_NAME
from .v20261114_0003_letter_bench_indexes import VERSION as LETTER_BENCH_INDEXES_VERSION
from .v20261114_0003_letter_bench_indexes import upgrade as upgrade_letter_bench_indexes
from .v20261115_0001_seed_claim_arbitration_skill_drafts import (
    DESCRIPTION as SEED_CLAIM_ARBITRATION_SKILL_DRAFTS_DESCRIPTION,
)
from .v20261115_0001_seed_claim_arbitration_skill_drafts import (
    NAME as SEED_CLAIM_ARBITRATION_SKILL_DRAFTS_NAME,
)
from .v20261115_0001_seed_claim_arbitration_skill_drafts import (
    VERSION as SEED_CLAIM_ARBITRATION_SKILL_DRAFTS_VERSION,
)
from .v20261115_0001_seed_claim_arbitration_skill_drafts import (
    upgrade as upgrade_seed_claim_arbitration_skill_drafts,
)
from .v20261116_0001_claim_bench_indexes import DESCRIPTION as CLAIM_BENCH_INDEXES_DESCRIPTION
from .v20261116_0001_claim_bench_indexes import NAME as CLAIM_BENCH_INDEXES_NAME
from .v20261116_0001_claim_bench_indexes import VERSION as CLAIM_BENCH_INDEXES_VERSION
from .v20261116_0001_claim_bench_indexes import upgrade as upgrade_claim_bench_indexes
from .v20261117_0001_claim_head_link_indexes import DESCRIPTION as CLAIM_HEAD_LINK_INDEXES_DESCRIPTION
from .v20261117_0001_claim_head_link_indexes import NAME as CLAIM_HEAD_LINK_INDEXES_NAME
from .v20261117_0001_claim_head_link_indexes import VERSION as CLAIM_HEAD_LINK_INDEXES_VERSION
from .v20261117_0001_claim_head_link_indexes import upgrade as upgrade_claim_head_link_indexes
from .v20261118_0001_claim_findings_indexes import DESCRIPTION as CLAIM_FINDINGS_INDEXES_DESCRIPTION
from .v20261118_0001_claim_findings_indexes import NAME as CLAIM_FINDINGS_INDEXES_NAME
from .v20261118_0001_claim_findings_indexes import VERSION as CLAIM_FINDINGS_INDEXES_VERSION
from .v20261118_0001_claim_findings_indexes import upgrade as upgrade_claim_findings_indexes
from .v20261119_0001_claim_escalation_and_scratch_drafts import DESCRIPTION as CLAIM_ESCALATION_DESCRIPTION
from .v20261119_0001_claim_escalation_and_scratch_drafts import NAME as CLAIM_ESCALATION_NAME
from .v20261119_0001_claim_escalation_and_scratch_drafts import VERSION as CLAIM_ESCALATION_VERSION
from .v20261119_0001_claim_escalation_and_scratch_drafts import upgrade as upgrade_claim_escalation
from .v20261120_0001_arbitration_matrix_origin_unique import DESCRIPTION as MATRIX_ORIGIN_DESCRIPTION
from .v20261120_0001_arbitration_matrix_origin_unique import NAME as MATRIX_ORIGIN_NAME
from .v20261120_0001_arbitration_matrix_origin_unique import VERSION as MATRIX_ORIGIN_VERSION
from .v20261120_0001_arbitration_matrix_origin_unique import upgrade as upgrade_matrix_origin
from .v20261121_0001_reviewer_role_identities import DESCRIPTION as REVIEWER_IDENTITIES_DESCRIPTION
from .v20261121_0001_reviewer_role_identities import NAME as REVIEWER_IDENTITIES_NAME
from .v20261121_0001_reviewer_role_identities import VERSION as REVIEWER_IDENTITIES_VERSION
from .v20261121_0001_reviewer_role_identities import upgrade as upgrade_reviewer_identities
from .v20261121_0002_reviewer_role_assignment_indexes import DESCRIPTION as REVIEWER_ASSIGNMENTS_DESCRIPTION
from .v20261121_0002_reviewer_role_assignment_indexes import NAME as REVIEWER_ASSIGNMENTS_NAME
from .v20261121_0002_reviewer_role_assignment_indexes import VERSION as REVIEWER_ASSIGNMENTS_VERSION
from .v20261121_0002_reviewer_role_assignment_indexes import upgrade as upgrade_reviewer_assignments
from .v20261122_0001_claim_contract_index import DESCRIPTION as CLAIM_CONTRACT_DESCRIPTION
from .v20261122_0001_claim_contract_index import NAME as CLAIM_CONTRACT_NAME
from .v20261122_0001_claim_contract_index import VERSION as CLAIM_CONTRACT_VERSION
from .v20261122_0001_claim_contract_index import upgrade as upgrade_claim_contract
from .v20261123_0001_skill_eval_runs_indexes import DESCRIPTION as SKILL_EVAL_RUNS_DESCRIPTION
from .v20261123_0001_skill_eval_runs_indexes import NAME as SKILL_EVAL_RUNS_NAME
from .v20261123_0001_skill_eval_runs_indexes import VERSION as SKILL_EVAL_RUNS_VERSION
from .v20261123_0001_skill_eval_runs_indexes import upgrade as upgrade_skill_eval_runs
from .v20261124_0001_subscription_gateway_identity_unique import DESCRIPTION as GATEWAY_IDENTITY_DESCRIPTION
from .v20261124_0001_subscription_gateway_identity_unique import NAME as GATEWAY_IDENTITY_NAME
from .v20261124_0001_subscription_gateway_identity_unique import VERSION as GATEWAY_IDENTITY_VERSION
from .v20261124_0001_subscription_gateway_identity_unique import upgrade as upgrade_gateway_identity


MIGRATIONS = [
    Migration(
        version=INDEX_VERSION,
        name=INDEX_NAME,
        description=INDEX_DESCRIPTION,
        upgrade=upgrade_index_baseline,
    ),
    Migration(
        version=SEED_VERSION,
        name=SEED_NAME,
        description=SEED_DESCRIPTION,
        upgrade=upgrade_seed_catalog,
    ),
    Migration(
        version=TASK_FIELDS_VERSION,
        name=TASK_FIELDS_NAME,
        description=TASK_FIELDS_DESCRIPTION,
        upgrade=upgrade_task_fields,
    ),
    Migration(
        version=INSURANCE_IDX_VERSION,
        name=INSURANCE_IDX_NAME,
        description=INSURANCE_IDX_DESCRIPTION,
        upgrade=upgrade_insurance_indexes,
    ),
    Migration(
        version=ARB_HARDENING_VERSION,
        name=ARB_HARDENING_NAME,
        description=ARB_HARDENING_DESCRIPTION,
        upgrade=upgrade_arbitration_hardening,
    ),
    Migration(
        version=ARB_JURISDICTION_VERSION,
        name=ARB_JURISDICTION_NAME,
        description=ARB_JURISDICTION_DESCRIPTION,
        upgrade=upgrade_arbitration_jurisdiction,
    ),
    Migration(
        version=ARB_EXPERT_VERSION,
        name=ARB_EXPERT_NAME,
        description=ARB_EXPERT_DESCRIPTION,
        upgrade=upgrade_arbitration_expert_alignment,
    ),
    Migration(
        version=ARB_PHASE0_VERSION,
        name=ARB_PHASE0_NAME,
        description=ARB_PHASE0_DESCRIPTION,
        upgrade=upgrade_arbitration_phase0,
    ),
    Migration(
        version=ARB_WORKFLOW_VERSION,
        name=ARB_WORKFLOW_NAME,
        description=ARB_WORKFLOW_DESCRIPTION,
        upgrade=upgrade_arbitration_workflow,
    ),
    Migration(
        version=LANGGRAPH_TTL_VERSION,
        name=LANGGRAPH_TTL_NAME,
        description=LANGGRAPH_TTL_DESCRIPTION,
        upgrade=upgrade_langgraph_ttl,
    ),
    Migration(
        version=ARB_PHASE6_SCOPE_VERSION,
        name=ARB_PHASE6_SCOPE_NAME,
        description=ARB_PHASE6_SCOPE_DESCRIPTION,
        upgrade=upgrade_arbitration_phase6_scope,
    ),
    Migration(
        version=ARB_EXPORT_EFFECT_VERSION,
        name=ARB_EXPORT_EFFECT_NAME,
        description=ARB_EXPORT_EFFECT_DESCRIPTION,
        upgrade=upgrade_arbitration_export_effects,
    ),
    Migration(
        version=ARB_ACCEPTANCE_VERSION,
        name=ARB_ACCEPTANCE_NAME,
        description=ARB_ACCEPTANCE_DESCRIPTION,
        upgrade=upgrade_arbitration_acceptance,
    ),
    Migration(
        version=ARB_EFFECT_RECOVERY_VERSION,
        name=ARB_EFFECT_RECOVERY_NAME,
        description=ARB_EFFECT_RECOVERY_DESCRIPTION,
        upgrade=upgrade_arbitration_effect_recovery,
    ),
    Migration(
        version=KEY_DATE_EOT_VERSION,
        name=KEY_DATE_EOT_NAME,
        description=KEY_DATE_EOT_DESCRIPTION,
        upgrade=upgrade_key_date_eot_revision,
    ),
    Migration(
        version=KEY_DATE_ATTRIBUTION_VERSION,
        name=KEY_DATE_ATTRIBUTION_NAME,
        description=KEY_DATE_ATTRIBUTION_DESCRIPTION,
        upgrade=upgrade_key_date_attribution,
    ),
    Migration(
        version=DOC_EXTRACTION_IDX_VERSION,
        name=DOC_EXTRACTION_IDX_NAME,
        description=DOC_EXTRACTION_IDX_DESCRIPTION,
        upgrade=upgrade_document_extraction_indexes,
    ),
    Migration(
        version=ENTITY_DOCUMENT_LINKS_VERSION,
        name=ENTITY_DOCUMENT_LINKS_NAME,
        description=ENTITY_DOCUMENT_LINKS_DESCRIPTION,
        upgrade=upgrade_entity_document_links,
    ),
    Migration(
        version=PERMISSION_NAME_UNIQUE_VERSION,
        name=PERMISSION_NAME_UNIQUE_NAME,
        description=PERMISSION_NAME_UNIQUE_DESCRIPTION,
        upgrade=upgrade_permission_name_unique,
    ),
    Migration(
        version=SKILL_REGISTRY_VERSION,
        name=SKILL_REGISTRY_NAME,
        description=SKILL_REGISTRY_DESCRIPTION,
        upgrade=upgrade_skill_registry,
    ),
    Migration(
        version=SEED_SYSTEM_SKILLS_VERSION,
        name=SEED_SYSTEM_SKILLS_NAME,
        description=SEED_SYSTEM_SKILLS_DESCRIPTION,
        upgrade=upgrade_seed_system_skills,
    ),
    Migration(
        version=LETTER_BENCH_INDEXES_VERSION,
        name=LETTER_BENCH_INDEXES_NAME,
        description=LETTER_BENCH_INDEXES_DESCRIPTION,
        upgrade=upgrade_letter_bench_indexes,
    ),
    Migration(
        version=SEED_CLAIM_ARBITRATION_SKILL_DRAFTS_VERSION,
        name=SEED_CLAIM_ARBITRATION_SKILL_DRAFTS_NAME,
        description=SEED_CLAIM_ARBITRATION_SKILL_DRAFTS_DESCRIPTION,
        upgrade=upgrade_seed_claim_arbitration_skill_drafts,
    ),
    Migration(
        version=CLAIM_BENCH_INDEXES_VERSION,
        name=CLAIM_BENCH_INDEXES_NAME,
        description=CLAIM_BENCH_INDEXES_DESCRIPTION,
        upgrade=upgrade_claim_bench_indexes,
    ),
    Migration(
        version=CLAIM_HEAD_LINK_INDEXES_VERSION,
        name=CLAIM_HEAD_LINK_INDEXES_NAME,
        description=CLAIM_HEAD_LINK_INDEXES_DESCRIPTION,
        upgrade=upgrade_claim_head_link_indexes,
    ),
    Migration(
        version=CLAIM_FINDINGS_INDEXES_VERSION,
        name=CLAIM_FINDINGS_INDEXES_NAME,
        description=CLAIM_FINDINGS_INDEXES_DESCRIPTION,
        upgrade=upgrade_claim_findings_indexes,
    ),
    Migration(
        version=CLAIM_ESCALATION_VERSION,
        name=CLAIM_ESCALATION_NAME,
        description=CLAIM_ESCALATION_DESCRIPTION,
        upgrade=upgrade_claim_escalation,
    ),
    Migration(
        version=MATRIX_ORIGIN_VERSION,
        name=MATRIX_ORIGIN_NAME,
        description=MATRIX_ORIGIN_DESCRIPTION,
        upgrade=upgrade_matrix_origin,
    ),
    Migration(
        version=REVIEWER_IDENTITIES_VERSION,
        name=REVIEWER_IDENTITIES_NAME,
        description=REVIEWER_IDENTITIES_DESCRIPTION,
        upgrade=upgrade_reviewer_identities,
    ),
    Migration(
        version=REVIEWER_ASSIGNMENTS_VERSION,
        name=REVIEWER_ASSIGNMENTS_NAME,
        description=REVIEWER_ASSIGNMENTS_DESCRIPTION,
        upgrade=upgrade_reviewer_assignments,
    ),
    Migration(
        version=CLAIM_CONTRACT_VERSION,
        name=CLAIM_CONTRACT_NAME,
        description=CLAIM_CONTRACT_DESCRIPTION,
        upgrade=upgrade_claim_contract,
    ),
    Migration(
        version=SKILL_EVAL_RUNS_VERSION,
        name=SKILL_EVAL_RUNS_NAME,
        description=SKILL_EVAL_RUNS_DESCRIPTION,
        upgrade=upgrade_skill_eval_runs,
    ),
    Migration(
        version=GATEWAY_IDENTITY_VERSION,
        name=GATEWAY_IDENTITY_NAME,
        description=GATEWAY_IDENTITY_DESCRIPTION,
        upgrade=upgrade_gateway_identity,
    ),
]
