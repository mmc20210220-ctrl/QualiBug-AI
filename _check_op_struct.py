"""Check payment_requests operation structure."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})
bir = v12.get("behavior_ir", {})
ops = bir.get("operations", [])

# Find payment_requests GET operation
pr_ops = [op for op in ops if "payment-requests" in str(op.get("path", "")) and str(op.get("method", "")).upper() == "GET"]
print(f"Payment requests GET operations: {len(pr_ops)}")

for op in pr_ops[:2]:
    print(f"\nOperation: {op.get('method')} {op.get('path')}")
    print(f"  id: {op.get('id')}")
    print(f"  keys: {list(op.keys())}")
    
    # Check for query parameters
    params = op.get("parameters", [])
    query_params = op.get("query_parameters", [])
    print(f"  parameters: {len(params)}")
    print(f"  query_parameters: {len(query_params)}")
    
    if params:
        for p in params[:5]:
            print(f"    - {p.get('name')}: in={p.get('in')}, type={p.get('type')}")
    
    if query_params:
        for p in query_params[:5]:
            print(f"    - {p.get('name')}: {p.get('type')}")
    
    # Check request schema
    req_schema = op.get("request_schema", {})
    print(f"  request_schema: {bool(req_schema)}")
    if req_schema:
        print(f"    keys: {list(req_schema.keys())[:10]}")
    
    # Check full operation (truncated)
    print(f"\n  Full operation (truncated):")
    op_str = json.dumps(op, indent=2, default=str)
    print(op_str[:2000])
