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
