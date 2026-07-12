"""Allow `python -m open_financial_data` as a packaging-independent CLI entry."""

from .cli import app


if __name__ == "__main__":
    app()
