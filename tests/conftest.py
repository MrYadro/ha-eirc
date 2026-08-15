"""Enable custom integrations for pytest-homeassistant-custom-component."""
import pytest
import pytest_socket


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def _marker_socket_after_plugins(request):
    if request.node.get_closest_marker("enable_socket"):
        pytest_socket.enable_socket()
