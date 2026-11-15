"""Cleaning rules for LeRobot episode time-series (state, action, timestamps)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

from rde.quality.rules import CleaningRules, Reason

STATE = "observation_state"


@dataclass
class EpisodeOutput:
    clean_frames: DataFrame
    episodes: DataFrame
    rejected_frames: DataFrame  # has a `reason` column


def normalize_columns(raw: DataFrame) -> DataFrame:
    """observation.state -> observation_state, next.reward -> next_reward, etc."""
    return raw.select([F.col(f"`{c}`").alias(re.sub(r"[^0-9a-zA-Z]+", "_", c)) for c in raw.columns])


def _has_nan(col: str):
    return F.exists(F.col(col), lambda x: F.isnan(x))


def clean_episodes(raw: DataFrame, rules: CleaningRules) -> EpisodeOutput:
    df = normalize_columns(raw)

    # row rules
    dup_rank = F.row_number().over(Window.partitionBy("episode_index", "frame_index").orderBy("index"))
    df = df.withColumn("_dup_rank", dup_rank).withColumn(
        "reason",
        F.when(F.col(STATE).isNull() | F.col("action").isNull(), Reason.NULL_VALUES)
        .when(_has_nan(STATE) | _has_nan("action"), Reason.NAN_VALUES)
        .when(F.col("_dup_rank") > 1, Reason.DUPLICATE_FRAME),
    )

    # out-of-range: any state dimension beyond z_max std devs from the clean mean
    first = df.where(F.col("reason").isNull()).select(STATE).first()
    if first is not None:
        dims = len(first[0])
        stats = (
            df.where(F.col("reason").isNull())
            .select(
                *[F.mean(F.col(STATE)[i]).alias(f"m{i}") for i in range(dims)],
                *[F.stddev(F.col(STATE)[i]).alias(f"s{i}") for i in range(dims)],
            )
            .first()
        )
        conds = [
            F.abs(F.col(STATE)[i] - F.lit(stats[f"m{i}"])) > F.lit(rules.z_max * stats[f"s{i}"])
            for i in range(dims)
            if stats[f"s{i}"]
        ]
        if conds:
            out = conds[0]
            for c in conds[1:]:
                out = out | c
            df = df.withColumn(
                "reason",
                F.when(F.col("reason").isNull() & out, Reason.OUT_OF_RANGE_STATE).otherwise(F.col("reason")),
            )

    # action spikes are flagged and kept, so modeling teams decide what to do with them
    w_ep = Window.partitionBy("episode_index").orderBy("frame_index")
    df = (
        df.withColumn("_prev_action", F.lag("action").over(w_ep))
        .withColumn(
            "action_spike",
            F.coalesce(
                F.exists(
                    F.arrays_zip("action", "_prev_action"),
                    lambda z: F.abs(z["action"] - z["_prev_action"]) > rules.action_spike,
                ),
                F.lit(False),
            ),
        )
        .drop("_dup_rank", "_prev_action")
    )

    bad = df.where(F.col("reason").isNotNull())
    good = df.where(F.col("reason").isNull()).drop("reason")

    # episode rules
    w_ts = Window.partitionBy("episode_index").orderBy("timestamp")
    episodes = (
        good.withColumn("_dt", F.col("timestamp") - F.lag("timestamp").over(w_ts))
        .groupBy("episode_index")
        .agg(
            F.count("*").alias("num_frames"),
            F.min("frame_index").alias("min_frame"),
            F.max("frame_index").alias("max_frame"),
            F.max("_dt").alias("max_dt_s"),
            (F.max("timestamp") - F.min("timestamp")).alias("duration_s"),
            F.sum(F.col("action_spike").cast("int")).alias("action_spikes"),
        )
    )
    max_gap_s = rules.max_gap_frames / rules.fps
    episodes = episodes.withColumn(
        "reasons",
        F.array_compact(
            F.array(
                F.when(F.col("num_frames") < rules.min_episode_frames, F.lit(Reason.TOO_SHORT)),
                F.when(
                    F.col("max_frame") - F.col("min_frame") + 1 != F.col("num_frames"),
                    F.lit(Reason.FRAME_GAP),
                ),
                F.when(F.col("max_dt_s") > max_gap_s, F.lit(Reason.TIMESTAMP_GAP)),
            )
        ),
    ).withColumn("status", F.when(F.size("reasons") == 0, "clean").otherwise("rejected"))

    rejected_ids = episodes.where(F.col("status") == "rejected").select("episode_index", "reasons")
    clean = good.join(rejected_ids, "episode_index", "left_anti")
    dropped = (
        good.join(rejected_ids, "episode_index")
        .withColumn("reason", F.concat(F.lit("episode_"), F.array_join("reasons", "+")))
        .drop("reasons")
    )

    return EpisodeOutput(clean, episodes, bad.unionByName(dropped))
