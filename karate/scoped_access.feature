Feature: scoped financial-data MCP -- independent proof via Karate

  A third, independent tool chain proving the same boundary
  scripts/verify_scopes.py (direct Postgres) and
  scripts/verify_mcp_server.py (Python MCP client) already prove --
  this time driven by Karate over real HTTP, real Keycloak tokens, and
  the real running server. Requires the full stack up: `docker compose
  up -d postgres keycloak`, migrations applied, and
  `uvicorn mcp_server.server:app --port 8000` running.

  Background:
    * def callTool =
      """
      function(session, name, args) {
        var res = karate.call('call_tool.feature', { token: session.token, sessionId: session.sessionId, toolName: name, toolArgs: args });
        return res.parsed;
      }
      """

  Scenario: alice(equity_research) can call get_market_snapshot
    * def session = call read('login.feature') { username: 'alice.research', password: 'alice_dev_only' }
    * def result = callTool(session, 'get_market_snapshot', {})
    * match result.decision == 'allow'
    * match result.result == '#[4]'

  Scenario: alice(equity_research) is denied get_portfolio_exposure -- no grant on positions
    * def session = call read('login.feature') { username: 'alice.research', password: 'alice_dev_only' }
    * def result = callTool(session, 'get_portfolio_exposure', { account_id: 'ACC-1001' })
    * match result.decision == 'deny'

  Scenario: bob(portfolio_risk) can call get_portfolio_exposure for his own account
    * def session = call read('login.feature') { username: 'bob.risk', password: 'bob_dev_only' }
    * def result = callTool(session, 'get_portfolio_exposure', { account_id: 'ACC-1001' })
    * match result.decision == 'allow'
    * match result.result == '#[2]'

  Scenario: bob(portfolio_risk) is denied place_order for ACC-1001, which he owns -- wrong role
    * def session = call read('login.feature') { username: 'bob.risk', password: 'bob_dev_only' }
    * def result = callTool(session, 'place_order', { account_id: 'ACC-1001', ticker: 'AAPL', side: 'buy', quantity: 1 })
    * match result.decision == 'deny'

  Scenario: carol(trade_execution) is denied get_portfolio_exposure for ACC-1001, which she owns -- right account, wrong role
    * def session = call read('login.feature') { username: 'carol.trader', password: 'carol_dev_only' }
    * def result = callTool(session, 'get_portfolio_exposure', { account_id: 'ACC-1001' })
    * match result.decision == 'deny'

  Scenario: carol(trade_execution) can place an order for ACC-1001, which she owns
    * def session = call read('login.feature') { username: 'carol.trader', password: 'carol_dev_only' }
    * def result = callTool(session, 'place_order', { account_id: 'ACC-1001', ticker: 'AAPL', side: 'buy', quantity: 1 })
    * match result.decision == 'allow'

  Scenario: carol(trade_execution) is denied placing an order for ACC-1002, which she does NOT own -- RLS
    * def session = call read('login.feature') { username: 'carol.trader', password: 'carol_dev_only' }
    * def result = callTool(session, 'place_order', { account_id: 'ACC-1002', ticker: 'AAPL', side: 'buy', quantity: 1 })
    * match result.decision == 'deny'

  Scenario: dave(client_support) can get his own account's balance
    * def session = call read('login.feature') { username: 'dave.support', password: 'dave_dev_only' }
    * def result = callTool(session, 'get_account_balance', { account_id: 'ACC-1002' })
    * match result.decision == 'allow'
    * match result.result.balance == 12000.0

  Scenario: dave(client_support) is denied get_portfolio_exposure -- no grant on positions
    * def session = call read('login.feature') { username: 'dave.support', password: 'dave_dev_only' }
    * def result = callTool(session, 'get_portfolio_exposure', { account_id: 'ACC-1002' })
    * match result.decision == 'deny'

  Scenario: dave(client_support) is denied place_order -- no grant on orders
    * def session = call read('login.feature') { username: 'dave.support', password: 'dave_dev_only' }
    * def result = callTool(session, 'place_order', { account_id: 'ACC-1002', ticker: 'AAPL', side: 'buy', quantity: 1 })
    * match result.decision == 'deny'

  Scenario: alice(equity_research) gets an 'error' (not 'deny') for an unknown ticker -- bad input, not a permission problem
    * def session = call read('login.feature') { username: 'alice.research', password: 'alice_dev_only' }
    * def result = callTool(session, 'get_fundamentals', { ticker: 'NOT-A-TICKER' })
    * match result.decision == 'error'

  Scenario: dave(client_support) can read his own audit log through the server
    * def session = call read('login.feature') { username: 'dave.support', password: 'dave_dev_only' }
    * def result = callTool(session, 'get_audit_log', {})
    * match result.decision == 'allow'
    * assert result.result.length >= 1

  Scenario: erin(no task-scope role) is denied list_tables with the role-is-None detail
    * def session = call read('login.feature') { username: 'erin.norole', password: 'erin_dev_only' }
    * def result = callTool(session, 'list_tables', {})
    * match result.decision == 'deny'
    * match result.detail == 'token carries no recognized task-scope role'
