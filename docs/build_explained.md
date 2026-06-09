# Fraud Platform — What We Built and Why
### A complete technical walkthrough, Day 1 through Day 14

---

## Before Anything: What Are We Actually Building?

A bank processes thousands of card transactions every second. Most are legitimate. A small number are fraud. The bank needs to decide, **in under 100 milliseconds**, whether to approve or flag each transaction — before the customer even removes their card from the terminal.

That decision has to be:
- **Fast** — under 100ms, every time, at scale
- **Accurate** — catch as much fraud as possible without blocking legitimate customers
- **Explainable** — a compliance officer must be able to understand and approve every model before it goes live
- **Auditable** — every prediction must be traceable back to exactly what data and model version produced it

This project builds that entire system, from raw data to live API, using real production engineering patterns. Not a notebook. Not a demo. The real thing.

**The headline result we are building toward:**
> "On the IEEE-CIS holdout dataset at a fixed 0.1% false positive rate, the system catches 23% more fraud value than a monthly-retrained baseline — on Investec's reported card volume, that is an indicative R42 million per year in recovered exposure, at constant customer friction."

Every single technical decision in this project traces back to that sentence.

---

## The Dataset

We are using the **IEEE-CIS Fraud Detection dataset** — real transaction data from Vesta Corporation, a payments processor. It was released for a Kaggle competition but the data itself is from production.

| Fact | Value |
|---|---|
| Total transactions | 590,540 |
| Fraud transactions | 20,663 (3.5%) |
| Legitimate transactions | 569,877 (96.5%) |
| Features per transaction | 434 |
| Time span | 182 days |
| File size | ~650MB |

Those 434 features include: transaction amount, card details, device fingerprints, billing address patterns, Vesta's proprietary identity scores (the "V" features — 339 of them), email domains, and more.

The fraud rate of 3.5% means the dataset is **heavily imbalanced** — for every fraudulent transaction, there are 27 legitimate ones. This matters enormously for how we train models.

---

---

# DAY 1 — Laying the Foundation

## What problem does Day 1 solve?

Before writing a single line of ML code, a production project needs structure. Without it, you end up with a folder of scripts nobody can reproduce, secrets accidentally committed to GitHub, and tests that only work on the original developer's machine.

Day 1 is about setting the construction site up correctly before building begins.

---

## The Folder Structure

We created a strict folder layout that mirrors how real ML engineering teams organise production code:

```
fraud-platform/
├── src/                    ← all application code
│   ├── train/              ← model training scripts
│   ├── serve/              ← the API that scores transactions
│   ├── ingest/             ← data pipeline code
│   ├── monitor/            ← drift detection, retraining triggers
│   └── pipelines/          ← orchestration (Airflow DAGs)
├── tests/                  ← automated tests
│   ├── unit/               ← fast tests, no database needed
│   └── integration/        ← tests that require real infrastructure
├── feature_repo/           ← Feast feature store definitions
├── data/                   ← raw data, processed data, feature files
├── docs/                   ← documentation
├── terraform/              ← cloud infrastructure as code
└── notebooks/              ← exploratory analysis only
```

The rule: **notebooks are for exploration, `src/` is for production**. Nothing that runs in production lives in a notebook.

---

## pyproject.toml — The Project's Constitution

`pyproject.toml` is a single file that defines everything about the project: what Python packages it needs, what version of Python to use, and how every tool should behave.

```toml
[project]
name = "fraud-platform"
requires-python = ">=3.11"

[tool.ruff]       ← code quality rules
[tool.mypy]       ← type checking rules
[tool.pytest]     ← test configuration
```

**Why this matters:** Anyone who clones this repository runs one command (`pip install -e ".[dev]"`) and gets an identical environment with identical rules. No "it works on my machine."

---

## Ruff — Automatic Code Quality

**Ruff** is a code linter and formatter. A linter reads your code and flags problems before you run it — things like unused variables, inconsistent spacing, potential bugs, and security issues.

It runs automatically before every commit. If your code has issues, the commit is blocked. This enforces a consistent standard across the whole codebase without requiring code reviews for every minor formatting decision.

Think of it as grammar-check for code, but one that also catches logical errors.

---

## Pre-commit Hooks — Automated Gatekeeping

A **git hook** is a script that runs automatically at a specific moment in the git workflow. We installed hooks that run on every `git commit`:

1. **Ruff** — checks code quality and formatting
2. **mypy** — checks that variable types are consistent (more on this below)
3. **detect-secrets** — scans for accidentally committed passwords, API keys, or connection strings

The detect-secrets hook is particularly important. If you accidentally write a database connection string or API key directly in a source file and try to commit it, the hook blocks the commit and alerts you. In production systems, leaked credentials cause security breaches. We enforce this at the git level, not as a reminder.

---

## Docker and Docker Compose — Reproducible Infrastructure

**Docker** is software that runs applications in isolated containers. A container is like a sealed box — it contains the application and everything it needs to run (operating system libraries, configurations, dependencies). It runs identically on any machine.

**Docker Compose** is a tool for defining and running multi-container applications. You describe what containers you need in a `docker-compose.yml` file, and one command starts all of them.

Our `docker-compose.yml` defines two services:
1. **postgres** — a PostgreSQL database (the feature store online store, discussed in Day 5)
2. **fraud-scoring-api** — the FastAPI application that will serve model predictions

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: fraud-postgres
    ports:
      - "5433:5432"    # host port 5433 → container port 5432
```

Note: port 5433 instead of the default 5432. That's because Windows already runs a native PostgreSQL service on 5432. Docker maps to 5433 to avoid the conflict.

**Why Docker matters:** On Day 6 when this goes to Azure, the exact same container definition deploys to the cloud. Local development and production use the same environment.

---

## The `.gitignore` — What NOT to Track

`.gitignore` tells Git which files to ignore. We exclude:
- `data/raw/` — 650MB of CSV files don't belong in version control
- `.venv/` — the Python virtual environment (reproduced from `pyproject.toml`)
- `mlruns/` — MLflow experiment data (large, local only)
- `*.parquet` — generated data files
- `.env` — environment variable files that might contain secrets

---

## Day 1 Result

A clean, professional project skeleton with automated quality enforcement, containerised infrastructure, and security guardrails. No ML code yet — but the foundation is solid.

---

---

# DAY 2 — Understanding the Data

## What problem does Day 2 solve?

You cannot build a good fraud model without deeply understanding the data. Day 2 is about three things:

1. **Splitting the data correctly** — this has a critical correctness requirement
2. **Engineering better features** — turning raw transaction data into signals the model can use
3. **Validating data quality** — automated checks that the data is what we expect

---

## The Most Important Rule: Temporal Splitting

This is Rule 1 of the project, and violating it invalidates everything.

### Why you cannot shuffle

Standard machine learning courses teach `train_test_split(shuffle=True)` — randomly mix the data and split it 80/20. **This is catastrophically wrong for fraud detection.**

Fraud data has a time dimension. A fraudster who used technique X in January might be caught by February. If you shuffle the data, your training set contains some February fraud records and your test set contains some January ones. The model accidentally "sees the future" during training. It learns patterns that were only visible in hindsight.

In production, you will **always** be predicting the future from the past. The model must be evaluated the same way.

### What we did instead

```
All 590,540 transactions, sorted by TransactionDT (time)
│
├── Training set:   first 70%  → 413,378 transactions
│                  (oldest data — what the model learns from)
│
├── Validation set: next 15%  → 88,581 transactions
│                  (used during training to tune hyperparameters)
│
└── Test set:       final 15% → 88,581 transactions
                   (touched EXACTLY ONCE at the very end)
```

The test set is sealed. We do not look at it until the final model evaluation. If we evaluated on the test set repeatedly, we would unconsciously tune the model to it, and our reported performance would be optimistic.

`TransactionDT` in the dataset is seconds elapsed since 2017-01-01. The 182-day span gives us roughly January to June 2017.

---

## Feature Engineering — Turning Raw Data Into Signals

The raw dataset has 434 columns, but many of them are raw measurements that don't directly express what the model needs to know. **Feature engineering** is the process of creating new, more informative columns from the raw ones.

We created 9 engineered features. All are prefixed `fe_` to distinguish them from raw columns.

### Velocity features — "how busy is this card?"

```
fe_card_txn_count_1h   — number of transactions on this card in the last hour
fe_card_txn_count_24h  — number of transactions in the last 24 hours
fe_card_txn_count_7d   — number of transactions in the last 7 days
```

**Why these matter:** A card that makes 11 transactions in one hour is behaving very differently from one that makes 1. Fraudsters often run multiple small test transactions before a large one.

**How they're calculated:** For each transaction row, we look backward in time at all previous transactions on the same card within the time window and count them. This is called a **rolling window** calculation.

The key technical detail: we use `closed='left'` on the window, meaning the current transaction is NOT included in its own count. We are asking "how many transactions happened BEFORE this one in the window" — not including itself.

### Amount statistics — "is this amount normal for this card?"

```
fe_card_amt_mean_24h    — average transaction amount on this card in last 24h
fe_card_amt_std_24h     — standard deviation of amounts (how variable?)
fe_card_amt_zscore_24h  — how many standard deviations is THIS amount from the mean?
```

**The z-score** is the most powerful of these. If a card's average spend in the last 24 hours is $50 with a standard deviation of $10, and a new transaction comes in for $200, the z-score is (200 - 50) / 10 = **+15**. That's 15 standard deviations above normal — extremely suspicious.

If the z-score is near 0, the transaction is typical for this card. If it's large and positive, the amount is unusually high. If it's negative, it's unusually low.

### Timing — "when was the last time this card was used?"

```
fe_time_since_last_txn — seconds between this transaction and the previous one
```

120 seconds (2 minutes) between transactions is very different from 3,594 seconds (1 hour). Combined with velocity, this reveals behavioural patterns. A fraudster who has just stolen a card often uses it immediately and repeatedly.

### Entropy — "how varied are this card's purchases?"

```
fe_card_entropy_product_7d — Shannon entropy of product categories over 7 days
```

**Shannon entropy** is a measure from information theory. It quantifies how unpredictable or varied a sequence is. A card that only buys category W (the most common) has low entropy — predictable. A card that buys all five categories roughly equally has high entropy — varied.

Fraudsters tend to have low entropy because they make specific, targeted purchases. Legitimate cardholders show more varied spending patterns over a week.

### Peer comparison — "is this unusual compared to similar customers?"

```
fe_peer_amt_deviation — signed z-score vs other cardholders on same product type
```

This compares the transaction amount to the median and standard deviation of all transactions of the same product type in the training set. A transaction at a W-merchant for $500, when the typical W-merchant transaction is $45, is a strong signal.

**Critical detail:** The peer statistics (median and standard deviation per product type) are computed on the **training set only** and then applied to validation and test. If we computed them on the full dataset, the validation and test sets would contain information from their own future — data leakage.

---

## Great Expectations — Automated Data Quality

**Great Expectations** is a Python library for data validation. You define "expectations" about what your data should look like, and it checks them automatically.

We wrote 20 expectations:

| Expectation | What it checks |
|---|---|
| Row count between 500k–700k | Data is roughly the right size |
| TransactionID is unique | No duplicate transactions |
| isFraud mean between 2%–5% | Fraud rate is in expected range |
| TransactionAmt > 0 | No negative or zero amounts |
| ProductCD in {W, H, C, S, R} | Only valid product codes |

**Result: 20/20 PASS on 590,540 rows.**

This suite runs automatically in CI/CD. If someone swaps in a different dataset or a pipeline produces corrupted data, the suite catches it before any model training happens.

---

## EDA — Exploratory Data Analysis

We built a Jupyter notebook to explore the data visually before any modelling. Key findings:

**Class balance confirmed:** 3.50% fraud rate (20,663 fraud / 590,540 total). This means we set `scale_pos_weight = 27` in LightGBM — the model treats each fraud example as 27 times more important than a legitimate one, compensating for the imbalance.

**V-features (the 339 "V" columns):** These are Vesta's proprietary device and identity scores. They have 43% average null rate, with 159 features over 50% null. Decision: **do not impute these**. LightGBM handles missing values natively, and the fact that a value is missing is itself informative (it means Vesta couldn't calculate that score for this transaction).

**Leakage scan:** We computed the correlation between each raw feature and the fraud label. Features with suspiciously high correlation might be data leakage — information that wouldn't be available at prediction time. The highest correlations were V-features at ~0.38. These are legitimate: Vesta's identity scores exist in production at scoring time. Correlation of 0.38 on a 3.5%-fraud dataset is strong but plausible. Leakage would look like 0.80+.

---

## Day 2 Result

- Temporal split correctly implemented and validated
- 9 engineered features created and tested
- 25 unit tests passing, 55.69% code coverage
- 20/20 Great Expectations checks passing on real data
- EDA reviewed: class balance confirmed, leakage scan clear, V-features decision made

---

---

# DAY 3 — Training the Champion Model

## What problem does Day 3 solve?

We have clean data and good features. Now we train the first model — the **champion** — that will make real fraud decisions.

---

## Why LightGBM?

**LightGBM** (Light Gradient Boosting Machine) is a decision tree ensemble algorithm developed by Microsoft. It's one of the most widely used algorithms for tabular data in production systems.

### How gradient boosting works (simply)

Imagine you're trying to predict fraud and you start with a terrible model — it just guesses "not fraud" for everything. That model makes errors. You train a second, small model specifically to correct those errors. Then a third to correct the remaining errors. Then a fourth. And so on.

Each new model focuses on what the previous ones got wrong. After hundreds of iterations, you have an ensemble where each model is a specialist in a different type of mistake. Combined, they're very accurate.

**Gradient boosting** is the mathematical framework for doing this optimally. **LightGBM** is a fast, memory-efficient implementation of gradient boosting that:
- Handles missing values natively (crucial for our V-features)
- Handles categorical features without one-hot encoding
- Trains very quickly (minutes, not hours)
- Is widely deployed in production at companies like Booking.com, Alibaba, and Tencent

### Why not a neural network?

Neural networks require enormous amounts of data to outperform gradient boosting on tabular data. Our 590,000 rows is a large dataset by most standards, but not by neural network standards. For structured data with mixed types and missing values, LightGBM consistently matches or outperforms neural networks while being far faster to train and easier to explain.

---

## The Training Process

```python
Features used: 440
  (434 raw columns + 9 engineered features - 3 excluded: TransactionID, isFraud, TransactionDT)

Training rows:   413,378
Validation rows:  88,581

LightGBM key parameters:
  n_estimators:       2000    (maximum trees to build)
  learning_rate:      0.05    (how much each tree contributes)
  num_leaves:         127     (complexity of each tree)
  scale_pos_weight:   27      (fraud examples weighted 27x)
  early_stopping:     100     (stop if no improvement for 100 rounds)
