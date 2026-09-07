import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from verify_links import wiring_sweep  # noqa: E402


def _content_file(tmp_path: Path, text: str) -> Path:
    content = tmp_path / "vol_test" / "content" / "S1"
    content.mkdir(parents=True)
    page = content / "page.htm"
    page.write_text(text)
    return page


def test_missing_book_warning_is_limited_to_wiring_endpoint(tmp_path, capsys):
    _content_file(tmp_path, '/tpsasps/ep_main.asp?book=EZZ&cell=10')

    assert wiring_sweep(tmp_path, ["vol_test"]) == 0
    assert "warning: 1 links to book EZZ" in capsys.readouterr().out


def test_non_wiring_leftover_with_missing_book_still_fails(tmp_path):
    _content_file(tmp_path, '/tpscontent/page.asp?book=EZZ')

    assert wiring_sweep(tmp_path, ["vol_test"]) == 1
