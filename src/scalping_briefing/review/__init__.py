"""Shared review-service query surface."""

from .service import ReviewService


def main(argv=None) -> int:
    """Lazily invoke offline review CLI without importing it on package load."""

    from .cli import main as run_cli

    return run_cli(argv)


__all__ = ["ReviewService", "main"]