```

**Early stopping:** We don't always need all 2000 trees. If the model stops improving on the validation set for 100 consecutive rounds, training stops. This prevents overfitting — building trees that memorise the training data rather than learning genuine patterns.

**A bug we found and fixed:** LightGBM's `early_stopping` by default monitors ALL metrics simultaneously — in our case, both AUC (accuracy) and binary log-loss. With `scale_pos_weight=27`, the log-loss is enormous at round 1 (because the heavily-weighted loss function makes early predictions look terrible), then improves rapidly. The early stopping logic saw log-loss as "best at round 1" and stopped training after just 101 rounds — producing a nearly useless model with AUC of 0.83. Fix: `first_metric_only=True`, which tells early stopping to only watch AUC.

---

## What is AUC?

**AUC** stands for Area Under the ROC Curve. It measures a model's ability to distinguish between the two classes (fraud vs legitimate), independent of any specific decision threshold.

- AUC = 0.5 means the model is no better than random guessing
- AUC = 1.0 means the model perfectly separates fraud from legitimate
- AUC = 0.92 means that if you randomly picked one fraud and one legitimate transaction, the model would rank the fraud higher 92% of the time

**Our champion: AUC = 0.920** — excellent performance.

---

## What is TPR at 0.1% FPR?

This is the metric that matters most for the business case.

**FPR (False Positive Rate)** — what fraction of legitimate transactions does the model incorrectly flag as fraud? At 0.1% FPR, only 1 in 1000 legitimate transactions is incorrectly blocked. This is the "customer friction budget" — how many innocent customers you're willing to inconvenience.

**TPR (True Positive Rate / Recall)** — of all the actual fraud transactions, what fraction does the model catch?

At a fixed 0.1% FPR, our champion catches **29.0% of fraud**. The target was 60%. That sounds bad, but it's actually expected — achieving 60% TPR at 0.1% FPR would require AUC above 0.97. Single models rarely achieve that. The ensemble approach (combining multiple models) is the path there.

---

## MLflow — The Experiment Tracking System

**MLflow** is an open-source platform for managing the machine learning lifecycle. Every training run automatically logs:

```
Parameters:     learning_rate, num_leaves, n_estimators, scale_pos_weight...
Metrics:        val_auc, val_tpr_at_001_fpr, val_brier
Tags:           developer, feature_set_version, split_strategy, dataset_version
Artifacts:      the model itself, calibration curve PNG, feature importance PNG
```

**Why this is non-negotiable:** Six months from now, if a compliance officer asks "which model made this prediction on this date, trained on what data, with what features?" — you need to be able to answer precisely. MLflow makes that possible. Without it, you have a model file with no history.

**The model signature:** We log the exact input format the model expects — column names, data types, and 5 example rows. This prevents the model from being deployed with the wrong input format.

---

## The Brier Score — Is the Model Calibrated?

AUC tells you if the model ranks fraud higher than legitimate. The **Brier score** tells you if the probability scores it outputs are meaningful.

If the model says "80% probability of fraud", it should be right about 80% of the time when it says that. A model that always says "50% probability" regardless of the situation has useless probability scores.

Brier score ranges from 0 (perfect) to 1 (worst). Our champion: **0.035** — well calibrated. The scores are trustworthy.

---

## Day 3 Result

| Metric | Target | Actual |
|---|---|---|
| val_auc | > 0.88 | **0.920** ✓ |
| val_brier | < 0.04 | **0.035** ✓ |
| val_tpr_at_001_fpr | > 0.60 | **0.290** (aspirational target, needs ensemble) |

LightGBM champion model saved to MLflow. Run ID: `9c599d91d7c546df82ad252837990c29`.

---

---

# DAY 4 — The Challenger Model and Head-to-Head Comparison

## What problem does Day 4 solve?

One model is not enough. In production ML systems, you always run a **champion-challenger** setup:
- The **champion** makes all real decisions
- The **challenger** runs in parallel, invisibly, processing the same transactions
- After a safe observation period, you compare them statistically and decide whether to promote the challenger

This is how you improve a model in production without risk. The challenger never affects customers until it has proven itself.

---

## CatBoost — A Different Approach to the Same Problem

**CatBoost** (Categorical Boosting) is a gradient boosting algorithm developed by Yandex (the Russian search engine company). It uses the same fundamental idea as LightGBM — building many trees sequentially, each correcting the last — but with different internal algorithms for handling categorical features.

### Key difference from LightGBM

LightGBM requires you to explicitly mark categorical columns, which it then label-encodes. CatBoost has a more sophisticated internal handling: it uses **ordered target statistics** to encode categoricals in a way that accounts for the target variable (fraud/legitimate) while avoiding data leakage within the training set.

Our implementation:
- Missing values in categorical columns get a sentinel value `"__NA__"`
- All 31 categorical columns are passed to CatBoost's `cat_features` parameter
- Training data wrapped in CatBoost's `Pool` object for memory efficiency

---

## The Comparison

After training both models on the same data (same temporal split, same features, same validation set), we compared them on four metrics:

| Metric | LightGBM Champion | CatBoost Challenger | Better |
|---|---:|---:|---|
| AUC | 0.9200 | 0.9179 | LightGBM |
| AUPRC | 0.5833 | 0.5575 | LightGBM |
| TPR @ 0.1% FPR | 0.2903 | 0.2535 | LightGBM |
| Brier score (lower = better) | 0.0349 | 0.0585 | LightGBM |

LightGBM wins on every metric. But is the difference real, or is it just noise?

---

## Bootstrap Confidence Intervals — Is the Difference Statistically Real?

**Bootstrap** is a statistical technique for estimating uncertainty. Here's how it works:

1. Take the validation set (88,581 transactions)
2. Randomly sample 88,581 rows **with replacement** (some rows will appear multiple times, some won't appear)
3. Compute TPR@0.1%FPR for both models on this sample
4. Record the difference (CatBoost − LightGBM)
5. Repeat 1,000 times
6. The distribution of 1,000 differences tells you how much uncertainty there is

**Our result:**
- Mean difference: −0.030 (CatBoost is 3.0 percentage points worse)
- 95% Confidence Interval: [−0.051, −0.007]

The upper bound of the CI is −0.007, which is **below zero**. This means: even in the most favourable scenario for CatBoost (the upper end of the confidence interval), it is still worse than LightGBM. The difference is statistically significant at 95% confidence.

**Decision: KEEP CHAMPION.** LightGBM is genuinely better.

---

## The Shadow Deployment Rule (Rule 5)

The CatBoost model is not thrown away. It becomes the shadow model. Rule 5 of this project states:

> "The challenger never makes live decisions. It writes to a shadow_decisions table only. The champion is the only model that returns a response to the caller."

This means:
- A customer's card is swiped
- LightGBM scores it → result returned to the payment terminal
- CatBoost also scores it → result written to a database table, never shown to anyone
- After two weeks of shadow operation, we compare the two models' real-world behaviour
- Only with statistical evidence and a human approval does the challenger ever get promoted

This is the production-grade way to update models without risk.

---

## Day 4 Result

- CatBoost challenger trained (22 minutes, CPU)
- Statistical comparison complete: KEEP_CHAMPION at 95% confidence
- Both models in MLflow with full audit trails
- Lift chart and calibration curve artifacts logged

---

---

# DAY 5 — The Feature Store

## What problem does Day 5 solve?

The model exists. It's good. But it cannot be deployed yet.

Here's the gap: during training, features like "how many transactions in the last hour" were computed from a CSV file sitting on disk. You could take all the time you needed. But in production, when a card swipes, you have **under 100 milliseconds total**. You cannot scan transaction history in that time.

You need a pre-computed, instantly accessible lookup table: *"For card 7919, here are its current statistics."*

That is what a **feature store** does.

---

## Three New Tools

### Tool 1: Feast

**Feast** (Feature Store) is an open-source framework used by Twitter, Gojek, Robinhood, and others in production. It does three things:

1. **Defines** what features exist, what entity they belong to, and how long they're valid
2. **Manages** two stores: a slow offline store (parquet files) for training, and a fast online store (Postgres) for serving
3. **Materialises**: copies data from offline → online, keeping only the latest values per entity

Feast is not a database. It's the layer that sits above the database and enforces consistency between training and serving.

### Tool 2: PostgreSQL

**PostgreSQL** (Postgres) is a relational database. It stores data in tables with rows and columns. You query it with SQL.

It is one of the most widely deployed databases in the world — used by Apple, Instagram, Spotify, and essentially every major tech company. It is open source, reliable, and extremely fast for lookup queries.

In our system, Postgres is the **online store** — the fast lookup table that the scoring API will query at runtime.

We run it inside Docker. The container is called `fraud-postgres` and it listens on port 5433 (5432 is taken by your Windows-native Postgres installation).

### Tool 3: pgAdmin

**pgAdmin** is a graphical interface for PostgreSQL. Instead of typing commands in a terminal, you get a visual tree of databases, schemas, and tables, plus a Query Tool where you write SQL.

When you connect pgAdmin to `fraud-platform (Docker)` on `localhost:5433`, you are directly browsing the database that Feast is writing to and the scoring API will read from.

---

## What We Defined in Feast

### Entity

An entity is the thing you're tracking features for — in our case, a card.

```python
Entity(
    name="card_id",
    join_keys=["card1"],       # the column name in the data
    value_type=ValueType.INT64  # card IDs are integers
)
```

### Feature View

A feature view is a group of related features attached to an entity, with a time-to-live (TTL) — how long a feature value remains valid before it expires.

```python
FeatureView(
    name="card_transaction_stats",
    entities=[card],
    ttl=timedelta(days=90),    # features expire after 90 days of inactivity
    schema=[
        Field(name="fe_card_txn_count_1h",       dtype=Float64),
        Field(name="fe_card_txn_count_24h",      dtype=Float64),
        Field(name="fe_card_txn_count_7d",       dtype=Float64),
        Field(name="fe_card_amt_mean_24h",       dtype=Float64),
        Field(name="fe_card_amt_std_24h",        dtype=Float64),
        Field(name="fe_card_amt_zscore_24h",     dtype=Float64),
        Field(name="fe_time_since_last_txn",     dtype=Float64),
        Field(name="fe_card_entropy_product_7d", dtype=Float64),
        Field(name="fe_peer_amt_deviation",      dtype=Float64),
    ],
    source=card_stats_source,  # the parquet file
    online=True,               # materialise to Postgres
)
```

### feature_store.yaml

This file tells Feast where everything lives:

```yaml
project: fraud_platform
registry: data/registry.db       # SQLite file tracking all definitions
provider: local
online_store:
  type: postgres
  host: localhost
  port: 5433
  database: fraud_platform
  user: fraud_user
  password: ${FEAST_POSTGRES_PASSWORD}   # from environment variable — no hardcoded secrets
  sslmode: disable
offline_store:
  type: file                     # parquet files on disk
entity_key_serialization_version: 3
```

Note `${FEAST_POSTGRES_PASSWORD}` — the password is never written into code. It's read from an environment variable at runtime. This is Rule 3 of the project: no secrets in code.

---

## The Materialisation Process — Step by Step

`store.materialize(start_date, end_date)` triggers a pipeline:

```
Step 1: LocalSourceReadNode
  → Reads card_transaction_stats.parquet
  → Filters rows where start_date ≤ event_timestamp ≤ end_date
  → Result: 501,959 rows

Step 2: LocalFilterNode
  → Removes rows outside the TTL window (90 days from end_date)
  → Result: 501,959 rows (all within TTL in our case)

Step 3: LocalDedupNode  ← most important step
  → Sorts by event_timestamp DESCENDING
  → Keeps only the FIRST row per card (= the most recent transaction)
  → Result: 12,917 rows (one per unique card)

Step 4: LocalOutputNode
  → For each of 12,917 cards × 9 features = 116,253 rows to write
  → Calls online_store.online_write_batch()
  → Executes: INSERT INTO fraud_platform_card_transaction_stats
              ON CONFLICT (entity_key, feature_name) DO UPDATE SET value = ...
  → If a card already exists in Postgres: update it. If not: insert it.

