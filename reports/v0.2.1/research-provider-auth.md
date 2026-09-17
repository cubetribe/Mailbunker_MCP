# IMAP-Authentifizierung für Mailbunker: Stand September 2026

**Autor:** Researcher  
**Datum:** 2026-09-17  
**Scope:** Authentifizierungsoptionen für Gmail, Google Workspace, iCloud Mail; macOS Keychain-Integration; Python IMAP Bibliotheken  

---

## Executive Summary

Für "Mailbunker" auf macOS mit Python (`aioimaplib`):

| Provider | Einfach? | Empfehlung | Implementierungsaufwand |
|----------|----------|------------|----------------------|
| **Gmail** | ⚠️ Nein (bald) | OAuth2 via Google Cloud | **MITTEL** (S: ~2–3h, API-Setup) |
| **Gmail App Passwords** | ✅ Ja | Nur als Übergang | **KLEIN** (Wenige Minuten, wird deprecated) |
| **iCloud Mail** | ✅ Ja | App-spezifisches Passwort | **KLEIN** (~5 Min Setup pro Konto) |
| **Klassische IMAP-Provider** | ✅ Ja | Direktes IMAP-Passwort | **KLEIN** (~2–3 Min) |
| **Keychain-Extraktion (Gmail/iCloud)** | ❌ Nein | Nicht praktikabel | **GROSS** (kaum möglich) |

**Fazit:** **Hybrid-Ansatz erforderlich**:
- iCloud Mail → **App-Passwörter** (einfach, stabil)
- Gmail → **OAuth2** (aufwendiger Setup, zukunftssicher)
- Klassische Provider → **Direktes IMAP-Passwort oder Keychain**

---

## 1. Gmail & Google Workspace – Stand September 2026

### 1.1 IMAP mit normalem Kontopasswort

**Status:** ❌ **NICHT MÖGLICH**

