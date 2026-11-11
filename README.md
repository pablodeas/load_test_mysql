🇺🇸 English | [🇧🇷 Português](README.pt-BR.md)

# carga_mysql.py

Load simulator for MySQL 8.4, built for **testing and learning environments**. It generates random operations (INSERT, UPDATE, DELETE, UPSERT, SELECT, transactions, deadlocks) so you can practice database tuning.

> **Do not use in production.** The script creates, modifies and deletes data in bulk.

## Requirements

- Python 3.8+
- MySQL 8.4, reachable over the network or locally
- `mysql-connector-python` library

```bash
sudo dnf install -y python3 python3-pip
pip3 install mysql-connector-python
```

## MySQL user

```sql
CREATE USER 'sim'@'%' IDENTIFIED BY 'Sim#123';
GRANT ALL PRIVILEGES ON sim_db.* TO 'sim'@'%';
GRANT CREATE, DROP ON *.* TO 'sim'@'%';   -- required to create the database
GRANT PROCESS ON *.* TO 'sim'@'%';        -- required for the metrics report
```

## Usage

**1. Create the database and tables**

```bash
python3 carga_mysql.py setup --user sim --password 'Sim#123'
```

**2. Fill it with random data** (default: 20k customers, 2k products, 40k orders)

```bash
python3 carga_mysql.py seed --user sim --password 'Sim#123'
```

**3. Run the simulation**

```bash
python3 carga_mysql.py run --user sim --password 'Sim#123' --threads 8 --duration 300 --profile mixed
```

To stop early, press `Ctrl+C`. The final summary is still printed.

## Commands

| Command | Purpose |
|---|---|
| `setup` | Creates the database and tables (`--reset` drops and recreates) |
| `seed` | Initial data load |
| `run` | Runs the simulation |
| `drop --yes` | Removes the database |

## Main options

Connection options (apply to all commands):

| Option | Default | Environment variable |
|---|---|---|
| `--host` | `127.0.0.1` | `MYSQL_HOST` |
| `--port` | `3306` | `MYSQL_PORT` |
| `--user` | `root` | `MYSQL_USER` |
| `--password` | empty | `MYSQL_PWD` |
| `--database` | `sim_db` | `MYSQL_DB` |

`seed` options: `--customers`, `--products`, `--orders`.

`run` options:

| Option | Default | Description |
|---|---|---|
| `--threads` | `8` | Concurrent connections |
| `--duration` | `300` | Duration in seconds |
| `--profile` | `mixed` | `mixed`, `read` (read-heavy) or `write` (write-heavy) |
| `--report` | `10` | Report interval in seconds |
| `--think` | `0` | Maximum random pause between operations (s) |
| `--ddl` | off | Includes CREATE/ALTER/DROP |
| `--rand-seed` | random | Seed to replay the same workload |

## What is simulated

- **INSERT:** single, bulk, and a full order transaction
- **UPDATE:** by key, bulk, and bulk without an index
- **DELETE:** by key, in batches, and with cascade
- **UPSERT:** `ON DUPLICATE KEY UPDATE` and `REPLACE INTO`
- **SELECT:** by PK, JOINs, `LIKE`, aggregations, top-N and deep pagination
- **Special transactions:** rollback, hot-row locking and forced deadlocks
- **DDL** (optional): temporary table creation, index and drop

## Output

While running, at every interval:

```
[  10s] app:   412.3 ops/s err+2 | srv: q/s=...  scan=...  lockwait=...  slow=... | run=3 conn=9
```

At the end, a table with total executions, errors, average time, p95 and max per operation, plus the count of deadlocks and lock timeouts.

## Suggested study path

1. Run it once and record the baseline (ops/s and p95 per operation).
2. Enable the slow log:
   ```sql
   SET GLOBAL slow_query_log = ON;
   SET GLOBAL long_query_time = 0.1;
   ```
3. Analyze the slow queries with `EXPLAIN ANALYZE`.
4. Create indexes and compare. Some are **missing on purpose**: `customers.city`, `orders.created_at`, `orders.status` and `products.category`.
5. Tune MySQL for 2 GB of RAM (`innodb_buffer_pool_size`, `max_connections`, `tmp_table_size`), **one at a time**.
6. Check `performance_schema` and `sys` (for example `sys.statements_with_full_table_scans` and `sys.innodb_lock_waits`).
7. Vary `--threads` (4, 8, 16, 32) to see where the 2 vCPUs saturate.

## Tips

- Run the script from **another machine** if possible, so it doesn't compete with MySQL for CPU and memory.
- Use `--rand-seed 42` to replay the same workload and compare before and after each change.
- `deadlock` and `lock_timeout` errors in the summary are expected: they are triggered on purpose.