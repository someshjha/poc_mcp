# Phase 1 — Liquibase Schema, Roles, RLS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and prove, against a local Dockerized Postgres, the database-level access-control core of the scoped financial-data MCP system: a Liquibase-managed schema, four task-scope roles with table-level `GRANT`s, and identity-scoped Row-Level Security — verified by a script that talks to Postgres directly, independent of any server.

**Architecture:** `docker-compose.yml` runs Postgres 16 plus a one-shot Liquibase container (custom-built with the Postgres JDBC driver) that applies changelogs from `db/changelog/`. Four formatted-SQL changesets build the schema, roles/grants, RLS policies, and seed data in order. `scripts/verify_scopes.py` connects as a low-privilege pooled login role, impersonates each of four demo users via `SET LOCAL ROLE` + `SET LOCAL app.current_user_id`, and asserts the access boundary holds for every combination the design specifies.

**Tech Stack:** Docker Compose, Postgres 16, Liquibase (official `liquibase/liquibase` image, formatted-SQL changelogs), Python 3 + `psycopg2-binary`.

## Global Constraints

- No hand-run SQL scripts — every schema change is a Liquibase changeset in `db/changelog/`, applied via `docker compose run --rm liquibase`.
- Local dev-only credentials (`postgres_dev_only`, `app_pool_dev_only`) are acceptable and intentional for this local, non-production demo — do not add a secrets manager or `.env` indirection for this phase.
- `account_owners` is a join table (`account_id`, `user_id`), not a scalar column on `accounts` — `ACC-1001` must be owned by both `bob.risk` and `carol.trader` to prove task-scope and user-identity are independent axes.
- Demo users and their task-scope role: `alice.research`→`equity_research`, `bob.risk`→`portfolio_risk`, `carol.trader`→`trade_execution`, `dave.support`→`client_support`. Account ownership: `bob.risk` owns `ACC-1001` and `ACC-2001`; `carol.trader` owns `ACC-1001`; `dave.support` owns `ACC-1002`; `alice.research` owns none.
- Fixture data (accounts, positions, orders, transactions, market_data, fundamentals) must match the browser-only mock demo's numbers exactly (same accounts, same positions, same prices) for narrative continuity between the two demos — see Task 4 for the exact values.
- This plan does not touch Keycloak, the MCP server, k8s, or Argo CD — those are later phases, out of scope here.

---

### Task 1: Docker Compose harness + Liquibase toolchain

