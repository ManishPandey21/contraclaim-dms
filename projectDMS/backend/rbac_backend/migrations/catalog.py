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
]
