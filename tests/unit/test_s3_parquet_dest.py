"""Unit tests for the S3-Parquet destination connector (Sprint 20, ADR-021)."""

import io

import pandas as pd
import pytest

from ai_etl.destinations.s3_parquet_dest import save_s3_parquet


def _parquet_bytes(df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    return buffer.getvalue()


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3Client:
    """Minimal in-memory stand-in for boto3's S3 client, no network I/O."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[tuple[str, str]] = []

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> None:  # noqa: N803
        self.put_calls.append((Bucket, Key))
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803
        return {"Body": _FakeBody(self.objects[(Bucket, Key)])}


def test_save_s3_parquet_succeeds(mocker) -> None:
    fake_client = _FakeS3Client()
    mocker.patch("boto3.client", return_value=fake_client)

    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    result = save_s3_parquet(df, bucket="my-bucket", key="warehouse/out.parquet")

    assert result["rows_loaded"] == 3
    assert result["destination"] == "s3://my-bucket/warehouse/out.parquet"
    assert fake_client.put_calls == [("my-bucket", "warehouse/out.parquet")]

    written = fake_client.objects[("my-bucket", "warehouse/out.parquet")]
    roundtrip = pd.read_parquet(io.BytesIO(written), engine="pyarrow")
    pd.testing.assert_frame_equal(roundtrip, df)


def test_save_s3_parquet_row_count_mismatch_raises(mocker) -> None:
    fake_client = _FakeS3Client()
    mocker.patch("boto3.client", return_value=fake_client)

    df = pd.DataFrame({"a": [1, 2, 3]})
    short_df = pd.DataFrame({"a": [1, 2]})

    # Simulate a corrupted/truncated write: the object actually stored has fewer
    # rows than what was requested to be written.
    mocker.patch.object(
        fake_client,
        "put_object",
        side_effect=lambda Bucket, Key, Body: fake_client.objects.update(  # noqa: N803
            {(Bucket, Key): _parquet_bytes(short_df)}
        ),
    )

    with pytest.raises(RuntimeError, match="Load count mismatch"):
        save_s3_parquet(df, bucket="my-bucket", key="out.parquet")
