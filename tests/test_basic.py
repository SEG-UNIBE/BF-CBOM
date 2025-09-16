"""Basic test for BF-CBOM package."""

import pytest
from bf_cbom import hello, __version__


def test_hello():
    """Test the hello function."""
    result = hello()
    assert result == "Hello from bf-cbom!"


def test_version():
    """Test that version is set."""
    assert __version__ == "0.1.0"