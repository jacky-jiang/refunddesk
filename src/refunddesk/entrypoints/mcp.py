from mcp.server import MCPServer

server = MCPServer("RefundDesk")
app = server.streamable_http_app()
