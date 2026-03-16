from pathlib import Path

from neonize.proto import Neonize_pb2

from faltoobot.bot import is_allowed_chat, source_chat_ids
from faltoobot.config import Config



def make_config(*, allowed_chats: set[str]) -> Config:
    root = Path('/tmp/faltoobot-test')
    return Config(
        home=root,
        root=root,
        config_file=root / 'config.toml',
        log_file=root / 'faltoobot.log',
        sessions_dir=root / 'sessions',
        session_db=root / 'session.db',
        launch_agent=root / 'launch-agent.plist',
        run_script=root / 'run.sh',
        openai_api_key='',
        openai_model='gpt-5.4',
        openai_thinking='high',
        openai_fast=False,
        system_prompt='',
        allow_groups=False,
        allowed_chats=allowed_chats,
    )



def jid(user: str, server: str) -> Neonize_pb2.JID:
    return Neonize_pb2.JID(User=user, Server=server)



def test_source_chat_ids_include_alt_phone_identity() -> None:
    source = Neonize_pb2.MessageSource(
        Chat=jid('56002716151848', 'lid'),
        Sender=jid('56002716151848', 'lid'),
        SenderAlt=jid('8960294979', 's.whatsapp.net'),
    )

    assert source_chat_ids(source) == {
        '56002716151848@lid',
        '8960294979@s.whatsapp.net',
    }



def test_allowlist_matches_sender_alt_phone_identity() -> None:
    source = Neonize_pb2.MessageSource(
        Chat=jid('56002716151848', 'lid'),
        Sender=jid('56002716151848', 'lid'),
        SenderAlt=jid('8960294979', 's.whatsapp.net'),
    )
    config = make_config(allowed_chats={'8960294979@s.whatsapp.net'})

    assert is_allowed_chat(config, source) is True
