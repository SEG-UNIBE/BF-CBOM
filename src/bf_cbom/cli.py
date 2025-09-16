"""CLI interface for BF-CBOM."""

import click
from bf_cbom import __version__


@click.group()
@click.version_option(version=__version__)
def main():
    """Benchmarking Framework for Cryptographic Bill of Materials."""
    pass


@main.command()
@click.option("--input", "-i", help="Input file path")
@click.option("--output", "-o", help="Output file path")
def analyze(input: str, output: str):
    """Analyze cryptographic components in a software project."""
    click.echo(f"Analyzing cryptographic components from {input}")
    if output:
        click.echo(f"Results will be saved to {output}")
    # TODO: Implement actual analysis logic
    click.echo("Analysis completed!")


@main.command()
def benchmark():
    """Run benchmarks on cryptographic libraries."""
    click.echo("Running cryptographic library benchmarks...")
    # TODO: Implement benchmark logic
    click.echo("Benchmarks completed!")


if __name__ == "__main__":
    main()