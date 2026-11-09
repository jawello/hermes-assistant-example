from unittest.mock import patch

import gmail_mcp.server as srv


def test_main_serves_streamable_http_on_all_interfaces():
    # mcp 2 takes host/port at run(); 0.0.0.0 so hermes reaches it by service
    # name (DNS-rebinding protection only auto-enables for localhost).
    with patch.object(srv.mcp, "run") as run:
        srv.main()
    run.assert_called_once_with(transport="streamable-http", host="0.0.0.0", port=8000)
