import pytest

from righthour.memory import MemoryStore


def test_sign_in_memory_and_lock(tmp_path):
    s = MemoryStore(tmp_path / "m.sqlite")
    tok, created = s.sign_in("Pune Forge", "1234", "Pune Forge")
    assert created and s.resume(tok) == "pune-forge"
    assert s.save("pune-forge", {"bias": 1.04, "cfg": {"machines": {"learned_rules": [{"machine": "press"}]}}}) == ["cfg", "bias"]
    assert s.save("pune-forge", {"bias": 1.04}) == []                 # unchanged: nothing written
    tok2, created2 = s.sign_in("pune-forge", "1234")
    assert not created2 and s.load("pune-forge")["bias"] == 1.04
    assert s.load("other") == {}                                       # plants never see each other's memory
    for _ in range(5):
        with pytest.raises(PermissionError):
            s.sign_in("pune-forge", "9999")
    with pytest.raises(PermissionError, match="Too many"):
        s.sign_in("pune-forge", "1234")
    s.sign_out(tok)
    assert s.resume(tok) is None and s.resume("made-up") is None
    with pytest.raises(ValueError):
        s.sign_in("demo", "1234")
