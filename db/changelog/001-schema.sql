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
