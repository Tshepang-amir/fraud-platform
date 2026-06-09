# Databricks notebook source
# MAGIC %md
# MAGIC # Silver → Gold: Rolling-Window Feature Engineering
# MAGIC
# MAGIC Computes the nine card-level rolling features that the LightGBM / CatBoost
# MAGIC models expect at serving time.  Writes:
# MAGIC - **Gold Parquet** on ADLS for model training
# MAGIC - **Feast Parquet** (`data/feast/card_transaction_stats.parquet`) which
# MAGIC   `feast_materialise.py` reads to push features into the online store.
# MAGIC
# MAGIC **Prerequisite:** notebook 02_bronze_to_silver must have run first.

# COMMAND ----------

storage_account = dbutils.secrets.get(scope="fraud-platform", key="adls-account-name")
storage_key     = dbutils.secrets.get(scope="fraud-platform", key="adls-key")

spark.conf.set(
    f"fs.azure.account.key.{storage_account}.dfs.core.windows.net",
    storage_key,
)

SILVER_DIR = f"abfss://silver@{storage_account}.dfs.core.windows.net/ieee-cis"
GOLD_DIR   = f"abfss://gold@{storage_account}.dfs.core.windows.net/ieee-cis"

# Local DBFS path for the Feast parquet that feast_materialise.py reads
FEAST_LOCAL = "/dbfs/FileStore/fraud-platform/feast/card_transaction_stats.parquet"

print(f"Silver : {SILVER_DIR}")
print(f"Gold   : {GOLD_DIR}")
print(f"Feast  : {FEAST_LOCAL}")

# COMMAND ----------
# MAGIC %md ## 1. Read Silver

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql import Window

silver = spark.read.parquet(f"{SILVER_DIR}/transactions")
print(f"Silver rows : {silver.count():,}")

# COMMAND ----------
# MAGIC %md ## 2. Rolling-window features
# MAGIC
# MAGIC All windows are keyed on `card1` and ordered by `TransactionDT` (seconds),
# MAGIC so the row-based `rangeBetween` boundaries map to wall-clock seconds.

# COMMAND ----------

ONE_HOUR  =  3_600
ONE_DAY   = 86_400
SEVEN_DAY = 7 * ONE_DAY

# Window specs — ordered by epoch seconds so rangeBetween is in real time
w_1h  = Window.partitionBy("card1").orderBy("TransactionDT").rangeBetween(-ONE_HOUR,  0)
w_24h = Window.partitionBy("card1").orderBy("TransactionDT").rangeBetween(-ONE_DAY,   0)
w_7d  = Window.partitionBy("card1").orderBy("TransactionDT").rangeBetween(-SEVEN_DAY, 0)

# Preceding-row lag window (unbounded, for time-since-last-txn)
w_lag = Window.partitionBy("card1").orderBy("TransactionDT")

feats = (
    silver
    # ── transaction counts ──────────────────────────────────────────────────
    .withColumn("fe_card_txn_count_1h",  F.count("TransactionID").over(w_1h))
    .withColumn("fe_card_txn_count_24h", F.count("TransactionID").over(w_24h))
    .withColumn("fe_card_txn_count_7d",  F.count("TransactionID").over(w_7d))
    # ── amount stats (24 h) ─────────────────────────────────────────────────
    .withColumn("fe_card_amt_mean_24h", F.mean("TransactionAmt").over(w_24h))
    .withColumn("fe_card_amt_std_24h",  F.stddev("TransactionAmt").over(w_24h))
    .withColumn(
        "fe_card_amt_zscore_24h",
        F.when(
            F.col("fe_card_amt_std_24h") > 0,
            (F.col("TransactionAmt") - F.col("fe_card_amt_mean_24h"))
            / F.col("fe_card_amt_std_24h"),
        ).otherwise(F.lit(0.0)),
    )
    # ── time since last transaction (seconds) ───────────────────────────────
    .withColumn(
        "fe_time_since_last_txn",
        F.col("TransactionDT") - F.lag("TransactionDT", 1).over(w_lag),
    )
    # ── product entropy over 7 d (diversity of spend categories) ────────────
    # Approximated as count of distinct ProductCD values in window.
    # True Shannon entropy requires a UDF; distinct-count is a fast proxy.
    .withColumn(
        "fe_card_entropy_product_7d",
        F.approx_count_distinct("ProductCD").over(w_7d).cast("double"),
    )
)

