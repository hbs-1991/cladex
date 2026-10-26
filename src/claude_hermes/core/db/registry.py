"""Every ORM model, imported once so Base.metadata is complete. This is the one
place core reaches into modules — Alembic needs the whole schema in one object,
and a module that forgets to register here silently ships without a table."""
from __future__ import annotations

from claude_hermes.core.db import models as _core_models  # noqa: F401
from claude_hermes.core.db.base import Base
from claude_hermes.modules.content.domain import models as _content  # noqa: F401
from claude_hermes.modules.extensions.domain import models as _ext  # noqa: F401
from claude_hermes.modules.files.domain import models as _files  # noqa: F401
from claude_hermes.modules.identity.domain import tables as _identity  # noqa: F401
from claude_hermes.modules.memory.domain import tables as _memory  # noqa: F401
from claude_hermes.modules.scheduler.domain import models as _sched  # noqa: F401
from claude_hermes.modules.sessions.domain import models as _sessions  # noqa: F401
from claude_hermes.modules.turns.domain import models as _turns  # noqa: F401

metadata = Base.metadata
