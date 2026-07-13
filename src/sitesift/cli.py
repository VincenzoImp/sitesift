"""Command-line interface — parsing and dispatch only (no business logic).

Commands are thin wrappers that load config and call into the pipeline modules:
``doctor`` (health check), ``init`` (starter config), ``run`` (full pipeline),
``reclassify`` (re-run classification from stored evidence, no re-fetch),
``status`` (frontier counts), ``taxonomy`` (inspect the topic tree), and
``eval`` (LLM accuracy on the golden set).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import Settings, load_config

app = typer.Typer(
    name="sitesift",
    help="Collect structured, validated metadata for URLs at scale.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

# Runtime dependencies checked by `doctor`. (Provider SDKs are optional extras.)
_REQUIRED_MODULES = [
    "httpx",
    "idna",
    "anyio",
    "protego",
    "tldextract",
    "trafilatura",
    "selectolax",
    "charset_normalizer",
    "py3langid",
    "pydantic",
    "pydantic_settings",
    "typer",
    "rich",
    "yaml",
    "zstandard",
]


@app.command()
def doctor() -> None:
    """Verify the environment: Python, dependencies, config. Exit 0 if healthy."""
    ok = True

    py = sys.version_info
    py_ok = py >= (3, 11)
    ok = ok and py_ok

    console.print(f"[bold]sitesift[/bold] {__version__}")
    console.print(
        f"Python {py.major}.{py.minor}.{py.micro} "
        + ("[green]OK[/green]" if py_ok else "[red]needs >= 3.11[/red]")
    )

    table = Table(title="Dependencies", show_header=True, header_style="bold")
    table.add_column("module")
    table.add_column("status")
    missing: list[str] = []
    for name in _REQUIRED_MODULES:
        found = importlib.util.find_spec(name) is not None
        table.add_row(name, "[green]OK[/green]" if found else "[red]MISSING[/red]")
        if not found:
            missing.append(name)
    console.print(table)
    ok = ok and not missing

    # Config loads and reports the identifying User-Agent.
    settings = load_config()
    console.print(f"User-Agent: [dim]{settings.user_agent()}[/dim]")
    if not settings.identity.contact:
        console.print(
            "[yellow]warning[/yellow] identity.contact is not set — the fetcher "
            "will refuse to run without it (set SITESIFT_IDENTITY__CONTACT or "
            "identity.contact in sitesift.toml)."
        )

    if ok:
        console.print("[bold green]doctor: healthy[/bold green]")
        raise typer.Exit(0)
    if missing:
        console.print(f"[bold red]doctor: missing dependencies[/bold red]: {', '.join(missing)}")
    raise typer.Exit(2)


def _read_input(source: str) -> list[str]:
    if source == "-":
        return sys.stdin.read().splitlines()
    return Path(source).expanduser().read_text(encoding="utf-8").splitlines()


def _apply_llm_options(
    settings: Settings,
    *,
    llm: str,
    provider: str,
    base_url: str,
    model_small: str,
    model_large: str,
) -> None:
    if llm == "off":
        settings.classify.mode = "off"
        return
    settings.classify.mode = "sync"
    if provider:
        settings.classify.provider = provider
    if base_url:
        settings.classify.base_url = base_url
    if model_small:
        settings.classify.model_small = model_small
    if model_large:
        settings.classify.model_large = model_large


def _print_stats(stats: object) -> None:
    line = (
        f"[green]done[/green] added={stats.added} classified={stats.classified} "  # type: ignore[attr-defined]
        f"needs_human={stats.needs_human} errors={stats.errors} skipped={stats.skipped}"  # type: ignore[attr-defined]
    )
    if stats.requeued:  # type: ignore[attr-defined]
        line += f" requeued={stats.requeued}"  # type: ignore[attr-defined]
    console.print(line)
    if stats.by_error:  # type: ignore[attr-defined]
        console.print(f"errors by code: {dict(stats.by_error)}")  # type: ignore[attr-defined]


@app.command()
def run(
    input: str = typer.Argument(..., help="URL list file (text or JSONL), or '-' for stdin"),
    out: str = typer.Option("out/results.jsonl", "--out", help="JSONL output path"),
    db: str = typer.Option(".sitesift/state.db", "--db", help="frontier/results SQLite DB"),
    scope: str = typer.Option("auto", "--scope", help="tag recorded in output (metadata only)"),
    llm: str = typer.Option("off", "--llm", help="off (extract only) | sync (LLM classifies)"),
    provider: str = typer.Option("", "--provider", help="anthropic | ollama (default: config)"),
    base_url: str = typer.Option("", "--base-url", help="LLM base URL (ollama/self-hosted)"),
    model_small: str = typer.Option("", "--model-small", help="override small model"),
    model_large: str = typer.Option("", "--model-large", help="override large model"),
    no_contact: bool = typer.Option(
        False, "--no-contact-i-accept-responsibility", help="run without a contact UA"
    ),
) -> None:
    """Run the full pipeline (normalize -> fetch -> extract -> classify)."""
    import anyio

    from .models import Scope
    from .pipeline import run_pipeline

    settings = load_config()
    _apply_llm_options(
        settings,
        llm=llm,
        provider=provider,
        base_url=base_url,
        model_small=model_small,
        model_large=model_large,
    )

    if not settings.identity.contact and not no_contact:
        console.print(
            "[red]error[/red] identity.contact is required before fetching. Set "
            "SITESIFT_IDENTITY__CONTACT (or identity.contact in sitesift.toml), or pass "
            "--no-contact-i-accept-responsibility."
        )
        raise typer.Exit(2)

    lines = _read_input(input)

    async def _go() -> object:
        return await run_pipeline(
            settings, lines, out_path=out, db_path=db, default_scope=Scope(scope)
        )

    stats = anyio.run(_go)
    _print_stats(stats)


@app.command()
def reclassify(
    out: str = typer.Option("out/results.jsonl", "--out", help="JSONL output path"),
    db: str = typer.Option(".sitesift/state.db", "--db", help="frontier/results SQLite DB"),
    llm: str = typer.Option("off", "--llm", help="off (extract only) | sync (LLM classifies)"),
    provider: str = typer.Option("", "--provider", help="anthropic | ollama (default: config)"),
    base_url: str = typer.Option("", "--base-url", help="LLM base URL (ollama/self-hosted)"),
    model_small: str = typer.Option("", "--model-small", help="override small model"),
    model_large: str = typer.Option("", "--model-large", help="override large model"),
) -> None:
    """Re-classify from stored evidence — no re-fetch (after a prompt/model/taxonomy change)."""
    import anyio

    from .pipeline import reclassify as run_reclassify

    settings = load_config()
    _apply_llm_options(
        settings,
        llm=llm,
        provider=provider,
        base_url=base_url,
        model_small=model_small,
        model_large=model_large,
    )

    async def _go() -> object:
        return await run_reclassify(settings, out_path=out, db_path=db)

    _print_stats(anyio.run(_go))


@app.command()
def status(db: str = typer.Option(".sitesift/state.db", "--db")) -> None:
    """Show URL counts by state from the frontier."""
    from .frontier.store import FrontierStore

    if not Path(db).expanduser().exists():
        console.print(f"[yellow]no frontier DB at {db}[/yellow]")
        raise typer.Exit(0)
    with FrontierStore(db) as store:
        counts = store.counts_by_status()
    table = Table(title="URLs by status")
    table.add_column("status")
    table.add_column("count", justify="right")
    for name, count in sorted(counts.items()):
        table.add_row(name, str(count))
    console.print(table)


@app.command()
def taxonomy(
    action: str = typer.Argument("list", help="list | show | validate"),
    node_id: str = typer.Argument("", help="node id (for 'show')"),
) -> None:
    """Inspect the loaded topic taxonomy."""
    from .taxonomy.loader import TaxonomyError, load_taxonomy

    settings = load_config()
    try:
        tax = load_taxonomy(taxonomy_id=settings.taxonomy.id, path=settings.taxonomy.path)
    except TaxonomyError as exc:
        console.print(f"[red]taxonomy error[/red]: {exc}")
        raise typer.Exit(2) from exc

    if action == "validate":
        console.print(f"[green]OK[/green] {tax.id} v{tax.version}: {len(tax.nodes)} nodes")
    elif action == "show" and node_id:
        node = tax.get(node_id)
        if node is None:
            console.print(f"[red]no such node[/red]: {node_id}")
            raise typer.Exit(1)
        console.print(f"[bold]{node.name}[/bold] ({node.id})")
        for child in tax.children_of(node_id):
            console.print(f"  {child.id:<20} {child.name}")
    else:  # list
        for node in tax.tier1():
            console.print(f"{node.id:<12} {node.name}")


@app.command()
def init() -> None:
    """Write a starter sitesift.toml (if absent) and verify the taxonomy loads."""
    from .taxonomy.loader import load_taxonomy

    path = Path("sitesift.toml")
    if not path.exists():
        path.write_text(_STARTER_CONFIG, encoding="utf-8")
        console.print(f"[green]created[/green] {path} — set identity.contact before running.")
    else:
        console.print(f"{path} already exists — leaving it untouched.")
    tax = load_taxonomy()
    console.print(f"taxonomy [green]OK[/green]: {tax.id} ({len(tax.nodes)} nodes)")


@app.command()
def eval(
    golden: str = typer.Option("eval/golden.jsonl", "--golden"),
    fixtures: str = typer.Option("eval/fixtures", "--fixtures"),
    provider: str = typer.Option("", "--provider", help="anthropic | ollama (default: config)"),
    base_url: str = typer.Option("", "--base-url", help="LLM base URL (ollama/self-hosted)"),
    model: str = typer.Option("", "--model", help="model for both rungs"),
    min_accuracy: float = typer.Option(
        0.0, "--min-accuracy", help="exit 3 if site_type_accuracy is below this"
    ),
) -> None:
    """Classify the golden fixtures through the LLM ladder and report accuracy.

    Requires a provider (free against a local Ollama). Judgment is the LLM's, so
    numbers vary run to run; pass --min-accuracy to use it as a gate.
    """
    from .classify.ladder import Ladder
    from .classify.llm import build_classifier
    from .evalharness import format_ladder_report, run_ladder_eval
    from .taxonomy.loader import load_taxonomy

    settings = load_config()
    settings.classify.mode = "sync"
    if provider:
        settings.classify.provider = provider
    if base_url:
        settings.classify.base_url = base_url
    if model:
        settings.classify.model_small = model
        settings.classify.model_large = model

    tax = load_taxonomy(taxonomy_id=settings.taxonomy.id, path=settings.taxonomy.path)
    classifier = build_classifier(settings, tax)
    if classifier is None:  # unreachable (mode forced to sync), but keep the type honest
        console.print("[red]eval needs a classifier[/red]")
        raise typer.Exit(2)
    ladder = Ladder(settings, classifier)
    console.print("[dim]running ladder eval (LLM)…[/dim]")
    report = run_ladder_eval(ladder=ladder, golden_path=golden, fixtures_dir=fixtures)
    console.print(format_ladder_report(report))
    classifier.close()

    if report.site_type_accuracy < min_accuracy:
        console.print(
            f"[red]below target[/red] accuracy {report.site_type_accuracy:.2f} < {min_accuracy:.2f}"
        )
        raise typer.Exit(3)


_STARTER_CONFIG = """\
[identity]
contact = ""  # REQUIRED before fetching, e.g. "you@example.com"
project_url = "https://github.com/VincenzoImp/sitesift"

[fetch]
max_concurrency = 200
min_host_delay = 1.0

[classify]
mode = "sync"      # LLM classifies every URL; "off" = extract facts only
provider = "anthropic"  # anthropic | ollama

[taxonomy]
id = "sitesift-custom-1"
"""


if __name__ == "__main__":  # pragma: no cover
    app()
