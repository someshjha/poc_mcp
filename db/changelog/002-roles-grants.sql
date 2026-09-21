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

--changeset someshjha:002b-audit-log-sequence-grant
grant usage, select on audit_log_id_seq to equity_research, portfolio_risk, trade_execution, client_support;

--changeset someshjha:002c-app-pool-noinherit
-- ALTER ROLE ... NOINHERIT only changes app_pool's default for FUTURE
-- role grants; it does not retroactively change the inherit_option
-- already recorded on the four existing memberships granted above
-- (PostgreSQL 16 tracks inherit_option per pg_auth_members row, fixed
-- at GRANT time to the grantee's rolinherit default back then). Both
-- statements are required: the ALTER so any future membership defaults
-- to NOINHERIT, and the re-GRANT ... WITH INHERIT FALSE to flip the
-- inherit_option already stored on the existing memberships. Verified
-- empirically: without the re-GRANT, app_pool could still read
-- scope-gated tables with no SET ROLE issued.
alter role app_pool noinherit;
grant equity_research, portfolio_risk, trade_execution, client_support
  to app_pool with inherit false;
