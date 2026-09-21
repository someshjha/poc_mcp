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
