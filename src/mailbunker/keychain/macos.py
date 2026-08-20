"""macOS Keychain & Apple Mail integration helper."""

from __future__ import annotations
import sys
import subprocess
import re
import logging
from typing import List, Dict, Optional, Any

logger = logging.getLogger("mailbunker.keychain")


def is_macos() -> bool:
    return sys.platform == "darwin"


def discover_keychain_internet_accounts() -> List[Dict[str, str]]:
    """
    Query macOS Keychain for email/IMAP internet passwords using `security`.
    Returns list of discovered account dictionaries with host, user, and protocol.
    """
    if not is_macos():
        return []

    discovered = []
    try:
        # Run security dump-keychain to find internet password items without dumping secrets
        cmd = ["security", "dump-keychain"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode != 0:
            return []

        # Parse output for 'inet' (internet password) items
        raw_output = res.stdout
        items = raw_output.split("keychain: ")
        
        for item in items:
            if '"inet"' not in item and 'class: "inet"' not in item:
                continue

            srvr_match = re.search(r'"srvr"<blob>="([^"]+)"', item)
            acct_match = re.search(r'"acct"<blob>="([^"]+)"', item)
            ptcl_match = re.search(r'"ptcl"<uint32>="([^"]+)"', item) or re.search(r'"ptcl"<blob>="([^"]+)"', item)

            if srvr_match and acct_match:
                server = srvr_match.group(1).strip()
                account = acct_match.group(1).strip()
                protocol = ptcl_match.group(1).strip() if ptcl_match else "imap"

                # Filter for email-like servers (imap, mail, smtp, exchange, etc.)
                if any(k in server.lower() for k in ["imap", "mail", "post", "email", "gmail", "outlook", "icloud"]):
                    discovered.append({
                        "server": server,
                        "account": account,
                        "protocol": protocol,
                    })

    except Exception as e:
        logger.debug(f"Keychain scan exception: {e}")

    return discovered


def get_keychain_password(server: str, account: str) -> Optional[str]:
    """
    Retrieve password for a specific server and account from macOS Keychain.
    Note: macOS may display a system dialog requesting permission.
    """
    if not is_macos():
        return None

    try:
        cmd = ["security", "find-internet-password", "-s", server, "-a", account, "-g"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        # security prints password to stderr as 'password: "..."'
        m = re.search(r'password:\s+"([^"]+)"', res.stderr)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None
