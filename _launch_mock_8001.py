"""Launch mock server on port 8002 for formal run."""
import sys
sys.path.insert(0, ".")
from projects.contractflow_c.mock_server import run_server
run_server(8002)
