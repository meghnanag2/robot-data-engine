"""Cleaning rules for extracted video frames, and mapping frames to episodes."""

from __future__ import annotations

import re

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

from rde.quality.rules import CleaningRules, Reason


def episode_time_ranges(episodes_meta: DataFrame) -> DataFrame | None:
    """LeRobot v3 meta/episodes stores, per camera, which file and time range each episode uses."""
    parts = []
    for c in episodes_meta.columns:
        m = re.match(r"videos/(.+)/from_timestamp$", c)
        if not m:
            continue
        cam = m.group(1)
        parts.append(
            episodes_meta.select(
                F.col("episode_index"),
                F.lit(cam).alias("r_camera"),
                F.col(f"`videos/{cam}/chunk_index`").cast("int").alias("r_chunk"),
                F.col(f"`videos/{cam}/file_index`").cast("int").alias("r_file"),
                F.col(f"`videos/{cam}/from_timestamp`").alias("r_from"),
                F.col(f"`videos/{cam}/to_timestamp`").alias("r_to"),
            )
        )
    if not parts:
        return None
    out = parts[0]
    for p in parts[1:]:
        out = out.unionByName(p)
    return out


def clean_frames(
    frames: DataFrame, ranges: DataFrame | None, rules: CleaningRules
) -> tuple[DataFrame, DataFrame]:
    frames = frames.drop("dataset", "run_id")
    if ranges is not None:
        frames = frames.join(
            ranges,
            (frames.camera == ranges.r_camera)
            & (frames.chunk_index == ranges.r_chunk)
            & (frames.file_index == ranges.r_file)
            & (frames.timestamp_s >= ranges.r_from)
            & (frames.timestamp_s < ranges.r_to),
            "left",
        ).drop("r_camera", "r_chunk", "r_file", "r_from", "r_to")
    else:
        frames = frames.withColumn("episode_index", F.lit(None).cast("long"))

    hash_rank = F.row_number().over(Window.partitionBy("source_video", "ahash").orderBy("timestamp_s"))
    frames = (
        frames.withColumn("_hash_rank", hash_rank)
        .withColumn(
            "reason",
            F.when(F.col("brightness") < rules.min_brightness, Reason.TOO_DARK)
            .when(F.col("brightness") > rules.max_brightness, Reason.OVEREXPOSED)
            .when(F.col("blur_score") < rules.min_blur_score, Reason.BLURRY)
            .when(F.col("_hash_rank") > 1, Reason.NEAR_DUPLICATE)
            .when(F.col("episode_index").isNull(), Reason.NO_EPISODE_MATCH),
        )
        .drop("_hash_rank")
    )
    clean = frames.where(F.col("reason").isNull()).drop("reason")
    rejected = frames.where(F.col("reason").isNotNull())
    return clean, rejected
