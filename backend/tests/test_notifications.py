"""The notifications app: dispatch, channel contract, and event wiring.

These tests deliberately stay inside the notifications package plus the event
bus. Nothing here imports a job handler, which is the point of the design: a
producer states a fact and this app decides what to do with it, so the two can
be tested — and changed — independently.
"""

from __future__ import annotations


import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from conftest import make_settings
from app.core import events
from app.core.projects import create_project
from app.db.base import Base
from app.models import Permission, Role, TelegramLink, User
from notifications import Attachment, Audience, Notification, Severity
from notifications import renderers, service as notification_service
from notifications import subscribers
from notifications.channels.telegram import TelegramChannel


# --- a recording channel, standing in for a real one -----------------------


class RecordingChannel:
    """Satisfies the NotificationChannel protocol and remembers what it got."""

    name = "recording"

    def __init__(self, *, configured: bool = True, recipients: int = 1) -> None:
        self._configured = configured
        self._recipients = recipients
        self.delivered: list[Notification] = []
        self.attachments: list[bytes | None] = []
        self.configured_checks = 0

    async def is_configured(self, session: AsyncSession) -> bool:
        self.configured_checks += 1
        return self._configured

    async def deliver(
        self, session: AsyncSession, notification: Notification, attachment: bytes | None = None,
    ) -> int:
        self.delivered.append(notification)
        self.attachments.append(attachment)
        return self._recipients


class ExplodingChannel:
    name = "exploding"

    async def is_configured(self, session: AsyncSession) -> bool:
        return True

    async def deliver(
        self, session: AsyncSession, notification: Notification, attachment: bytes | None = None,
    ) -> int:
        raise RuntimeError("channel is broken")


