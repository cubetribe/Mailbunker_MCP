# 🔒 Mailbunker_MCP

> **Zero-Trust Encrypted Email Archive, Real-Time IMAP Push Ingestion (IDLE), Obsidian Vault Generator, and Model Context Protocol (MCP) Server.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP Ready](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io/)

---

## 🌟 Übersicht / Overview

**Mailbunker_MCP** löst das Problem des sicheren Archivierens, Durchsuchens und Nutzens von E-Mails ein für alle Mal. Es verbindet beliebige IMAP-Postfächer (Work, Personal, iCloud, Gmail, Posteo, Hetzner, Mailbox.org, etc.), nimmt E-Mails in Echtzeit via **IMAP IDLE Push** entgegen, verschlüsselt alles mit **Zero-Trust AES-256-GCM** und einem Master-Passwort, indiziert alles für **Sub-Millisekunden-Volltextsuche (SQLite FTS5)** und stellt einen **Model Context Protocol (MCP) Server** sowie einen **Obsidian Vault Exporter** bereit.

### ✨ Kernfunktionen (Key Features)

1. ⚡ **Echtzeit-Push Ingestion (IMAP IDLE)**:
   - Keine verzögerten Cron-Jobs nötig: Sobald eine E-Mail auf dem Server ankommt, wird sie per IMAP `IDLE` (RFC 2177) sofort im Bunker gespeichert.
   - Intelligenter Fallback mit automatischem Reconnect und Keepalive-Erneuerung.

2. 🛡️ **Zero-Trust Verschlüsselung (At-Rest Encryption)**:
   - Alle E-Mail-Inhalte (Markdown, HTML, Roh-Header) und Anhänge (PDFs, Bilder, Office-Dateien) werden mit **AES-256-GCM** verschlüsselt abgelegt.
   - Schlüsselableitung mit modernem **Argon2id** (resistent gegen GPU/Brute-Force Angriffe) aus dem Master-Passwort (`VAULT_PASSWORD`).
   - Ohne das Passwort sind die Daten auf der Festplatte oder im Docker-Container unlesbar.

3. 🔍 **Blitzschnelle Volltextsuche (SQLite FTS5)**:
   - Sucht in Zehntausenden E-Mails in Millisekunden.
   - Unterstützt Phrasensuche (`"genaue phrase"`), Präfixsuche (`rech*`), Boolesche Operatoren (`invoice AND 2026 NOT draft`) und strukturierte Filter (nach Absender, Ordner, Konto, Datum, Anhängen).
   - Liefert farbige Vorschau-Snippets mit Match-Hervorhebung.

4. 📓 **Obsidian Vault Format**:
   - Wandelt HTML-Mails in sauberes GitHub Flavored Markdown um.
   - Vollständige YAML-Frontmatter (`id`, `subject`, `from`, `to`, `date`, `account`, `folder`, `tags`, `attachments`).
   - E-Mail-Threading via Obsidian Wikilinks (`[[Parent Note]]`).
   - Jederzeit mit `mailbunker export-vault` entschlüsselt exportierbar oder automatisch synchronisierbar.

5. 🤖 **Model Context Protocol (MCP) Server**:
   - Nahtlose Integration in Claude Desktop, Cursor, Antigravity und alle MCP-kompatiblen KI-Agenten.
   - Stellt mächtige Tools bereit: `search_emails`, `get_email`, `list_accounts`, `sync_now`, `get_sync_status`, `export_obsidian_vault`.

6. 🔑 **macOS Keychain & Multi-Account (.env)**:
   - Automatische Erkennung vorhandener Konten aus dem macOS Keychain (`mailbunker keychain-import`).
   - Flexible Konfiguration von bis zu beliebig vielen Konten (`MAIL_1_...` bis `MAIL_5_...` etc.) in `.env`.

---

## 🚀 Schnellstart / Quick Start

### 1. Installation

```bash
# Repository klonen
git clone https://github.com/cubetribe/Mailbunker_MCP.git
cd Mailbunker_MCP

# Virtuelle Umgebung erstellen und Abhängigkeiten installieren
uv venv
source .venv/bin/activate
uv pip install -e .
```

### 2. Konfiguration (`.env`)

Kopiere die Vorlage `.env.example` nach `.env`:

```bash
cp .env.example .env
```

Passe die `.env` an:

```ini
# Zero-Trust Master-Passwort (WICHTIG: sicher wählen!)
VAULT_PASSWORD=DeinSuperSicheresMasterPasswort123!

# Speicherpfad für verschlüsselte Datenbank & Anhänge
STORAGE_PATH=./data

# Konto 1 (z.B. iCloud / Apple Mail / Work)
MAIL_1_ENABLED=true
MAIL_1_NAME=Work
MAIL_1_HOST=imap.example.com
MAIL_1_PORT=993
MAIL_1_USER=deine-email@example.com
MAIL_1_PASSWORD=dein-app-spezifisches-passwort
MAIL_1_SSL=true
MAIL_1_FOLDERS=INBOX,Sent

# Konto 2 (z.B. Posteo / Mailbox / Gmail)
MAIL_2_ENABLED=true
MAIL_2_NAME=Personal
MAIL_2_HOST=imap.posteo.de
MAIL_2_PORT=993
MAIL_2_USER=personal@posteo.de
MAIL_2_PASSWORD=dein-passwort
MAIL_2_SSL=true
MAIL_2_FOLDERS=INBOX
```

