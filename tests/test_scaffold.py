import json
import re
from pathlib import Path

COMPONENT = Path(__file__).parent.parent / "custom_components" / "eirc_spb"


def test_manifest_valid():
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["domain"] == "eirc_spb"
    assert manifest["requirements"] == []
    assert re.match(r"^\d+\.\d+\.\d+$", manifest["version"])


def test_const_domain():
    from custom_components.eirc_spb.const import DOMAIN

    assert DOMAIN == "eirc_spb"


def test_exceptions_exist():
    from custom_components.eirc_spb.exceptions import (
        EircSpbApiError,
        EircSpbAuthError,
        EircSpbConfirmationError,
    )

    assert issubclass(EircSpbAuthError, EircSpbApiError)
    assert issubclass(EircSpbConfirmationError, EircSpbApiError)