Result: 116,253 rows committed to Postgres.
```

The deduplication step is crucial. We don't want 500 rows for Card 7919 in the online store — one per transaction. We want **one row per (card, feature)**, always showing the card's most recent state. When the card swipes next, we look up those 9 values instantly.

---

## What the Postgres Table Actually Looks Like

The table `fraud_platform_card_transaction_stats` has this schema:

| Column | Type | Contents |
|---|---|---|
| entity_key | BYTEA | Binary serialisation of the card ID |
| feature_name | TEXT | e.g. "fe_card_txn_count_1h" |
| value | BYTEA | Binary (protobuf) serialisation of the float value |
| value_text | TEXT | NULL (used for string features, not ours) |
| event_ts | TIMESTAMPTZ | When this feature value was computed |
| created_ts | TIMESTAMPTZ | When it was written to the store |

**Why binary?** Feast serialises entity keys and values using **Protocol Buffers (protobuf)** — a compact binary format developed by Google. It is significantly smaller and faster to read than text formats like JSON. The trade-off is that you cannot read the values directly in pgAdmin — they look like gibberish. Feast decodes them back to numbers when you call `get_online_features()`.

This is why, when you look at the table in pgAdmin, you see unreadable bytes in the `entity_key` and `value` columns. The data is there — it just needs Feast to decode it.

### Real values from our online store

Here's what it looks like decoded (via `store.get_online_features()`):

| card1 | txns_in_dataset | txn_1h | txn_24h | txn_7d | amt_mean_24h | amt_zscore | secs_since_last |
|---|---|---|---|---|---|---|---|
| 7919 | 12,508 | 11 | 36 | 199 | $166.96 | −0.324 | 120s |
| 9500 | 11,881 | 4 | 82 | 496 | $135.67 | −0.337 | 2,173s |
| 15885 | 8,864 | 3 | 58 | 237 | $40.46 | −0.711 | 725s |

Card 7919 made 11 transactions in the last hour, 36 in the last day, 199 in the last week — and its last transaction was only 120 seconds before its most recent one. Its current amount was 0.3 standard deviations below its typical spending. Normal behaviour for a very active card.

---

## The Skew Test — Rule 2 of the Project

**Training-serving skew** is one of the most common and hardest-to-detect bugs in production ML.

Here's the scenario: during training, Card 7919's "transactions in last 24h" was calculated one way (from the parquet file using pandas rolling windows). During serving, the same feature is served from Postgres. If those two values ever differ — even slightly — the model is making decisions based on inputs it has never seen in that form. Predictions degrade silently. No error is thrown. Everything looks fine. But the model is wrong.

To prevent this, we built three automated tests in `tests/integration/test_feature_skew.py`:

### Test 1: `test_offline_equals_online_for_known_cards`

```
1. Pick 5 cards with ≥10 transactions (non-trivial rolling features)
2. Take each card's most recent row from the parquet file (offline)
3. Fetch the same card's features from Postgres via Feast (online)
4. Compare every feature value: |offline - online| must be < 0.000001
```

**Result: PASS for all 5 cards × 9 features = 45 comparisons.**

### Test 2: `test_all_feature_columns_present_online`

Verifies that all 9 expected features exist in the online response for every card. No missing columns.

### Test 3: `test_online_values_are_not_all_null`

Verifies that the online features are not all null/None. Catches the case where materialisation ran but wrote empty values.

**All 3 tests passed. 16.74 seconds.**

This is a mandatory gate. No deployment happens until this test suite passes.

---

## The Problem We Diagnosed: Stale Connections

During development, materialization was hanging indefinitely — sometimes for 10+ minutes without writing a single row. Here's exactly what was happening.

### Background: how Postgres handles concurrent writes

When you run `INSERT ... ON CONFLICT DO UPDATE` (called an "upsert"), Postgres locks the row being inserted or updated. Other transactions that try to modify the same row must wait for the lock to be released — either when the first transaction commits or when it rolls back.

This is normal and correct behaviour. It prevents two transactions from simultaneously updating the same row in conflicting ways.

### The bug: 19 zombie connections

Every time we killed a test script mid-execution (Ctrl+C, or force-stopped by the IDE), the Python process was killed immediately. Python's cleanup code (including psycopg3's connection teardown) never ran. The database connection remained open on the server side.

The Postgres server doesn't know the client is dead. It waits. And the transaction — with all its row locks — stays open indefinitely.

After several test runs, we had accumulated **19 zombie connections**, all holding row locks from their incomplete upsert operations. When a fresh materialization tried to upsert the same rows, it blocked on every single one of them. The queue never cleared.

### How we found it

We queried `pg_stat_activity` — Postgres's internal view of all active connections and what they're doing:

```sql
SELECT pid, state, wait_event_type, wait_event, query
FROM pg_stat_activity
WHERE datname = 'fraud_platform';
```

Output showed 19 connections all in `wait_event = 'transactionid'` or `wait_event = 'relation'` — meaning they were all blocked on locks held by other transactions. One connection (PID 50) was in `ClientRead` state — the server had processed its commands and was waiting for the client to send more. But the client (Feast's pipeline) was waiting for the server's responses. A deadlock.

### The fix

```sql
-- Kill all other connections to this database
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'fraud_platform'
AND pid != pg_backend_pid();

-- Clear any partial data
TRUNCATE TABLE public.fraud_platform_card_transaction_stats;
```

19 terminated. Table cleared. Fresh materialisation: completed in ~40 seconds.

### The lesson

**Always kill stale connections and truncate before re-running materialisation** if any previous attempt was interrupted. This is now documented in the project memory so future sessions know to do it automatically.

---

## The Full Data Flow — Where Everything Fits

```
OFFLINE WORLD (training time)              ONLINE WORLD (serving time, real-time)
──────────────────────────────             ──────────────────────────────────────

data/raw/                                  Card swipes at merchant
  train_transaction.csv (590k rows)                    │
  train_identity.csv                                   ▼
        │                                    FastAPI receives request
        ▼                                    {card1: 7919, amount: 150.00, ...}
temporal_split.py                                       │
  train (70%), val (15%), test (15%)                   ▼
        │                              Feast online_store.get_online_features(
        ▼                                  features=[...], entity_rows=[{'card1': 7919}]
feature_engineering.py                 )
  9 engineered features                               │
        │                                             ▼
        ▼                              9 features retrieved from Postgres in <5ms
data/feast/                            {fe_card_txn_count_1h: 11.0,
  card_transaction_stats.parquet        fe_card_amt_mean_24h: 166.96, ...}
  (501,959 rows)                                      │
        │                                             ▼
        ▼                              Combined with raw transaction features
feast apply                            (440 features total)
  Creates table in Postgres                           │
  Registers feature definitions                       ▼
        │                              LightGBM.predict_proba(features)
        ▼                                             │
feast materialize                                     ▼
  501,959 → dedup → 12,917 cards        fraud_probability = 0.023
  × 9 features = 116,253 rows                        │
  Written to Postgres                                 ▼
        │                              {"fraud_probability": 0.023,
        ▼                               "decision": "APPROVE",
Offline == Online?                      "model_version": "lgbm-champion-v1"}
  test_feature_skew.py: PASS ✓
        │
        ▼
Day 6: Deploy to Azure →
```

---

## pgAdmin — A Complete Guide to What You're Looking At

### The left panel: Object Explorer

This is a tree structure of everything in your Postgres server.

```
Servers (2)                     ← you have two Postgres servers registered
├── PostgreSQL 18               ← your Windows-native Postgres (port 5432)
│   └── Databases (3)
│       ├── Water               ← other projects of yours
│       ├── aquadecide
│       └── postgres
│
└── fraud-platform (Docker)     ← our project database (port 5433)
    └── Databases (2)
        ├── fraud_platform      ← THIS is where our data lives
        │   └── Schemas (1)
        │       └── public
        │           └── Tables
        │               └── fraud_platform_card_transaction_stats ← THE TABLE
        └── postgres            ← default system database (ignore)
```

### The Query Tool

When you open Tools → Query Tool with `fraud_platform` selected, you get a text editor connected to that specific database. You write SQL in the top half; results appear in the bottom half when you press F5.

### SQL you can run right now

**Count everything:**
```sql
SELECT COUNT(*) AS total_rows FROM fraud_platform_card_transaction_stats;
-- Should return: 116,253
```

**How many cards?**
```sql
SELECT COUNT(DISTINCT entity_key) AS unique_cards
FROM fraud_platform_card_transaction_stats;
-- Should return: 12,917
```

**Exactly 9 features per card?**
```sql
SELECT feature_name, COUNT(*) AS cards_with_this_feature
FROM fraud_platform_card_transaction_stats
GROUP BY feature_name
ORDER BY feature_name;
-- Should show 9 rows, each with count = 12,917
```

**When was the most recent feature computed?**
```sql
SELECT feature_name, MAX(event_ts) AS most_recent
FROM fraud_platform_card_transaction_stats
GROUP BY feature_name
ORDER BY feature_name;
```

**The raw binary data (what Feast actually stores):**
```sql
SELECT
    encode(entity_key, 'hex') AS card_key_hex,
    feature_name,
    encode(value, 'hex') AS value_hex,
    event_ts
FROM fraud_platform_card_transaction_stats
LIMIT 5;
```

This shows you exactly why the `entity_key` and `value` columns look like gibberish — they're binary data encoded as hexadecimal. The feature name and timestamp are human-readable. The actual float value is inside the binary blob, which Feast's Python code decodes using protobuf when you call `get_online_features()`.

---

## Day 5 Result

| Check | Result |
|---|---|
| Parquet file written | 501,959 rows |
| Online store populated | 116,253 rows (12,917 cards × 9 features) |
| Data durable across process boundaries | Confirmed (subprocess check) |
| `test_offline_equals_online_for_known_cards` | PASS |
| `test_all_feature_columns_present_online` | PASS |
| `test_online_values_are_not_all_null` | PASS |
| Rule 2 gate | **CLEARED** |

---

---

---

---

# DAY 6 — Cloud Infrastructure and the Pre-commit Battle

## What problem does Day 6 solve?

Everything built so far runs on a laptop. That is fine for development but not for production. A payment fraud scoring system needs to be:

- **Always available** — it cannot go down when your laptop lid closes
- **Scalable** — it needs to handle thousands of transactions per second, not dozens
- **Isolated from local configuration** — the model should behave identically on any machine
- **Governed** — audit trails need to live in a cloud storage account, not a local folder

Day 6 provisions all of the cloud infrastructure the system will run on, using Terraform.

But before any of that could happen, we had to fix a problem that had been quietly building since Day 1: the pre-commit hooks were broken.

---

## Part 1: Why the Pre-commit Hooks Were Failing

When we tried to make the first real Git commit containing all of Days 1-5 work, pre-commit ran its checks and found 56 errors. None of the code was broken — the logic was correct, all tests passed. The problem was the **type-checking layer**, and it revealed something interesting about how Python type systems work in practice.

### What mypy does and why it was unhappy

**mypy** is a static type checker. It reads Python code and verifies that when you write:

```python
def train(df: pd.DataFrame) -> float:
    return df["isFraud"].mean()
```

...the type annotations are consistent throughout the whole program. If somewhere else in the code you call `train(42)` and pass an integer instead of a DataFrame, mypy catches that before you ever run the code.

This sounds simple, but it has a complication: mypy needs to know the types of every function in every library you use. Libraries ship **type stubs** — files that describe the types of all their functions. Some libraries have complete stubs. Some have partial stubs. Some have no stubs at all.

### The 56 errors

Our 56 errors fell into several categories:

**Category 1: Missing stubs for entire libraries**

Libraries like `pandas`, `scikit-learn`, `matplotlib`, and `great_expectations` do not ship complete type stubs by default. When mypy encounters `import pandas`, it says: "I don't know the types of any pandas functions. I should flag this."

Fix: Add those libraries to an `ignore_missing_imports` override in `pyproject.toml`. This tells mypy: "Yes, I know these libraries aren't fully typed. Don't flag them."

**Category 2: Stale `# type: ignore` comments**

This is the subtle one. When we originally wrote `feature_engineering.py`, pandas had stubs and mypy was flagging `pd.Series` type arguments. We added `# type: ignore[type-arg]` comments to suppress those specific errors.

When we later added pandas to the `ignore_missing_imports` list, pandas was now treated as completely untyped (`Any`). The `type-arg` errors disappeared. But now those `# type: ignore` comments were suppressing errors that no longer existed — they were suppressing nothing.

mypy has a rule called `warn_unused_ignores`. When active, it flags any `# type: ignore` comment that isn't actually suppressing anything. Thirteen such comments had to be removed from `feature_engineering.py` alone.

**Category 3: The return type problem in evaluate.py**

`compare_models()` had return type `dict[str, object]`. Downstream in `train_catboost.py`, code was doing:

```python
result = compare_models(...)
auc = result["LightGBM_metrics"]["auc"]   # ERROR
```

When the return type is `dict[str, object]`, subscripting returns `object`. And `object["auc"]` is invalid — the `object` type doesn't support subscripting.

Fix: Change the return type to `dict[str, Any]`. `Any` is the escape hatch in Python's type system — it means "I know what this is at runtime, but I can't express the shape statically." Subscripting `Any` is always valid.

**Category 4: The Great Expectations problem**

`bronze_validation.py` imports expectation classes from Great Expectations. GE 1.17 has type stubs, but they are incomplete — the stubs don't explicitly export all the expectation class names. This produced 15 `attr-defined` errors.

Fix: Add a per-module override for `bronze_validation.py` with `ignore_errors = true`. This is the nuclear option — suppress all mypy errors for that one file. It's justified here because the library's incomplete stubs are the problem, not our code.

### The Windows path problem

The pre-commit hooks for mypy and detect-secrets use Python from the local virtual environment (`.venv`). The hook configuration looked like this:

```yaml
- id: mypy-type-check
  entry: .venv/Scripts/python.exe -m mypy
```

On macOS and Linux, this works. On Windows, it fails with `[WinError 2] The system cannot find the file specified`.

**Why?** Windows's `CreateProcess` API — the underlying system call that creates new processes — does not resolve relative paths that use forward slashes. It requires backslashes in relative paths.

But there's a second layer: pre-commit uses Python's `shlex.split()` to parse the `entry` string. `shlex` defaults to POSIX mode, where a backslash is an escape character. So `.venv\Scripts\python.exe` gets parsed as `.venvScriptspython.exe` — the backslashes are consumed as escape characters.

The solution is double-backslash in the YAML:

```yaml
entry: .venv\\Scripts\\python.exe -m mypy
```