> **Tipp (macOS Keychain)**: Führe `mailbunker keychain-import` aus, um gespeicherte E-Mail-Server und Kontonamen aus dem macOS Keychain anzeigen zu lassen!

---

## 💻 CLI Befehle / Usage

Mailbunker kommt mit einem modernen, farbigen Terminal-Interface:

| Befehl | Beschreibung |
|---|---|
| `mailbunker start` | Startet den Hintergrund-Daemon mit IMAP IDLE Push-Listenern für alle Konten. |
| `mailbunker sync` | Führt eine sofortige Synchronisation aller konfigurierten Postfächer durch. |
| `mailbunker search "<query>"` | Durchsucht alle gespeicherten E-Mails via FTS5 und zeigt Treffer tabellarisch an. |
| `mailbunker get <id>` | Zeigt eine vollständige E-Mail entschlüsselt mit Markdown im Terminal an. |
| `mailbunker status` | Zeigt Statistiken: Anzahl E-Mails, Anhänge, Speichergröße, Verschlüsselungsstatus. |
| `mailbunker export-vault -o ./MyVault` | Entschlüsselt alle E-Mails und exportiert sie als fertigen Obsidian-Vault. |
| `mailbunker keychain-import` | Sucht im macOS Keychain nach gespeicherten Zugangsdaten. |
| `mailbunker mcp` | Startet den FastMCP Server über stdio. |

### Beispiele

```bash
# Sofortige Synchronisation starten
mailbunker sync

# Nach Rechnungen aus 2026 suchen
mailbunker search "Rechnung 2026"

# Status des Bunkers abfragen
mailbunker status

# Als Obsidian Vault exportieren
mailbunker export-vault --output ~/Documents/Obsidian/MailVault
```

---

## 🤖 MCP Integration (Claude Desktop / Cursor / Antigravity)

Füge Mailbunker zu deiner MCP-Konfiguration hinzu (z.B. in `claude_desktop_config.json` oder Cursor Settings):

```json
{
  "mcpServers": {
    "mailbunker": {
      "command": "/Volumes/2TB_CodingProjekte/Coding_Projekte/Mailbunker_MCP/.venv/bin/mailbunker-mcp",
      "env": {
        "VAULT_PASSWORD": "DeinSuperSicheresMasterPasswort123!",
        "STORAGE_PATH": "/Volumes/2TB_CodingProjekte/Coding_Projekte/Mailbunker_MCP/data"
      }
    }
  }
}
```

### Verfügbare MCP Tools:

- `search_emails(query, account, folder, start_date, end_date, limit)`: Blitzschnelle FTS5-Suche.
- `get_email(email_id, format='markdown'|'json'|'text')`: E-Mail entschlüsseln und lesen.
- `list_accounts()`: Übersicht aller Konten und Status.
- `list_mailboxes(account_name)`: Postfach-Ordner auflisten.
- `sync_now(account, folder)`: Sofort-Sync anfordern.
- `get_sync_status()`: Real-Time IDLE & DB Status.
- `export_obsidian_vault(target_path, password)`: Vault-Export durchführen.

---

## 🐳 Docker Deployment

Mailbunker kann vollständig isoliert im Docker-Container betrieben werden:

```bash
docker compose up -d
```

---

## 🔒 Sicherheitsarchitektur (Zero-Trust)

```
[ Unverschlüsseltes Master-Passwort ]
                 │
                 ▼ Argon2id KDF (Salt, 64MB RAM, 3 Iterationen)
        [ 256-bit AES-GCM Key ]
                 │
  ┌──────────────┴──────────────┐
  ▼                             ▼
[ E-Mail Payloads ]      [ Anhänge auf Disk ]
(Body, Headers, JSON)    (PDFs, JPGs, etc.)
  │                             │
  ▼ AES-256-GCM (IV + Tag)      ▼ AES-256-GCM (IV + Tag)
[ Encrypted SQLite DB ]  [ data/attachments/... ]
```

- **Zero-Plaintext Leak**: Weder E-Mail-Texte noch Anhänge liegen unverschlüsselt auf dem Speichermedium.
- **Integritätsschutz**: Authenticated Encryption (GCM Auth Tag) verhindert Manipulationen an Daten.
- **Memory-Hard**: Brute-Force Angriffe werden durch Argon2id extrem erschwert.

---

## 🧪 Tests

```bash
source .venv/bin/activate
pytest -v
```

---

## 📄 Lizenz

MIT License - Copyright (c) 2026 cubetribe
