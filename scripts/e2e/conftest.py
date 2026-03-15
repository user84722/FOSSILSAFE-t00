"""
Pytest configuration for E2E tests
"""
import pytest


def pytest_addoption(parser):
    """Add custom command line options"""
    # --slow-mo / --slowmo is already provided by pytest-playwright
    try:
        parser.addoption(
            "--slow-mo",
            action="store",
            default=0,
            type=int,
            help="Slow down Playwright operations by specified milliseconds"
        )
    except ValueError:
        pass  # Already registered by pytest-playwright


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, pytestconfig):
    """Configure browser context for Playwright tests"""
    return {
        **browser_context_args,
        "ignore_https_errors": True,  # Accept self-signed certs
        "viewport": {"width": 1920, "height": 1080},
    }


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args, pytestconfig):
    """Configure browser launch args for Playwright tests"""
    return {
        **browser_type_launch_args,
        "headless": not pytestconfig.getoption("--headed"),
        "slow_mo": pytestconfig.getoption("--slow-mo"),
    }


# Markers for test categorization
def pytest_configure(config):
    """Register custom markers"""
    config.addinivalue_line(
        "markers", "install: Installation tests"
    )
    config.addinivalue_line(
        "markers", "api: API tests"
    )
    config.addinivalue_line(
        "markers", "ui: UI tests (Playwright)"
    )
    config.addinivalue_line(
        "markers", "websocket: WebSocket tests"
    )
    config.addinivalue_line(
        "markers", "slow: Slow tests (skip in fast mode)"
    )
    config.addinivalue_line(
        "markers", "requires_hardware: Tests requiring real hardware"
    )
