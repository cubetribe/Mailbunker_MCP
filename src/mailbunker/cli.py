"""Rich Command-Line Interface for Mailbunker_MCP."""

from __future__ import annotations
import sys
import asyncio
from pathlib import Path
from typing import Optional
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
from rich.prompt import Prompt

from .config import load_config, MailbunkerConfig
from .crypto.engine import CryptoEngine, VaultSentinel
from .crypto.vault import EncryptedFileVault
from .storage.database import MailbunkerDatabase
from .storage.obsidian import ObsidianVaultExporter, format_email_markdown
from .storage.models import SearchQuery
from .imap.sync_manager import SyncManager
from .keychain.macos import discover_keychain_internet_accounts, is_macos
from .mcp.server import run_server as start_mcp_server

console = Console()


def get_initialized_context(env_file: Optional[str] = None):
    """Load config and initialize core components."""
    config = load_config(env_file)
    crypto = CryptoEngine(config.vault_password)
    VaultSentinel.initialize(config.storage_path, crypto)
    vault_files = EncryptedFileVault(config.attachments_path, crypto)
    db = MailbunkerDatabase(config.db_path, crypto, vault_files)
    obsidian_exporter = ObsidianVaultExporter(db, vault_files)
    sync_manager = SyncManager(config, db, obsidian_exporter)
    return config, crypto, db, obsidian_exporter, sync_manager


@click.group()
@click.version_option(version="0.1.0")
def main():
    """Mailbunker: Zero-Trust Encrypted Email Archive, Real-Time IMAP Push Ingestion, and MCP Server."""
    pass


@main.command()
@click.option("--env", "env_file", help="Path to custom .env file", type=click.Path(exists=True))
@click.option("--idle/--no-idle", default=True, help="Enable real-time IMAP IDLE push listeners")
def start(env_file: Optional[str], idle: bool):
    """Start the Mailbunker background sync daemon and push listener."""
    config, crypto, db, obsidian_exporter, sync_manager = get_initialized_context(env_file)

    console.print(Panel.fit(
        f"[bold green]Mailbunker Daemon Active[/bold green]\n"
        f"Storage: [cyan]{config.storage_path}[/cyan]\n"
        f"Configured Accounts: [yellow]{len(config.accounts)}[/yellow]\n"
        f"Zero-Trust Encryption: [bold magenta]AES-256-GCM (Argon2id)[/bold magenta]",
        title="🔒 Mailbunker",
        border_style="green"
    ))

    async def _run():
        if idle:
            console.print("[cyan]Starting IMAP IDLE push listeners...[/cyan]")
            await sync_manager.start_idle_daemon()

            # Wait forever
            try:
                while True:
                    await asyncio.sleep(3600)
            except (asyncio.CancelledError, KeyboardInterrupt):
                console.print("\n[yellow]Stopping IDLE listeners...[/yellow]")
                await sync_manager.stop_idle_daemon()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("[green]Mailbunker stopped cleanly.[/green]")


@main.command()
@click.option("--account", "-a", help="Specific account name to sync")
@click.option("--folder", "-f", help="Specific folder to sync")
@click.option("--env", "env_file", help="Path to custom .env file", type=click.Path(exists=True))
def sync(account: Optional[str], folder: Optional[str], env_file: Optional[str]):
    """Trigger an immediate one-time sync across mail accounts."""
    config, crypto, db, obsidian_exporter, sync_manager = get_initialized_context(env_file)

    async def _run_sync():
        with console.status("[bold blue]Syncing email accounts...[/bold blue]"):
            if account:
                acc = next((a for a in config.accounts if a.name.lower() == account.lower()), None)
                if not acc:
                    console.print(f"[red]Error: Account '{account}' not found in configuration.[/red]")
                    return
                if folder:
                    count = await sync_manager.sync_folder(acc, folder)
                    console.print(f"[green]Synced {count} new emails from {acc.name}/{folder}[/green]")
                else:
                    count = await sync_manager.sync_account(acc.name)
                    console.print(f"[green]Synced {count} new emails from {acc.name}[/green]")
            else:
                results = await sync_manager.sync_all()
                table = Table(title="Sync Results", border_style="blue")
                table.add_column("Account", style="cyan")
                table.add_column("New Emails Ingested", justify="right", style="green")
                total = 0
                for acc_name, count in results.items():
                    table.add_row(acc_name, str(count))
                    total += count
                table.add_section()
                table.add_row("[bold]Total[/bold]", f"[bold]{total}[/bold]")
                console.print(table)

    asyncio.run(_run_sync())


@main.command()
@click.argument("query", required=False, default="")
@click.option("--account", "-a", help="Filter by account")
@click.option("--folder", "-f", help="Filter by folder")
@click.option("--limit", "-n", default=20, help="Max results to return")
@click.option("--env", "env_file", help="Path to custom .env file", type=click.Path(exists=True))
def search(query: str, account: Optional[str], folder: Optional[str], limit: int, env_file: Optional[str]):
    """Search through stored emails using full-text search (FTS5)."""
    config, crypto, db, obsidian_exporter, sync_manager = get_initialized_context(env_file)

    q = SearchQuery(
        query=query,
        account=account,
        folder=folder,
        limit=limit,
    )

    results = db.search_emails(q)

    if not results.results:
        console.print(f"[yellow]No emails found matching query '{query}'.[/yellow]")
        return

    table = Table(title=f"Search Results for '{query}' ({results.total} total)", border_style="cyan")
    table.add_column("ID", style="dim", width=12)
    table.add_column("Date", style="blue", width=16)
    table.add_column("Account / Folder", style="magenta")
    table.add_column("From", style="yellow")
    table.add_column("Subject", style="bold white")
    table.add_column("📎", justify="center", width=3)

    for item in results.results:
        attach_mark = str(item.attachment_count) if item.has_attachments else ""
        date_str = item.date.strftime("%Y-%m-%d %H:%M")
        table.add_row(
            item.id[:10],
            date_str,
            f"{item.account}/{item.folder}",
            item.sender[:30],
            item.subject[:50],
            attach_mark,
        )

    console.print(table)
    console.print(f"[dim]Use `mailbunker get <ID>` to view the complete decrypted message.[/dim]")


