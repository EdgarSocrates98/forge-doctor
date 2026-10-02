"""Typer application object and root callback.

Kept dependency-free: command modules import ``app`` from here and register
themselves, so this module never imports them (no circularity).
"""

from __future__ import annotations

from typing import Annotated

import typer

from forge_doctor import __version__

app = typer.Typer(
    name="forge-doctor",
    help="Deterministic diagnostics for data engineering projects.",
    epilog=(
        "Start here: forge-doctor scan .  |  "
        "forge-doctor checks  |  forge-doctor explain <CHECK-ID>\n\n"
        "Docs & issues: https://github.com/EdgarSocrates98/forge-doctor"
    ),
    no_args_is_help=True,
    add_completion=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"forge-doctor {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version_callback, is_eager=True)
    ] = False,
) -> None:
    """Deterministic diagnostics for data engineering projects."""
