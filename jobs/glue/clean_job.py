"""Glue 5.0 entry point. All logic lives in the rde package (shipped via --extra-py-files)."""

import json
import sys

from awsglue.utils import getResolvedOptions
from pyspark.sql import SparkSession

from rde.quality.rules import CleaningRules
from rde.spark.episodes import clean_episodes
from rde.spark.frames import clean_frames, episode_time_ranges
from rde.spark.iceberg import IcebergWriter, quarantine_rows
from rde.storage import paths


def main() -> None:
    args = getResolvedOptions(sys.argv, ["DATA_BUCKET", "DATABASE", "DATASET", "RUN_ID", "RULES"])
    bucket, dataset, run_id = args["DATA_BUCKET"], args["DATASET"], args["RUN_ID"]
    rules = CleaningRules.from_json(args["RULES"])
    spark = SparkSession.builder.getOrCreate()
    writer = IcebergWriter(spark, args["DATABASE"], dataset, run_id)
    raw = f"s3://{bucket}/{paths.raw_prefix(dataset)}"
    # LeRobot v3 nests files as <dir>/chunk-NNN/file-NNN.parquet; without this Spark finds no files
    parquet = spark.read.option("recursiveFileLookup", "true")

    # 1. episode time-series
    ep = clean_episodes(parquet.parquet(f"{raw}data/"), rules)
    writer.write(ep.clean_frames, "episode_frames")
    writer.write(ep.episodes, "episodes")

    # 2. extracted frames
    frames = spark.read.json(f"s3://{bucket}/{paths.frame_meta_prefix(dataset, run_id)}")
    ranges = episode_time_ranges(parquet.parquet(f"{raw}meta/episodes/"))
    clean, rejected = clean_frames(frames, ranges, rules)
    writer.write(clean, "video_frames")

    # 3. quarantine (every rejected row, with a reason code)
    q = quarantine_rows(ep.rejected_frames, "episode_frame", "index").unionByName(
        quarantine_rows(rejected, "video_frame", "frame_s3")
    )
    writer.write(q, "quarantine")

    summary = spark.sql(
        f"SELECT entity, reason, count(*) AS n FROM {writer.name('quarantine')} "
        f"WHERE dataset = '{dataset}' AND run_id = '{run_id}' GROUP BY entity, reason ORDER BY n DESC"
    ).collect()
    print(json.dumps({"run_id": run_id, "quarantine": [r.asDict() for r in summary]}))


if __name__ == "__main__":
    main()