# fe_time_since_last_txn is NULL for the very first transaction per card — fill with 0
feats = feats.fillna({"fe_time_since_last_txn": 0.0})

# COMMAND ----------
# MAGIC %md ## 3. Peer amount deviation
# MAGIC
# MAGIC For each transaction, how far is the amount from the mean amount of all
# MAGIC transactions with the same `card4` (Visa / Mastercard / etc.) on the same day?

# COMMAND ----------

feats = feats.withColumn("txn_date", F.to_date("event_timestamp"))

peer_stats = (
    feats
    .groupBy("card4", "txn_date")
    .agg(
        F.mean("TransactionAmt").alias("_peer_mean"),
        F.stddev("TransactionAmt").alias("_peer_std"),
    )
)

feats = (
    feats
    .join(peer_stats, on=["card4", "txn_date"], how="left")
    .withColumn(
        "fe_peer_amt_deviation",
        F.when(
            F.col("_peer_std") > 0,
            (F.col("TransactionAmt") - F.col("_peer_mean")) / F.col("_peer_std"),
        ).otherwise(F.lit(0.0)),
    )
    .drop("_peer_mean", "_peer_std", "txn_date")
)

# COMMAND ----------
# MAGIC %md ## 4. Select Gold columns

# COMMAND ----------

FEATURE_COLS = [
    "fe_card_txn_count_1h",
    "fe_card_txn_count_24h",
    "fe_card_txn_count_7d",
    "fe_card_amt_mean_24h",
    "fe_card_amt_std_24h",
    "fe_card_amt_zscore_24h",
    "fe_time_since_last_txn",
    "fe_card_entropy_product_7d",
    "fe_peer_amt_deviation",
]

gold = feats.select(
    "TransactionID",
    "card1",
    "isFraud",
    "TransactionAmt",
    "TransactionDT",
    "event_timestamp",
    *FEATURE_COLS,
)

null_counts = {c: gold.filter(F.col(c).isNull()).count() for c in FEATURE_COLS}
print("Null counts per feature:")
for feat, n in null_counts.items():
    print(f"  {feat}: {n:,}")

# COMMAND ----------
# MAGIC %md ## 5. Write Gold Parquet to ADLS

# COMMAND ----------

(
    gold.write
    .mode("overwrite")
    .partitionBy("isFraud")
    .parquet(f"{GOLD_DIR}/card_features")
)
print(f"Gold written to {GOLD_DIR}/card_features")

# COMMAND ----------
# MAGIC %md ## 6. Write Feast Parquet to DBFS
# MAGIC
# MAGIC `feast_materialise.py` reads `data/feast/card_transaction_stats.parquet` from
# MAGIC the local filesystem.  We write it to DBFS so it can be downloaded to the driver
# MAGIC or copied to the repo's `data/feast/` directory.

# COMMAND ----------

FEAST_ADLS_DIR = f"abfss://gold@{storage_account}.dfs.core.windows.net/feast"

feast_df = gold.select("card1", "event_timestamp", *FEATURE_COLS)

feast_df.coalesce(1).write.mode("overwrite").parquet(FEAST_ADLS_DIR)
print(f"Feast parquet written to: {FEAST_ADLS_DIR}")
print(f"Rows: {feast_df.count():,}")
print("Download the part-*.parquet file from the gold/feast/ folder in ADLS.")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Done
# MAGIC
# MAGIC **Next steps:**
# MAGIC 1. Download the Feast parquet from DBFS to the local repo:
# MAGIC    ```
# MAGIC    dbutils.fs.cp("dbfs:/FileStore/fraud-platform/feast/card_transaction_stats.parquet",
# MAGIC                  "file:/path/to/fraud-platform/data/feast/card_transaction_stats.parquet")
# MAGIC    ```
# MAGIC    Or use `databricks fs cp` from the CLI.
# MAGIC 2. Run `python -m src.train.feast_materialise` to push features to the online store.
# MAGIC 3. Run `python -m src.train.train_lgbm` to train the model.
