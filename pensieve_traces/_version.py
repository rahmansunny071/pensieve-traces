"""The installed version and the install command shown in error hints.

The version is read from the installed package metadata, so it cannot drift from ``pyproject.toml``.
The package is not on PyPI; it installs from GitHub, pinned to the release tag.
"""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["UNKNOWN_VERSION", "__version__", "install_command"]

UNKNOWN_VERSION = "0+unknown"
_REPO_URL = "git+https://github.com/rahmansunny071/pensieve-traces"

try:
    __version__ = version("pensieve-traces")
except PackageNotFoundError:
    __version__ = UNKNOWN_VERSION


def install_command(extra: str) -> str:
    """Return the ``pip install`` command for ``extra``, pinned to the installed version's tag."""
    ref = "" if __version__ == UNKNOWN_VERSION else f"@v{__version__}"
    return f'pip install "pensieve-traces[{extra}] @ {_REPO_URL}{ref}"'
