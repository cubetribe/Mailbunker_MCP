# 🔒 Mailbunker_MCP

<div align="center">

**Zero-Trust verschlüsseltes E-Mail-Archiv, Echtzeit IMAP-Push Ingestion (IDLE), Obsidian Vault Generator und Model Context Protocol (MCP) Server.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP Ready](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io/)
[![Security: Zero--Trust](https://img.shields.io/badge/Security-Zero--Trust%20AES--256--GCM-red.svg)](#-zero-trust-sicherheitsarchitektur)
[![SQLite FTS5](https://img.shields.io/badge/Search-SQLite%20FTS5-orange.svg)](#-funktionen)

[English](README.md) • [Deutsch](README.de.md)

</div>

---

## 📖 Über Mailbunker

**Mailbunker_MCP** ist ein Open-Source, Privacy-First E-Mail-Archivierungs- und Suchsystem, das E-Mail-Organisation, Unabhängigkeit von Providern und schnelles Auffinden ein für alle Mal löst.

Es verbindet beliebig viele IMAP-Postfächer (Work, Personal, iCloud, Gmail, Posteo, Hetzner, Mailbox.org, eigene Server), empfängt eingehende E-Mails in Echtzeit per **IMAP IDLE Push**, schützt alle Inhalte mit **Zero-Trust AES-256-GCM Verschlüsselung** basierend auf einem Master-Passwort via **Argon2id**, indiziert alle Nachrichten für **Sub-Millisekunden-Volltextsuche (SQLite FTS5)** und stellt Standard-**Model Context Protocol (MCP)** Tools für Claude Desktop, Cursor und KI-Agenten bereit.

---

## ✨ Funktionen

- ⚡ **Echtzeit-Push Ingestion (IMAP IDLE)**:
  - Sofortige Erfassung neuer E-Mails ohne Verzögerung via IMAP `IDLE` (RFC 2177). Keine langsamen Cron-Jobs notwendig.
  - Automatische Keepalive-Erneuerung und Reconnect mit exponentiellem Backoff bei Verbindungsabbrüchen.

- 🛡️ **Zero-Trust Verschlüsselung (At-Rest)**:
  - Alle E-Mail-Texte (Markdown & HTML), Roh-Header und Dateianhänge (PDFs, Bilder, Dokumente) werden mit **AES-256-GCM** verschlüsselt gespeichert.
  - Schlüsselableitung mit modernem **Argon2id** (`VAULT_PASSWORD`), resistent gegen GPU- und Brute-Force-Angriffe.
  - Kein Klartext-Leak auf der Festplatte oder in Docker-Volumes.

- 🔍 **Blitzschnelle Volltextsuche (SQLite FTS5)**:
  - Durchsucht über 100.000 E-Mails in Millisekunden.
  - Unterstützt Phrasensuche (`"genaue phrase"`), Präfixsuche (`rech*`), Boolesche Operatoren (`rechnung AND 2026 NOT entwurf`) und strukturierte Filter (nach Absender, Datumsbereich, Konto, Ordner, Anhängen).
  - Liefert kontextbezogene Snippets mit Treffer-Hervorhebung.

- 📓 **Obsidian-kompatibler Markdown Vault**:
  - Wandelt komplexe HTML-Mails in sauberes GitHub Flavored Markdown um.
  - Vollständige YAML-Frontmatter-Metadaten (`id`, `subject`, `from`, `to`, `date`, `account`, `folder`, `tags`, `attachments`).
  - Native Obsidian Wikilinks zur Nachverfolgung von E-Mail-Threads (`[[Parent Note]]`).
  - Jederzeit exportierbar (`mailbunker export-vault`) oder automatischer Live-Export.

- 🤖 **Model Context Protocol (MCP) Server**:
  - FastMCP-Server mit Tools: `search_emails`, `get_email`, `list_accounts`, `list_mailboxes`, `sync_now`, `get_sync_status`, `export_obsidian_vault`.
  - Nahtlose Integration in Claude Desktop, Cursor IDE, Antigravity und alle MCP-fähigen Assistenten.

- 🔑 **Multi-Account & macOS Keychain Integration**:
  - Einfache Konfiguration von 1 bis 5+ Konten in `.env` (`MAIL_1_...` bis `MAIL_5_...`).
  - Interaktiver Assistent zum Auslesen gespeicherter Zugangsdaten aus dem macOS Keychain (`mailbunker keychain-import`).

---

## 🚀 Schnellstart

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

Kopiere die Vorlage `.env.example`:

```bash
cp .env.example .env
```

Passe die `.env` an:

```ini
# ==============================================================================
# Zero-Trust Master-Passwort (ERFORDERLICH)
# ==============================================================================
VAULT_PASSWORD=DeinSuperSicheresMasterPasswort123!

# Speicherpfad für verschlüsselte Datenbank & Anhänge
STORAGE_PATH=./data

# ==============================================================================
# E-Mail-Konten (Konto 1 bis 5 oder mehr konfigurieren)
# ==============================================================================
MAIL_1_ENABLED=true
MAIL_1_NAME=Work
MAIL_1_HOST=imap.example.com
MAIL_1_PORT=993
MAIL_1_USER=deine-email@example.com
MAIL_1_PASSWORD=dein-passwort-oder-app-token
MAIL_1_SSL=true
MAIL_1_FOLDERS=INBOX,Sent

MAIL_2_ENABLED=false
MAIL_2_NAME=Personal
MAIL_2_HOST=imap.posteo.de
MAIL_2_PORT=993
MAIL_2_USER=personal@posteo.de
MAIL_2_PASSWORD=dein-passwort
MAIL_2_SSL=true
MAIL_2_FOLDERS=INBOX
```

---

## 💻 CLI Befehle

Mailbunker bietet ein modernes Terminal-Interface:

| Befehl | Beschreibung |
|---|---|
| `mailbunker start` | Startet den Hintergrund-Daemon mit IMAP IDLE Push-Listenern für alle aktiven Konten. |
| `mailbunker sync` | Führt eine sofortige Synchronisation aller konfigurierten Postfächer durch. |
| `mailbunker search "<query>"` | Durchsucht alle gespeicherten E-Mails via FTS5 und zeigt Treffer tabellarisch an. |
| `mailbunker get <id>` | Zeigt eine vollständige E-Mail entschlüsselt mit Markdown im Terminal an. |
| `mailbunker status` | Zeigt Statistiken: Anzahl E-Mails, Anhänge, Speichergröße, Verschlüsselungsstatus. |
| `mailbunker export-vault -o <dir>` | Entschlüsselt alle E-Mails und exportiert sie als fertigen Obsidian-Vault. |
| `mailbunker keychain-import` | Sucht im macOS Keychain nach gespeicherten Zugangsdaten. |
| `mailbunker mcp` | Startet den FastMCP Server über stdio. |

### CLI Beispiele

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

Füge Mailbunker zu deiner MCP-Konfigurationsdatei hinzu (z.B. in `claude_desktop_config.json` oder den Cursor Einstellungen):

```json
{
  "mcpServers": {
    "mailbunker": {
      "command": "uv",
      "args": [
        "--directory",
        "/pfad/zu/Mailbunker_MCP",
        "run",
        "mailbunker-mcp"
      ],
      "env": {
        "VAULT_PASSWORD": "DeinSuperSicheresMasterPasswort123!",
        "STORAGE_PATH": "/pfad/zu/Mailbunker_MCP/data"
      }
    }
  }
}
```

### Verfügbare MCP Tools

- `search_emails(query, account, folder, start_date, end_date, limit)`: Blitzschnelle FTS5-Suche.
- `get_email(email_id, format='markdown'|'json'|'text')`: E-Mail entschlüsseln und lesen.
- `list_accounts()`: Übersicht aller Konten und Status.
- `list_mailboxes(account_name)`: Postfach-Ordner auflisten.
- `sync_now(account, folder)`: Sofort-Sync anfordern.
- `get_sync_status()`: Real-Time IDLE & DB Status.
- `export_obsidian_vault(target_path, password)`: Vault-Export durchführen.

---

## 🔒 Zero-Trust Sicherheitsarchitektur

```
[ Master-Passwort: VAULT_PASSWORD ]
                 │
                 ▼ Argon2id KDF (16-Byte Salt, 64MB RAM, 3 Iterationen)
        [ 256-bit AES-GCM Key ]
                 │
  ┌──────────────┴──────────────┐
  ▼                             ▼
[ E-Mail Payloads ]      [ Dateianhänge auf Disk ]
(Body, Header, JSON)     (PDFs, Dokumente, Bilder)
  │                             │
  ▼ AES-256-GCM (IV + Tag)      ▼ AES-256-GCM (IV + Tag)
[ Verschlüsselte SQLite DB ] [ data/attachments/... ]
```

- **Kein Klartext-Leak**: Weder E-Mail-Texte noch Anhänge liegen jemals unverschlüsselt auf dem Speichermedium.
- **Integritätsschutz**: Authenticated Encryption (GCM Auth Tag) verhindert Manipulationen an Daten.
- **Argon2id Schlüsselableitung**: Speicherintensive Parameter schützen vor Offline-Angriffen per GPU/ASIC.

---

## 🐳 Docker Deployment

Mailbunker im isolierten Docker-Container betreiben:

```bash
docker compose up -d
```

---

## 🧪 Tests

```bash
source .venv/bin/activate
pytest -v
```

---

## 📄 Lizenz

MIT License — Copyright (c) 2026 [cubetribe](https://github.com/cubetribe)
