import gzip
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from live_gzip import complete_lines


def test_growing_stream_needs_no_footer_and_never_invents_last_record(tmp_path):
    path = tmp_path / "live.gz"
    with path.open("wb") as output, gzip.GzipFile(fileobj=output, mode="wb") as writer:
        writer.write(b'{"received":1}\n{"received":2')
        writer.flush()
        assert list(complete_lines(path)) == ['{"received":1}\n']
        writer.write(b"}\n")
        writer.flush()
        assert list(complete_lines(path)) == ['{"received":1}\n', '{"received":2}\n']
    assert len(list(complete_lines(path))) == 2


def test_closed_stream_with_incomplete_line_is_data_quality_failure(tmp_path):
    path = tmp_path / "incomplete.gz"
    path.write_bytes(gzip.compress(b'{"received":1'))
    with pytest.raises(ValueError, match="incomplete JSONL"):
        list(complete_lines(path))


def test_corruption_is_not_silently_treated_as_live_eof(tmp_path):
    path = tmp_path / "bad.gz"
    path.write_bytes(b"not a gzip file")
    import zlib

    with pytest.raises(zlib.error):
        list(complete_lines(path))