**Files:**
- Create: `docker-compose.yml`
- Create: `db/liquibase.Dockerfile`
- Create: `db/liquibase.properties`
- Create: `db/changelog/changelog-master.xml`
- Create: `.gitignore` (repo root — if it doesn't already exist from worktree setup, otherwise append)

**Interfaces:**
- Produces: a working `docker compose run --rm liquibase` command that applies whatever changesets `db/changelog/changelog-master.xml` includes, against a Postgres reachable at the Compose service name `postgres:5432`. All later tasks add changesets to this same changelog.

- [ ] **Step 1: Create the Postgres + Liquibase Compose file**

Create `docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres_dev_only
      POSTGRES_DB: financial_mcp
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d financial_mcp"]
      interval: 2s
      timeout: 3s
      retries: 20

  liquibase:
    build:
      context: ./db
      dockerfile: liquibase.Dockerfile
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - ./db:/liquibase/changelog
    working_dir: /liquibase/changelog
    entrypoint: ["liquibase"]
    command: ["--defaultsFile=liquibase.properties", "update"]
    profiles: ["tools"]
```

The `liquibase` service has `profiles: ["tools"]` so `docker compose up` never starts it automatically — it only runs when explicitly invoked with `docker compose run --rm liquibase`.

- [ ] **Step 2: Create the Liquibase image with the Postgres JDBC driver baked in**

The official `liquibase/liquibase` image does not bundle a Postgres driver. Create `db/liquibase.Dockerfile`:

```dockerfile
FROM liquibase/liquibase
ADD https://repo1.maven.org/maven2/org/postgresql/postgresql/42.7.4/postgresql-42.7.4.jar /liquibase/lib/postgresql.jar
USER root
RUN chmod 644 /liquibase/lib/postgresql.jar
USER liquibase
```

The `chmod` step is required: `ADD <url>` downloads the file as root with `600` permissions, which the image's non-root `liquibase` user cannot read without this fix — verified directly; omitting it produces `Unexpected error running Liquibase: /liquibase/lib/postgresql.jar (Permission denied)`.

- [ ] **Step 3: Create the Liquibase properties file**

Create `db/liquibase.properties`:

```properties
changelog-file: changelog/changelog-master.xml
url: jdbc:postgresql://postgres:5432/financial_mcp
username: postgres
password: postgres_dev_only
```

The `postgres` superuser is used here because changesets need to `CREATE ROLE` and `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`. The low-privilege `app_pool` login role created in Task 2 is what the verification script (Task 5) and the future MCP server actually connect as at runtime — the migration runner and the application connection are intentionally different roles.

- [ ] **Step 4: Create the (initially empty) master changelog**

Create `db/changelog/changelog-master.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<databaseChangeLog
    xmlns="http://www.liquibase.org/xml/ns/dbchangelog"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:schemaLocation="http://www.liquibase.org/xml/ns/dbchangelog
      http://www.liquibase.org/xml/ns/dbchangelog/dbchangelog-latest.xsd">
  <include file="changelog/001-schema.sql" relativeToChangelogFile="false"/>
</databaseChangeLog>
```

(Task 2 creates `001-schema.sql`; this include is added now so Step 5 below has something real to run. Later tasks append one `<include>` line each for `002-roles-grants.sql`, `003-rls.sql`, `004-seed.sql`, in that order — do not reorder existing includes.)

- [ ] **Step 5: Add `.gitignore`**

Create (or append to) `.gitignore`:

```
.worktrees/
```

(If this file already exists from worktree setup with this line present, skip.)

- [ ] **Step 6: Verify — bring up Postgres and confirm it is empty**

Run:
```bash
docker compose up -d postgres
```
Wait for health (poll until healthy, e.g. `docker compose ps` shows `postgres` as `healthy`; typically under 10s). Then:
```bash
docker compose exec postgres psql -U postgres -d financial_mcp -c "\dt"
```
Expected: `Did not find any relations.` (no tables yet — Task 2 hasn't been applied).

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml db/liquibase.Dockerfile db/liquibase.properties db/changelog/changelog-master.xml .gitignore
git commit -m "Add Docker Compose Postgres + Liquibase toolchain"
```

---

### Task 2: Schema changeset

**Files:**
- Create: `db/changelog/001-schema.sql`

**Interfaces:**
- Consumes: none (first real changeset).
- Produces: tables `accounts`, `account_owners`, `positions`, `orders`, `transactions`, `market_data`, `fundamentals`, `audit_log` — every later task's changesets and the verification script depend on these exact table/column names.

- [ ] **Step 1: Write the schema changeset**

Create `db/changelog/001-schema.sql`:

```sql
--liquibase formatted sql

--changeset someshjha:001-schema
create table accounts (
  account_id text primary key,
  owner_name text not null,
  account_type text not null,
  opened_at date not null
);

create table account_owners (
  account_id text not null references accounts(account_id),
  user_id text not null,
  primary key (account_id, user_id)
);

create table positions (
  account_id text not null references accounts(account_id),
  ticker text not null,
  quantity numeric not null,
  avg_cost numeric not null,
  primary key (account_id, ticker)
);

create table orders (
  order_id text primary key,
  account_id text not null references accounts(account_id),
  ticker text not null,
  side text not null,
  quantity numeric not null,
  status text not null,
  submitted_at timestamptz not null
);

create table transactions (
  transaction_id text primary key,
  account_id text not null references accounts(account_id),
  type text not null,
  amount numeric not null,
  occurred_at timestamptz not null
);

create table market_data (
  ticker text primary key,
  price numeric not null,
  change_pct numeric not null,
  as_of timestamptz not null
);

create table fundamentals (
  ticker text primary key,
  pe_ratio numeric not null,
  market_cap text not null,
  sector text not null
);

create table audit_log (
  id serial primary key,
  at timestamptz not null default now(),
  scope text,
  user_id text,
  kind text not null,
  name text not null,
  params jsonb,
  decision text not null,
  detail text
);
```

- [ ] **Step 2: Apply and verify**

Run:
```bash
docker compose run --rm liquibase
```
Expected: `UPDATE SUMMARY` showing `Run: 1`, exit code 0.

Run:
```bash
docker compose exec postgres psql -U postgres -d financial_mcp -c "\dt"
```
Expected: 8 tables listed — `accounts`, `account_owners`, `positions`, `orders`, `transactions`, `market_data`, `fundamentals`, `audit_log` (plus Liquibase's own `databasechangelog`/`databasechangeloglock`).

- [ ] **Step 3: Commit**

```bash
git add db/changelog/001-schema.sql
git commit -m "$(cat <<'EOF'
Add schema changeset: accounts, positions, orders, transactions, market_data, fundamentals, account_owners, audit_log

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Roles and grants changeset

**Files:**
- Create: `db/changelog/002-roles-grants.sql`
- Modify: `db/changelog/changelog-master.xml`

**Interfaces:**
- Consumes: tables from Task 2.
- Produces: Postgres roles `equity_research`, `portfolio_risk`, `trade_execution`, `client_support` (each `NOLOGIN`, table-scoped `GRANT`s) and a login role `app_pool` (member of all four) — Task 4's RLS policies and Task 5's verification script both depend on these exact role names, and on `app_pool` being able to `SET ROLE` to any of the four.

- [ ] **Step 1: Add the include to the master changelog**

In `db/changelog/changelog-master.xml`, add a second `<include>` line right after the existing one:

```xml
  <include file="changelog/001-schema.sql" relativeToChangelogFile="false"/>
  <include file="changelog/002-roles-grants.sql" relativeToChangelogFile="false"/>
```

- [ ] **Step 2: Write the roles and grants changeset**

Create `db/changelog/002-roles-grants.sql`:

```sql
--liquibase formatted sql

--changeset someshjha:002-roles-grants
create role equity_research;
grant select on market_data, fundamentals to equity_research;
grant select on account_owners to equity_research;

create role portfolio_risk;
grant select on positions, market_data to portfolio_risk;
grant select on account_owners to portfolio_risk;

create role trade_execution;
grant select on market_data to trade_execution;
grant select, insert on orders to trade_execution;
grant select on account_owners to trade_execution;

create role client_support;
grant select on accounts, transactions to client_support;
grant select on account_owners to client_support;

grant select, insert on audit_log to equity_research, portfolio_risk, trade_execution, client_support;

create role app_pool login password 'app_pool_dev_only';
grant equity_research, portfolio_risk, trade_execution, client_support to app_pool;
```

`account_owners` is granted to every role that can reach an account-owned table (Task 4's RLS policies subquery `account_owners` from `accounts`/`positions`/`orders`/`transactions` policies — without this grant, that subquery would fail with `permission denied for table account_owners` for any role lacking it, even though the role has rights on the table the policy protects). `equity_research` never touches an owned table but is granted it too, for consistency and because it costs nothing.

- [ ] **Step 3: Apply and verify**

Run:
```bash
docker compose run --rm liquibase
```
Expected: `Run: 1` (the new changeset only — Liquibase skips 001, already applied).

Run:
```bash
docker compose exec postgres psql -U postgres -d financial_mcp -c "\du"
```
Expected: `equity_research`, `portfolio_risk`, `trade_execution`, `client_support` (no login), and `app_pool` (login) listed among the roles.

Run:
```bash
docker compose exec postgres psql -U postgres -d financial_mcp -c "select rolname from pg_roles r join pg_auth_members m on m.member = r.oid join pg_roles g on g.oid = m.roleid where g.rolname = 'app_pool';"
```
Expected: empty (this query is backwards — `app_pool` is the *member*, not the *group*). Instead run:
```bash
docker compose exec postgres psql -U postgres -d financial_mcp -c "select g.rolname as granted_role from pg_auth_members m join pg_roles g on g.oid = m.roleid join pg_roles r on r.oid = m.member where r.rolname = 'app_pool';"
```
Expected: 4 rows — `equity_research`, `portfolio_risk`, `trade_execution`, `client_support`.

- [ ] **Step 4: Commit**

```bash
git add db/changelog/002-roles-grants.sql db/changelog/changelog-master.xml
git commit -m "$(cat <<'EOF'
Add task-scope roles, table grants, and app_pool login role

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Row-Level Security changeset

**Files:**
- Create: `db/changelog/003-rls.sql`
- Modify: `db/changelog/changelog-master.xml`

**Interfaces:**
- Consumes: tables from Task 2, `account_owners` grants from Task 3.
- Produces: RLS enabled + an `owner_only` policy on `accounts`, `positions`, `orders`, `transactions`, gated on `current_setting('app.current_user_id', true)`. Task 5's verification script sets this via `SET LOCAL app.current_user_id = '<demo user>'` before every query.

- [ ] **Step 1: Add the include**

In `db/changelog/changelog-master.xml`, add a third `<include>` line:

```xml
  <include file="changelog/002-roles-grants.sql" relativeToChangelogFile="false"/>
  <include file="changelog/003-rls.sql" relativeToChangelogFile="false"/>
```

- [ ] **Step 2: Write the RLS changeset**

Create `db/changelog/003-rls.sql`:

```sql
--liquibase formatted sql

--changeset someshjha:003-rls
alter table accounts enable row level security;
create policy owner_only on accounts
  using (account_id in (
    select account_id from account_owners
    where user_id = current_setting('app.current_user_id', true)
  ));

alter table positions enable row level security;
create policy owner_only on positions
  using (account_id in (
    select account_id from account_owners
    where user_id = current_setting('app.current_user_id', true)
  ));

alter table orders enable row level security;
create policy owner_only on orders
  using (account_id in (
    select account_id from account_owners
    where user_id = current_setting('app.current_user_id', true)
  ));

alter table transactions enable row level security;
create policy owner_only on transactions
  using (account_id in (
    select account_id from account_owners
    where user_id = current_setting('app.current_user_id', true)
  ));
```

Each policy is created without an explicit `FOR`/`WITH CHECK` clause, which defaults to `FOR ALL` with the `USING` expression also serving as the `WITH CHECK` — this has been verified directly against Postgres 16: an `INSERT` for a row whose `account_id` is not in the current user's `account_owners` rows is rejected with `new row violates row-level security policy`, not silently allowed. `market_data` and `fundamentals` are intentionally not RLS-gated — they are reference data with no owning account.

- [ ] **Step 3: Apply and verify**

Run:
```bash
docker compose run --rm liquibase
```
Expected: `Run: 1`.

Run:
```bash
docker compose exec postgres psql -U postgres -d financial_mcp -c "select tablename, rowsecurity from pg_tables where schemaname = 'public' and tablename in ('accounts','positions','orders','transactions','market_data','fundamentals') order by tablename;"
```
Expected: `rowsecurity = t` for `accounts`, `orders`, `positions`, `transactions`; `rowsecurity = f` for `fundamentals`, `market_data`.

- [ ] **Step 4: Commit**

```bash
git add db/changelog/003-rls.sql db/changelog/changelog-master.xml
git commit -m "$(cat <<'EOF'
Add Row-Level Security policies scoped to account_owners

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Seed data, verification script, and README

**Files:**
- Create: `db/changelog/004-seed.sql`
- Modify: `db/changelog/changelog-master.xml`
- Create: `scripts/verify_scopes.py`
- Create: `scripts/requirements.txt`
- Create: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1-4 — the four demo users/roles/ownership from Global Constraints, the table names from Task 2, the role names from Task 3, the RLS behavior from Task 4.
- Produces: nothing further tasks depend on (this is the last task of Phase 1) — but establishes the exact fixture data values that the later MCP-server phase's tool implementations will read against, and that must match the browser-only mock demo (`financial-mcp/mock-mcp-server.js` in the `someshjha.github.io` repo) exactly.

- [ ] **Step 1: Add the include**

In `db/changelog/changelog-master.xml`, add a fourth `<include>` line:

```xml
  <include file="changelog/003-rls.sql" relativeToChangelogFile="false"/>
  <include file="changelog/004-seed.sql" relativeToChangelogFile="false"/>
```

- [ ] **Step 2: Write the seed changeset**

Create `db/changelog/004-seed.sql`:

```sql
--liquibase formatted sql

--changeset someshjha:004-seed
insert into accounts (account_id, owner_name, account_type, opened_at) values
  ('ACC-1001', 'Priya Nair', 'individual', '2021-03-14'),
  ('ACC-1002', 'Marcus Webb', 'individual', '2019-11-02'),
  ('ACC-2001', 'Ridgeline Capital', 'institutional', '2017-06-30');

insert into account_owners (account_id, user_id) values
  ('ACC-1001', 'bob.risk'),
  ('ACC-1001', 'carol.trader'),
  ('ACC-2001', 'bob.risk'),
  ('ACC-1002', 'dave.support');

insert into positions (account_id, ticker, quantity, avg_cost) values
  ('ACC-1001', 'AAPL', 120, 152.30),
  ('ACC-1001', 'MSFT', 40, 301.10),
  ('ACC-1002', 'TSLA', 25, 210.75),
  ('ACC-2001', 'AAPL', 5200, 148.60),
  ('ACC-2001', 'NVDA', 900, 410.20),
  ('ACC-2001', 'MSFT', 1800, 295.40),
  ('ACC-2001', 'TSLA', 3000, 240.00);

insert into orders (order_id, account_id, ticker, side, quantity, status, submitted_at) values
  ('ORD-9001', 'ACC-1001', 'AAPL', 'buy', 10, 'filled', '2026-09-18T14:02:00Z'),
  ('ORD-9002', 'ACC-2001', 'NVDA', 'sell', 100, 'filled', '2026-09-19T09:31:00Z');

insert into transactions (transaction_id, account_id, type, amount, occurred_at) values
  ('TXN-5001', 'ACC-1001', 'deposit', 25000, '2026-08-01T00:00:00Z'),
  ('TXN-5002', 'ACC-1001', 'withdrawal', -4000, '2026-08-20T00:00:00Z'),
  ('TXN-5003', 'ACC-1002', 'deposit', 12000, '2026-07-12T00:00:00Z'),
  ('TXN-5004', 'ACC-2001', 'deposit', 4500000, '2026-01-05T00:00:00Z'),
  ('TXN-5005', 'ACC-2001', 'withdrawal', -250000, '2026-06-11T00:00:00Z');

insert into market_data (ticker, price, change_pct, as_of) values
  ('AAPL', 231.42, 0.8, '2026-09-21T13:30:00Z'),
  ('MSFT', 428.10, -0.3, '2026-09-21T13:30:00Z'),
  ('TSLA', 256.77, 2.1, '2026-09-21T13:30:00Z'),
  ('NVDA', 118.95, 1.4, '2026-09-21T13:30:00Z');

insert into fundamentals (ticker, pe_ratio, market_cap, sector) values
  ('AAPL', 34.2, '3.55T', 'Technology'),
  ('MSFT', 36.8, '3.18T', 'Technology'),
  ('TSLA', 68.5, '820B', 'Consumer Discretionary'),
  ('NVDA', 44.1, '2.92T', 'Technology');
```

- [ ] **Step 3: Apply and verify seed data**

Run:
```bash
docker compose run --rm liquibase
```
Expected: `Run: 1`.

Run:
```bash
docker compose exec postgres psql -U postgres -d financial_mcp -c "select (select count(*) from accounts) accounts, (select count(*) from account_owners) account_owners, (select count(*) from positions) positions, (select count(*) from orders) orders, (select count(*) from transactions) transactions, (select count(*) from market_data) market_data, (select count(*) from fundamentals) fundamentals;"
```
Expected: `accounts=3, account_owners=4, positions=7, orders=2, transactions=5, market_data=4, fundamentals=4`.

- [ ] **Step 4: Write the verification script**

Create `scripts/requirements.txt`:

```
psycopg2-binary==2.9.9
```

Create `scripts/verify_scopes.py`:

```python
"""Independent proof that Postgres itself enforces both scoping axes.

Connects as the low-privilege app_pool login role, impersonates each demo
user via SET LOCAL ROLE + SET LOCAL app.current_user_id (exactly what the
MCP server will do at runtime), and asserts every allow/deny outcome the
design specifies. Does not import or call anything from a future MCP
server -- if this script passes, the database boundary is real regardless
of what the application code does.
"""
import os
import sys

import psycopg2

DSN = os.environ.get(
    "VERIFY_DSN",
    "host=localhost port=5432 dbname=financial_mcp user=app_pool password=app_pool_dev_only",
)

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def run_as(conn, user_id, role, sql, params=None):
    """Run one statement as the given demo user/role. Returns (ok, rows_or_error)."""
    with conn:
        with conn.cursor() as cur:
            cur.execute("begin")
            cur.execute(f"set local role {role}")
            cur.execute("set local app.current_user_id = %s", (user_id,))
            try:
                cur.execute(sql, params)
                rows = cur.fetchall() if cur.description else None
                cur.execute("commit")
                return True, rows
            except psycopg2.Error as exc:
                conn.rollback()
                return False, str(exc).strip()


def audit(conn, user_id, role, kind, name, decision, detail):
    with conn:
        with conn.cursor() as cur:
            cur.execute("begin")
            cur.execute(f"set local role {role}")
            cur.execute("set local app.current_user_id = %s", (user_id,))
            cur.execute(
                "insert into audit_log (scope, user_id, kind, name, decision, detail) "
                "values (%s, %s, %s, %s, %s, %s)",
                (role, user_id, kind, name, decision, detail),
            )
            cur.execute("commit")


def attempt(conn, user_id, role, label, sql, params=None):
    ok, result = run_as(conn, user_id, role, sql, params)
    decision = "allow" if ok else "deny"
    detail = "ok" if ok else result
    audit(conn, user_id, role, "query", label, decision, detail)
    return ok, result


def main():
    conn = psycopg2.connect(DSN)

    # --- alice.research / equity_research ---
    ok, rows = attempt(conn, "alice.research", "equity_research", "select_market_data",
                        "select * from market_data")
    check("alice(equity_research) can select market_data", ok and len(rows) == 4)

    ok, rows = attempt(conn, "alice.research", "equity_research", "select_fundamentals",
                        "select * from fundamentals")
    check("alice(equity_research) can select fundamentals", ok and len(rows) == 4)

    ok, _ = attempt(conn, "alice.research", "equity_research", "select_positions_denied",
                     "select * from positions")
    check("alice(equity_research) is denied positions (no grant)", not ok)

    ok, _ = attempt(conn, "alice.research", "equity_research", "select_accounts_denied",
                     "select * from accounts")
    check("alice(equity_research) is denied accounts (no grant)", not ok)

    # --- bob.risk / portfolio_risk (owns ACC-1001, ACC-2001) ---
    ok, rows = attempt(conn, "bob.risk", "portfolio_risk", "select_positions_rls",
                        "select account_id from positions")
    account_ids = {r[0] for r in rows} if ok else set()
    check(
        "bob(portfolio_risk) sees only his own accounts' positions (RLS)",
        ok and len(rows) == 6 and account_ids == {"ACC-1001", "ACC-2001"},
    )

    ok, rows = attempt(conn, "bob.risk", "portfolio_risk", "select_market_data",
                        "select * from market_data")
    check("bob(portfolio_risk) can select market_data (ungated)", ok and len(rows) == 4)

    ok, _ = attempt(conn, "bob.risk", "portfolio_risk", "select_accounts_denied",
                     "select * from accounts")
    check("bob(portfolio_risk) is denied accounts (no grant)", not ok)

    ok, _ = attempt(
        conn, "bob.risk", "portfolio_risk", "insert_order_wrong_role",
        "insert into orders (order_id, account_id, ticker, side, quantity, status, submitted_at) "
        "values ('ORD-TEST-BOB', 'ACC-1001', 'AAPL', 'buy', 1, 'filled', now())",
    )
    check(
        "bob(portfolio_risk) is denied place_order for his OWN account -- wrong role, right account",
        not ok,
    )

    # --- carol.trader / trade_execution (owns ACC-1001 only) ---
    ok, rows = attempt(conn, "carol.trader", "trade_execution", "select_market_data",
                        "select * from market_data")
    check("carol(trade_execution) can select market_data", ok and len(rows) == 4)

    ok, _ = attempt(conn, "carol.trader", "trade_execution", "select_positions_wrong_role",
                     "select * from positions")
    check(
        "carol(trade_execution) is denied positions for an account SHE OWNS -- right account, wrong role",
        not ok,
    )

    ok, _ = attempt(
        conn, "carol.trader", "trade_execution", "insert_order_own_account",
        "insert into orders (order_id, account_id, ticker, side, quantity, status, submitted_at) "
        "values ('ORD-TEST-CAROL-OWN', 'ACC-1001', 'AAPL', 'buy', 1, 'filled', now())",
    )
    check("carol(trade_execution) can place an order for ACC-1001, which she owns", ok)

    ok, _ = attempt(
        conn, "carol.trader", "trade_execution", "insert_order_not_owned",
        "insert into orders (order_id, account_id, ticker, side, quantity, status, submitted_at) "
        "values ('ORD-TEST-CAROL-OTHER', 'ACC-1002', 'AAPL', 'buy', 1, 'filled', now())",
    )
    check(
        "carol(trade_execution) is denied placing an order for ACC-1002, which she does NOT own (RLS)",
        not ok,
    )

    # --- dave.support / client_support (owns ACC-1002 only) ---
    ok, rows = attempt(conn, "dave.support", "client_support", "select_accounts_rls",
                        "select account_id from accounts")
    check(
        "dave(client_support) sees only ACC-1002 (RLS)",
        ok and len(rows) == 1 and rows[0][0] == "ACC-1002",
    )

    ok, rows = attempt(conn, "dave.support", "client_support", "select_transactions_rls",
                        "select transaction_id from transactions")
    check(
        "dave(client_support) sees only ACC-1002's transaction (RLS)",
        ok and len(rows) == 1 and rows[0][0] == "TXN-5003",
    )

    ok, _ = attempt(conn, "dave.support", "client_support", "select_positions_denied",
                     "select * from positions")
    check("dave(client_support) is denied positions (no grant)", not ok)

    ok, _ = attempt(conn, "dave.support", "client_support", "select_market_data_denied",
                     "select * from market_data")
    check("dave(client_support) is denied market_data (no grant)", not ok)

    # --- audit trail check, independent of the above ---
    with conn.cursor() as cur:
        cur.execute("select count(*) from audit_log")
        total = cur.fetchone()[0]
        cur.execute("select count(*) from audit_log where decision = 'deny'")
        denied = cur.fetchone()[0]
    check("audit_log recorded every attempt (16)", total == 16)
    check("audit_log recorded the expected number of denials (8)", denied == 8)

    conn.close()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the verification script**

Run:
```bash
pip install -r scripts/requirements.txt
python3 scripts/verify_scopes.py
```
Expected: every line prints `[PASS]`, ending with `All checks passed.` and exit code 0.

If any line prints `[FAIL]`, do not proceed — fix the underlying changeset (re-running `docker compose run --rm liquibase` re-applies only new/changed changesets; to re-run a changed changeset during development, `docker compose down -v` to drop the volume and start clean, then re-run Steps 1-3 of Tasks 2-5 in order) and re-run this script until clean.

- [ ] **Step 6: Write the README**

Create `README.md`:

```markdown
# poc_mcp — Scoped financial-data MCP (real implementation)

Real implementation of the "scoped financial-data tools for agents" proof of concept: an MCP server backed by Postgres, where task-scope access is enforced by Postgres roles and per-user data access is enforced by Row-Level Security -- not by application code. See the [design spec](https://github.com/someshjha/someshjha.github.io/blob/main/docs/superpowers/specs/2026-09-21-scoped-financial-mcp-real-implementation-design.md) for the full architecture and business rationale, and the [browser-only mock](https://someshjha.com/financial-mcp/) for the same demo without a real backend.

## Status

This repo currently implements **Phase 1 only**: the Postgres schema, roles, grants, and Row-Level Security policies, managed as Liquibase changelogs, verified locally. The MCP server, Keycloak, showcase UI, and Kubernetes/Argo CD deployment are later phases, not yet built.

## Phase 1 quickstart

Requires Docker and Python 3.

```bash
docker compose up -d postgres
docker compose run --rm liquibase
pip install -r scripts/requirements.txt
python3 scripts/verify_scopes.py
```

The verification script proves, independently of any server, that:
- Each of the four demo users (`alice.research`, `bob.risk`, `carol.trader`, `dave.support`) can only reach the Postgres tables their task-scope role (`equity_research`, `portfolio_risk`, `trade_execution`, `client_support`) is granted.
- Within those tables, each user sees only rows for accounts they own (via `account_owners`), even though `bob.risk` and `carol.trader` share ownership of `ACC-1001` -- proving the role boundary and the ownership boundary are checked independently.
- Every attempt, allowed or denied, is recorded in `audit_log`.

To reset the database and start clean:
```bash
docker compose down -v
```
```

- [ ] **Step 7: Commit**

```bash
git add db/changelog/004-seed.sql db/changelog/changelog-master.xml scripts/verify_scopes.py scripts/requirements.txt README.md
git commit -m "$(cat <<'EOF'
Add seed data, verify_scopes.py, and README

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review

**Spec coverage:** Task 2 covers the schema section (all 6 mock tables + `account_owners` + `audit_log`); Task 3 covers the roles/grants section exactly (4 roles, matching table grants, `app_pool`); Task 4 covers RLS exactly as specified (4 policies, `market_data`/`fundamentals` ungated); Task 5 covers seed data (matching the mock's fixture values) and the verification-strategy section (independent script, both axes, the `bob`/`carol` overlapping-ownership proof, audit trail check). No spec requirement from the "Postgres schema and RLS design" or "Verification strategy" sections is left uncovered.

**Type/name consistency:** Table names (`accounts`, `account_owners`, `positions`, `orders`, `transactions`, `market_data`, `fundamentals`, `audit_log`) are identical across Tasks 2-5. Role names (`equity_research`, `portfolio_risk`, `trade_execution`, `client_support`, `app_pool`) are identical across Tasks 3-5. Demo user IDs and their role/ownership mapping match the Global Constraints section exactly in both the seed data (Task 5) and the verification script's assertions.

**Placeholder scan:** No TBD/TODO; every code block is complete, runnable content, not a description of what to write.
