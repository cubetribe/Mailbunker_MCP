import os
import pytest
from pathlib import Path
from mailbunker.config import load_config


def test_load_config_numbered_accounts(monkeypatch, tmp_path: Path):
    env_file = tmp_path / ".env"
    env_content = """
VAULT_PASSWORD=SecurePassword999
STORAGE_PATH=/tmp/mailbunker_data
OBSIDIAN_AUTO_EXPORT=true
OBSIDIAN_VAULT_PATH=/tmp/obsidian_vault

MAIL_1_ENABLED=true
MAIL_1_NAME=WorkMail
MAIL_1_HOST=imap.work.example
MAIL_1_PORT=993
MAIL_1_USER=alice@work.example
MAIL_1_PASSWORD=secret1
MAIL_1_FOLDERS=INBOX,Archive

MAIL_2_ENABLED=false
MAIL_2_NAME=OldMail
MAIL_2_HOST=imap.old.example
MAIL_2_USER=alice@old.example
MAIL_2_PASSWORD=secret2

MAIL_3_ENABLED=true
MAIL_3_NAME=Gmail
MAIL_3_HOST=imap.gmail.com
MAIL_3_USER=alice@gmail.example
MAIL_3_PASSWORD=app_password
"""
    env_file.write_text(env_content)

    config = load_config(env_path=env_file)

    assert config.vault_password == "SecurePassword999"
    assert config.obsidian_auto_export is True
    assert len(config.accounts) == 3

    acc1 = config.accounts[0]
    assert acc1.name == "WorkMail"
    assert acc1.host == "imap.work.example"
    assert acc1.user == "alice@work.example"
    assert acc1.folders == ["INBOX", "Archive"]
    assert acc1.enabled is True

    acc2 = config.accounts[1]
    assert acc2.name == "OldMail"
    assert acc2.enabled is False

    acc3 = config.accounts[2]
    assert acc3.name == "Gmail"
    assert acc3.user == "alice@gmail.example"
