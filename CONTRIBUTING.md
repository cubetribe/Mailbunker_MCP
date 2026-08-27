# 🤝 Contributing to Mailbunker

First of all, thank you for considering contributing to **Mailbunker**! 🎉

Whether you're fixing a typo, proposing a new feature, reporting a bug, adding a parser for specific email providers, or building new MCP tools, every contribution is warmly welcome and deeply appreciated.

---

## 📜 Code of Conduct

This project and everyone participating in it is governed by the [Mailbunker Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to [info@cubetribe.com](mailto:info@cubetribe.com).

---

## 🌟 How Can You Contribute?

Here are some great ways to get involved:

- **🐛 Report Bugs**: Found an issue or edge case? Let us know with steps to reproduce via [GitHub Issues](https://github.com/cubetribe/Mailbunker_MCP/issues).
- **💡 Propose Features**: Share your ideas for new MCP tools, storage backends, or integrations.
- **🛠️ Submit Pull Requests**:
  - Add support for new export formats (e.g. Logseq, Joplin, Notion).
  - Enhance email parsing (e.g. complex calendar invites, rich newsletters, embedded inline CID images).
  - Implement hybrid vector search (SQLite FTS5 + semantic embeddings).
  - Build UI dashboards or tray helpers.
- **📚 Improve Documentation**: Fix unclear phrasing, add code examples, or translate guides.
- **🔒 Security Audits**: Review our Argon2id + AES-256-GCM encryption architecture and help us keep user data bulletproof.

---

## 💻 Development Setup

Mailbunker uses modern Python tooling (`uv` recommended, standard `pip` / `venv` also supported).

### 1. Fork & Clone

```bash
git clone https://github.com/<your-username>/Mailbunker_MCP.git
cd Mailbunker_MCP
```

### 2. Create Virtual Environment

Using **uv** (recommended for blazing-fast setups):
```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Or using standard **venv & pip**:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 3. Run the Test Suite

Before making any changes, make sure all existing tests pass:

```bash
pytest -v
```

---

## 🧪 Testing Guidelines

- Write unit tests for every new feature or bug fix in the `tests/` directory.
- Aim for high test coverage for cryptographic routines, MIME parsing, and database transactions.
- Use `pytest-asyncio` for async IMAP and MCP server tests.
- Run tests before pushing:
  ```bash
  pytest
  ```

---

## 🎨 Code Style & Standards

We follow clean, modern Python standards:

- **Type Hints**: Use Python 3.10+ type annotations wherever possible.
- **Async First**: Use `asyncio` for all I/O, network, and MCP server operations.
- **Formatting & Linting**: We adhere to PEP 8.
- **Docstrings**: Provide clear Google/Sphinx style docstrings for public classes and functions.
- **Zero Plaintext Leak Rule**: Never log decrypted email content, attachments, or passwords to stdout, stderr, or unencrypted temp files.

---

## 🚀 Pull Request Workflow

1. **Branch Naming**:
   - `feat/feature-name` for new features
   - `fix/bug-name` for bug fixes
   - `docs/topic-name` for documentation updates
   - `refactor/clean-up` for code refactoring

2. **Commit Messages**:
   Use clear, conventional commit messages:
   - `feat(mcp): add export_threads tool`
   - `fix(parser): handle multipart/related inline attachments`
   - `docs(readme): add Cursor IDE configuration guide`

3. **Open a PR**:
   - Push your branch to your fork.
   - Open a Pull Request against the `main` branch of `cubetribe/Mailbunker_MCP`.
   - Fill out the PR template with details about what changed and how you tested it.

---

## 💬 Questions & Support

- **Discussions & Ideas**: Open an issue or join our community discussions.
- **Security Vulnerabilities**: Please see [SECURITY.md](SECURITY.md) for responsible disclosure.

Thank you for helping make Mailbunker the ultimate private email vault! 🚀