@pytest_asyncio.fixture
async def notify_env(monkeypatch):
    """A clean bus and channel registry, over a database the app can open its
    own session against (dispatch does not take one from the caller)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(notification_service, "get_session_factory", lambda: factory)
    notification_service.clear()
    events.clear_subscribers()
    renderers.clear()

    yield factory

    notification_service.clear()
    events.clear_subscribers()
    renderers.clear()
    await engine.dispose()


def _notification(**overrides) -> Notification:
    base = dict(title="Something happened", audience=Audience.administrators())
    return Notification(**{**base, **overrides})


# --- dispatch --------------------------------------------------------------


async def test_dispatch_with_no_channels_is_a_no_op(notify_env):
    assert await notification_service.dispatch(_notification()) == 0


async def test_dispatch_reaches_every_configured_channel(notify_env):
    first, second = RecordingChannel(recipients=2), RecordingChannel(recipients=3)
    second.name = "recording-2"
    notification_service.register(first)
    notification_service.register(second)

    reached = await notification_service.dispatch(_notification())

    assert reached == 5
    assert len(first.delivered) == 1
    assert len(second.delivered) == 1


async def test_an_unconfigured_channel_is_never_asked_to_deliver(notify_env):
    channel = RecordingChannel(configured=False)
    notification_service.register(channel)

    assert await notification_service.dispatch(_notification()) == 0
    assert channel.configured_checks == 1
    assert channel.delivered == []


async def test_a_broken_channel_does_not_stop_the_others(notify_env):
    """A notification is always secondary to whatever produced it, so one
    channel raising must not deny the rest — or reach the caller."""
    healthy = RecordingChannel(recipients=1)
    notification_service.register(ExplodingChannel())
    notification_service.register(healthy)

    reached = await notification_service.dispatch(_notification())

    assert reached == 1
    assert len(healthy.delivered) == 1


async def test_registering_the_same_channel_name_twice_replaces_it(notify_env):
    """Guards against double delivery if a wiring module is imported twice."""
    first, second = RecordingChannel(), RecordingChannel()
    notification_service.register(first)
    notification_service.register(second)

    await notification_service.dispatch(_notification())

    assert notification_service.registered() == ["recording"]
    assert first.delivered == []
    assert len(second.delivered) == 1


# --- attachments are built lazily ------------------------------------------


def _report_attachment() -> Attachment:
    return Attachment(
        filename="r.pdf", media_type="application/pdf", kind="report", params={"id": 1},
    )


async def test_attachment_is_not_rendered_when_no_channel_is_configured(notify_env):
    """Rendering an analysis report is an unbounded query plus a multi-page
    render. On a deployment with no channel configured — the common case — it
    must never run."""
    calls = []

    async def _render(params) -> bytes:
        calls.append(params)
        return b"%PDF-"

    renderers.register("report", _render)
    notification_service.register(RecordingChannel(configured=False))
    await notification_service.dispatch(_notification(attachment=_report_attachment()))

    assert calls == []


async def test_attachment_is_rendered_once_for_configured_channels(notify_env):
    """Rendered by the dispatcher and shared, not once per channel — two
    channels must not each pay for the same multi-page report."""
    calls = []

    async def _render(params) -> bytes:
        calls.append(params)
        return b"%PDF-"

    renderers.register("report", _render)
    first, second = RecordingChannel(), RecordingChannel()
    second.name = "recording-2"
    notification_service.register(first)
    notification_service.register(second)

    await notification_service.dispatch(_notification(attachment=_report_attachment()))

    assert calls == [{"id": 1}]
    assert first.attachments == [b"%PDF-"]
    assert second.attachments == [b"%PDF-"]


async def test_an_unregistered_attachment_kind_still_sends_the_text(notify_env):
    """The package never learns how a report is built; with no renderer for
    the kind it degrades to a text notification rather than losing it."""
    channel = RecordingChannel()
    notification_service.register(channel)

    reached = await notification_service.dispatch(_notification(attachment=_report_attachment()))

    assert reached == 1
    assert channel.attachments == [None]


async def test_a_failing_renderer_degrades_to_text(notify_env):
    async def _broken(params) -> bytes:
        raise RuntimeError("cannot render")

    renderers.register("report", _broken)
    channel = RecordingChannel()
    notification_service.register(channel)

    reached = await notification_service.dispatch(_notification(attachment=_report_attachment()))

    assert reached == 1
    assert channel.attachments == [None]


# --- the event bus ---------------------------------------------------------


async def test_publish_without_subscribers_is_a_no_op(notify_env):
    await events.publish(events.BackupFailed(mode="full", error="disk full"))


async def test_a_failing_subscriber_never_reaches_the_publisher(notify_env):
    seen = []

    async def _broken(event):
        raise RuntimeError("subscriber is broken")

    async def _healthy(event):
        seen.append(event)

    events.subscribe(events.BackupFailed, _broken)
    events.subscribe(events.BackupFailed, _healthy)

    await events.publish(events.BackupFailed(mode="full", error="disk full"))

    assert len(seen) == 1


async def test_subscribing_the_same_handler_twice_delivers_once(notify_env):
    seen = []

    async def _handler(event):
        seen.append(event)

    events.subscribe(events.BackupFailed, _handler)
    events.subscribe(events.BackupFailed, _handler)

    await events.publish(events.BackupFailed(mode="full", error="x"))

    assert len(seen) == 1
    assert events.subscriber_count(events.BackupFailed) == 1


# --- events become notifications -------------------------------------------


async def test_backup_failure_notifies_administrators(notify_env):
    channel = RecordingChannel()
    notification_service.register(channel)
    subscribers.register()

    await events.publish(events.BackupFailed(mode="full", error="no space left"))

    assert len(channel.delivered) == 1
    sent = channel.delivered[0]
    assert sent.audience.admins is True
    assert sent.severity is Severity.ERROR
    assert "no space left" in sent.body


async def test_analysis_completion_notifies_the_project(notify_env):
    channel = RecordingChannel()
    notification_service.register(channel)
    subscribers.register()

    await events.publish(events.AnalysisCompleted(
        project_id=7, analysis_run_id=1, sonar_project_id=2, repo_name="acme/api",
        ref="main", commit_sha="abcdef1234", quality_gate="OK", issues_count=3,
        bugs=1, vulnerabilities=0, code_smells=2, coverage=81.5,
    ))

    assert len(channel.delivered) == 1
    sent = channel.delivered[0]
    assert sent.audience.project_id == 7
    assert sent.severity is Severity.SUCCESS
    assert ("Quality gate", "OK") in sent.fields


async def test_a_failed_quality_gate_is_a_warning_not_a_success(notify_env):
    channel = RecordingChannel()
    notification_service.register(channel)
    subscribers.register()

    await events.publish(events.AnalysisCompleted(
        project_id=7, analysis_run_id=1, sonar_project_id=2, repo_name="acme/api",
        ref="main", commit_sha="abcdef1234", quality_gate="ERROR", issues_count=9,
        bugs=4, vulnerabilities=2, code_smells=3, coverage=None,
    ))

    assert channel.delivered[0].severity is Severity.WARNING


async def test_no_report_is_attached_when_no_renderer_is_registered(notify_env):
    """The notifications app knows a report *can* be attached, never how to
    build one — with no renderer supplied it simply sends the summary."""
    channel = RecordingChannel()
    notification_service.register(channel)
    subscribers.register()

    await events.publish(events.AnalysisCompleted(
        project_id=7, analysis_run_id=1, sonar_project_id=2, repo_name="acme/api",
        ref="main", commit_sha="abcdef1234", quality_gate="OK", issues_count=0,
        bugs=0, vulnerabilities=0, code_smells=0, coverage=None,
    ))

    assert channel.attachments == [None]


# --- the Telegram channel --------------------------------------------------


async def _permission(session, key: str) -> Permission:
    from sqlalchemy import select

    existing = await session.scalar(select(Permission).where(Permission.key == key))
    if existing is not None:
        return existing
    perm = Permission(key=key, description=key)
    session.add(perm)
    await session.flush()
    return perm


async def _user(session, *, permissions: tuple[str, ...] = ()) -> User:
    role = Role(name=f"role-{id(object())}", access_mode="unrestricted")
    session.add(role)
    for key in permissions:
        role.permissions.append(await _permission(session, key))
    user = User(
        username=f"u{id(object())}", email=f"u{id(object())}@example.com",
        password_hash="x", is_active=True, roles=[role],
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def test_telegram_channel_escapes_interpolated_values():
    """Telegram renders HTML, including live ``<a href>``. An unescaped
    Project name is both an injection vector and — when the tag is malformed —
    a message the recipient never receives at all."""
    channel = TelegramChannel(settings=make_settings())
    rendered = channel.render(_notification(
        title='Rotsy <a href="https://evil.example">re-authenticate</a>',
        body="<script>alert(1)</script>",
        fields=(("Repo", "acme/<v2>"),),
    ))

    assert "<a href" not in rendered
    assert "<script>" not in rendered
    assert "&lt;v2&gt;" in rendered
    # Its own structural markup survives.
    assert "<b>" in rendered


def test_telegram_channel_marks_severity():
    channel = TelegramChannel(settings=make_settings())
    assert "❌" in channel.render(_notification(severity=Severity.ERROR))
    assert "✅" in channel.render(_notification(severity=Severity.SUCCESS))


async def test_telegram_channel_is_not_configured_without_a_token(notify_env):
    factory = notify_env
    channel = TelegramChannel(settings=make_settings(TELEGRAM_BOT_TOKEN=""))
    async with factory() as session:
        assert await channel.is_configured(session) is False


async def test_telegram_channel_resolves_project_members_only(notify_env, monkeypatch):
    factory = notify_env
    settings = make_settings(TELEGRAM_BOT_TOKEN="test-token:ABC")
    channel = TelegramChannel(settings=settings)

    sent: list[int] = []

    async def _fake_send_message(self, chat_id, text, reply_markup=None):
        sent.append(chat_id)
        return {}

    monkeypatch.setattr(
        "app.modules.telegram.client.TelegramClient.send_message", _fake_send_message,
    )

    async with factory() as session:
        member = await _user(session)
        outsider = await _user(session)
        project = await create_project(session, "Acme", member)
        session.add(TelegramLink(user_id=member.id, chat_id=5001, linked_by="admin"))
        session.add(TelegramLink(user_id=outsider.id, chat_id=5002, linked_by="admin"))
        await session.commit()
        project_id = project.id

    async with factory() as session:
        reached = await channel.deliver(session, _notification(audience=Audience.project(project_id)))

    assert reached == 1
    assert sent == [5001]


async def test_telegram_channel_resolves_administrators_only(notify_env, monkeypatch):
    factory = notify_env
    channel = TelegramChannel(settings=make_settings(TELEGRAM_BOT_TOKEN="test-token:ABC"))

    sent: list[int] = []

    async def _fake_send_message(self, chat_id, text, reply_markup=None):
        sent.append(chat_id)
        return {}

    monkeypatch.setattr(
        "app.modules.telegram.client.TelegramClient.send_message", _fake_send_message,
    )

    async with factory() as session:
        admin = await _user(session, permissions=("system:execute",))
        regular = await _user(session, permissions=("projects:read",))
        session.add(TelegramLink(user_id=admin.id, chat_id=6001, linked_by="admin"))
        session.add(TelegramLink(user_id=regular.id, chat_id=6002, linked_by="admin"))
        await session.commit()

    async with factory() as session:
        reached = await channel.deliver(session, _notification(audience=Audience.administrators()))

    assert reached == 1
    assert sent == [6001]


async def test_telegram_delivery_failure_is_counted_not_raised(notify_env, monkeypatch):
    """One unreachable recipient must not deny the others or the caller."""
    from app.modules.telegram.client import TelegramError

    factory = notify_env
    channel = TelegramChannel(settings=make_settings(TELEGRAM_BOT_TOKEN="test-token:ABC"))

    async def _always_fails(self, chat_id, text, reply_markup=None):
        raise TelegramError("blocked by user")

    monkeypatch.setattr(
        "app.modules.telegram.client.TelegramClient.send_message", _always_fails,
    )

    async with factory() as session:
        admin = await _user(session, permissions=("system:execute",))
        session.add(TelegramLink(user_id=admin.id, chat_id=7001, linked_by="admin"))
        await session.commit()

    async with factory() as session:
        reached = await channel.deliver(session, _notification(audience=Audience.administrators()))

    assert reached == 0