- **Seit 2022:** "Unsichere App"-Zugriff wurde deprecated
- **Seit März 2025:** Vollständige Abschaltung – alle dritten Apps müssen OAuth nutzen
- **Quelle:** [Google Workspace Admin Help – Transition from less secure apps to OAuth](https://knowledge.workspace.google.com/admin/sync/transition-from-less-secure-apps-to-oauth?hl=en)

### 1.2 App Passwords (Anwendungs-Passwörter)

**Status:** ⚠️ **NOCH VORHANDEN, aber in Deprecation-Phase**

#### Anforderungen (Sept 2026)
- ✅ **2-Step Verification (2SV)** muss aktiv sein (obligatorisch)
- ✅ App Password generierbar über [Google Account > Security > App Passwords](https://myaccount.google.com/apppasswords)
- ✅ 16-stelliges Passwort, einmalig angezeigt

#### Besonderheiten für Google Workspace
- **Admin-Kontrolle:** Workspace-Admins können App Passwords **deaktivieren** (falls `Less Secure App Access` global blockt)
- **Mischszenario:** Manche Workspace-Orgs unterstützen App Passwords noch, andere nicht
- **Empfehlung:** NICHT für neue Integration nutzen; nur als Fallback

#### Auslaufdatum
- **Offiziell kein Enddatum angekündigt** (Sept 2026), aber Google phased schrittweise aus
- **Realität:** App Passwords funktionieren, aber langfristig unsicher
- **Quelle:** [Gmail OAuth 2.0 Changes 2026 – Getmailbird](https://www.getmailbird.com/gmail-oauth-changes-app-password-phase-out/)

### 1.3 OAuth2 / XOAUTH2 – Der zukunftssichere Weg

**Status:** ✅ **OBLIGATORISCH seit März 2025, Standardweg in 2026**

#### OAuth Scope
```
https://mail.google.com/
```
Diese Scope erlaubt IMAP, SMTP und POP3 Zugriff.

#### Google Cloud Setup (Konkrete Schritte)
1. **Projekt erstellen:** [Google Cloud Console](https://console.cloud.google.com/)
2. **Gmail API aktivieren:** `APIs & Services > Library > Gmail API`
3. **OAuth Client erstellen:** `Credentials > Create OAuth 2.0 Client ID > Desktop Application`
4. **Redirect URI setzen:** z.B. `http://localhost:8080/callback`
5. **Scopes konfigurieren:** `https://mail.google.com/`
6. **OAuth Consent Screen:** Konfigurieren (App-Name, Support Email, Datenschutz)

#### Test Phase & Limits
- **Test Users:** Beliebig viele können in der Testing-Phase hinzugefügt werden
- **Production:** App benötigt CASA Tier 2 Security Assessment ($540–$1.000 Lab-Gebühren) für externe Nutzer
- **Für Eigennutzung:** Testing-Phase reicht aus; keine Zertifizierung nötig
- **Quelle:** [Google OAuth Verification & Gmail API Credentials (2026) – Unipile](https://www.unipile.com/integrating-google-oauth-2-0-user-authentication-into-your-app/)

#### Token-Lifespan
- **Private Gmail-Konten:** 72 Stunden (dann Refresh Token nötig)
- **Workspace-Konten:** Länger (meist 3600s + Refresh)
- **Speicherung erforderlich:** Refresh Token persistent speichern für Langzugriff

#### XOAUTH2 SASL Format (für `aioimaplib`)
```
user=<email>@gmail.com
auth=Bearer <access_token>
```
(Base64-kodiert mit `\x01` als Separator)

#### Komplexität
| Schritt | Aufwand |
|---------|---------|
| Google Cloud Projekt + OAuth Client | 15–20 Min |
| OAuth Flow implementieren (Web-Browser öffnen, Token erhalten) | 30–45 Min |
| XOAUTH2 in `aioimaplib` integrieren | 30–60 Min |
| Token-Refresh-Handling | 20–30 Min |
| **Gesamt** | **~2–3 Stunden** |

**Quelle:** [OAuth 2.0 mechanism – Gmail Google for Developers](https://developers.google.com/workspace/gmail/imap/xoauth2-protocol)

---

## 2. iCloud Mail (Apple) – Stand September 2026

### 2.1 IMAP mit normalem Apple ID Passwort

**Status:** ❌ **NICHT MÖGLICH**

- Apple lehnt das reguläre Account-Passwort ab
- Wird durch 2FA bewehrt

### 2.2 App-spezifische Passwörter – Der Standard

**Status:** ✅ **STANDARD UND EMPFOHLEN FÜR IMAP**

#### Anforderungen
- ✅ **2-Factor Authentication (2FA)** muss aktiv sein (Voraussetzung)
- ✅ App-spezifisches Passwort generieren unter [appleid.apple.com > Sign-In and Security > App-Specific Passwords](https://appleid.apple.com)

#### Generierung
1. `Sign-In and Security` aufrufen
2. `App-Specific Passwords` wählen
3. Label eingeben (z.B. "Mailbunker")
4. `Create` drücken
5. **16-stelliges Passwort kopieren** (wird nur einmal angezeigt!)

#### IMAP Server Settings
```
IMAP Server: imap.mail.me.com
Port: 993
SSL/TLS: YES (erforderlich)
Username: Meist nur der lokale Teil (z.B. "john" statt "john@icloud.com"), sonst vollständige E-Mail
Passwort: Das generierte 16-stellige App-Passwort
```

#### Stabilität
- ✅ Diese Methode ist stabil und wird von Apple langfristig unterstützt
- ✅ Keine Deprecation angekündigt
- ✅ Funktioniert mit allen Standard-IMAP-Clients

**Quelle:** [iCloud Mail server settings for other email client apps – Apple Support](https://support.apple.com/en-us/102525)

### 2.3 OAuth für iCloud IMAP

**Status:** ⚠️ **THEORETISCH MÖGLICH, aber nicht dokumentiert**

- **Microsoft Outlook** unterstützt OAuth für iCloud (neuere Versionen)
- **iCloud IMAP direkt:** OAuth wird vom Server angekündigt (Hostname `imap.mail.me.com` advertised `XOAUTH2`), aber **nicht offiziell dokumentiert**
- **Apple Mail:** Nutzt intern OAuth, aber Token ist **nicht für direkten IMAP-Login verwendbar**
- **Empfehlung:** App-Passwörter sind der praktikable Weg; OAuth hier nicht zu empfehlen

**Quelle:** [GitHub – MailKit Issue #1397 – iCloud and OAuth2](https://github.com/jstedfast/MailKit/issues/1397)

---

## 3. macOS Keychain & IMAP-Passwort-Extraktion

### 3.1 Verfügbarkeit von `security find-internet-password`

**Status:** ✅ **TOOL EXISTIERT, aber stark eingeschränkt (TCC/Hardening)**

#### Kommando-Format
```bash
security find-internet-password -s imap.gmail.com -a email@gmail.com -g
```

#### Zugriffs-Einschränkungen (macOS 14–15)
- **TCC (Transparency, Consent, and Control):** Skripte ohne Sandbox benötigen Keychain-Zugriff-Erlaubnis
- **Benutzer-Prompt:** Erscheint beim ersten Zugriff, kann mit `-a` (allow) automatisiert werden
- **System Integrity Protection (SIP):** Verstärkt die Kontrolle seit macOS 11
- **Malware-Warnung:** Einige Antivirus-Tools erkennen `security` als verdächtig

#### Großes Security-Problem (CVE-2025-24204)
- **Betroffen:** macOS Sequoia 15.0
- **Schwachstelle:** Apples `gcore` Utility hatte eine System-Level Entitlement, das **jeder** nutzen konnte
- **Auswirkung:** Master-Key der Login Keychain konnte ausgelesen werden → alle Passwörter dekryptierbar
- **Status:** 🔧 **Gefixt in macOS 15.3**
- **Lehre:** Keychain ist auf älteren macOS-Versionen nicht 100% sicher

**Quelle:** [macOS Sequoia flaw could have exposed Keychain data – AppleInsider](https://appleinsider.com/articles/25/09/04/macos-sequoia-flaw-could-have-exposed-keychain-data-including-passwords)

### 3.2 Internet Accounts in macOS (Apple Mail)

**Status:** ⚠️ **OAUTH TOKEN, nicht IMAP-Passwort**

#### Was passiert beim Setup?
- **Gmail in Internet Accounts:** Nutzer klickt "Sign in with Google" → OAuth-Flow
- **Speicherung:** OAuth **Refresh Token** wird im Keychain gespeichert
- **Nicht nutzbar für direkten IMAP:** Das Token ist **nicht** das klassische IMAP-Passwort
- **Für IMAP-Apps:** Refresh Token kann gelesen, aber nicht direkt als IMAP-Authentifizierung genutzt werden (würde Token zu Access Token exchange erfordern)

**Schlussfolgerung:** Keychain-Extraktion für Gmail/iCloud funktioniert nicht praktikabel, da nur OAuth Tokens gespeichert werden, nicht IMAP-Passwörter.

### 3.3 Klassische IMAP-Provider (All-Inkl, Posteo, mailbox.org)

**Status:** ✅ **IMAP-PASSWORT IM KEYCHAIN ERREICHBAR**

#### Zugriff via `security find-internet-password`
```bash
security find-internet-password -s mailbox.org -a username@mailbox.org -g 2>&1 | grep password
```

#### Praktische Anwendung
- ✅ Wenn ein Nutzer sein Posteo/All-Inkl-Konto in Apple Mail aufgesetzt hat, liegt das echte IMAP-Passwort im Keychain
- ✅ Skript kann es abrufen (mit User-Prompt oder `-a` für Automation)
- ✅ Dann direkt an `aioimaplib` übergeben

#### Sicherheitsaspekte
- ⚠️ TCC-Prompt wird erwartet (es sei denn, App hat Keychain-Zugriff)
- ⚠️ Passwort wird im CLI sichtbar (besser in Variablen handhaben)
- ⚠️ Audit-Log zeichnet Zugriff auf

**Quelle:** [Keychain Password Retrieval via Command Line – Detection.FYI](https://detection.fyi/elastic/detection-rules/macos/credential_access_keychain_pwd_retrieval_security_cmd/)

---

## 4. Python IMAP Bibliotheken & XOAUTH2-Unterstützung

### 4.1 Übersicht

| Bibliothek | XOAUTH2-Support | Async | Community | IMAP-Vollständigkeit |
|-----------|-----------------|-------|-----------|----------------------|
| **imaplib** (stdlib) | ❌ Nein (manuell möglich) | ❌ Nein | ⭐⭐⭐⭐⭐ | RFC 3501 |
| **aioimaplib** (aktuell) | ⚠️ Manuell | ✅ Ja | ⭐⭐ (klein) | ~90% |
| **IMAPClient** | ✅ `oauth2_login()` | ❌ Nein | ⭐⭐⭐⭐ | RFC 3501 |
| **imapclient** | ✅ Ja | ❌ Nein | ⭐⭐⭐⭐ | RFC 3501 |
| **python-oauth2** | ✅ Wrapper | ❌ Nein | ⭐⭐⭐ | Wrap imaplib |

### 4.2 Für Mailbunker: `aioimaplib` mit XOAUTH2

**Status:** ⚠️ **MÖGLICH, aber manuell**

#### Aktuelle Implementierung in Mailbunker
```python
# aus src/mailbunker/imap/client.py
async def login(self, username: str, password: str):
    await self.client.login(username, password)
```

#### OAuth2-Variante (erforderliche Änderung)
```python
async def login_xoauth2(self, email: str, access_token: str):
    # Manuell: XOAUTH2 String konstruieren
    auth_string = f"user={email}\x01auth=Bearer {access_token}\x01\x01"
    auth_bytes = base64.b64encode(auth_string.encode()).decode()
    
    # aioimaplib authentifizieren
    await self.client.authenticate("XOAUTH2", lambda r: auth_bytes)
```

#### Aufwand
- **Scope:** Etwa 40–60 Codezeilen
- **Testing:** 1–2 Stunden
- **Zeit gesamt:** ~**1 Stunde** (wenn Google Cloud Setup schon done)

**Quelle:** [aioimaplib – GitHub](https://github.com/bamthomas/aioimaplib), [XOAUTH2 SASL – Gmail Developers](https://developers.google.com/workspace/gmail/imap/xoauth2-protocol)

### 4.3 Alternative: IMAPClient (synchron)

**Status:** ✅ **EINFACHER, aber nicht async**

- **oauth2_login() Methode** vorhanden
- **Größere Community** → bessere Docs
- **Nachteil:** Synchron (blockiert), passt nicht zu `aioimaplib` Async-Architektur von Mailbunker
- **Hybrid:** Könnte parallel für Fallback-Konten genutzt werden (z.B. wenn nur App-Passwörter)

**Quelle:** [IMAPClient – Dokumentation oauth2_login](https://imapclient.readthedocs.io/)

---

## 5. Konkrete Empfehlung für Mailbunker

### 5.1 Hybrid-Authentifizierung implementieren

#### Phase 1: Sofort (für MVP)
```python
# Klassische Methode: App Passwords + Plain IMAP
login_method = "password"  # Standard IMAP
providers = {
    "gmail": "App Password (2FA erforderlich)",
    "icloud": "App-spezifisches Passwort (2FA erforderlich)",
    "posteo": "IMAP-Passwort direkt oder Keychain",
}
```

**Aufwand:** 0 (bereits implementiert)  
**Nutzer-Experience:** Einfach, aber aufwändig (manuell Passwörter eingeben)

#### Phase 2: Mittelfristig (nächste Sprint)
```python
# OAuth2 für Gmail
login_method = "oauth2"  
google_oauth = {
    "cloud_project": "mailbunker-app",
    "scope": "https://mail.google.com/",
    "test_users": ["user@gmail.com"],  # Testing Phase
}
```

**Aufwand:** 2–3 Stunden (+ Google Cloud Setup 15 Min)  
**Nutzer-Experience:** Einmal OAuth-Login, dann nahtlos  
**Sicherheit:** ⭐⭐⭐⭐⭐

#### Phase 3: Langfristig (wenn Multi-Konten-Support ausbaut)
```python
# Keychain-Integration für klassische Provider
keychain_support = {
    "enabled": True,
    "fallback_to_manual": True,
    "providers": ["posteo", "mailbox.org", "all-inkl"],
}
```

**Aufwand:** 2–3 Stunden  
**Nutzer-Experience:** Konten-Daten aus macOS Auto-Extract  
**Sicherheit:** ⭐⭐⭐ (TCC-bewehrt, aber praktisch)

### 5.2 Implementierungs-Roadmap

| Phase | Methode | Zeit | Priorität | Blockiert? |
|-------|---------|------|-----------|-----------|
| **MVP (jetzt)** | App Passwords manuell | - | 🔴 | ✅ Nein |
| **Phase 1** | OAuth2 für Gmail | 2–3h | 🟠 | ✅ Nein |
| **Phase 2** | iCloud App Passwords | 30 min | 🟢 | ✅ Nein |
| **Phase 3** | Keychain-Integration | 2–3h | 🟢 | ✅ Nein |

---

## 6. Security Considerations

### 6.1 Passwort-Speicherung
- ❌ **NICHT in plain text** in DB speichern
- ✅ **Verschüsseln** vor Speicherung (z.B. mit `cryptography` lib)
- ✅ **Refresh Tokens** ähnlich behandeln

### 6.2 OAuth Token-Handling
- ✅ **Refresh Token persistent** speichern (DB + Verschlüsselung)
- ✅ **Access Token** kurzzeitig cachen (Lifespan beachten)
- ✅ **Automatic Refresh** vor Ablauf implementieren

### 6.3 Keychain-Zugriff
- ✅ TCC-Prompt akzeptieren (normal für Python-CLI-Tools)
- ❌ **NICHT** in privater Sandbox laufen (würde Keychain blockieren)
- ✅ **Audit Log** beachten (Apple protokolliert Zugriffe)

### 6.4 2FA-Anforderung bei App Passwords
- ✅ **Gmail App Passwords** benötigen 2FA (erzwingt Sicherheit)
- ✅ **iCloud App Passwords** benötigen 2FA (erzwingt Sicherheit)
- ⚠️ **Workspace-Konten** können 2FA deaktivieren (Org-Richtlinie beachten)

---

## 7. Quellen & Primärdokumentation

### Google & Workspace
- [Transition from less secure apps to OAuth – Google Workspace Admin Help](https://knowledge.workspace.google.com/admin/sync/transition-from-less-secure-apps-to-oauth?hl=en)
- [OAuth 2.0 mechanism – Gmail Google for Developers](https://developers.google.com/workspace/gmail/imap/xoauth2-protocol)
- [Gmail OAuth 2.0 Changes 2026 – Getmailbird](https://www.getmailbird.com/gmail-oauth-changes-app-password-phase-out/)
- [Google OAuth Verification & Gmail API Credentials (2026) – Unipile](https://www.unipile.com/integrating-google-oauth-2-0-user-authentication-into-your-app/)

### Apple & iCloud
- [iCloud Mail server settings for other email client apps – Apple Support](https://support.apple.com/en-us/102525)
- [iCloud and OAuth2 – GitHub MailKit Issue #1397](https://github.com/jstedfast/MailKit/issues/1397)

### Python Libraries
- [aioimaplib – GitHub](https://github.com/bamthomas/aioimaplib)
- [IMAPClient – Dokumentation](https://imapclient.readthedocs.io/)
- [IMAP API in Python – Unipile (2026)](https://www.unipile.com/imap-api-python/)

### macOS & Keychain Security
- [macOS Sequoia flaw exposed Keychain data – AppleInsider](https://appleinsider.com/articles/25/09/04/macos-sequoia-flaw-could-have-exposed-keychain-data-including-passwords)
- [Keychain Password Retrieval via Command Line – Detection.FYI](https://detection.fyi/elastic/detection-rules/macos/credential_access_keychain_pwd_retrieval_security_cmd/)

---

## Anhang: Quick Reference

### Gmail IMAP Setup (Current)
```
Server: smtp.gmail.com
Port: 993 (IMAP)
Auth: App Password (nach 2FA generiert)
User: email@gmail.com
```

### iCloud Mail IMAP Setup (Current)
```
Server: imap.mail.me.com
Port: 993
Auth: App-spezifisches Passwort (nach 2FA generiert)
User: email@icloud.com oder nur lokaler Teil
```

### Google OAuth Scopes
```
https://mail.google.com/          # IMAP/SMTP/POP
https://www.googleapis.com/auth/gmail.imap_admin  # Workspace Domain-Delegation
```

---

**Ende des Reports**
