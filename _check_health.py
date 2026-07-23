import urllib.request
ports = [8001,8002,8003,8004,8005,8006,8007,8008,8009,8010,8080]
names = ["auth","user","product","inventory","cart","coupon","order","payment","refund","report","gateway"]
for name, port in zip(names, ports):
    try:
        r = urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2)
        print(f"  {name:12s} :{port} -> {r.status} OK")
    except Exception as e:
        print(f"  {name:12s} :{port} -> FAIL ({type(e).__name__})")
