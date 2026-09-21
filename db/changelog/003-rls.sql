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
