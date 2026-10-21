from importlib.metadata import version

import crypteo


def test_package_imports() -> None:
    assert crypteo.__name__ == "crypteo"


def test_installed_package_version() -> None:
    assert version("crypteo") == "2.0.0"
