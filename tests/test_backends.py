from stream_stats import get_backend_info, list_backends


def test_all_first_party_backends_are_discoverable():
    infos = {info.name: info for info in list_backends()}
    assert set(infos) >= {"python", "assembly", "c", "cpp", "rust", "go", "scala", "java"}
    assert infos["python"].available


def test_unknown_backend_is_reported_as_unknown():
    info = get_backend_info("missing")
    assert not info.available
    assert info.provider == "unknown"
