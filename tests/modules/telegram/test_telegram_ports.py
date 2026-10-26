"""The ports are the module's contract with the rest of the system. These are
structural checks: an object that has the methods satisfies the Protocol, and one
that is missing a method does not — which is exactly what composition relies on,
since nothing ever declares that it implements a port.
"""
from __future__ import annotations

from pathlib import Path

from claude_hermes.modules.telegram.application import ports


class _Acl:
    async def check(self, user_id, chat_type, profile=None):
        return "allow"

    async def is_owner(self, user_id):
        return True


class _Runner:
    async def run_turn(self, session_key, text, *, user_id=0, attachments=None,
                       channel="telegram"):
        return "ok"

    async def reset(self, session_key):
        return None


class _Bus:
    def subscribe(self, session_key, listener):
        return lambda: None

    async def ready(self, session_key):
        return None


class _Secrets:
    async def put(self, name, value):
        return None


class _Approvals:
    async def begin(self, name):
        return None

    async def confirm(self, name, token):
        return None

    async def clear(self, kind, name):
        return None


class _Catalog:
    def is_repo_entry(self, name):
        return False

    def get(self, name):
        return None


class _Writer:
    def promote(self, manifest, raw):
        return None


class _LearnPrompt:
    def build(self, text):
        return text


class _Titler:
    async def is_new_thread(self, session_key, thread_id):
        return False

    def spawn(self, chat_id, thread_id, text, session_key=None):
        return None


class _Voice:
    async def transcribe(self, audio, filename):
        return ""

    async def synthesize(self, text):
        return b""


class _Broker:
    async def resolve(self, qid, token, user_id):
        return True


class _Workspace:
    async def lane_dir(self, session_key, channel="telegram", user_id=0):
        return Path(".")

    async def lane_owner(self, session_key, channel="telegram", user_id=0):
        return 0


class _Downloader:
    def specs(self, message):
        return []

    async def download_all(self, bot, items, dest):
        return []


class _BotApi:
    async def call(self, method, payload):
        return {}


class _Sender:
    async def send(self, chat_id, thread_id, text):
        return None

    async def send_draft(self, chat_id, thread_id, status, draft_id=1):
        return None


PAIRS = [
    (ports.AclPort, _Acl), (ports.TurnRunnerPort, _Runner),
    (ports.TurnEventBusPort, _Bus), (ports.SecretStorePort, _Secrets),
    (ports.ApprovalPort, _Approvals), (ports.CatalogPort, _Catalog),
    (ports.CatalogWriterPort, _Writer), (ports.LearnPromptPort, _LearnPrompt),
    (ports.ThreadTitlerPort, _Titler), (ports.VoicePort, _Voice),
    (ports.AskBrokerPort, _Broker), (ports.WorkspacePort, _Workspace),
    (ports.AttachmentDownloaderPort, _Downloader), (ports.BotApiPort, _BotApi),
    (ports.SenderPort, _Sender),
]


def test_every_port_is_satisfied_structurally():
    for port, impl in PAIRS:
        assert isinstance(impl(), port), port.__name__


def test_a_bare_object_satisfies_nothing():
    for port, _ in PAIRS:
        assert not isinstance(object(), port), port.__name__


def test_the_real_infrastructure_satisfies_the_ports_it_claims():
    from claude_hermes.modules.telegram.infrastructure.attachments import (
        AiogramDownloader,
    )
    from claude_hermes.modules.telegram.infrastructure.bot_api import BotApiClient
    from claude_hermes.modules.telegram.infrastructure.rich_sender import RichSender
    from claude_hermes.modules.telegram.infrastructure.titler import Titler

    assert isinstance(BotApiClient("t", object()), ports.BotApiPort)
    assert isinstance(RichSender(lambda m, p: None), ports.SenderPort)
    assert isinstance(AiogramDownloader(), ports.AttachmentDownloaderPort)
    # The titler's three collaborators are ports too, so the composition root can
    # hand over sessions' objects without the titler importing that module.
    titler = Titler(object(), generator=_TitleGenerator(), sessions=_Lanes(),
                    titles=_TitleWriter())
    assert isinstance(titler, ports.ThreadTitlerPort)
    assert isinstance(_TitleGenerator(), ports.TitleGeneratorPort)
    assert isinstance(_Lanes(), ports.LaneBindingPort)
    assert isinstance(_TitleWriter(), ports.ThreadTitleWriterPort)


class _TitleGenerator:
    async def generate(self, text):
        return ""


class _Lanes:
    async def is_unbound(self, session_key):
        return True


class _TitleWriter:
    async def apply(self, session_key, title):
        return None
