Feature: call one tool on an already-initialized MCP session and parse its response

  Called with `token`/`sessionId` (from login.feature) and `toolName`/
  `toolArgs`. The MCP server's response is Server-Sent Events, not plain
  JSON, and the tool's own return value is itself JSON-encoded a second
  time inside `result.content[0].text` -- both are unwrapped here so
  callers get the tool's actual `{decision, result|detail}` object.

  Scenario:
    Given url 'http://localhost:8000/mcp'
    And header Authorization = 'Bearer ' + token
    And header Accept = 'application/json, text/event-stream'
    And header mcp-session-id = sessionId
    And request { jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name: '#(toolName)', arguments: '#(toolArgs)' } }
    When method post
    Then status 200
    * def jsonLine = response.split('\n').filter(function(l){ return l.indexOf('data: ') === 0; })[0]
    * def envelope = JSON.parse(jsonLine.substring('data: '.length))
    * def parsed = JSON.parse(envelope.result.content[0].text)
