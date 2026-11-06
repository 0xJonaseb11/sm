#!/usr/bin/env python3
"""
scripts/enrich.py

batch wallet identity resolver.
takes wallet addresses from stdin, a file, or the leaderboard,
resolves each one against fomoscan + nansen, and prints a clean table.

usage:
    # from leaderboard
    python scripts/enrich.py --board pump --top 25
    python scripts/enrich.py --board fomo

    # from a file (one address per line)
    python scripts/enrich.py --file wallets.txt

    # piped from another command
    echo "GV6UUmNxz2RpKxmNAPadYKb7uQpszwqQAu3qLJxVdC52" | python scripts/enrich.py

    # export to json
    python scripts/enrich.py --board pump --top 25 --json > enriched.json
"""

import asyncio
import sys
import json
import datetime
import argparse

from rich.console import Console
from rich.table import Table
from rich.rule import Rule
from rich import box

sys.path.insert(0, __import__("os").path.dirname(__file__) + "/..")

from sm.clients.fomoscan import FomoscanClient
from sm.clients.nansen import NansenClient
from sm.enricher import Enricher, WalletIdentity
from sm.config import cfg
from sm import fmt

console = Console()


async def collect_from_board(client: FomoscanClient, board: str, top: int) -> list[tuple[str, str]]:
    """returns list of (wallet, handle_hint) tuples."""
    pairs = []
    try:
        if board == "pump":
            r = await client.pump_leaderboard()
            for entry in (r.get("entries", []) or [])[:top]:
                wallet = entry.get("wallet", "")
                handle = entry.get("username") or entry.get("handle") or ""
                if wallet:
                    pairs.append((wallet, handle))

        elif board == "fomo":
            rankings = await client.list_rankings()
            meta     = rankings.get("items", [])
            item     = next((r for r in meta if r.get("window") == "7d"), meta[0] if meta else None)
            if item:
                data = await client.get_ranking(item.get("key", ""))
                for entry in data.get("entries", [])[:top]:
                    subject = entry.get("subject", {}) or {}
                    sources = subject.get("sources", {}) or {}
                    # try to get sol address from sources
                    sol = ""
                    handle = (
                        (subject.get("canonical") or {}).get("display", {}).get("handle", "")
                    )
                    # look up wallet via v2
                    if handle:
                        try:
                            u   = await client.get_user_by_handle(handle)
                            sol = u.get("solanaAddress", "") or ""
                        except Exception:
                            pass
                    if sol:
                        pairs.append((sol, handle))
                    await asyncio.sleep(0.1)
    except Exception as e:
        console.print(f"[dim]board collect error: {e}[/]")
    return pairs


def build_table(identities: list[WalletIdentity]) -> Table:
    t = Table(
        title=f"wallet identity enrichment - {len(identities)} wallets",
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        expand=False,
    )
    t.add_column("wallet",      width=14)
    t.add_column("chain",       width=7)
    t.add_column("fomoscan",    width=20)
    t.add_column("x handle",    width=16)
    t.add_column("nansen fomo", width=20)
    t.add_column("sources",     width=14)

    for identity in identities:
        fs_name  = identity.fs_handle or identity.fs_name or ""
        x_handle = identity.twitter_handle
        ns_name  = identity.ns_fomo_handle or identity.ns_label or ""

        # show which sources have data
        sources = []
        if identity.fs_handle:
            sources.append("[cyan]fs[/]")
        if identity.ns_is_fomo:
            sources.append("[bold cyan]fomo[/]")
        elif identity.ns_label:
            sources.append("[dim]ns[/]")

        err = identity.fs_error or identity.ns_error
        fs_display = (
            f"[bold]{fs_name}[/]" if fs_name
            else f"[dim red]{identity.fs_error[:18]}[/]" if identity.fs_error
            else "[dim]-[/]"
        )

        t.add_row(
            fmt.short_addr(identity.address, 5),
            identity.chain,
            fs_display,
            f"[cyan]{x_handle}[/]" if x_handle else "[dim]-[/]",
            f"[bold cyan]{ns_name}[/]" if ns_name else "[dim]-[/]",
            " ".join(sources) if sources else "[dim]-[/]",
        )
    return t


async def main(
    board: str | None,
    top: int,
    wallets_file: str | None,
    as_json: bool,
    chain: str,
) -> None:
    fs = FomoscanClient() if cfg.has_fomoscan() else None
    ns = NansenClient()   if cfg.has_nansen()   else None

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    if not as_json:
        console.print(Rule(f"[bold]enrich batch[/]  [dim]{now}[/]"))

    # collect wallets
    pairs: list[tuple[str, str]] = []

    if board and fs:
        if not as_json:
            console.print(f"[dim]collecting wallets from {board} board (top {top})...[/]")
        pairs = await collect_from_board(fs, board, top)

    if wallets_file:
        try:
            with open(wallets_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        pairs.append((line, ""))
        except FileNotFoundError:
            console.print(f"[red]file not found:[/] {wallets_file}")
            return

    if not pairs:
        # read from stdin if nothing else
        if not sys.stdin.isatty():
            for line in sys.stdin:
                line = line.strip()
                if line:
                    pairs.append((line, ""))

    if not pairs:
        console.print("[red]no wallets to enrich[/]")
        return

    if not as_json:
        console.print(f"[dim]enriching {len(pairs)} wallets (fomoscan + nansen)...[/]")

    enricher = Enricher(fomoscan=fs, nansen=ns)
    addresses = [p[0] for p in pairs]
    identities = await enricher.enrich_batch(addresses, chain=chain, concurrency=4)

    # merge hint handles for wallets that fomoscan couldn't resolve
    hint_map = {p[0]: p[1] for p in pairs if p[1]}
    for identity in identities:
        if not identity.fs_handle and hint_map.get(identity.address):
            identity.fs_handle = hint_map[identity.address]

    if as_json:
        result = [i.to_dict() for i in identities]
        print(json.dumps(result, indent=2))
        return

    console.print(build_table(identities))

    fomo_count = sum(1 for i in identities if i.ns_is_fomo)
    fs_count   = sum(1 for i in identities if i.fs_handle)
    console.print(
        f"\n[dim]{fs_count} wallets resolved via fomoscan  |  "
        f"{fomo_count} confirmed FOMO traders via nansen[/]"
    )

    if fs:
        await fs.close()
    await ns.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="batch wallet identity enrichment")
    p.add_argument("--board",  default=None, help="collect from leaderboard: pump | fomo")
    p.add_argument("--top",    type=int, default=25, help="top N from board (default 25)")
    p.add_argument("--file",   default=None, help="file with one wallet address per line")
    p.add_argument("--json",   action="store_true", dest="as_json",
                   help="output as JSON (pipe-friendly)")
    p.add_argument("--chain",  default="solana", help="solana | evm | bnb")
    args = p.parse_args()
    asyncio.run(main(
        board=args.board,
        top=args.top,
        wallets_file=args.file,
        as_json=args.as_json,
        chain=args.chain,
    ))
