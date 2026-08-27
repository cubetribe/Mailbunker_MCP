# 🛡️ Security Policy

Security and data privacy are the foundational pillars of **Mailbunker**. Because Mailbunker handles sensitive personal and business emails, we treat all security matters with the highest level of urgency and diligence.

---

## 📦 Supported Versions

We actively provide security patches and updates for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1.0 | :x:                |

---

## 🔒 Cryptographic Architecture

Mailbunker is engineered around a **Zero-Trust At-Rest** model:

1. **Key Derivation Function (KDF)**:
   - **Argon2id** (`t=3`, `m=65536` (64 MB), `p=4`) derived from the user's `VAULT_PASSWORD` and a cryptographic 16-byte random salt.
   - Designed to be memory-hard and highly resilient to offline GPU/ASIC brute-force attacks.

2. **Symmetric Encryption**:
   - **AES-256-GCM** (Galois/Counter Mode).
   - Generates a unique 96-bit (12-byte) initialization vector (IV) per payload and computes an authentication tag to guarantee cryptographic integrity and prevent tampering.

3. **Storage Security**:
   - Email bodies (HTML, plain text, parsed Markdown) and headers are stored encrypted inside SQLite.
   - Binary attachments (PDFs, images, office files) are encrypted with AES-256-GCM before writing to the filesystem.
   - Search indexes (SQLite FTS5) contain parsed searchable tokens; full content reconstruction requires the master password.

---

## 🚨 Reporting a Vulnerability

If you discover a security vulnerability, please follow our responsible disclosure process:

1. **Do NOT open a public GitHub issue.**
2. Send an email to **[info@cubetribe.com](mailto:info@cubetribe.com)** with the subject line:  
   `[SECURITY VULNERABILITY] Mailbunker - <Brief Description>`.
3. Include detailed information:
   - Type of vulnerability (e.g., cryptographic flaw, injection, plaintext leak, denial of service).
   - Step-by-step instructions or proof-of-concept (PoC) script to reproduce the issue.
   - Any affected components or environment specifications.

### ⏱️ Our Commitment

- We will acknowledge receipt of your vulnerability report within **48 hours**.
- We will provide a status update on verification and remediation within **7 days**.
- Once a fix is verified, a patched release will be issued alongside appropriate credit in the release notes (unless anonymity is requested).

---

## 💡 Best Practices for Users

- **Master Password**: Use a strong, unique passphrase (e.g., 20+ characters or a 4-word diceware passphrase) for `VAULT_PASSWORD`.
- **Environment Isolation**: Protect your `.env` file with restrictive permissions (`chmod 600 .env`).
- **MCP Tokens**: Do not commit MCP configuration files containing your `VAULT_PASSWORD` to public git repositories.