@main.command()
@click.argument("email_id")
@click.option("--env", "env_file", help="Path to custom .env file", type=click.Path(exists=True))
def get(email_id: str, env_file: Optional[str]):
    """Display the full decrypted email and markdown in the terminal."""
    config, crypto, db, obsidian_exporter, sync_manager = get_initialized_context(env_file)

    msg = db.get_email(email_id)
    if not msg:
        # Try prefix search if user passed truncated ID
        with db._get_conn() as conn:
            cur = conn.execute("SELECT id FROM emails WHERE id LIKE ? LIMIT 2", (f"{email_id}%",))
            matches = [r[0] for r in cur.fetchall()]
            if len(matches) == 1:
                msg = db.get_email(matches[0])

    if not msg:
        console.print(f"[red]Error: Email ID '{email_id}' not found.[/red]")
        return

    md_text = format_email_markdown(msg)
    console.print(Markdown(md_text))


@main.command()
@click.option("--env", "env_file", help="Path to custom .env file", type=click.Path(exists=True))
def status(env_file: Optional[str]):
    """Display bunker statistics and account statuses."""
    config, crypto, db, obsidian_exporter, sync_manager = get_initialized_context(env_file)

    stats = db.get_stats()
    statuses = sync_manager.get_statuses()

    console.print(Panel(
        f"[bold]Total Emails:[/bold] {stats['total_emails']:,}\n"
        f"[bold]Total Attachments:[/bold] {stats['total_attachments']:,}\n"
        f"[bold]Raw Email Size:[/bold] {round(stats['total_raw_size_bytes'] / (1024*1024), 2)} MB\n"
        f"[bold]Database Size:[/bold] {round(stats['db_size_bytes'] / (1024*1024), 2)} MB\n"
        f"[bold]Zero-Trust Storage:[/bold] [green]Encrypted (AES-256-GCM)[/green]",
        title="🔒 Mailbunker Status",
        border_style="green",
    ))

    table = Table(title="Configured Accounts", border_style="blue")
    table.add_column("Account", style="cyan")
    table.add_column("Host & User", style="dim")
    table.add_column("Enabled", justify="center")
    table.add_column("Indexed Emails", justify="right", style="green")

    for s in statuses:
        enabled_mark = "✅" if s.enabled else "❌"
        table.add_row(
            s.name,
            f"{s.user} @ {s.host}",
            enabled_mark,
            f"{s.total_emails:,}",
        )

    console.print(table)


@main.command(name="export-vault")
@click.option("--output", "-o", required=True, type=click.Path(), help="Target directory for Obsidian Vault")
@click.option("--env", "env_file", help="Path to custom .env file", type=click.Path(exists=True))
def export_vault(output: str, env_file: Optional[str]):
    """Decrypt and export the entire bunker into an organized Obsidian Vault."""
    config, crypto, db, obsidian_exporter, sync_manager = get_initialized_context(env_file)

    dest = Path(output).resolve()
    console.print(f"[blue]Exporting Obsidian Vault to [bold]{dest}[/bold]...[/blue]")
    count = obsidian_exporter.export_all(dest)
    console.print(f"[bold green]Successfully exported {count} emails and attachments to {dest}![/bold green]")


@main.command(name="keychain-import")
def keychain_import():
    """Discover email accounts from macOS Keychain and generate .env configuration."""
    if not is_macos():
        console.print("[yellow]Keychain auto-discovery is only available on macOS.[/yellow]")
        return

    console.print("[cyan]Scanning macOS Keychain for email credentials...[/cyan]")
    accounts = discover_keychain_internet_accounts()

    if not accounts:
        console.print("[yellow]No internet email accounts were discovered automatically via Keychain.[/yellow]")
        console.print("[dim]You can configure your accounts manually in the .env file using MAIL_1_*, MAIL_2_*, etc.[/dim]")
        return

    console.print(f"[green]Discovered {len(accounts)} potential email accounts in Keychain:[/green]\n")
    table = Table(title="Discovered Accounts", border_style="cyan")
    table.add_column("#", justify="right")
    table.add_column("Server / Host", style="cyan")
    table.add_column("Account / User", style="green")

    for i, acc in enumerate(accounts, 1):
        table.add_row(str(i), acc["server"], acc["account"])

    console.print(table)
    console.print("\n[dim]Add these to your .env file as MAIL_1_HOST, MAIL_1_USER, etc.[/dim]")


@main.command()
def mcp():
    """Run the Model Context Protocol (MCP) server over stdio."""
    start_mcp_server()


if __name__ == "__main__":
    main()
