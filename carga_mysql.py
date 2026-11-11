#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
carga_mysql.py - Simulador de carga para MySQL 8.4 (ambiente de testes/estudo)

Subcomandos:
  setup   cria database e tabelas (use --reset para recriar do zero)
  seed    popula com dados aleatorios (clientes, produtos, pedidos)
  run     executa a carga de trabalho com N threads por X segundos
  drop    remove o database (exige --yes)

Exemplos:
  python3 carga_mysql.py setup --user sim --password 'Sim#123'
  python3 carga_mysql.py seed  --user sim --password 'Sim#123'
  python3 carga_mysql.py run   --user sim --password 'Sim#123' --threads 8 --duration 300 --profile mixed

Dependencia: pip install mysql-connector-python
ATENCAO: use somente em ambiente de testes.

OUTROS:
    CREATE USER 'sim'@'%' IDENTIFIED BY 'Tzqm63mR!!C99De';
    GRANT ALL PRIVILEGES ON sim_db.* TO 'sim'@'%';
    GRANT CREATE, DROP ON *.* TO 'sim'@'%';
    GRANT PROCESS ON *.* TO 'sim'@'%';
"""

import argparse
import os
import random
import signal
import sys
import threading
import time
import unicodedata
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta
from decimal import Decimal

try:
    import mysql.connector
except ImportError:
    sys.exit("Dependencia ausente. Instale com: pip install mysql-connector-python")

# --------------------------------------------------------------------------
# Dados para geracao aleatoria
# --------------------------------------------------------------------------
FIRST = ["Ana", "Bruno", "Carlos", "Daniela", "Eduardo", "Fernanda", "Gabriel", "Helena",
         "Igor", "Juliana", "Kleber", "Larissa", "Marcos", "Natália", "Otávio", "Patrícia",
         "Rafael", "Sabrina", "Thiago", "Vanessa", "Lucas", "Mariana", "João", "Beatriz",
         "Felipe", "Camila", "André", "Letícia", "Rodrigo", "Aline"]
LAST = ["Silva", "Santos", "Oliveira", "Souza", "Pereira", "Costa", "Rodrigues", "Almeida",
        "Nascimento", "Lima", "Araújo", "Fernandes", "Carvalho", "Gomes", "Martins",
        "Rocha", "Ribeiro", "Alves", "Monteiro", "Mendes", "Barros", "Freitas", "Moreira"]
CITIES = [("São Paulo", "SP"), ("Rio de Janeiro", "RJ"), ("Belo Horizonte", "MG"),
          ("Vitória", "ES"), ("Vila Velha", "ES"), ("Curitiba", "PR"), ("Porto Alegre", "RS"),
          ("Salvador", "BA"), ("Recife", "PE"), ("Fortaleza", "CE"), ("Brasília", "DF"),
          ("Goiânia", "GO"), ("Manaus", "AM"), ("Florianópolis", "SC"), ("Campinas", "SP")]
DDD = ["11", "21", "27", "31", "41", "51", "71", "81", "85", "61", "62", "92", "48", "19"]
DOMAINS = ["gmail.com", "outlook.com", "yahoo.com.br", "empresa.com.br", "mail.com", "uol.com.br"]
CATEGORIES = ["Eletrônicos", "Livros", "Casa", "Esporte", "Moda", "Brinquedos",
              "Beleza", "Automotivo", "Alimentos", "Informática"]
ADJ = ["Premium", "Compacto", "Ultra", "Básico", "Pro", "Eco", "Max", "Smart", "Classic", "Turbo"]
NOUN = ["Fone", "Mochila", "Teclado", "Garrafa", "Luminária", "Cadeira", "Relógio",
        "Câmera", "Caderno", "Tênis", "Mouse", "Monitor", "Panela", "Bola", "Perfume"]
BRAND = ["Aurora", "Nexus", "Vértice", "Orion", "Atlas", "Delta", "Zênite", "Solar"]
PAY_METHODS = ["credit_card", "debit_card", "pix", "boleto"]
ORDER_STATUS = ["pending", "paid", "shipped", "delivered", "cancelled"]
ORDER_STATUS_W = [10, 20, 15, 45, 10]

HOT = 5            # quantidade de produtos "quentes" usados para gerar contencao/deadlock
SESSION_POOL = 5000  # pool de tokens de sessao (gera colisoes para o REPLACE)


def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.lower().replace(" ", ".")


def rand_name():
    return f"{random.choice(FIRST)} {random.choice(LAST)} {random.choice(LAST)}"


def rand_email(name):
    return f"{slug(name)}.{uuid.uuid4().hex[:6]}@{random.choice(DOMAINS)}"


def rand_phone():
    return f"({random.choice(DDD)}) 9{random.randint(1000, 9999)}-{random.randint(1000, 9999)}"


def rand_dt(days_back):
    return datetime.now() - timedelta(seconds=random.randint(0, days_back * 86400))


def rand_money(lo, hi):
    return Decimal(f"{random.uniform(lo, hi):.2f}")


def rand_birth():
    return (datetime.now() - timedelta(days=random.randint(18 * 365, 80 * 365))).date()


# --------------------------------------------------------------------------
# Schema (alguns indices FALTAM de proposito, para voce praticar otimizacao)
# --------------------------------------------------------------------------
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS customers (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        email VARCHAR(160) NOT NULL,
        phone VARCHAR(20),
        city VARCHAR(80),
        state CHAR(2),
        birth_date DATE,
        status ENUM('active','inactive','blocked') NOT NULL DEFAULT 'active',
        balance DECIMAL(12,2) NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_email (email)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS products (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        sku VARCHAR(30) NOT NULL,
        name VARCHAR(120) NOT NULL,
        category VARCHAR(40) NOT NULL,
        price DECIMAL(10,2) NOT NULL,
        stock INT NOT NULL DEFAULT 0,
        active TINYINT(1) NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_sku (sku)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS orders (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        customer_id BIGINT UNSIGNED NOT NULL,
        status ENUM('pending','paid','shipped','delivered','cancelled') NOT NULL DEFAULT 'pending',
        total DECIMAL(12,2) NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        KEY idx_customer (customer_id),
        CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id)
            REFERENCES customers(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS order_items (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        order_id BIGINT UNSIGNED NOT NULL,
        product_id BIGINT UNSIGNED NOT NULL,
        quantity INT NOT NULL,
        unit_price DECIMAL(10,2) NOT NULL,
        KEY idx_order (order_id),
        KEY idx_product (product_id),
        CONSTRAINT fk_items_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
        CONSTRAINT fk_items_product FOREIGN KEY (product_id) REFERENCES products(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS payments (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        order_id BIGINT UNSIGNED NOT NULL,
        method ENUM('credit_card','debit_card','pix','boleto') NOT NULL,
        amount DECIMAL(12,2) NOT NULL,
        status ENUM('approved','refused','refunded') NOT NULL DEFAULT 'approved',
        paid_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_order (order_id),
        CONSTRAINT fk_pay_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS audit_log (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        entity VARCHAR(30) NOT NULL,
        action VARCHAR(20) NOT NULL,
        ref_id BIGINT UNSIGNED,
        details VARCHAR(255),
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS sessions (
        token CHAR(32) PRIMARY KEY,
        customer_id BIGINT UNSIGNED,
        payload VARCHAR(500),
        last_seen DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]
DROP_ORDER = ["audit_log", "payments", "order_items", "orders", "sessions", "products", "customers"]

INSERT_CUSTOMER = ("INSERT INTO customers (name,email,phone,city,state,birth_date,status,balance,created_at) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)")
INSERT_PRODUCT = ("INSERT INTO products (sku,name,category,price,stock,active,created_at) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s)")


def customer_row():
    name = rand_name()
    city, uf = random.choice(CITIES)
    status = random.choices(["active", "inactive", "blocked"], [80, 15, 5])[0]
    return (name, rand_email(name), rand_phone(), city, uf, rand_birth(), status,
            rand_money(0, 5000), rand_dt(730))


def product_row():
    cat = random.choice(CATEGORIES)
    sku = f"{slug(cat)[:3].upper()}-{uuid.uuid4().hex[:10].upper()}"
    name = f"{random.choice(NOUN)} {random.choice(ADJ)} {random.choice(BRAND)}"
    return (sku, name, cat, rand_money(5, 2500), random.randint(20, 500),
            1 if random.random() < 0.95 else 0, rand_dt(730))


# --------------------------------------------------------------------------
# Conexao
# --------------------------------------------------------------------------
def make_conn(a, with_db=True):
    kw = dict(host=a.host, port=a.port, user=a.user, password=a.password,
              charset="utf8mb4", connection_timeout=10, autocommit=True)
    if with_db:
        kw["database"] = a.database
    return mysql.connector.connect(**kw)


# --------------------------------------------------------------------------
# Estatisticas
# --------------------------------------------------------------------------
class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.ok = defaultdict(int)
        self.err = defaultdict(int)          # (op, tipo_erro) -> n
        self.total_time = defaultdict(float)
        self.max_time = defaultdict(float)
        self.lat = defaultdict(lambda: deque(maxlen=5000))
        self.seen_msgs = set()
        self.ops_done = 0
        self.errs_done = 0

    def record(self, op, dt, error=None, msg=None):
        with self.lock:
            self.ops_done += 1
            if error:
                self.errs_done += 1
                self.err[(op, error)] += 1
                if (error not in self.seen_msgs) and msg:
                    self.seen_msgs.add(error)
                    print(f"  ! primeiro erro '{error}' em {op}: {msg[:160]}", flush=True)
                return
            self.ok[op] += 1
            self.total_time[op] += dt
            self.lat[op].append(dt)
            if dt > self.max_time[op]:
                self.max_time[op] = dt

    def summary(self):
        print("\n" + "=" * 86)
        print(f"{'operacao':<26}{'ok':>9}{'erros':>8}{'media ms':>11}{'p95 ms':>10}{'max ms':>10}")
        print("-" * 86)
        ops = sorted(set(self.ok) | {o for o, _ in self.err})
        for op in ops:
            lats = sorted(self.lat[op])
            p95 = lats[int(len(lats) * 0.95) - 1] * 1000 if lats else 0
            avg = (self.total_time[op] / self.ok[op] * 1000) if self.ok[op] else 0
            errs = sum(n for (o, _), n in self.err.items() if o == op)
            print(f"{op:<26}{self.ok[op]:>9}{errs:>8}{avg:>11.2f}{p95:>10.2f}{self.max_time[op] * 1000:>10.2f}")
        print("-" * 86)
        by_type = defaultdict(int)
        for (_, t), n in self.err.items():
            by_type[t] += n
        if by_type:
            print("Erros por tipo: " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
        print(f"Total de operacoes: {self.ops_done}  (erros: {self.errs_done})")
        print("=" * 86)


def classify(e):
    n = getattr(e, "errno", None)
    if n == 1213:
        return "deadlock"
    if n == 1205:
        return "lock_timeout"
    if n in (2006, 2013, 2055, 2003) or n is None:
        return "connection"
    return f"err_{n}"


# --------------------------------------------------------------------------
# Pesos por perfil:       (mixed, read, write)
# --------------------------------------------------------------------------
WEIGHTS = {
    # INSERT
    "insert_customer":         (6, 1, 12),
    "insert_customers_bulk":   (1, 0, 3),
    "insert_product":          (1, 0, 2),
    "create_order":            (12, 3, 25),   # transacao com varios INSERT/UPDATE + SELECT FOR UPDATE
    "audit_insert":            (4, 0, 8),
    # UPDATE
    "update_customer":         (6, 1, 10),
    "update_product_price":    (3, 0, 6),
    "update_order_status":     (6, 1, 10),
    "update_bulk_orders":      (1, 0, 2),
    "restock":                 (1, 0, 2),
    "update_category_price":   (0.5, 0, 1),
    # DELETE
    "delete_audit":            (2, 0, 4),
    "delete_customer":         (1, 0, 2),
    "delete_cancelled_orders": (1, 0, 2),
    "session_cleanup":         (1, 0, 2),
    # UPSERT / REPLACE
    "upsert_customer":         (3, 0, 5),
    "session_replace":         (4, 0, 8),
    # SELECT
    "select_pk":               (15, 40, 3),
    "select_orders_customer":  (8, 25, 2),
    "select_search_like":      (3, 6, 0.5),
    "select_by_city":          (3, 6, 0.5),
    "select_date_range":       (3, 6, 0.5),
    "select_report_category":  (1, 3, 0.2),
    "select_top_customers":    (1, 2, 0.2),
    "select_deep_pagination":  (1, 2, 0.2),
    # TRANSACOES ESPECIAIS
    "tx_rollback":             (2, 0, 3),
    "tx_hot_row_lock":         (2, 0, 4),
    "tx_deadlock":             (1, 0, 3),
    # DDL (so com --ddl)
    "ddl_temp_report":         (0, 0, 0),
}
PROFILE_IDX = {"mixed": 0, "read": 1, "write": 2}


# --------------------------------------------------------------------------
# Worker
# --------------------------------------------------------------------------
class Worker(threading.Thread):
    def __init__(self, wid, args, stats, stop_evt, weights):
        super().__init__(daemon=True, name=f"worker-{wid}")
        self.wid, self.a, self.stats, self.stop_evt = wid, args, stats, stop_evt
        self.names = [n for n, w in weights.items() if w > 0]
        self.w = [weights[n] for n in self.names]
        self.conn = None
        self.cur = None
        self.cache = {}

    # ---- infra ----
    def connect(self):
        self.close()
        self.conn = make_conn(self.a)
        self.cur = self.conn.cursor(buffered=True)
        self.cur.execute("SET SESSION innodb_lock_wait_timeout = 5")

    def close(self):
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass
        self.conn = self.cur = None

    def run(self):
        while not self.stop_evt.is_set():
            op = random.choices(self.names, self.w)[0]
            self.execute(op)
            if self.a.think > 0:
                time.sleep(random.uniform(0, self.a.think))
        self.close()

    def execute(self, op):
        t0 = time.perf_counter()
        try:
            if not self.conn or not self.conn.is_connected():
                self.connect()
            getattr(self, "op_" + op)()
            self.stats.record(op, time.perf_counter() - t0)
        except mysql.connector.Error as e:
            kind = classify(e)
            try:
                if self.conn:
                    self.conn.rollback()
            except Exception:
                pass
            if kind == "connection":
                self.close()
                time.sleep(0.5)
            self.stats.record(op, time.perf_counter() - t0, error=kind, msg=str(e))

    def bounds(self, table):
        b = self.cache.get(table)
        now = time.time()
        if not b or now - b[2] > 15:
            self.cur.execute(f"SELECT COALESCE(MIN(id),0), COALESCE(MAX(id),0) FROM {table}")
            lo, hi = self.cur.fetchone()
            b = (int(lo), int(hi), now)
            self.cache[table] = b
        return b

    def rid(self, table):
        lo, hi, _ = self.bounds(table)
        return random.randint(lo, hi) if hi else 0

    def existing_id(self, table):
        rid = self.rid(table)
        if not rid:
            return 0
        self.cur.execute(f"SELECT id FROM {table} WHERE id >= %s ORDER BY id LIMIT 1", (rid,))
        r = self.cur.fetchone()
        return r[0] if r else 0

    def audit(self, entity, action, ref, details=""):
        self.cur.execute("INSERT INTO audit_log (entity,action,ref_id,details) VALUES (%s,%s,%s,%s)",
                         (entity, action, ref, details[:250]))

    # ======================= INSERT =======================
    def op_insert_customer(self):
        self.cur.execute(INSERT_CUSTOMER, customer_row())

    def op_insert_customers_bulk(self):
        rows = [customer_row() for _ in range(random.randint(20, 200))]
        self.cur.executemany(INSERT_CUSTOMER, rows)

    def op_insert_product(self):
        self.cur.execute(INSERT_PRODUCT, product_row())

    def op_audit_insert(self):
        rows = [(random.choice(["customer", "order", "product"]),
                 random.choice(["create", "update", "delete", "login"]),
                 random.randint(1, 100000), uuid.uuid4().hex * 2)
                for _ in range(random.randint(1, 30))]
        self.cur.executemany("INSERT INTO audit_log (entity,action,ref_id,details) VALUES (%s,%s,%s,%s)", rows)

    def op_create_order(self):
        """Transacao completa: lock de estoque, pedido, itens, baixa de estoque, pagamento, auditoria."""
        cid = self.existing_id("customers")
        pids = sorted({self.rid("products") for _ in range(random.randint(1, 5))} - {0})
        if not cid or not pids:
            return
        self.cur.execute("START TRANSACTION")
        fmt = ",".join(["%s"] * len(pids))
        self.cur.execute(f"SELECT id, price, stock FROM products WHERE id IN ({fmt}) AND active=1 "
                         f"ORDER BY id FOR UPDATE", pids)
        items, total = [], Decimal(0)
        for pid, price, stock in self.cur.fetchall():
            qty = random.randint(1, 3)
            if stock >= qty:
                items.append((pid, qty, price))
                total += price * qty
        if not items:
            self.conn.rollback()
            return
        self.cur.execute("INSERT INTO orders (customer_id,status,total) VALUES (%s,'pending',%s)", (cid, total))
        oid = self.cur.lastrowid
        self.cur.executemany("INSERT INTO order_items (order_id,product_id,quantity,unit_price) VALUES (%s,%s,%s,%s)",
                             [(oid, p, q, pr) for p, q, pr in items])
        for p, q, _ in items:
            self.cur.execute("UPDATE products SET stock = stock - %s WHERE id=%s", (q, p))
        if random.random() < 0.7:
            self.cur.execute("INSERT INTO payments (order_id,method,amount,status) VALUES (%s,%s,%s,'approved')",
                             (oid, random.choice(PAY_METHODS), total))
            self.cur.execute("UPDATE orders SET status='paid' WHERE id=%s", (oid,))
        self.audit("order", "create", oid, f"cliente={cid} itens={len(items)} total={total}")
        self.conn.commit()

    # ======================= UPDATE =======================
    def op_update_customer(self):
        city, uf = random.choice(CITIES)
        self.cur.execute("UPDATE customers SET phone=%s, city=%s, state=%s WHERE id=%s",
                         (rand_phone(), city, uf, self.rid("customers")))

    def op_update_product_price(self):
        self.cur.execute("UPDATE products SET price = ROUND(price * %s, 2) WHERE id=%s",
                         (round(random.uniform(0.9, 1.15), 3), self.rid("products")))

    def op_update_order_status(self):
        self.cur.execute("UPDATE orders SET status=%s WHERE id=%s",
                         (random.choices(ORDER_STATUS, ORDER_STATUS_W)[0], self.rid("orders")))

    def op_update_bulk_orders(self):
        self.cur.execute("UPDATE orders SET status='shipped' WHERE status='paid' "
                         "AND created_at < NOW() - INTERVAL %s DAY LIMIT %s",
                         (random.randint(1, 120), random.randint(50, 500)))

    def op_restock(self):
        self.cur.execute("UPDATE products SET stock = stock + %s WHERE stock < 20 LIMIT %s",
                         (random.randint(50, 300), random.randint(20, 200)))

    def op_update_category_price(self):  # UPDATE em massa sem indice em category
        self.cur.execute("UPDATE products SET price = ROUND(price * %s, 2) WHERE category=%s",
                         (round(random.uniform(0.97, 1.05), 3), random.choice(CATEGORIES)))

    # ======================= DELETE =======================
    def op_delete_audit(self):
        self.cur.execute("DELETE FROM audit_log WHERE created_at < NOW() - INTERVAL %s MINUTE LIMIT %s",
                         (random.randint(1, 30), random.randint(100, 1000)))

    def op_delete_customer(self):  # cascata em orders/order_items/payments
        self.cur.execute("DELETE FROM customers WHERE id=%s AND status <> 'active'", (self.rid("customers"),))

    def op_delete_cancelled_orders(self):
        self.cur.execute("DELETE FROM orders WHERE status='cancelled' ORDER BY id LIMIT %s",
                         (random.randint(5, 50),))

    def op_session_cleanup(self):
        self.cur.execute("DELETE FROM sessions WHERE last_seen < NOW() - INTERVAL %s SECOND",
                         (random.randint(30, 600),))

    # ======================= UPSERT / REPLACE =======================
    def op_upsert_customer(self):
        row = list(customer_row())
        if random.random() < 0.5:  # reaproveita e-mail existente para forcar o ON DUPLICATE KEY
            self.cur.execute("SELECT email FROM customers WHERE id >= %s ORDER BY id LIMIT 1",
                             (self.rid("customers"),))
            r = self.cur.fetchone()
            if r:
                row[1] = r[0]
        self.cur.execute(
            "INSERT INTO customers (name,email,phone,city,state,birth_date,status,balance,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) AS nv "
            "ON DUPLICATE KEY UPDATE balance = customers.balance + nv.balance, phone = nv.phone, "
            "updated_at = NOW()", row)

    def op_session_replace(self):
        token = "%032x" % random.randint(0, SESSION_POOL)
        self.cur.execute("REPLACE INTO sessions (token,customer_id,payload,last_seen) VALUES (%s,%s,%s,NOW())",
                         (token, self.rid("customers"), uuid.uuid4().hex * random.randint(1, 10)))

    # ======================= SELECT =======================
    def op_select_pk(self):
        self.cur.execute("SELECT * FROM customers WHERE id=%s", (self.rid("customers"),))
        self.cur.fetchall()

    def op_select_orders_customer(self):
        self.cur.execute(
            "SELECT o.id,o.status,o.total,o.created_at,p.name,oi.quantity,oi.unit_price "
            "FROM orders o JOIN order_items oi ON oi.order_id=o.id JOIN products p ON p.id=oi.product_id "
            "WHERE o.customer_id=%s ORDER BY o.created_at DESC LIMIT 50", (self.rid("customers"),))
        self.cur.fetchall()

    def op_select_search_like(self):  # LIKE com wildcard no inicio -> full scan
        self.cur.execute("SELECT id,name,city FROM customers WHERE name LIKE %s LIMIT 50",
                         (f"%{random.choice(FIRST)} {random.choice(LAST)}%",))
        self.cur.fetchall()

    def op_select_by_city(self):  # sem indice em city
        self.cur.execute("SELECT id,name,email FROM customers WHERE city=%s AND status='active' LIMIT 100",
                         (random.choice(CITIES)[0],))
        self.cur.fetchall()

    def op_select_date_range(self):  # sem indice em created_at
        start = rand_dt(365)
        self.cur.execute("SELECT id,customer_id,total,status FROM orders WHERE created_at BETWEEN %s AND %s "
                         "ORDER BY created_at DESC LIMIT 50",
                         (start, start + timedelta(days=random.randint(1, 15))))
        self.cur.fetchall()

    def op_select_report_category(self):
        self.cur.execute(
            "SELECT p.category, COUNT(*) itens, SUM(oi.quantity*oi.unit_price) receita "
            "FROM order_items oi JOIN orders o ON o.id=oi.order_id JOIN products p ON p.id=oi.product_id "
            "WHERE o.created_at >= NOW() - INTERVAL %s DAY AND o.status <> 'cancelled' "
            "GROUP BY p.category ORDER BY receita DESC", (random.randint(7, 365),))
        self.cur.fetchall()

    def op_select_top_customers(self):
        self.cur.execute(
            "SELECT c.id, c.name, SUM(o.total) t FROM customers c JOIN orders o ON o.customer_id=c.id "
            "WHERE o.status IN ('paid','shipped','delivered') GROUP BY c.id, c.name ORDER BY t DESC LIMIT 10")
        self.cur.fetchall()

    def op_select_deep_pagination(self):
        _, hi, _ = self.bounds("orders")
        self.cur.execute("SELECT * FROM orders ORDER BY id LIMIT 20 OFFSET %s", (random.randint(0, max(hi - 20, 0)),))
        self.cur.fetchall()

    # ======================= TRANSACOES ESPECIAIS =======================
    def op_tx_rollback(self):
        self.cur.execute("START TRANSACTION")
        self.cur.execute(INSERT_CUSTOMER, customer_row())
        self.audit("customer", "create", self.cur.lastrowid, "sera revertido")
        self.cur.execute("UPDATE products SET stock = stock + 1 WHERE id=%s", (self.rid("products"),))
        self.conn.rollback()

    def op_tx_hot_row_lock(self):  # segura lock numa linha "quente" -> gera lock waits
        lo, hi, _ = self.bounds("products")
        if not hi:
            return
        pid = random.randint(lo, min(hi, lo + HOT - 1))
        self.cur.execute("START TRANSACTION")
        self.cur.execute("SELECT stock FROM products WHERE id=%s FOR UPDATE", (pid,))
        self.cur.fetchall()
        time.sleep(random.uniform(0.02, 0.2))
        self.cur.execute("UPDATE products SET stock = stock + %s WHERE id=%s", (random.randint(1, 5), pid))
        self.conn.commit()

    def op_tx_deadlock(self):  # ordem aleatoria de locks entre threads -> deadlock
        lo, hi, _ = self.bounds("products")
        top = min(hi, lo + HOT - 1)
        if not hi or top - lo < 1:
            return
        a, b = random.sample(range(lo, top + 1), 2)
        self.cur.execute("START TRANSACTION")
        self.cur.execute("UPDATE products SET stock = stock + 1 WHERE id=%s", (a,))
        time.sleep(0.05)
        self.cur.execute("UPDATE products SET stock = stock + 1 WHERE id=%s", (b,))
        self.conn.commit()

    # ======================= DDL (--ddl) =======================
    def op_ddl_temp_report(self):
        t = f"tmp_report_{self.wid}"
        self.cur.execute(f"DROP TABLE IF EXISTS {t}")
        self.cur.execute(
            f"CREATE TABLE {t} AS SELECT c.state, COUNT(*) qtd, SUM(o.total) total "
            f"FROM orders o JOIN customers c ON c.id=o.customer_id GROUP BY c.state")
        self.cur.execute(f"ALTER TABLE {t} ADD INDEX idx_state (state)")
        self.cur.execute(f"SELECT * FROM {t}")
        self.cur.fetchall()
        self.cur.execute(f"DROP TABLE {t}")


# --------------------------------------------------------------------------
# Reporter: throughput da aplicacao + metricas do servidor
# --------------------------------------------------------------------------
STATUS_COUNTERS = ["Questions", "Com_select", "Com_insert", "Com_update", "Com_delete", "Select_scan",
                   "Created_tmp_disk_tables", "Innodb_row_lock_waits", "Slow_queries"]
STATUS_GAUGES = ["Threads_running", "Threads_connected"]


def server_status(cur):
    names = ",".join(f"'{v}'" for v in STATUS_COUNTERS + STATUS_GAUGES)
    cur.execute(f"SHOW GLOBAL STATUS WHERE Variable_name IN ({names})")
    return {k: int(v) for k, v in cur.fetchall()}


def reporter(args, stats, stop_evt):
    try:
        conn = make_conn(args, with_db=False)
        cur = conn.cursor()
    except mysql.connector.Error as e:
        print(f"Reporter sem conexao: {e}")
        return
    prev, prev_ops, prev_err, prev_t = server_status(cur), 0, 0, time.time()
    t_start = prev_t
    while not stop_evt.wait(args.report):
        now = time.time()
        dt = now - prev_t
        cur_s = server_status(cur)
        ops, errs = stats.ops_done, stats.errs_done
        d = {k: (cur_s[k] - prev[k]) for k in STATUS_COUNTERS}
        print(f"[{int(now - t_start):>4}s] app: {(ops - prev_ops) / dt:7.1f} ops/s err+{errs - prev_err:<4} | "
              f"srv: q/s={d['Questions'] / dt:7.1f} sel={d['Com_select']} ins={d['Com_insert']} "
              f"upd={d['Com_update']} del={d['Com_delete']} scan={d['Select_scan']} "
              f"tmpdisk={d['Created_tmp_disk_tables']} lockwait={d['Innodb_row_lock_waits']} "
              f"slow={d['Slow_queries']} | run={cur_s['Threads_running']} conn={cur_s['Threads_connected']}",
              flush=True)
        prev, prev_ops, prev_err, prev_t = cur_s, ops, errs, now
    conn.close()


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------
def cmd_setup(a):
    conn = make_conn(a, with_db=False)
    cur = conn.cursor()
    if a.reset:
        cur.execute(f"DROP DATABASE IF EXISTS `{a.database}`")
    cur.execute(f"CREATE DATABASE IF NOT EXISTS `{a.database}` CHARACTER SET utf8mb4")
    cur.execute(f"USE `{a.database}`")
    for ddl in SCHEMA:
        cur.execute(ddl)
    print(f"Database '{a.database}' e tabelas prontas.")


def cmd_drop(a):
    if not a.yes:
        sys.exit("Use --yes para confirmar a remocao do database.")
    conn = make_conn(a, with_db=False)
    conn.cursor().execute(f"DROP DATABASE IF EXISTS `{a.database}`")
    print(f"Database '{a.database}' removido.")


def cmd_seed(a):
    conn = make_conn(a)
    cur = conn.cursor()
    B = 1000
    t0 = time.time()

    for start in range(0, a.customers, B):
        n = min(B, a.customers - start)
        cur.executemany(INSERT_CUSTOMER, [customer_row() for _ in range(n)])
        print(f"\rclientes: {start + n}/{a.customers}", end="", flush=True)
    print()

    for start in range(0, a.products, B):
        n = min(B, a.products - start)
        cur.executemany(INSERT_PRODUCT, [product_row() for _ in range(n)])
        print(f"\rprodutos: {start + n}/{a.products}", end="", flush=True)
    print()

    cur.execute("SELECT id FROM customers")
    cust_ids = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT id, price FROM products")
    prods = cur.fetchall()
    if not cust_ids or not prods:
        sys.exit("Sem clientes/produtos para gerar pedidos.")
    cur.execute("SELECT COALESCE(MAX(id),0) FROM orders")
    next_id = cur.fetchone()[0] + 1

    for start in range(0, a.orders, B):
        n = min(B, a.orders - start)
        orders, items, pays = [], [], []
        for i in range(n):
            oid = next_id + start + i
            chosen = random.sample(prods, min(random.randint(1, 5), len(prods)))
            total = Decimal(0)
            for pid, price in chosen:
                q = random.randint(1, 4)
                total += price * q
                items.append((oid, pid, q, price))
            created = rand_dt(365)
            status = random.choices(ORDER_STATUS, ORDER_STATUS_W)[0]
            orders.append((oid, random.choice(cust_ids), status, total, created, created))
            if status in ("paid", "shipped", "delivered"):
                pays.append((oid, random.choice(PAY_METHODS), total, created))
        cur.executemany("INSERT INTO orders (id,customer_id,status,total,created_at,updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s)", orders)
        cur.executemany("INSERT INTO order_items (order_id,product_id,quantity,unit_price) "
                        "VALUES (%s,%s,%s,%s)", items)
        cur.executemany("INSERT INTO payments (order_id,method,amount,paid_at) VALUES (%s,%s,%s,%s)", pays)
        print(f"\rpedidos: {start + n}/{a.orders}", end="", flush=True)
    print(f"\nSeed concluido em {time.time() - t0:.1f}s")


def cmd_run(a):
    if a.rand_seed is not None:
        random.seed(a.rand_seed)
    idx = PROFILE_IDX[a.profile]
    weights = {k: v[idx] for k, v in WEIGHTS.items()}
    if a.ddl:
        weights["ddl_temp_report"] = 0.3

    stats, stop_evt = Stats(), threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_evt.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_evt.set())

    print(f"Iniciando: {a.threads} threads, {a.duration}s, perfil={a.profile}, ddl={a.ddl}  (Ctrl+C para parar)")
    workers = [Worker(i, a, stats, stop_evt, weights) for i in range(a.threads)]
    rep = threading.Thread(target=reporter, args=(a, stats, stop_evt), daemon=True)
    for w in workers:
        w.start()
        time.sleep(0.05)
    rep.start()

    end = time.time() + a.duration
    while time.time() < end and not stop_evt.is_set():
        time.sleep(0.2)
    stop_evt.set()
    for w in workers:
        w.join(timeout=15)
    rep.join(timeout=5)
    stats.summary()


def build_parser():
    p = argparse.ArgumentParser(description="Simulador de carga MySQL 8.4")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--host", default=os.getenv("MYSQL_HOST", "127.0.0.1"))
        sp.add_argument("--port", type=int, default=int(os.getenv("MYSQL_PORT", "3306")))
        sp.add_argument("--user", default=os.getenv("MYSQL_USER", "root"))
        sp.add_argument("--password", default=os.getenv("MYSQL_PWD", ""))
        sp.add_argument("--database", default=os.getenv("MYSQL_DB", "sim_db"))

    s = sub.add_parser("setup", help="cria database e tabelas"); common(s)
    s.add_argument("--reset", action="store_true", help="apaga o database antes de criar")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("seed", help="carga inicial de dados"); common(s)
    s.add_argument("--customers", type=int, default=20000)
    s.add_argument("--products", type=int, default=2000)
    s.add_argument("--orders", type=int, default=40000)
    s.set_defaults(func=cmd_seed)

    s = sub.add_parser("run", help="executa a simulacao"); common(s)
    s.add_argument("--threads", type=int, default=8)
    s.add_argument("--duration", type=int, default=300, help="segundos")
    s.add_argument("--profile", choices=list(PROFILE_IDX), default="mixed")
    s.add_argument("--report", type=int, default=10, help="intervalo do relatorio (s)")
    s.add_argument("--think", type=float, default=0.0, help="pausa maxima aleatoria entre ops (s)")
    s.add_argument("--ddl", action="store_true", help="inclui operacoes DDL (CREATE/ALTER/DROP)")
    s.add_argument("--rand-seed", type=int, default=None, help="semente para reproduzir a carga")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("drop", help="remove o database"); common(s)
    s.add_argument("--yes", action="store_true")
    s.set_defaults(func=cmd_drop)
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    try:
        args.func(args)
    except mysql.connector.Error as e:
        sys.exit(f"Erro MySQL: {e}")