YAML passes `\\` as a literal two-character sequence. `shlex.split()` sees `\\` and interprets it as a single escaped backslash, producing `\`. Windows `CreateProcess` receives `.venv\Scripts\python.exe` — a valid relative path with a backslash.

### The BOM problem with detect-secrets

**detect-secrets** scans all files for credential-like strings (passwords, API keys, connection strings) and compares them against a baseline file. If a new secret appears that wasn't in the baseline, the commit is blocked.

We regenerated the baseline with PowerShell:

```powershell
$output = & ".\.venv\Scripts\detect-secrets.exe" scan
$output | Out-File .secrets.baseline -Encoding utf8
```

This failed with "Unable to read baseline." The baseline was valid JSON but detect-secrets couldn't parse it.

**Why?** PowerShell 5.1's `Out-File -Encoding utf8` adds a **UTF-8 BOM** (Byte Order Mark) at the start of the file — three hidden bytes: `0xEF 0xBB 0xBF`. These bytes are invisible in most text editors but cause JSON parsers to fail because the file doesn't start with `{`.

Fix: Use .NET directly to write without BOM:

```powershell
$encoding = New-Object System.Text.UTF8Encoding $false   # $false = no BOM
[System.IO.File]::WriteAllText("$pwd\.secrets.baseline", ($output | Out-String), $encoding)
```

With all hooks fixed, the commit succeeded. 41 unit tests + 3 integration tests, all passing. First clean commit of Days 1-5 work.

---

## Part 2: Terraform — Infrastructure as Code

### What is Terraform?

**Terraform** is a tool for defining cloud infrastructure in code. Instead of clicking through the Azure portal to create resources, you write configuration files that describe exactly what you want, and Terraform creates it.

```hcl
resource "azurerm_storage_account" "main" {
  name                     = "stfraudf95d0b0e"
  resource_group_name      = azurerm_resource_group.main.name
  location                 = "southafricanorth"
  account_tier             = "Standard"
  account_replication_type = "LRS"
  is_hns_enabled           = true   ← ADLS Gen2 (hierarchical namespace)
}
```

**Why this matters:**
- The infrastructure is documented in code and version-controlled in Git
- Destroying and recreating the environment is one command (`terraform destroy` then `terraform apply`)
- Another team member can provision an identical environment without following a manual checklist
- Changes are shown as a plan before any action is taken — you see "3 resources to add, 1 to change, 0 to destroy" before anything happens

### Terraform state — how Terraform knows what already exists

Terraform needs to track what it has already created. It keeps this in a **state file** — a JSON document mapping each resource in your code to its real ID in Azure.

We store this state file in Azure itself, in a separate storage account (`stterraform0dp0eo`). This means:
- The state is not on anyone's laptop (if your laptop is lost, the state isn't)
- Multiple people can run Terraform without overwriting each other's state
- The state file is protected by Azure access controls

This is configured in `infra/backend.hcl`:

```hcl
resource_group_name  = "rg-tfstate-fraud"
storage_account_name = "stterraform0dp0eo"
container_name       = "tfstate"
key                  = "fraud-platform.tfstate"
```

### The plan → apply workflow

Terraform works in two stages:

**Stage 1: `terraform plan`**
Terraform reads your configuration, reads the current state, compares them, and produces a detailed plan of what it will do. No changes are made. You review it.

```
Plan: 23 to add, 0 to change, 0 to destroy.
```

**Stage 2: `terraform apply`**
Terraform executes the plan. Each resource is created in the right order (it knows that a database must be created before a firewall rule on that database). It shows progress in real time.

---

## The Six Infrastructure Modules

We organised the infrastructure into six modules — self-contained groups of related resources.

### Module 1: Data Lake (ADLS Gen2)

**Azure Data Lake Storage Gen2** is Azure's cloud object storage with a hierarchical file system. Think of it like a USB drive in the cloud — but one that supports file paths, permissions, and transactions at petabyte scale.

The "Gen2" refers to a feature called **Hierarchical Namespace (HNS)**. Regular Azure Blob Storage treats all data as a flat list of keys (like a dictionary). ADLS Gen2 treats data as an actual folder hierarchy. This enables atomic directory operations — renaming a folder with 1 million files happens in one metadata operation, not 1 million rename operations. Delta Lake (used in Day 7) requires this.

We created three **filesystems** (the ADLS term for a top-level folder):

```
stfraudf95d0b0e/
├── bronze/     ← raw data exactly as it arrived from Event Hubs
├── silver/     ← cleaned, validated data (no duplicates, clipped negatives)
└── gold/       ← aggregated, feature-ready data (one row per card, latest stats)
```

The Bronze → Silver → Gold progression is a standard data engineering pattern called the **medallion architecture**. Raw data is never modified — it's preserved in Bronze exactly as received. Each layer adds trust and value.

### Module 2: PostgreSQL Flexible Server

This is the cloud-managed Postgres database that replaces the Docker container on your laptop. Azure manages backups, patches, and high availability. You just use it.

**Tier: B_Standard_B1ms** — one virtual CPU, 2GB RAM. This is the cheapest tier that supports stopping (pausing) the server when not in use. A paused server costs nothing while stopped. This is critical for staying under the $45 budget.

We also enabled the **pgvector extension** — a Postgres extension that adds vector similarity search. This is needed for the online feature store (Feast stores feature values in Postgres) and will be used in later days for embedding-based fraud detection experiments.

The admin password is generated by Terraform using a random password resource and immediately stored in Key Vault. It is never written into any file or seen by any human.

### Module 3: Event Hubs

**Azure Event Hubs** is a high-throughput message streaming service — like a conveyor belt for events. A transaction happens at a merchant terminal → an event is published to Event Hubs → a consumer (Databricks streaming job) reads it within seconds → the feature store is updated.

**Tier: Basic** — the cheapest tier, priced per million events (not per hour). A payment system sending 1,000 transactions per second for 24 hours produces 86.4 million events — about $1.30 at Basic pricing. For our development workload (hundreds of events, not millions), the cost is effectively zero.

We created one Event Hub called `transactions`, with two **SAS rules** (Shared Access Signatures):
- `producer` — write-only access (the Azure Function that publishes events)
- `consumer` — read-only access (the Databricks job that consumes events)

Least-privilege access: each component only has the permission it needs.

### Module 4: Key Vault

**Azure Key Vault** is a secrets manager. Instead of storing the Postgres password in an environment variable or a config file, you store it in Key Vault and retrieve it at runtime with:

```python
secret = keyvault_client.get_secret("postgres-password")
```

Access to Key Vault is controlled by Azure RBAC. A Container App can be given exactly the permission to read exactly the `postgres-password` secret — nothing else.

We stored two secrets:
- `postgres-password` — the auto-generated Postgres admin password
- `eventhub-connection-string` — the Event Hubs connection string (used by producers)

Key Vault is free for development use (10,000 secret operations per month included).

### Module 5: Container Registry + Container Apps

**Azure Container Registry (ACR)** stores Docker images. When we `docker push` the fraud scoring API image, it goes to ACR. When a Container App deploys, it pulls from ACR.

**Azure Container Apps** is a managed platform for running Docker containers. You give it an image, CPU/memory requirements, and scaling rules. It handles everything else: deploying, routing, health checks, and scaling.

The crucial feature for cost control: **scale-to-zero**. When no requests are arriving, Container Apps scales the container down to zero replicas — running nothing. When a request arrives, it scales back up in a few seconds. An idle Container App costs nothing.

This is why we chose Container Apps over AKS (Azure Kubernetes Service), which would cost $800/month minimum for a node pool regardless of usage.

### Module 6: Monitoring

**Log Analytics Workspace** is the centralised logging backend. Everything — Container App logs, function execution traces, database diagnostics — flows into Log Analytics and is queryable with a SQL-like language called KQL.

**Application Insights** is the application-level observability layer. It tracks:
- Request rates and latency (p50, p95, p99)
- Dependency calls (how long each Postgres or Key Vault call takes)
- Exceptions and error rates
- Custom metrics (fraud score distributions, model versions)

Both resources charge by data volume ingested. At development volumes (tens of requests, not millions), cost is effectively zero.

---

## The Apply Run: What Terraform Actually Did

The `terraform apply` command provisioned all 23 resources in about 8 minutes. The slowest resource was the PostgreSQL Flexible Server (5 minutes) — Azure has to provision a virtual machine, install Postgres, configure networking, and run health checks.

```
Apply complete! Resources: 23 added, 0 changed, 0 destroyed.

postgres_server_fqdn      = psql-fraud-f95d0b0e.postgres.database.azure.com
keyvault_uri              = https://kv-fraud-f95d0b0e.vault.azure.net/
data_lake_storage_account = stfraudf95d0b0e
eventhub_namespace_name   = evhns-fraud-f95d0b0e
container_registry        = acrfraudf95d0b0e.azurecr.io
resource_suffix           = f95d0b0e
```

The `f95d0b0e` suffix is a random 4-byte hex string generated by Terraform's `random_id` resource. Azure requires globally unique names for storage accounts, Key Vaults, and container registries. Appending a random suffix to `st-fraud-` guarantees uniqueness without manual coordination.

---

## What Couldn't Be Automated: OIDC

The ideal GitHub Actions setup uses **OpenID Connect (OIDC) federation** — a way for GitHub Actions workflows to authenticate to Azure without storing any long-lived credentials. GitHub gets a short-lived token, Azure validates it came from your specific repository, and access is granted.

This requires creating an **App Registration** in Azure Active Directory (now called Microsoft Entra ID). However, the Azure for Students subscription is linked to the University of Cape Town's managed Entra ID tenant, and UCT's IT administrators have disabled App Registration creation for student accounts. The CLI returned a 401 "Insufficient privileges" error; the portal returned the same.

This is documented and will be set up when the project migrates to a personal Azure account for the final production demo. For all Day 7 onward work, deployments are made directly via the Azure CLI (`az`) — equivalent security for a solo development project.

---

## The Resource Naming Convention

Every resource in this project follows a pattern:

```
{type-prefix}-{project}-{random-suffix}
```

Examples:
- `rg-fraud-platform` — resource group
- `psql-fraud-f95d0b0e` — PostgreSQL server
- `kv-fraud-f95d0b0e` — Key Vault
- `evhns-fraud-f95d0b0e` — Event Hubs namespace

This makes resources immediately identifiable in the Azure portal even when you have many subscriptions. The prefix tells you the resource type; the suffix differentiates instances. It also prevents naming collisions across Azure's global namespace.

---

## Day 6 Result

| Infrastructure component | Resource | Status |
|---|---|---|
| Resource group | rg-fraud-platform | ✓ Live |
| Data Lake (Bronze/Silver/Gold) | stfraudf95d0b0e | ✓ Live |
| PostgreSQL Flexible B1ms PG16 | psql-fraud-f95d0b0e | ✓ Live |
| Event Hubs Basic + transactions hub | evhns-fraud-f95d0b0e | ✓ Live |
| Key Vault + 2 secrets | kv-fraud-f95d0b0e | ✓ Live |
| Container Registry Basic | acrfraudf95d0b0e.azurecr.io | ✓ Live |
| Container Apps Environment | cae-fraud-f95d0b0e | ✓ Live |
| Log Analytics + Application Insights | law-fraud-f95d0b0e | ✓ Live |
| Terraform remote state | stterraform0dp0eo | ✓ Live |

Pre-commit hooks: all passing. First clean Git commit made.
Total Azure spend: ~$0 (resources provisioned end of day; Postgres accrues ~$0.43/day when running — stop it when not in use).

---

---

# The Big Picture: Days 1–6 as a System

```
DAY 1          DAY 2              DAY 3           DAY 4           DAY 5           DAY 6
Scaffold   →   Data & Features →  Champion    →   Challenger  →   Feature Store → Cloud Infra
                                  LightGBM        CatBoost        Feast + Postgres Terraform
                                  AUC: 0.920      AUC: 0.918      116,253 rows    23 resources
                                                  KEEP CHAMPION   Skew test: PASS South Africa
```

**What's been built:**
- A reproducible, quality-enforced codebase with all pre-commit hooks passing
- A correctly-split dataset with 9 engineered features and 20/20 data quality checks
- A LightGBM champion model with AUC 0.920 and full MLflow audit trail
- A CatBoost challenger model with statistical comparison proving the champion is better
- A Feast feature store with Postgres online store serving features in milliseconds
- A mandatory skew test preventing training-serving mismatch from reaching production
- All cloud infrastructure provisioned as code: ADLS Gen2, PostgreSQL, Event Hubs, Key Vault, Container Registry, Container Apps, monitoring

---

---

# Day 7 — Azure Databricks + Cloud Data Pipeline Setup

**Date completed:** 2026-05-06
**What this day delivered:** Databricks workspace provisioned, IEEE-CIS data uploaded to the cloud data lake, Databricks secrets configured, and the full Bronze → Silver → Gold pipeline written as cloud notebooks.

---

## What Is Databricks and Why Do We Need It?

Your laptop has about 16 GB of RAM. The IEEE-CIS dataset has 590,540 transactions across 434 columns. Loading it, joining it with the identity table, and computing rolling-window features across every card would take 30+ minutes on a laptop and might crash it entirely.

Databricks solves this with **Apache Spark** — a distributed computing engine that splits the data into chunks and processes them in parallel across multiple CPU cores. What takes 30 minutes on a laptop takes 5 minutes on a Spark cluster.

Think of it like this:

```
Laptop (single worker)          Databricks cluster (parallel workers)
─────────────────────           ────────────────────────────────────
You process row 1               Worker 1 processes rows 1–147,000
Then row 2                      Worker 2 processes rows 147,001–294,000
Then row 3          vs.         Worker 3 processes rows 294,001–441,000
...                             Worker 4 processes rows 441,001–590,540
590,540 rows later              All done simultaneously
```

---

## What Was Built in Day 7

### Azure Databricks Workspace

Provisioned through the Azure portal (manual — not Terraform, because the student subscription requires UCT approval for Databricks resources):

| Setting | Value |
|---|---|
| Workspace name | `adb-fraud-platform` |
| Pricing tier | Trial (Premium, 14-day free DBUs) |
| Region | South Africa North |
| Workspace URL | `https://adb-7405604945524635.15.azuredatabricks.net` |

**What is a DBU?** Databricks Unit — the billing unit for compute time. The Trial gives you free DBUs for 14 days. After that, you pay per DBU consumed. Shutting the cluster off when not in use stops DBU consumption.

### The Cluster

A cluster is the actual virtual machine (or group of machines) that runs your notebooks:

| Setting | Value | Why |
|---|---|---|
| Machine type | Standard_D4ds_v4 | 4 cores, 16 GB RAM — enough for 590k rows |
| Runtime | 14.3 LTS (Spark 3.5.0, Python 3.11) | LTS = Long Term Support, stable for 3 years |
| Mode | Single node | Driver and workers on the same machine — simpler for dev |
| Auto-terminate | 30 minutes | Stops automatically when idle — saves DBUs |
| Cost | 0.75 DBU/h | ~$0.30/hr during Trial (free) |

**Important:** The cluster turns off automatically after 30 minutes of inactivity. You always need to click **Start** before running a notebook. This is intentional — leaving a cluster running 24/7 would consume your entire DBU budget overnight.

### IEEE-CIS Data Uploaded to ADLS

The raw dataset was uploaded to the Bronze layer of your data lake using the Python SDK (the `az` CLI was blocked by the Zutari corporate SSL proxy):

| File | Size | Rows | Location in ADLS |
|---|---|---|---|
| `train_transaction.csv` | 683.4 MB | 590,540 | `bronze/ieee-cis/train_transaction.csv` |
| `train_identity.csv` | 26.5 MB | 144,233 | `bronze/ieee-cis/train_identity.csv` |

**Why the Python SDK instead of az CLI?** The Zutari corporate network uses an SSL inspection proxy. The `az` CLI doesn't respect `REQUESTS_CA_BUNDLE` and fails with `CERTIFICATE_VERIFY_FAILED`. The Python `azure-storage-file-datalake` SDK accepts `connection_verify=False` as a parameter, bypassing the proxy inspection.

### Databricks Secret Scope

A secret scope is a named vault inside Databricks where you store credentials. Notebooks read from it using `dbutils.secrets.get()` — the actual values are never printed or logged.

Three secrets stored in scope `fraud-platform`:

| Key | Value stored | Why |
|---|---|---|
| `adls-account-name` | `stfraudf95d0b0e` | The storage account name — notebooks need this to build the ADLS path |
| `adls-key` | Storage account key | The password that grants read/write access to ADLS |
| `eventhub-producer-conn` | Event Hubs connection string | For the streaming demo producer |

These were fetched from Azure Key Vault (where Terraform stored them) using the Python SDK with `connection_verify=False` and pushed to Databricks via REST API.

---

## The Three Notebooks Written

### notebooks/02_bronze_to_silver.py — Read, Clean, Join

This notebook's job is to take the raw CSVs (Bronze) and produce a clean, validated, joined dataset (Silver).

**What it does step by step:**

```
Step 1: Mount ADLS
   - Reads adls-account-name and adls-key from Databricks secrets
   - Configures Spark to authenticate to ADLS using those credentials
   - No password ever appears in the notebook code

Step 2: Load raw CSVs
   - Reads train_transaction.csv  → 590,540 rows, 394 columns
   - Reads train_identity.csv     → 144,233 rows, 41 columns

Step 3: Schema validation
   - Checks that critical columns exist (TransactionID, isFraud, card1, etc.)
   - If any are missing → notebook fails with a clear error
   - This prevents silent data corruption downstream

Step 4: Deduplicate
   - Removes any duplicate TransactionIDs
   - In real banking data, network retries can cause duplicate events

Step 5: Join transactions with identity
   - LEFT JOIN on TransactionID
   - LEFT because identity data is sparse — only 24% of transactions have identity rows
   - Result: 590,540 rows, 436 columns (all transactions, identity columns filled where available)

Step 6: Add event_timestamp
   - TransactionDT in IEEE-CIS is seconds from an unknown reference point
   - We fix the reference to 2017-12-01 00:00:00 UTC
   - Converts to a real timestamp so Feast can use it

Step 7: Write Silver Parquet
   - Written to: silver container, ieee-cis/transactions/
   - Partitioned by year_month (e.g. year_month=2017-12/)
   - Partitioning means "if I only need December 2017, only read that folder"
   - Parquet format instead of CSV = 10x smaller file, 5x faster to read
```

### notebooks/03_silver_to_gold.py — Feature Engineering at Scale

This notebook reads Silver and computes the 9 rolling-window features that the machine learning model needs.

**The 9 features and how they're computed:**

```
For every transaction, look back at that card's history and compute:

1. fe_card_txn_count_1h    — how many times has this card transacted in the last hour?
2. fe_card_txn_count_24h   — how many times in the last 24 hours?
3. fe_card_txn_count_7d    — how many times in the last 7 days?
4. fe_card_amt_mean_24h    — what is this card's average spend in the last 24 hours?
5. fe_card_amt_std_24h     — how variable is this card's spend in the last 24 hours?
6. fe_card_amt_zscore_24h  — is THIS transaction's amount unusually large for this card?
                             (zscore = (amount - mean) / std)
7. fe_time_since_last_txn  — how many seconds since this card last transacted?
8. fe_card_entropy_product_7d — how many different product types has this card bought
                                in the last 7 days? (diversity measure)
9. fe_peer_amt_deviation   — how does this transaction's amount compare to other cards
                             of the same type (Visa/Mastercard) on the same day?
```

**Why are these features fraud-detecting?** A fraudster who steals a card typically:
- Makes many transactions quickly (high `txn_count_1h`)
- Spends much more than the card's normal pattern (high `amt_zscore_24h`)
- Buys across many different product types (high `entropy_product_7d`)
- Transacts very soon after the previous transaction (low `time_since_last_txn`)

The model learns which combination of these patterns indicates fraud.

**The Gold output:**
```
gold container → ieee-cis/card_features/
  - 590,540 rows
  - 15 columns: TransactionID, card1, isFraud, TransactionAmt, TransactionDT,
                event_timestamp, + 9 feature columns
  - Partitioned by isFraud (0 = legitimate, 1 = fraud)
  - Used for model training
```

**The Feast Parquet output:**
```
gold container → feast/card_transaction_stats.parquet/
  - 590,540 rows
  - 11 columns: card1, event_timestamp, + 9 features (no labels)
  - Used by feast_materialise.py to populate the online store
```

### src/ingest/eventhub_producer.py — The Streaming Demo

Written to simulate a bank sending live transactions into Event Hubs for demo purposes:

```python
# How to run it:
python -m src.ingest.eventhub_producer \
    --paysim data/paysim/PS_log.csv \
    --speed 100 \          # 100x faster than real-time
    --max-events 1000      # stop after 1000 events
```

It reads PaySim synthetic transaction data, maps it to IEEE-CIS schema (using `paysim_to_ieee.py`), sorts events by time, and fires them into Event Hubs in order. The `--speed` flag compresses time — at 100x, transactions that happened hours apart in simulation are sent seconds apart in the demo.

---

## Day 7 Result

| Component | Status |
|---|---|
| Databricks workspace | Live at adb-7405604945524635.15.azuredatabricks.net |
| Cluster (fraud-cluster) | Created, 14.3 LTS, Standard_D4ds_v4 |
| IEEE-CIS data in ADLS Bronze | 683.4 MB + 26.5 MB uploaded |
| Databricks secret scope | 3 secrets stored |
| Notebook 02 (Bronze → Silver) | Written, not yet run |
| Notebook 03 (Silver → Gold) | Written, not yet run |
| EventHub producer | Written and linted |

---

---

# Day 8 — Platform Navigation, Pipeline Execution, and Feature Store Population

**Date completed:** 2026-05-07
**What this day delivered:** Full understanding of every Azure resource, both Databricks notebooks executed successfully, Feast parquet downloaded to laptop, features materialised into PostgreSQL, skew test passed 3/3. The platform is ready for model training and API deployment.

---

## Part 1 — Understanding What You Built in Azure

Before running anything, we did a full guided tour of every Azure resource. This section explains what each one does and what to look for when you open it.

---

### How to Find Everything: The Resource Group

Everything the platform uses lives in one place:

```
Azure Portal → rg-fraud-platform (Resource group)
```

A resource group is a logical container — like a folder in Windows. When you delete the resource group, everything inside it gets deleted too. When you need to find anything, start here.

---

### Resource 1: stfraudf95d0b0e — Storage Account (Your Data Lake)

**Where to find it:** `rg-fraud-platform → stfraudf95d0b0e → Containers`

This is where all data in the platform lives. Terraform created 4 containers inside it:

| Container | What lives here |
|---|---|
| `bronze` | Raw data, never modified. Your IEEE-CIS CSVs live at `bronze/ieee-cis/` |
| `silver` | Cleaned and joined data. After notebook 02 runs: `silver/ieee-cis/transactions/` |
| `gold` | Feature-engineered data. After notebook 03 runs: `gold/ieee-cis/card_features/` and `gold/feast/` |
| `$logs` | Azure's own diagnostic logs — don't touch |

**Important lesson learned today:** The notebook originally used `abfss://data@storage.../bronze/` — treating `data` as the container name. But Terraform created separate containers (`bronze`, `silver`, `gold`), not one `data` container. The fix was changing the path to `abfss://bronze@storage.../ieee-cis/`.

**Rule:** Always verify your actual container names in the Azure portal before writing code that references them. What you expect and what Terraform created are not always the same.

---

### Resource 2: evhns-fraud-f95d0b0e — Event Hubs Namespace

**Where to find it:** `rg-fraud-platform → evhns-fraud-f95d0b0e → Event Hubs`

The message queue — where card transactions arrive before being processed.

**What to look for on the Overview page:**
- **Requests graph:** Shows management API calls (spikes when scripts connect to check credentials)
- **Messages graph:** Shows actual transaction volume — flat when nothing is streaming, spikes when the producer runs
- **Throughput graph:** Shows data volume in bytes — flat until live transactions flow

**Inside the `transactions` hub:**
| Property | Value | Meaning |
|---|---|---|
| Status | Active | Ready to receive messages |
| Message retention | 24 hours | Events sit here for 24h then are deleted |
| Partition count | 2 | Two parallel lanes — allows two consumers simultaneously |

**The conveyor belt analogy:**
```
Bank (producer)                    Platform (consumer)
Card swipe → [event] →→→→→→→→→→→→→→ Azure Function picks it up
             [event] →→→→→→→→→→→→→→ (within milliseconds)
             
          ← 24 hour window →
          (events wait here until consumed)
```

---

### Resource 3: kv-fraud-f95d0b0e — Key Vault

**Where to find it:** `rg-fraud-platform → kv-fraud-f95d0b0e → Secrets`

The safe. Every password and connection string lives here — never in code.

**What you see when you click Secrets:**
- A list of secret names (e.g. `eventhub-connection-string`, `postgres-password`)
- Clicking any name → click the version ID → click "Show Secret Value" to see the actual value

**How code reads from Key Vault:**
```
Code runs and needs a password
  │
  └── Calls: GET https://kv-fraud-f95d0b0e.vault.azure.net/secrets/eventhub-connection-string
       │
       └── Key Vault checks: "is this service/user allowed to read this?"
            ├── Yes → returns the secret value
            └── No  → returns 403 Forbidden
```

This is why the first Databricks PAT (token) returned 403 — it had "BI Tools" scope, not "All APIs" scope. Key Vault rejected it because the token didn't have permission.

---

### Resource 4: psql-fraud-f95d0b0e — PostgreSQL Flexible Server

**Where to find it:** `rg-fraud-platform → psql-fraud-f95d0b0e → Databases`

Your database. It has two jobs: store pre-computed card features (Feast online store) and store every fraud scoring decision (audit log).

**What you see in the Databases tab:**

| Database | What it is |
|---|---|
| `azure_maintenance` | Azure internal — never touch |
| `azure_sys` | Azure internal — never touch |
| `postgres` | Default empty database — not used |
| `fraud_platform` | **Your database** — Feast tables and decision log live here |

**Configuration:** Burstable B1ms — 1 vCore, 2 GB RAM, 32 GB storage. Small and cheap for dev. The yellow warning ("optimized for dev/test") is expected — upgrade to General Purpose for production.

**What fills the `fraud_platform` database:**
- `feast_materialise.py` writes card features into tables here
- The scoring API writes every decision here
- Right now: features are in there (we just ran materialise)

---

### Resource 5: adb-fraud-platform — Azure Databricks Service

**Where to find it:** `rg-fraud-platform → adb-fraud-platform → Launch Workspace`

The big-data processing environment. Clicking "Launch Workspace" takes you to the Databricks UI.

**Inside Databricks — what each sidebar section does:**

| Sidebar item | What it is |
|---|---|
| Workspace | Your file system — notebooks live here |
| Compute (All-purpose compute tab) | Your cluster — start it here before running notebooks |
| Catalog | Tables and data assets (populated after notebooks run) |
| Jobs & Pipelines | Scheduled notebook runs |
| AI/ML → Experiments | MLflow experiment tracking |
| Data Engineering → Runs | History of all notebook runs |

**Finding the cluster:** In the newer Databricks UI, the cluster is at:
```
Compute (left sidebar) → All-purpose compute tab
→ fraud-cluster (State: Terminated when off, Running when active)
```

**Starting the cluster:** Click the cluster name → click "Start" button top right. Takes about 3 minutes to become green/Running. Cost starts when it's Running.

---

### Resource 6: acrfraudf95d0b0e — Container Registry

**Where to find it:** `rg-fraud-platform → acrfraudf95d0b0e → Repositories`

Where Docker images of the scoring API are stored. Currently empty — the API hasn't been built and pushed yet. After Day 9 deployment, you'll see `fraud-platform/scoring-api` listed here.

---

### Resource 7: cae-fraud-f95d0b0e — Container Apps Environment

**Where to find it:** `rg-fraud-platform → cae-fraud-f95d0b0e → Container Apps`

Where the scoring API will run. Currently empty. After deployment, you'll see `fraud-scoring-api` listed with its live URL — the endpoint banks call to get fraud scores.

---

### Resource 8: appi-fraud-f95d0b0e — Application Insights

**Where to find it:** `rg-fraud-platform → appi-fraud-f95d0b0e → Overview`

The monitoring dashboard. Every API request, response time, and error is sent here automatically.

**What you'll see after the API is deployed:**
- Failed requests graph
- Server response time graph
- Request volume
- Live metrics (real-time, updates every second)

Currently flat at zero — the API isn't deployed yet.

---

### Resource 9: law-fraud-f95d0b0e — Log Analytics Workspace

**Where to find it:** `rg-fraud-platform → law-fraud-f95d0b0e → Logs`

Central log collector. All services send logs here. You can query them using KQL (Kusto Query Language):

```
AzureActivity
| take 10
```

This returns the 10 most recent activity events across all your Azure resources.

---

### Resource 10: Application Insights Smart Detection — Action Group

Alert rules. When Application Insights detects something unusual (error spike, slow response), this fires a notification. You can add your email address here to receive alerts.

---

## Part 2 — The Full Platform Guide

This section documents what every part of the codebase does, written so that anyone picking up this project can understand it without prior context.

---

### The One-Sentence Description

When a card transaction happens, this platform decides within milliseconds whether it looks fraudulent — and it does that automatically, reliably, and in a way that can be audited, monitored, and improved over time.

---

### The Journey of One Transaction

Everything in the codebase exists to support this flow:

```
Card swipe
    │
    ▼
Event Hubs (evhns-fraud-f95d0b0e)
    │  The message queue — transaction arrives here first
    │  Retained for 24 hours, 2 partitions
    ▼
Azure Function (src/ingest/azure_function/function_app.py)
    │  Triggered automatically when an event arrives
    ├──→ Writes raw event to ADLS Bronze (permanent record)
    └──→ Triggers Feast feature update
    ▼
Feast online store (psql-fraud-f95d0b0e, fraud_platform database)
    │  Pre-computed features for every card
    │  Updated after each transaction
    ▼
Scoring API (FastAPI, running on cae-fraud-f95d0b0e Container Apps)
    │  ├── Fetches card features from Feast  (~2ms)
    │  ├── Runs LightGBM model               (~1ms)
    │  ├── Returns fraud_score (0.0–1.0)
    │  ├── Converts score to decision:
    │  │       score < 0.3  → APPROVE
    │  │       score 0.3–0.7 → REVIEW
    │  │       score > 0.7  → DECLINE
    │  └── Logs every decision to PostgreSQL (audit trail)
    ▼
Bank system receives:
    { "fraud_score": 0.87, "decision": "DECLINE", "latency_ms": 23 }
```

**Target latency:** Under 50ms end to end.

---

### The Folder Map

```
fraud-platform/
│
├── src/                    ← All production Python code
│   ├── ingest/             ← Getting data INTO the system
│   ├── train/              ← Building and training the model
│   ├── serve/              ← The scoring API (what banks call)
│   ├── monitor/            ← Watching for model decay over time
│   ├── pipelines/          ← Bronze→Silver→Gold data pipeline
│   ├── retrain/            ← Auto-retraining schedule
│   └── dbt_fraud/          ← SQL models on top of Gold data
│
├── notebooks/              ← Databricks notebooks (run on the cloud cluster)
├── feature_repo/           ← Feast feature store definitions
├── infra/                  ← Terraform (Azure infrastructure as code)
├── tests/                  ← Automated quality checks
├── docs/                   ← Architecture, decisions, this document
├── governance/             ← Rules for promoting/rolling back models
├── model_cards/            ← What the model can and cannot do
├── scripts/                ← One-off setup and utility scripts
└── mlruns/                 ← MLflow experiment results (local)
```

---

### src/ingest/ — Getting Data In

| File | What it does |
|---|---|
| `eventhub_producer.py` | Simulates a bank sending transactions into Event Hubs (demo only). Run with `--speed 100` for 100x faster than real time |
| `paysim_to_ieee.py` | Converts PaySim synthetic data to IEEE-CIS schema. This is a demo seam — in production, real card transactions arrive pre-formatted from the payment processor |
| `bronze_validation.py` | Validates incoming data against 20 Great Expectations rules before storing. Wrong column type = rejection |
| `azure_function/function_app.py` | The serverless function triggered by every Event Hubs message. Writes to ADLS Bronze and triggers Feast update |

---

### src/train/ — Building the Model

| File | What it does |
|---|---|
| `feature_engineering.py` | Computes the 9 rolling-window features. **Single source of truth** — used identically at training time and serving time |
| `temporal_split.py` | Splits by time: train on older data, validate/test on newer. Never use future data to predict the past |
| `train_lgbm.py` | Trains champion LightGBM model. Logs all parameters, metrics, and the model file to MLflow |
| `train_catboost.py` | Trains challenger CatBoost model. Same logging discipline |
| `evaluate.py` | Compares champion vs challenger on AUC, TPR@0.1%FPR, and Brier score. Champion kept if it wins on all three |
| `feast_materialise.py` | Pushes computed features into PostgreSQL online store. Run after every new training dataset |
| `register_model.py` | Promotes the winning model to "Production" status in MLflow model registry |

**Critical rule:** `feature_engineering.py` must never diverge between training and serving. If training uses a 1-hour window but the API uses a 2-hour window, the model's measured AUC is meaningless — it will perform differently in production.

---

### src/serve/ — The Scoring API

| File | What it does |
|---|---|
| `main.py` | FastAPI application entry point. Starts the server, registers middleware and routers |
| `routers/score.py` | POST `/score` endpoint — the one banks call |
| `routers/health.py` | GET `/health` — Azure uses this to check if the container is alive |
| `schemas/request.py` | Pydantic model defining what a valid request looks like. Invalid requests are rejected before they reach the model |
| `schemas/response.py` | What the response looks like: `fraud_score`, `decision`, `latency_ms`, `model_version` |
| `services/feature_service.py` | Fetches pre-computed features from Feast for the incoming card |
| `services/model_service.py` | Loads the LightGBM model from MLflow and runs inference |
| `services/decision_log.py` | Writes every scoring decision to PostgreSQL. Never deleted — permanent audit trail |
| `middleware/telemetry.py` | Measures response time for every request and sends traces to Application Insights |

---

### src/monitor/ — Watching for Model Decay

Models go stale. Fraud patterns change — new attack vectors emerge, customer behaviour shifts. These files detect when that's happening:

| File | What it does |
|---|---|
| `psi.py` | Population Stability Index. Compares the distribution of incoming transactions to the training distribution. PSI > 0.2 = model should be retrained |
| `drift_report.py` | Full Evidently drift report — shows which specific features have drifted and by how much |
| `champion_challenger.py` | Shadow mode. Runs a new model in parallel with the live model, logging both scores. Used to validate the new model before promoting it |
| `trigger_retrain.py` | Automation. When PSI crosses the threshold, triggers `train_lgbm.py` automatically |

---

### notebooks/ — Databricks Notebooks

| File | What it does |
|---|---|
| `01_eda.ipynb` | Exploratory data analysis — fraud rate by product, transaction amount distributions, feature correlations |
| `02_bronze_to_silver.py` | Reads raw CSVs from ADLS, validates schema, joins transactions + identity, adds `event_timestamp`, writes Silver Parquet partitioned by month |
| `03_silver_to_gold.py` | Computes all 9 rolling-window features using Spark window functions, writes Gold Parquet and Feast Parquet to ADLS |

**The Bronze/Silver/Gold pattern explained simply:**
```
Bronze = raw data, exactly as received, never modified
         "What arrived at our door"

Silver = cleaned and joined
         "What we know is correct and complete"

Gold   = feature-engineered, ready for the model
         "What the model needs to make a prediction"
```

---

### feature_repo/ — Feast Feature Definitions

Feast answers one question: "How do I make pre-computed features available in 2ms without scanning 590,000 rows?"

| File | What it does |
|---|---|
| `entities.py` | Defines `card1` as the entity — the "primary key" that features are attached to |
| `features.py` | Defines all 9 features with their data types and the parquet source file |
| `feature_services.py` | Groups features into a named service — the API requests `card_features` and gets all 9 back |
| `feature_store.yaml` | Config file: offline store = parquet files, online store = PostgreSQL at localhost:5433 |

**Why PostgreSQL and not something faster like Redis?** See `docs/decisions/ADR-002-postgres-vs-redis-feature-store.md`. Short answer: the latency difference is 2ms vs 1ms — not meaningful for a 50ms budget — but PostgreSQL is much cheaper and simpler to operate.

---

### infra/ — Azure Infrastructure as Code

Terraform is "infrastructure as code." Instead of clicking through the Azure portal to create resources, you write `.tf` files and run `terraform apply`. Benefits:
- Reproducible: run it again and get the exact same infrastructure
- Reviewable: changes go through Git/pull request like code
- Destroyable: `terraform destroy` removes everything cleanly

| Module | What Terraform creates |
|---|---|
| `modules/data_lake/` | Storage account + 3 containers (bronze, silver, gold) |
| `modules/event_hubs/` | Event Hubs namespace + `transactions` hub |
| `modules/postgres/` | PostgreSQL Flexible Server B1ms + `fraud_platform` database |
| `modules/keyvault/` | Key Vault + access policies + 2 initial secrets |
| `modules/container_apps/` | Container Apps Environment (where the API will run) |
| `modules/monitoring/` | Log Analytics workspace + Application Insights |

All 23 resources were provisioned in Day 6 with a single `terraform apply` command that ran for 8 minutes.

---

### tests/ — Automated Quality Checks

| Folder | What it tests | When it runs |
|---|---|---|
| `tests/unit/` | Individual Python functions (no database, no Azure) | Every `git push` via GitHub Actions CI |
| `tests/integration/` | Full pipeline against real Postgres + Feast | Manually, before every deployment |
| `tests/load/` | API performance under 100+ concurrent requests | Before production go-live |

**The most important test:** `tests/integration/test_feature_skew.py`

This test is the mandatory deployment gate. It:
1. Takes 5 known cards
2. Fetches their features from the offline store (training path)
3. Fetches their features from the online store (serving path)
4. Compares every feature value within tolerance 0.000001

If ANY value differs, the test fails and deployment must stop. This prevents the most common ML production failure: the model performing worse in production than in testing because the features were computed differently.

---

### .github/workflows/ — CI/CD Pipeline

| File | Trigger | What it does |
|---|---|---|
| `ci.yml` | Every `git push` | Ruff lint → Ruff format check → mypy type check → detect-secrets scan → unit tests with 50% coverage gate |
| `cd.yml` | After CI passes on `main` | Build Docker image → deploy to Container Apps (currently skipped — OIDC blocked by UCT tenant) |

**In plain English:** Every time you push code, GitHub automatically runs all quality checks. If anything fails, you see a red X and the code is blocked. All green = code is safe to ship. This is what professional engineering teams do — no manual "did I break anything?" — the machine checks automatically.

---

### The Four Rules the Platform Is Built Around

| Rule | What it means | How it's enforced |
|---|---|---|
| Rule 1 — No training/serving skew | Features at training time must be identical to features at serving time | `feature_engineering.py` is the single source of truth used by both |
| Rule 2 — Test skew before deploy | The skew test must pass before any deployment | `tests/integration/test_feature_skew.py` must pass |
| Rule 3 — No secrets in code | Passwords and keys live in Key Vault, never in source code | `detect-secrets` hook in pre-commit and CI blocks any secret from being committed |
| Rule 4 — Shadow before replace | New models run silently in parallel before replacing the live model | `src/monitor/champion_challenger.py` implements shadow mode |

---

## Part 3 — Running the Pipeline: What Happened Today

### Step 1: Databricks Cluster Navigation

The newer Databricks UI (2025+) changed where the cluster management lives. It is no longer on the default "Compute" page — it requires navigating to the direct URL or using the "All-purpose compute" tab which is hidden unless you know where to look.

**How to always find it:**
```
Browser address bar → https://adb-7405604945524635.15.azuredatabricks.net/#/setting/clusters
```

Or from Databricks: `Compute → All-purpose compute tab (scroll left if hidden)`

---

### Step 2: Importing Notebooks

In the newer Databricks UI there is no "Import" button. The way to import `.py` notebook files is drag and drop:

```
Windows Explorer → navigate to fraud-platform/notebooks/
Databricks Workspace → Users → your email folder
Drag 02_bronze_to_silver.py from Explorer → drop into Databricks workspace
Drag 03_silver_to_gold.py from Explorer → drop into Databricks workspace
```

Notebooks appear immediately as clickable files. Open one and select your cluster from the cluster dropdown at the top right before running.

---

### Step 3: Running Notebook 02 (Bronze → Silver)

**First attempt: PATH_NOT_FOUND error**

```
Error: Path does not exist:
abfss://data@stfraudf95d0b0e.dfs.core.windows.net/bronze/ieee-cis/train_transaction.csv
```

**Root cause:** The notebook was written with `abfss://data@storage...` — assuming a container named `data`. But Terraform created separate containers (`bronze`, `silver`, `gold`).

**Fix:** Changed all paths to use the correct container names:
```python
# Wrong:
ADLS_ROOT  = f"abfss://data@{storage_account}.dfs.core.windows.net"
BRONZE_DIR = f"{ADLS_ROOT}/bronze/ieee-cis"

# Correct:
BRONZE_DIR = f"abfss://bronze@{storage_account}.dfs.core.windows.net/ieee-cis"
SILVER_DIR = f"abfss://silver@{storage_account}.dfs.core.windows.net/ieee-cis"
```

**Result after fix:**
```
Transactions : 590,540 rows  394 cols
Identity     : 144,233 rows  41 cols
Silver rows  : 590,540  cols : 436
Written to abfss://silver@stfraudf95d0b0e.dfs.core.windows.net/ieee-cis/transactions
Duration: 3 minutes
```

---

### Step 4: Running Notebook 03 (Silver → Gold)

**First attempt: OSError on /dbfs/FileStore**

```
OSError: [Errno 95] Operation not supported: '/dbfs/FileStore'
```

**Root cause:** The notebook tried to write to `/dbfs/FileStore` using `os.makedirs`. In Unity Catalog-enabled Databricks workspaces, direct DBFS local filesystem access is blocked for security reasons.

**Second attempt: IllegalAccessException on /tmp/**

```
IllegalAccessException: Cannot access non /Workspace local filesystem path
file:/tmp/card_transaction_stats.parquet on Shared cluster
```

**Root cause:** Unity Catalog also blocks `file://` paths. Any local filesystem reference is forbidden.

**Final fix:** Write the Feast parquet directly to ADLS using Spark, bypassing the local filesystem entirely:
```python
FEAST_ADLS_DIR = f"abfss://gold@{storage_account}.dfs.core.windows.net/feast"
feast_df.coalesce(1).write.mode("overwrite").parquet(FEAST_ADLS_DIR)
```

**Result after fix:**
```
Gold written to abfss://gold@stfraudf95d0b0e.dfs.core.windows.net/ieee-cis/card_features
Feast parquet written to: abfss://gold@stfraudf95d0b0e.dfs.core.windows.net/feast
Rows: 590,540
Duration: ~8 minutes
```

---

### Step 5: Downloading the Feast Parquet

**Why not just use the file from the cloud?** `feast_materialise.py` runs locally on your laptop and reads from `data/feast/card_transaction_stats.parquet`. It cannot read directly from ADLS without extra configuration.

**The Spark multi-part file problem:**

When Spark writes a parquet "file", it actually creates a folder:
```
gold/feast/card_transaction_stats.parquet/     ← this is a FOLDER
    ├── _SUCCESS                               ← empty marker: job completed OK
    ├── _committed_7048988360960087375          ← Spark bookkeeping
    ├── _started_7048988360960087375            ← Spark bookkeeping
    ├── part-00000-xxx.snappy.parquet          ← actual data, ~147,000 rows
    ├── part-00001-xxx.snappy.parquet          ← actual data, ~147,000 rows
    ├── part-00002-xxx.snappy.parquet          ← actual data, ~147,000 rows
    └── part-00003-xxx.snappy.parquet          ← actual data, ~147,000 rows
```

Spark distributes work across 4 parallel workers, each writing their own chunk. This is by design for distributed systems — each machine writes its portion independently.

**The solution:** Download all 4 part files, merge them with pandas, save as one file:
```python
# Download each part file from ADLS
for part in part_files:
    download_from_adls(part, local_temp)
    frames.append(pd.read_parquet(local_temp))

# Merge all 4 into one
df = pd.concat(frames, ignore_index=True)
df.to_parquet("data/feast/card_transaction_stats.parquet", index=False)
```

**Result:**
```
data/feast/card_transaction_stats.parquet
Rows: 590,540
Columns: card1, event_timestamp + 9 features
Size: 21.4 MB
```

---

### Step 6: Running feast_materialise.py

This script reads the parquet file and writes all 590,540 feature rows into the PostgreSQL online store.

**First attempt: Hung for 15+ minutes**

Feast started materialising but never completed. The script produced repeated "Registry cache expired, so refreshing" messages and then stopped responding.

**Root cause:** A previous killed Python process had left an open transaction in the local Docker PostgreSQL container. Feast was trying to write to the `card_transaction_stats` table but PostgreSQL was blocking — waiting for the previous session's lock to be released. Since the previous session was dead, the lock would never be released.

**The fix:** Restart the Docker PostgreSQL container — this closes all connections and clears all locks:
```bash
docker-compose restart postgres
```

**Second attempt: Success**
```
Materialising 1 feature view into postgres online store
card_transaction_stats: DONE
Online store (Postgres) is populated and ready.
```

**What now lives in PostgreSQL:**
```sql
-- In fraud_platform database, Feast created a table like:
SELECT card1, fe_card_txn_count_1h, fe_card_amt_mean_24h, ...
FROM card_transaction_stats
WHERE card1 = 12345;
-- Returns instantly via primary key lookup — ~2ms
```

---

### Step 7: Skew Test — 3/3 PASSED

```
pytest tests/integration/test_feature_skew.py -v

tests/integration/test_feature_skew.py ...   [100%]
```

**What each test verified:**

| Test | What it checked | Result |
|---|---|---|
| `test_offline_equals_online_for_known_cards` | For 5 known cards, offline features (training path) == online features (serving path) within 0.000001 | PASSED |
| `test_all_feature_columns_present_online` | All 9 feature columns exist in PostgreSQL | PASSED |
| `test_online_values_are_not_all_null` | PostgreSQL contains real values, not empty rows | PASSED |

**Why this matters:** You just proved that what the model was trained on is identical to what the API will serve. There is no training/serving skew. The model's AUC of 0.920 measured in testing is a truthful prediction of its production performance.

---

## Day 8 Result

```
✅ All 10 Azure resources understood and navigated
✅ Databricks notebooks imported and cluster connected
✅ Notebook 02 (Bronze → Silver): 590,540 rows written to ADLS silver container
✅ Notebook 03 (Silver → Gold): 9 features computed, Gold Parquet written
✅ Feast parquet downloaded from ADLS (4 parts merged → 590,540 rows, 21.4 MB)
✅ feast_materialise.py: online store populated in PostgreSQL
✅ Skew test: 3/3 PASSED — no training/serving skew
```

**What comes next:** Model training (`python -m src.train.train_lgbm`), model registration, and scoring API deployment to Azure Container Apps.

---

---

# The Big Picture: Days 1–8 as a System

```
DAY 1      DAY 2          DAY 3       DAY 4       DAY 5        DAY 6       DAY 7       DAY 8
Scaffold → Data+Features → Champion → Challenger → Feature  → Cloud    → Databricks → Pipeline
                           LightGBM   CatBoost     Store       Infra       + Upload    Executed
                           AUC:0.920  AUC:0.918    116k rows   23 Azure    590k rows   Skew:PASS
                                      KEEP CHAMP   Skew:PASS   resources   to ADLS     3/3 tests
```

**What's been built:**
- A reproducible, quality-enforced codebase with all CI checks passing
- A correctly-split dataset with 9 engineered features and 20/20 data quality checks
- A LightGBM champion model with AUC 0.920 and full MLflow audit trail
- A CatBoost challenger model with statistical comparison proving the champion is better
- A Feast feature store with Postgres online store serving features in milliseconds
- A mandatory skew test confirming zero training/serving divergence
- All cloud infrastructure provisioned as code in South Africa North
- A Databricks cluster that processed 590,540 transactions through Bronze → Silver → Gold
- A populated PostgreSQL online store ready for the scoring API

**What comes next:**
- **Day 9:** Deploy the FastAPI scoring API to Azure Container Apps
- **Day 10:** Shadow deployment — new model runs silently alongside live model
- **Day 11:** Drift monitoring — PSI alerts and automatic retrain trigger
- **Day 12:** Performance testing with Locust (100 concurrent users)
- **Day 13:** Model card, governance documentation, rollback runbook
- **Day 14:** End-to-end demo: PaySim producer → Event Hubs → API → decision log

---

---

---

# DAY 10 — Monitoring and Drift Detection

**Date completed:** 2026-05-07
**What this day delivered:** A drift detection system that watches for model decay, a live Grafana dashboard at `tshepangamir.grafana.net`, and OpenTelemetry metrics wired from the scoring API to Grafana Cloud.

---

## The Problem Day 10 Solves

Imagine you trained a perfect fraud model in December. It has AUC 0.920. You deploy it. Six months later, it has AUC 0.780. What happened?

The answer is almost always the same: **the world changed, but the model didn't.**

Card fraud patterns shift constantly. New attack vectors emerge — a new type of card-not-present fraud. Customer behaviour changes — average transaction amounts increase due to inflation. New merchants go live — different product category mixes. The model was trained on December's patterns. By June it's scoring July's patterns using rules it learned in December. Its mental model of "what fraud looks like" is six months out of date.

This is called **model drift**. It is the single most common reason production ML systems degrade silently — not bugs, not infrastructure failures, but the world quietly becoming different from what the model expected.

Day 10 builds the system that catches this before it causes real damage.

---

## What Does "Drift" Actually Mean?

There are two kinds of drift to watch for:

**Feature drift (what Day 10 measures):** The distribution of incoming transactions shifts. Cards that used to average 2 transactions per hour now average 8. Transaction amounts that used to cluster around $50 now cluster around $150. The model was trained on old distributions. New distributions may push inputs into parts of the feature space the model never trained on — it's being asked to extrapolate rather than interpolate.

**Concept drift:** The relationship between features and fraud changes. Something that used to be a strong fraud signal (e.g., many small transactions in quick succession) stops being one, because fraudsters have adapted. Concept drift is harder to detect — it requires measuring model accuracy on labelled production data, which often isn't available in real time.

Day 10 measures feature drift, which is detectable immediately without needing labels.

---

## The Population Stability Index (PSI)

The standard industry metric for measuring distribution shift is called the **Population Stability Index (PSI)**. It was originally developed by credit scoring teams at banks — exactly the right provenance for a fraud detection system.

The intuition is simple: if you split the training data into 10 buckets by percentile (0–10th, 10th–20th, ..., 90th–100th), and then put the new production data into those same buckets, do the buckets fill up the same way?

- Training: bucket 1 gets 10% of data, bucket 2 gets 10%, etc. (by definition of percentiles)
- Production: if the distribution hasn't changed, each bucket should still get ~10%
- If production bucket 1 gets 25% and bucket 5 gets 2%, something has shifted

The PSI formula measures how different the two distributions are:

```
PSI = Σ (current_pct − reference_pct) × ln(current_pct / reference_pct)
```

The result is interpreted with three fixed thresholds — these are the industry standard, and **Rule 6 of this project says they are not configurable**:

| PSI value | What it means | Action |
|---|---|---|
| < 0.10 | No significant shift | No action |
| 0.10 – 0.20 | Moderate shift | Log a warning, watch closely |
| > 0.20 | Major shift | Trigger retraining |

Why fixed? Because making thresholds configurable creates ambiguity — different teams might set them differently, a threshold might get changed under pressure when drift is detected ("let's just raise the threshold so it doesn't alert"). Fixed thresholds are a governance decision, not a technical one.

---

## What We Built: `src/monitor/psi.py`

```python
def compute_psi(reference, current, features, bins=10) -> dict[str, float]:
```

This function takes two DataFrames — training data and recent production data — and returns a dictionary mapping each feature name to its PSI score.

The implementation uses **percentile-based binning** on the reference data:

```python
breakpoints = np.percentile(reference, np.linspace(0, 100, bins + 1))
breakpoints[0] = -np.inf   # catch any value below the minimum
breakpoints[-1] = np.inf   # catch any value above the maximum
```

Why percentile-based? Because it creates equal-sized buckets in the reference distribution, which is the correct basis for PSI. Equal-width bins would be wrong — if transaction amounts range from $1 to $50,000, a $5,000-wide bin at the bottom would capture most transactions, and a $5,000 bin at the top would capture almost none. Percentile bins ensure each bucket has equal representation in the training data.

---

## What We Built: `src/monitor/drift_report.py`

This is the module that generates a full drift report. It does three things:

**1. Loads training and production data:**
```python
# Reference = first 80% of feature parquet (training distribution)
# Current = last 20% with simulated production shift applied
reference = df.iloc[:split]
current = df.iloc[split:].copy()

# Simulate a real-world fraud wave: elevated velocity and amounts
current["fe_card_txn_count_24h"] *= rng.uniform(0.9, 1.4)
current["fe_card_amt_mean_24h"]  *= rng.uniform(0.85, 1.35)
```

Since no live production traffic exists yet (the API isn't deployed), we simulate a realistic shift. Real fraud waves look exactly like this — transaction velocity and amounts spike when a compromised card batch hits.

**2. Runs an Evidently report:**

[Evidently](https://evidentlyai.com) is an open-source library for ML monitoring. Its `DataDriftPreset` runs a statistical test on every feature and produces a beautiful HTML report showing which features drifted, by how much, and what their distributions look like side by side.

```python
report = Report(metrics=[DataDriftPreset()])
report.run(reference_data=reference, current_data=current)
report.save_html("reports/drift_report.html")
```

The output is a self-contained HTML file you can open in a browser. It shows histograms of every feature comparing reference vs current, flagged in red if drift is detected.

**3. Computes PSI for every feature and logs the summary:**

```
PSI Summary (Rule 6 thresholds)
──────────────────────────────────────────────────────────
  fe_card_entropy_product_7d         0.1512  [warn]
  fe_card_txn_count_7d               0.0417  [ok]
  fe_time_since_last_txn             0.0169  [ok]
  fe_card_amt_mean_24h               0.0162  [ok]
  fe_card_txn_count_1h               0.0138  [ok]
  fe_peer_amt_deviation              0.0123  [ok]
  fe_card_txn_count_24h              0.0122  [ok]
  fe_card_amt_std_24h                0.0052  [ok]
  fe_card_amt_zscore_24h             0.0024  [ok]
```

One feature in the **warn** tier: `fe_card_entropy_product_7d` (PSI 0.1512). This is the 7-day product category entropy — how many different types of merchants a card is transacting at. It warns but doesn't trigger retraining. In a real production system this would generate a Slack alert and be watched closely for a week.

The simulated shift on transaction count and amounts didn't propagate strongly to entropy — which makes sense. Entropy measures variety of merchant categories, not volume or amount. The model is seeing higher spending but at the same mix of merchants. This is realistic — a compromised card often mimics the cardholder's usual merchant types to evade detection.

---

## What We Built: The Grafana Dashboard

Drift reports are weekly batch jobs. But a production system also needs **real-time observability** — what's the API doing right now?

We chose **Grafana Cloud** as the dashboard platform. It's free, hosted (no infrastructure to manage), and supports Prometheus metrics natively. The alternative — Azure Monitor — required an App Registration (blocked by the Azure for Students tenant), so Grafana Cloud was both the practical and the better choice.

The dashboard (`grafana/fraud_platform_dashboard.json`) has 7 panels:

```
┌─────────────────┬───────────────────┬──────────────────┬───────────────┐
│  Request Rate   │  p95 Latency (ms) │ p99 Latency (ms) │  Error Rate   │
│  (req/s)        │  green < 50ms     │  green < 100ms   │  red > 1%     │
│  stat panel     │  red > 100ms      │  red > 200ms     │  stat panel   │
└─────────────────┴───────────────────┴──────────────────┴───────────────┘
┌────────────────────────────────┬─────────────────────────────────────────┐
│  Scoring Latency Percentiles   │  Decision Distribution                  │
│  p50 / p95 / p99 over time     │  Donut: APPROVE / REVIEW / DECLINE      │
│  time series, threshold 100ms  │  proportions over the last hour         │
└────────────────────────────────┴─────────────────────────────────────────┘
┌───────────────────────────────────────────────────────────────────────────┐
│  Fraud Score Distribution Over Time                                        │
│  p50 / p90 / p99 of the raw fraud probability score                        │
│  Rising p90 signals that more transactions are being scored as high-risk   │
│  This could be: a real fraud wave, feature drift, or model calibration issue│
└───────────────────────────────────────────────────────────────────────────┘
```

The latency threshold annotations are deliberate: p95 must stay under 100ms (the project target). When a deployment degrades performance, the panel turns red immediately — no need to manually compute percentiles from logs.

The **Decision Distribution donut** is the most useful operational panel. In a healthy system you expect ~97% APPROVE, ~2% REVIEW, ~1% DECLINE (approximately matching the fraud rate). If DECLINE spikes to 30%, something is wrong — either a fraud wave is hitting or the model has miscalibrated.

The **Fraud Score Distribution** is the leading indicator. Score distributions shift before decision distributions, because the thresholds are fixed. If the p90 score rises from 0.15 to 0.45, that's a warning — more transactions are being scored as suspicious even before they cross the REVIEW threshold.

---

## How the Metrics Get to Grafana

The wiring uses **OpenTelemetry (OTel)** — the industry standard for application instrumentation.

The scoring API was already instrumented with OTel in Day 8 (FastAPIInstrumentor for automatic HTTP spans). Day 10 adds two custom metrics:

```python
# In src/serve/middleware/telemetry.py

_fraud_score_histogram = _meter.create_histogram(
    "fraud_score",
    description="Champion model fraud probability per scored transaction"
)

_decision_counter = _meter.create_counter(
    "fraud_decisions_total",
    description="Count of scoring decisions by outcome"
)
```

And in the score router, after every transaction:

```python
record_score(champion_score, decision)
# Records: fraud_score histogram += champion_score
#          fraud_decisions_total{decision="APPROVE"} += 1
```

These metrics flow through OTel's export pipeline to Grafana Cloud via the OTLP HTTP protocol:

```
POST /score request
      │
      ▼
score_transaction() runs LightGBM
      │
      ▼
record_score(0.87, "DECLINE")
      │ adds 0.87 to fraud_score histogram
      │ increments fraud_decisions_total{decision="DECLINE"}
      │
      ▼ (every 15 seconds)
OTLPMetricExporter
      │ HTTPS POST to otlp-gateway-prod-sa-east-1.grafana.net/otlp/v1/metrics
      │ Authentication: Basic auth (Instance ID 1627081 + API token)
      │
      ▼
Grafana Cloud Prometheus
      │ Stores metrics with service_name="fraud-scorer" label
      │
      ▼
Grafana Dashboard
      │ Queries: histogram_quantile(0.95, ...)
      ▼
Panel: p95 Latency = 23ms ✓
```

The `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_EXPORTER_OTLP_HEADERS` environment variables control where metrics go. In development (no endpoint set), metrics go to the console. In production (Container App), they go to Grafana Cloud. The code doesn't change — only the configuration does.

---

## Why Not Azure Monitor?

Azure Monitor is Microsoft's built-in observability service — Application Insights was already provisioned in Day 6. Why not use it?

Azure Monitor is the right answer for Azure-specific metrics (VM CPU, database connections, function invocations). For ML-specific metrics (fraud score distributions, decision distributions, model version tracking), it's significantly harder to use:
- No first-class support for histogram quantiles
- Queries use KQL (Kusto Query Language) — yet another language to learn
- Custom metric ingestion is more expensive
- Most importantly: requires a Service Principal to connect from GitHub Actions, which is blocked by the Azure for Students tenant

Grafana Cloud is free, supports Prometheus/PromQL natively (industry standard), has better histogram support, and connects to Grafana Cloud with a single API token. The dashboard we built would have taken three times longer to build in Azure Monitor.

---

## The Evidently Version Problem

A note on a version mismatch we encountered — documented because it will come up again.

The project initially imported Evidently using the pre-0.4 API:
```python
from evidently.metric_preset import DataDriftPreset  # old API
from evidently.report import Report                  # old API
```

Evidently 0.7 (which installed automatically) completely redesigned the top-level API. The new API is a programmatic SDK focused on LLM evaluation — quite different from the batch reporting the old API provided. The old HTML report generation still works, but through the legacy module:

```python
from evidently.legacy.metric_preset import DataDriftPreset  # 0.7+ API
from evidently.legacy.report import Report                  # 0.7+ API
```

This is a common problem with fast-moving ML tooling libraries — minor version bumps can break imports. The `evidently>=0.7.0` pin in `requirements-dev.txt` and the use of `evidently.legacy` protects against future breakage.

---

## Day 10 Result

| Component | Status |
|---|---|
| `src/monitor/psi.py` — PSI with Rule 6 thresholds | ✓ Built and tested |
| `src/monitor/drift_report.py` — Evidently HTML report | ✓ Built and runs |
| `reports/drift_report.html` — first drift report generated | ✓ Generated |
| `reports/psi_scores.json` — PSI for all 9 features | ✓ Generated |
| `src/serve/middleware/telemetry.py` — Grafana Cloud wiring | ✓ Updated |
| `grafana/fraud_platform_dashboard.json` — 7-panel dashboard | ✓ Built |
| Grafana dashboard imported at tshepangamir.grafana.net | ✓ Live |
| Dashboard panels show "No data" | Expected — Container App not yet deployed |

**PSI scan result:** 1 of 9 features in warn tier (`fe_card_entropy_product_7d`, PSI 0.1512). 8 of 9 features stable. No retraining triggered.

**What "No data" in Grafana means:** The dashboard is correctly configured and connected to the Prometheus data source. The panels will populate automatically the moment the Container App is deployed and starts processing requests. The infrastructure is ready; the data just isn't flowing yet.

---

---

# Day 11 — Airflow Retraining DAG and the Human Approval Gate

## The Problem It Solves

You have a drift monitor (Day 10) that tells you when the fraud score distribution is shifting. You have a retraining pipeline (Day 3–4) that can produce a challenger. But how do you connect them safely? How do you prevent a newly trained model from promoting itself to production automatically?

This is the governance problem. The answer in this project is an Airflow DAG with a Rule 7 hard stop.

## What Rule 7 Actually Means

**Rule 7: No automated path from Staging to Production.** The Airflow DAG may train and evaluate a challenger automatically, but it stops at a sensor that waits for a human to explicitly set an Airflow Variable to `"approved"` before any promotion happens.

This is not a bureaucratic checkbox. In regulated environments (and Investec, MTN, and most South African banks operate in heavily regulated environments), a model risk officer or fraud operations lead must sign off on every model change. Automation that promotes without human oversight creates compliance risk and liability. The approval gate is a feature, not a limitation.

## The DAG Structure

The retraining DAG has eight tasks arranged as a directed acyclic graph:

```
prepare_training_data
        ↓
 train_challenger
        ↓
evaluate_challenger
        ↓
branch_on_evaluation
       / \
(REJECT)  (REQUEST_APPROVAL)
     ↓           ↓
archive_challenger  request_human_approval
                        ↓
               wait_for_human_approval  ← SENSOR — pauses here
                        ↓
               promote_to_production
                        ↓
                       done
```

The branch at `branch_on_evaluation` is the automated gate. The wait at `wait_for_human_approval` is the human gate.

## The Automated Gate: Three Metric Tests

Before a challenger even reaches the human approval step, it must pass three statistical tests:

**1. Bootstrap CI lower bound for TPR@0.1%FPR difference must be positive.**

This is the key metric for this use case. At a fixed false positive rate (0.1% — meaning the system declines or flags 1 in every 1,000 legitimate transactions), how many more fraud transactions does the challenger catch? We use bootstrap sampling to build a 95% confidence interval on this difference. If the lower bound of that interval is ≤ 0, we cannot be statistically confident the challenger is better, and it is rejected automatically.

**2. AUC regression tolerance: max 0.005.**

The challenger's AUC must not drop more than 0.005 below the champion's. A drop larger than this indicates the challenger has materially degraded overall discrimination ability, even if it passes the CI test on the targeted operating point.

**3. Brier score regression tolerance: max 0.002.**

The Brier score measures calibration — whether the model's 0.85 fraud probability actually corresponds to ~85% of those transactions being fraud. Poor calibration produces unreliable thresholds. The challenger cannot regress by more than 0.002 on this measure.

These thresholds are not arbitrary. They represent the minimum bar at which a challenger is worth even presenting to a human reviewer.

## The Human Gate: Airflow Variables

When the automated gate passes, the DAG sets a log message:

```
Human approval required.
Set Airflow Variable fraud_retrain_approval_<run_id>=approved to promote <run_id>.
```

The `wait_for_human_approval` sensor then polls every 5 minutes, with a 7-day timeout, checking whether that variable exists and equals `"approved"`. Nothing happens until a human explicitly makes that change in the Airflow UI (or via the Airflow API). The sensor mode is `reschedule` — it releases the worker slot between polls rather than holding it.

## The PSI Trigger Integration

The DAG does not schedule itself. It is triggered by `src/monitor/trigger_retrain.py`, which:

1. Reads `reports/psi_scores.json` (produced by the Day 10 drift report)
2. Checks each feature's PSI value against the Rule 6 threshold (0.20)
3. If any feature exceeds 0.20, builds a DAG run configuration with the triggering features and their scores
4. Calls the Airflow REST API (`/api/v1/dags/retrain_fraud_scorer/dagRuns`) to trigger the run

The DAG run configuration includes the names of the drifting features so the `prepare_training_data` task can log which features drove the retraining decision — producing a clear audit trail.

## Why Airflow, Not a Cron Job

You could trigger retraining from a cron job. The reason to use Airflow is observability and auditability. Every DAG run produces a complete execution log with timestamps, task states, XCom values, and branch decisions. When a model risk officer asks "why did the system recommend promoting this challenger on March 12th?", you can show them the exact DAG run, the PSI scores that triggered it, the evaluation metrics that passed the automated gate, and the timestamp when approval was granted.

A cron job produces a log file that may or may not still exist.

---

# Day 12 — Model Card and Governance Documentation

## Why Model Cards Are Not Optional

A fraud model affects real customers. If a model incorrectly declines a legitimate transaction, a customer cannot buy their groceries. If it misses fraud, a customer loses money they may not recover. A model card is the documentation that forces you to be explicit about what the model does, what it was trained on, where it fails, and who is responsible for it.

In practice, a model card is what a model risk officer reads before approving deployment. Without a complete model card, deployment approval is not possible.

## What the Model Card Contains

The model card at `model_cards/fraud_scorer_v1.md` documents:

**Identity and lineage:** The champion is LightGBM, MLflow run ID `9c599d91d7c546df82ad252837990c29`, trained on the temporal training split of the IEEE-CIS dataset. The CatBoost challenger (run ID `cd2da7878fd44ad39dab091dde2984fb`) was evaluated and rejected — KEEP_CHAMPION decision.

**Performance metrics at the operating point:**
- AUC: 0.9200 (champion), 0.9179 (challenger)
- AUPRC: 0.5833
- TPR at 0.1% FPR: 0.2903
- Bootstrap CI on TPR difference: lower bound −0.0712 (challenger is worse)

**Decision thresholds:** APPROVE below 0.50, REVIEW at 0.50–0.89, DECLINE at 0.90+. These are configurable via environment variables.

**Subgroup observations:** `ProductCD=C` transactions have a materially higher fraud rate than `ProductCD=W`. This is from EDA — the model handles it through learned feature weights, not subgroup-specific thresholds, and this tradeoff is explicitly documented.

**Known limitations:** The +23% fraud value improvement is a portfolio/scenario framing, not a claim of production validation on South African bank traffic. PaySim is a simulated stream. Feast online store materialisation has a known table mismatch in Azure that the serving layer handles via a fallback.

**Monitoring policy:** PSI watch on 9 features, Grafana latency and error dashboards, champion/challenger comparison in Postgres.

## The Four ADRs

Architecture Decision Records are short documents that capture a significant decision, the context that drove it, and the consequences. They are important because decisions that seem obvious today become confusing in six months when you no longer remember why you made them.

**ADR-001: Event Hubs over Kafka.** Self-hosted Kafka on AKS would cost ~$40/month in compute. Event Hubs Basic at demo-scale event volumes costs ~$0.015 per million events. The Kafka protocol compatibility means this choice can be reversed without changing the consumer code.

**ADR-002: Postgres over Redis for Feast online store.** Redis Cache Basic is $16/month with no stop capability. Postgres Flexible Server B1ms is $13/month and can be stopped when idle. At under 100 req/s, Postgres sub-10ms feature retrieval is sufficient. Postgres also serves as the decision log and shadow log, consolidating two services into one.

**ADR-003: Container Apps over AKS.** AKS minimum viable node pool: ~$800/month. Container Apps: ~$0 at demo traffic with scale-to-zero. A single stateless FastAPI application does not need Kubernetes orchestration.

**ADR-004: Shadow mode over A/B test.** In fraud detection, assigning customers to a potentially inferior challenger exposes them to undetected fraud. Shadow mode validates the challenger on 100% of production traffic with zero customer risk. The statistical power is identical because all requests are scored by both models.

---

# Day 13 — Demo Video Preparation

## What the Demo Needs to Show

The demo script at `docs/demo_video_script.md` covers seven proof points in 5–7 minutes:

1. **The codebase** — README, architecture diagram, governance links
2. **The live API** — a real POST /score request to the staging Container App returning a decision in under 100ms
3. **Live dashboard traffic** — `scripts/send_demo_traffic.py` sending 250 requests to generate fresh Grafana data
4. **Grafana dashboard** — request rate, latency percentiles, decision distribution, fraud score histogram all populating in real time
5. **Model card and governance** — champion AUC, challenger rejection, approval gate policy
6. **Airflow approval gate** — the DAG structure showing the sensor paused at `wait_for_human_approval`
7. **Honest limitations** — Feast online store mismatch, demo framing of business numbers

The demo traffic helper (`scripts/send_demo_traffic.py`) sends deterministic synthetic requests to the staging URL with configurable concurrency and counts. It reports decision distribution, client-observed latency, and API-reported scoring latency in the terminal output — this terminal output is itself a proof point for the demo.

## The Pre-Recording Checklist

The demo script includes a safety checklist that prevents secrets from appearing on screen:
- Close browser tabs with Key Vault, ACR credentials, or env files
- Clear terminal scrollback before opening it on camera
- Check `.env.grafana.example` is the file shown (the example, not the real `.env`)
- Verify the staging URL is set as an environment variable, not typed inline

This matters because the demo video goes in a public GitHub repository. Credentials that appear in a video, even briefly, are compromised.

---

# Day 14 — Hardening and Portfolio Artefact

## What Day 14 Is For

Days 1–13 built a working system. Day 14 is about making it shippable as a portfolio piece — the documentation, the one-pager, and the final quality check that ensures an interviewer at Investec or MTN can pick up the repo and immediately understand what was built and why.

## The README Structure

The README at the root of the repo is the front door. An interviewer who opens the GitHub repo will see it first. It is structured to answer the questions an interviewer asks in order:

1. **What does this system do?** — Headline Results table with AUC, latency proof, smoke test results
2. **How does it work?** — Mermaid architecture diagram
3. **What problem does it solve?** — Business Problem section
4. **What were the key decisions?** — Architecture Decision Records table with four ADRs and their one-line rationale
5. **Where is the governance?** — Key Artifacts table linking model card, promotion policy, rollback runbook
6. **How do I run it?** — Local Setup and Demo Commands sections
7. **What are the honest limitations?** — Honest Limitations table

## The Interview One-Pager

`docs/interview_one_pager.html` is a print-ready A4 document designed to be handed to an interviewer at the start of a technical conversation. It uses a two-column grid layout that fits the entire project on one page without reducing below readable font size.

The narrative at the bottom is the most important sentence: "I did not just train a fraud model. I built the platform around it: ingestion, feature consistency, serving, monitoring, governance, and rollback."

This is the core claim. Every section of the document is evidence for it.

## The Financial Framing — Revisited

The project builds toward one number: **+23% fraud value caught at fixed 0.1% FPR versus a monthly-retrained baseline.** This is not a guarantee. It is not from a production backtest on South African bank traffic. It is a scenario-backed framing that shows what the operating point improvement would mean at Investec's reported card volume.

The reason to include it — and the reason to be explicit about its limitations — is that this is how fraud teams think. Not in AUC points. In rand value recovered at constant customer friction. The model card documents the actual metrics. The financial framing contextualises why those metrics matter.

---

# The Big Picture: Days 1–14 as a System

```
DAY 1    DAY 2       DAY 3      DAY 4       DAY 5       DAY 6     DAY 7
Scaffold Data+Feats  Champion   Challenger  Feat Store  Cloud     Databricks
                     LightGBM   CatBoost    Feast       Infra     +Upload
                     AUC:0.920  AUC:0.918   116k rows   23 Azure  590k rows
                                KEEP CHAMP  Skew:PASS   resources to ADLS

DAY 8        DAY 9         DAY 10      DAY 11          DAY 12    DAY 13    DAY 14
Serve+OTel   Container     Monitoring  Airflow DAG     Model     Demo      README
FastAPI      Deploy        +Grafana    +Approval Gate  Card      Script    One-Pager
smoke 3/3    CD pipeline   PSI scan    50 unit tests   4 ADRs    Traffic   Final QA
shadow mode  live smoke    1 warn      Rule 7 gate     complete  helper    complete
             6/6 passed    9 features
```

**What was built, end to end:**
- A reproducible codebase with CI on every push, Ruff linting, pytest coverage
- A correctly-split IEEE-CIS dataset with 9 engineered features and 20/20 data quality checks
- LightGBM champion (AUC 0.9200) and CatBoost challenger, both in MLflow with full lineage
- A Feast feature store with Postgres online store and a passing skew test
- All Azure infrastructure provisioned as Terraform code in South Africa North
- A Databricks pipeline processing 590k transactions through Bronze → Silver → Gold
- A public FastAPI scoring service on Azure Container Apps with champion/challenger shadow mode
- OpenTelemetry metrics flowing to a Grafana Cloud dashboard with 7 panels
- PSI drift detection watching 9 features with Rule 6 fixed thresholds
- An Airflow retraining DAG with automated metric gates and a Rule 7 human approval sensor
- A complete governance layer: model card, promotion policy, rollback runbook, four ADRs
- A demo traffic helper and demo script covering all seven required proof points
- A print-ready interview one-pager and a comprehensive README

**The headline result:**
> "On the IEEE-CIS holdout at a fixed 0.1% FPR, the system catches 23% more fraud value than a monthly-retrained baseline — on Investec's reported card volume, that is an indicative R42 million per year in recovered exposure, at constant customer friction. The shadow deployment ran for evaluation before any promotion decision was made, and the human approval gate means no model enters production without a compliance sign-off."

---

*This document was last updated 2026-05-15, Day 14 of 14.*

*This document was last updated 2026-05-07, Day 10 of 14.*
