from app.utils.files import atomic_write


def test_atomic_write_creates_and_overwrites(tmp_path):
    target = tmp_path / "sample.spec.ts"
    atomic_write(target, "first")
    assert target.read_text() == "first"
    atomic_write(target, "second")
    assert target.read_text() == "second"


def test_atomic_write_leaves_no_temp_files(tmp_path):
    target = tmp_path / "sample.spec.ts"
    atomic_write(target, "content")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []
