"""Installed MCP servers and plugins, plus the OAuth tokens some of them need."""
from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Integer, LargeBinary, Text
from sqlalchemy.orm import Mapped, mapped_column

from claude_hermes.core.db.base import Base


class Extension(Base):
    __tablename__ = "extensions"
    # Wave 5: an install is per user. The owner's row and a second user's row for
    # one catalog name are different installs with different credentials, and
    # `state`, `last_error` and `tool_count` are that user's verdict on it.
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_version: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="enabled")
    installed_at: Mapped[str] = mapped_column(Text, nullable=False)
    installed_by: Mapped[int | None] = mapped_column(BigInteger)
    last_error: Mapped[str | None] = mapped_column(Text)
    # NULL = never probed; the connectors tab shows the tool count only once a
    # probe has actually counted them.
    tool_count: Mapped[int | None] = mapped_column(Integer)
    # The names behind the count, a JSON list as text (see store.tool_names).
    tool_names: Mapped[str | None] = mapped_column(Text)


class OauthToken(Base):
    __tablename__ = "oauth_tokens"
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    issuer: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    client_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    client_secret: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    access_token: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    expires_at: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class ExtensionSecret(Base):
    """One credential an installed extension needs at launch, Fernet-sealed.

    Wave 3a replaces `secrets.env` on the data volume with this table: the file
    was readable by anything running as the agent, including the agent's own
    Bash. The ciphertext here is useless without CH_SECRETS_KEY, which lives in
    the environment and is popped before the CLI can be spawned.
    """
    __tablename__ = "extension_secrets"
    # CASCADE and not SET NULL, unlike the Wave 4a columns: there a row could
    # outlive its owner harmlessly, here it would be a decryptable credential
    # that belongs to nobody.
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    # BYTEA, not TEXT: a Fernet token is bytes, and round-tripping it through a
    # text column is one encoding assumption away from an undecryptable row.
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class CatalogDraft(Base):
    """A manifest the agent wrote and a human has not approved yet.

    `raw` is the YAML exactly as submitted — never a parsed form. Approval is a
    host-side action (`scripts/approve_draft.py` writes `catalog/`, which is
    mounted read-only into the container), so what the reviewer reads has to be
    byte-for-byte what was proposed.
    """
    __tablename__ = "catalog_drafts"
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    raw: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    # Why a reviewer said no — shown back to the agent so it stops re-proposing
    # the same manifest. NULL while the draft is still pending.
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    # Wave 4a — who proposed it. Audit only: approval is still owner-only and a
    # draft is still deploy-wide state (D8), so nothing reads this to decide
    # anything. Installs stay the owner's decision and the owner's credentials.
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"))


class ApprovalSnapshot(Base):
    """The bytes an approval decision was made against.

    The token a reviewer approves is bound to *these* bytes: if the draft changes
    between the request and the tap, the snapshot no longer matches and the
    approval is refused. Storing the parsed manifest instead would make that
    check meaningless.
    """
    __tablename__ = "approval_snapshots"
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    raw: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
