"""Runs the real Spark cleaning code on small synthetic data (skipped if pyspark is not installed)."""

import pytest

pyspark = pytest.importorskip("pyspark")

from pyspark.sql import SparkSession  # noqa: E402

from rde.quality.rules import CleaningRules  # noqa: E402
from rde.spark.episodes import clean_episodes  # noqa: E402
from rde.spark.frames import clean_frames, episode_time_ranges  # noqa: E402


@pytest.fixture(scope="module")
def spark():
    s = SparkSession.builder.master("local[1]").config("spark.ui.enabled", "false").getOrCreate()
    yield s
    s.stop()


def _episode_rows(ep, n, fps=10.0, start_index=0, skip_frame=None):
    rows = []
    for i in range(n):
        if i == skip_frame:
            continue
        rows.append((float(i) / fps, i, ep, start_index + i, [100.0 + i, 200.0], [1.0, 1.0]))
    return rows


def test_episode_rules(spark):
    rows = (
        _episode_rows(0, 20)  # clean
        + _episode_rows(1, 20, start_index=100, skip_frame=5)  # frame gap -> rejected episode
        + _episode_rows(2, 5, start_index=200)  # too short
    )
    rows.append((0.1, 1, 0, 999, [101.0, 200.0], [1.0, 1.0]))  # duplicate of ep 0 frame 1
    rows.append((2.5, 25, 0, 1000, None, [1.0, 1.0]))  # null state
    df = spark.createDataFrame(
        rows,
        "timestamp float, frame_index long, episode_index long, index long, "
        "`observation.state` array<float>, action array<float>",
    )

    out = clean_episodes(df, CleaningRules())
    eps = {r.episode_index: (r.status, r.reasons) for r in out.episodes.collect()}
    reasons = {r.reason for r in out.rejected_frames.select("reason").distinct().collect()}

    assert eps[0] == ("clean", [])
    assert eps[1][0] == "rejected" and "frame_gap" in eps[1][1]
    assert eps[2] == ("rejected", ["too_short"])
    assert {"duplicate_frame", "null_values"} <= reasons
    assert out.clean_frames.count() == 20


def test_frame_rules_and_episode_mapping(spark):
    meta = spark.createDataFrame(
        [(0, 0, 0, 0.0, 2.0), (1, 0, 0, 2.0, 4.0)],
        "episode_index long, `videos/cam/chunk_index` long, `videos/cam/file_index` long, "
        "`videos/cam/from_timestamp` double, `videos/cam/to_timestamp` double",
    )
    frames = spark.createDataFrame(
        [
            ("v", "cam", 0, 0, 0.0, "s3://a", 120.0, 40.0, 500.0, "aa"),
            ("v", "cam", 0, 0, 1.0, "s3://b", 120.0, 40.0, 500.0, "aa"),  # near duplicate
            ("v", "cam", 0, 0, 2.0, "s3://c", 5.0, 2.0, 500.0, "bb"),  # too dark: dark and flat
            ("v", "cam", 0, 0, 3.0, "s3://d", 120.0, 40.0, 1.0, "cc"),  # blurry
            ("v", "cam", 0, 0, 9.0, "s3://e", 120.0, 40.0, 500.0, "dd"),  # no episode
            ("v", "cam", 0, 0, 2.5, "s3://f", 120.0, 40.0, 500.0, "ee"),  # clean, episode 1
            ("v", "cam", 0, 0, 3.5, "s3://g", 250.0, 60.0, 500.0, "ff"),  # bright sim frame with detail
            ("v", "cam", 0, 0, 3.8, "s3://h", 252.0, 1.0, 500.0, "gg"),  # bright and flat: overexposed
        ],
        "source_video string, camera string, chunk_index long, file_index long, timestamp_s double, "
        "frame_s3 string, brightness double, contrast double, blur_score double, ahash string",
    )

    clean, rejected = clean_frames(frames, episode_time_ranges(meta), CleaningRules())
    got = {r.frame_s3: r.reason for r in rejected.collect()}
    assert got == {
        "s3://b": "near_duplicate",
        "s3://c": "too_dark",
        "s3://d": "blurry",
        "s3://e": "no_episode_match",
        "s3://h": "overexposed",
    }
    assert {(r.frame_s3, r.episode_index) for r in clean.collect()} == {
        ("s3://a", 0),
        ("s3://f", 1),
        ("s3://g", 1),
    }


def test_reads_lerobot_nested_layout(spark, tmp_path):
    """LeRobot v3 stores data/chunk-000/file-000.parquet; the job must find nested files."""
    nested = tmp_path / "data" / "chunk-000"
    nested.mkdir(parents=True)
    spark.createDataFrame([(1,), (2,)], "a long").coalesce(1).write.parquet(str(nested / "file-000"))
    df = spark.read.option("recursiveFileLookup", "true").parquet(str(tmp_path / "data"))
    assert df.count() == 2


def test_align_to_table_orders_fills_and_appends(spark):
    from rde.spark.iceberg import align_to_table

    table = spark.createDataFrame([], "a string, b double, c long").schema
    df = spark.createDataFrame([("x", 1.0, "new")], "a string, b double, z string").select("z", "b", "a")
    out = align_to_table(df, table)
    assert out.columns == ["a", "b", "c", "z"]
    row = out.first()
    assert (row.a, row.b, row.c, row.z) == ("x", 1.0, None, "new")
