"""GitHub App installation + discovered-repository state.

Owned by ``modules/github``. ``github_repositories.project_id`` is a plain FK
into the core ``projects`` table (mapping happens after discovery, so it's
nullable); nothing here is referenced by another module's tables.

``GitHubInstallation`` is deliberately **not** tied to a Project: GitHub's own
install-redirect only ever carries ``installation_id``, never a Project to
associate it with, and in practice one installation's repositories usually
end up mapped to several different Projects. The per-project ``github``
Integration row is created lazily when a repository from an installation is
actually mapped to a Project (see ``routers/github.py:map_repository``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class GitHubInstallation(Base):
    __tablename__ = "github_installations"

    id: Mapped[int] = mapped_column(primary_key=True)
    # GitHub's own installation id — what the App auth flow exchanges for a token.
    installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class GitHubRepository(Base):
    __tablename__ = "github_repositories"

    id: Mapped[int] = mapped_column(primary_key=True)
    # NULL means "connected by URL, no App installation" (see
    # routers/github.py:connect_public_repository) — only possible for a
    # public repository, and only manual analysis works for one, since
    # GitHub has no installation to deliver push webhooks through.
    installation_id: Mapped[int | None] = mapped_column(
        ForeignKey("github_installations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)  # "org/repo"
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    webhook_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
