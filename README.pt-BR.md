[🇺🇸 English](README.md) | 🇧🇷 Português

# carga_mysql.py

Simulador de carga para MySQL 8.4, feito para ambientes de **teste e estudo**. Gera operações aleatórias (INSERT, UPDATE, DELETE, UPSERT, SELECT, transações, deadlocks) para você praticar a otimização do banco.

> **Não use em produção.** O script cria, altera e apaga dados em massa.

## Requisitos

- Python 3.8+
- MySQL 8.4 acessível pela rede ou localmente
- Biblioteca `mysql-connector-python`

```bash
sudo dnf install -y python3 python3-pip
pip3 install mysql-connector-python
```

## Usuário no MySQL

```sql
CREATE USER 'sim'@'%' IDENTIFIED BY 'Sim#123';
GRANT ALL PRIVILEGES ON sim_db.* TO 'sim'@'%';
GRANT CREATE, DROP ON *.* TO 'sim'@'%';   -- necessário para criar o database
GRANT PROCESS ON *.* TO 'sim'@'%';        -- necessário para o relatório de métricas
```

## Como usar

**1. Criar o database e as tabelas**

```bash
python3 carga_mysql.py setup --user sim --password 'Sim#123'
```

**2. Popular com dados aleatórios** (padrão: 20 mil clientes, 2 mil produtos, 40 mil pedidos)

```bash
python3 carga_mysql.py seed --user sim --password 'Sim#123'
```

**3. Rodar a simulação**

```bash
python3 carga_mysql.py run --user sim --password 'Sim#123' --threads 8 --duration 300 --profile mixed
```

Para parar antes do tempo, use `Ctrl+C`. O resumo final é exibido mesmo assim.

## Comandos

| Comando | Função |
|---|---|
| `setup` | Cria o database e as tabelas (`--reset` apaga e recria) |
| `seed` | Carga inicial de dados |
| `run` | Executa a simulação |
| `drop --yes` | Remove o database |

## Opções principais

Opções de conexão (valem para todos os comandos):

| Opção | Padrão | Variável de ambiente |
|---|---|---|
| `--host` | `127.0.0.1` | `MYSQL_HOST` |
| `--port` | `3306` | `MYSQL_PORT` |
| `--user` | `root` | `MYSQL_USER` |
| `--password` | vazio | `MYSQL_PWD` |
| `--database` | `sim_db` | `MYSQL_DB` |

Opções do `seed`: `--customers`, `--products`, `--orders`.

Opções do `run`:

| Opção | Padrão | Descrição |
|---|---|---|
| `--threads` | `8` | Conexões simultâneas |
| `--duration` | `300` | Duração em segundos |
| `--profile` | `mixed` | `mixed`, `read` (mais leitura) ou `write` (mais escrita) |
| `--report` | `10` | Intervalo do relatório em segundos |
| `--think` | `0` | Pausa aleatória máxima entre operações (s) |
| `--ddl` | desligado | Inclui CREATE/ALTER/DROP |
| `--rand-seed` | aleatório | Semente para repetir a mesma carga |

## O que é simulado

- **INSERT:** individual, em lote e transação completa de pedido
- **UPDATE:** por chave, em massa e em massa sem índice
- **DELETE:** por chave, em lote e com cascata
- **UPSERT:** `ON DUPLICATE KEY UPDATE` e `REPLACE INTO`
- **SELECT:** por PK, JOINs, `LIKE`, agregações, top-N e paginação profunda
- **Transações especiais:** rollback, lock em linha quente e deadlock provocado
- **DDL** (opcional): criação de tabela temporária, índice e drop

## Saída

Durante a execução, a cada intervalo:

```
[  10s] app:   412.3 ops/s err+2 | srv: q/s=...  scan=...  lockwait=...  slow=... | run=3 conn=9
```

No final, uma tabela com total de execuções, erros, tempo médio, p95 e máximo por operação, além da contagem de deadlocks e lock timeouts.

## Roteiro de estudo sugerido

1. Rode uma vez e anote o resultado (ops/s e p95 por operação).
2. Ative o slow log:
   ```sql
   SET GLOBAL slow_query_log = ON;
   SET GLOBAL long_query_time = 0.1;
   ```
3. Analise as queries lentas com `EXPLAIN ANALYZE`.
4. Crie índices e compare. Alguns **estão faltando de propósito**: `customers.city`, `orders.created_at`, `orders.status` e `products.category`.
5. Ajuste os parâmetros do MySQL para 2 GB de RAM (`innodb_buffer_pool_size`, `max_connections`, `tmp_table_size`), **um por vez**.
6. Consulte `performance_schema` e `sys` (por exemplo `sys.statements_with_full_table_scans` e `sys.innodb_lock_waits`).
7. Varie `--threads` (4, 8, 16, 32) para ver onde os 2 vCPUs saturam.

## Dicas

- Rode o script de **outra máquina**, se possível, para ele não competir por CPU e memória com o MySQL.
- Use `--rand-seed 42` para repetir a mesma carga e comparar o antes e o depois de cada ajuste.
- Erros `deadlock` e `lock_timeout` no resumo são esperados: são provocados de propósito.