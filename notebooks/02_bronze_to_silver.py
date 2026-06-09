# Databricks notebook source
# MAGIC %md
# MAGIC # Bronze → Silver: IEEE-CIS Card Fraud
# MAGIC
# MAGIC Reads raw CSVs from ADLS Gen2 bronze layer, validates schema, joins
# MAGIC transactions with identity, and writes clean Parquet to the silver layer.
# MAGIC
# MAGIC **Run order:** This notebook must run before 03_silver_to_gold.

# COMMAND ----------

# Mount ADLS Gen2 using secrets stored in the fraud-platform scope
storage_account = dbutils.secrets.get(scope="fraud-platform", key="adls-account-name")
storage_key     = dbutils.secrets.get(scope="fraud-platform", key="adls-key")

spark.conf.set(
    f"fs.azure.account.key.{storage_account}.dfs.core.windows.net",
    storage_key,
)

BRONZE_DIR = f"abfss://bronze@{storage_account}.dfs.core.windows.net/ieee-cis"
SILVER_DIR = f"abfss://silver@{storage_account}.dfs.core.windows.net/ieee-cis"

print(f"Bronze : {BRONZE_DIR}")
print(f"Silver : {SILVER_DIR}")

# COMMAND ----------
# MAGIC %md ## 1. Load raw CSVs

# COMMAND ----------

txn_raw = (
    spark.read
    .option("header", "true")
    .option("inferSchema", "true")
    .csv(f"{BRONZE_DIR}/train_transaction.csv")
)

id_raw = (
    spark.read
    .option("header", "true")
    .option("inferSchema", "true")
    .csv(f"{BRONZE_DIR}/train_identity.csv")
)

print(f"Transactions : {txn_raw.count():,} rows  {len(txn_raw.columns)} cols")
print(f"Identity     : {id_raw.count():,} rows  {len(id_raw.columns)} cols")

# COMMAND ----------
# MAGIC %md ## 2. Schema validation — assert critical columns exist

# COMMAND ----------

REQUIRED_TXN = [
    "TransactionID", "isFraud", "TransactionDT", "TransactionAmt",
    "ProductCD", "card1", "card4", "card6", "addr1", "P_emaildomain",
]
REQUIRED_ID = ["TransactionID"]

missing_txn = [c for c in REQUIRED_TXN if c not in txn_raw.columns]
missing_id  = [c for c in REQUIRED_ID  if c not in id_raw.columns]

assert not missing_txn, f"Missing transaction columns: {missing_txn}"
assert not missing_id,  f"Missing identity columns: {missing_id}"
print("Schema validation passed.")

# COMMAND ----------
# MAGIC %md ## 3. Deduplicate on TransactionID

# COMMAND ----------

from pyspark.sql import functions as F

txn_dedup = txn_raw.dropDuplicates(["TransactionID"])
id_dedup  = id_raw.dropDuplicates(["TransactionID"])

dropped_txn = txn_raw.count() - txn_dedup.count()
dropped_id  = id_raw.count()  - id_dedup.count()
print(f"Dropped duplicates — transactions: {dropped_txn}  identity: {dropped_id}")

# COMMAND ----------
# MAGIC %md ## 4. Join transactions with identity (left join — identity is sparse)

# COMMAND ----------

silver = txn_dedup.join(id_dedup, on="TransactionID", how="left")
print(f"Silver rows : {silver.count():,}  cols : {len(silver.columns)}")

# COMMAND ----------
# MAGIC %md ## 5. Add wall-clock event timestamp
# MAGIC
# MAGIC `TransactionDT` is seconds since a reference epoch (not a real UNIX timestamp).
# MAGIC We fix the reference to 2017-12-01 00:00:00 UTC — the approximate start of
# MAGIC the IEEE-CIS dataset — so Feast can use it as `event_timestamp`.

# COMMAND ----------

REFERENCE_EPOCH = 1512086400  # 2017-12-01 00:00:00 UTC

silver = silver.withColumn(
    "event_timestamp",
    F.to_timestamp(F.from_unixtime(F.col("TransactionDT") + REFERENCE_EPOCH)),
)

# COMMAND ----------
# MAGIC %md ## 6. Cast card1 to integer (entity key used by Feast)

# COMMAND ----------

silver = silver.withColumn("card1", F.col("card1").cast("int"))

# COMMAND ----------
# MAGIC %md ## 7. Quick quality checks

# COMMAND ----------

total       = silver.count()
fraud_count = silver.filter(F.col("isFraud") == 1).count()
null_card1  = silver.filter(F.col("card1").isNull()).count()
null_ts     = silver.filter(F.col("event_timestamp").isNull()).count()

print(f"Total rows       : {total:,}")
print(f"Fraud rows       : {fraud_count:,}  ({100*fraud_count/total:.2f}%)")
print(f"Null card1       : {null_card1:,}")
print(f"Null event_ts    : {null_ts:,}")

assert null_ts == 0, "event_timestamp must not be null"

# COMMAND ----------
# MAGIC %md ## 8. Write Silver Parquet (partitioned by month)

# COMMAND ----------

silver_out = silver.withColumn(
    "year_month",
    F.date_format(F.col("event_timestamp"), "yyyy-MM"),
)

(
    silver_out.write
    .mode("overwrite")
    .partitionBy("year_month")
    .parquet(f"{SILVER_DIR}/transactions")
)

print(f"Written to {SILVER_DIR}/transactions")

# COMMAND ----------
# MAGIC %md ## Done
# MAGIC
# MAGIC Next step: run **03_silver_to_gold** to compute rolling-window features.
