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
]
