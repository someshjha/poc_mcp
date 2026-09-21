Feature: obtain a Keycloak token and an initialized MCP session for one user

  Called by other features with `username`/`password` karate-args; returns
  `token` and `sessionId` for use with call_tool.feature.

  Scenario:
    Given url 'http://localhost:8080/realms/financial-mcp/protocol/openid-connect/token'
    And form field grant_type = 'password'
    And form field client_id = 'mcp-server'
    And form field username = username
    And form field password = password
    When method post
    Then status 200
    * def token = response.access_token

    Given url 'http://localhost:8000/mcp'
    And header Authorization = 'Bearer ' + token
    And header Accept = 'application/json, text/event-stream'
    And request { jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'karate', version: '1' } } }
    When method post
    Then status 200
    * def sessionId = responseHeaders['mcp-session-id'][0]

    Given url 'http://localhost:8000/mcp'
    And header Authorization = 'Bearer ' + token
    And header Accept = 'application/json, text/event-stream'
    And header mcp-session-id = sessionId
    And request { jsonrpc: '2.0', method: 'notifications/initialized' }
    When method post
    Then status 202
