"""GitLab connection + discovered-repository state.

Owned by ``modules/gitlab``. Two independent ways a repository ends up here,
matching the two supported connection modes:

  * **User-level**: a :class:`GitLabConnection` holds one PAT; syncing it
    discovers every repository the token can see and creates a
    :class:`GitLabRepository` row per repo, each carrying its own encrypted
    copy of that token (not a live reference) and ``connection_id`` set.
  * **Repository-level**: a :class:`GitLabRepository` is created directly
    with its own PAT and no ``connection_id`` — a project connected this way
    is managed entirely independently of any user-level connection.

Either way, a repository's credential is always on the repository row
itself, so the analysis worker and webhook receiver never need to know which
mode a given repository came from — "does this repo have a token" is a single
column, not a conditional join. Tokens are Fernet-encrypted at rest using the
same cipher as the Nexus/Sonar dashboard-managed connections
(:func:`app.core.config_store.encrypt_password`), never stored in the clear.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class GitLabConnection(Base):
    __tablename__ = "gitlab_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    gitlab_url: Mapped[str] = mapped_column(String(512), nullable=False)  # e.g. https://gitlab.com
    account_username: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class GitLabRepository(Base):
    __tablename__ = "gitlab_repositories"

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("gitlab_connections.id", ondelete="CASCADE"), nullable=True, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    gitlab_url: Mapped[str] = mapped_column(String(512), nullable=False)
    gitlab_project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # GitLab's own numeric project id
    full_path: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)  # "namespace/repo"
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    webhook_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
