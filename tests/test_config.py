from agentops import config


def test_container_name_and_url():
    assert config.container_name("api") == "agentops-api"
    assert config.service_url("gateway") == "http://127.0.0.1:8000"


def test_services_cover_app_services():
    assert set(config.APP_SERVICES) < set(config.SERVICES)
    assert set(config.PORTS) == set(config.APP_SERVICES)
