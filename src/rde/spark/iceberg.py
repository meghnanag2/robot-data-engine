"""Iceberg table writes through the Glue catalog, idempotent per run."""

from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

CATALOG = "glue_catalog"


class IcebergWriter:
    def __init__(self, spark: SparkSession, database: str, dataset: str, run_id: str) -> None:
        self.spark, self.database, self.dataset, self.run_id = spark, database, dataset, run_id

    def name(self, table: str) -> str:
        return f"{CATALOG}.{self.database}.{table}"

    def write(self, df: DataFrame, table: str) -> None:
        """First run creates the table; later runs replace only this run's partition.

        Schema evolution: new columns in the data (e.g. a new quality metric) are added to the
        table instead of failing the write. Dropped columns are kept as nulls, never removed.
        """
        df = df.withColumn("dataset", F.lit(self.dataset)).withColumn("run_id", F.lit(self.run_id))
        target = self.name(table)
        if self.spark.catalog.tableExists(target):
            df = align_to_table(df, self.spark.table(target).schema)
            self.spark.sql(f"ALTER TABLE {target} SET TBLPROPERTIES ('write.spark.accept-any-schema'='true')")
            df.writeTo(target).option("merge-schema", "true").overwritePartitions()
        else:
            (
                df.writeTo(target)
                .partitionedBy("dataset", "run_id")
                .tableProperty("format-version", "2")
                .tableProperty("write.spark.accept-any-schema", "true")
                .create()
            )


def align_to_table(df: DataFrame, table_schema) -> DataFrame:
    """Order columns like the table (Iceberg checks order), fill missing ones with typed nulls,
    and append columns the table does not have yet so schema evolution can add them."""
    have = set(df.columns)
    cols = [
        F.col(f.name) if f.name in have else F.lit(None).cast(f.dataType).alias(f.name)
        for f in table_schema.fields
    ]
    known = {f.name for f in table_schema.fields}
    cols += [F.col(c) for c in df.columns if c not in known]
    return df.select(*cols)


def quarantine_rows(df: DataFrame, entity: str, id_col: str, reason_col: str = "reason") -> DataFrame:
    """Normalize rejected rows of any shape into the quarantine schema."""
    detail_cols = [c for c in df.columns if c not in (reason_col, "dataset", "run_id")]
    return df.select(
        F.lit(entity).alias("entity"),
        F.col(id_col).cast("string").alias("entity_id"),
        F.col(reason_col).alias("reason"),
        F.to_json(F.struct(*detail_cols)).alias("detail"),
        F.current_timestamp().alias("quarantined_at"),
    )
