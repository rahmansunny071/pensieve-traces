"""Smoke test so the factory's checks have something to run from day one."""

import importlib.metadata

import pytest

import pensieve_traces
from pensieve_traces import _version


def test_package_imports_and_has_a_version():
    assert isinstance(pensieve_traces.__version__, str)


def test_version_is_the_installed_metadata_version():
    assert pensieve_traces.__version__ == importlib.metadata.version("pensieve-traces")


def test_install_command_is_pinned_to_the_version_tag(monkeypatch):
    monkeypatch.setattr(_version, "__version__", "1.2.3")
    assert _version.install_command("hub") == (
        'pip install "pensieve-traces[hub] @ git+https://github.com/rahmansunny071/pensieve-traces@v1.2.3"'
    )


def test_install_command_is_unpinned_when_version_unknown(monkeypatch):
    monkeypatch.setattr(_version, "__version__", _version.UNKNOWN_VERSION)
    assert _version.install_command("redact") == (
        'pip install "pensieve-traces[redact] @ git+https://github.com/rahmansunny071/pensieve-traces"'
    )


@pytest.mark.parametrize("extra", ["redact", "hub"])
def test_install_command_never_names_pypi_form(extra):
    assert f'pip install "pensieve-traces[{extra}]"' not in _version.install_command(extra)
