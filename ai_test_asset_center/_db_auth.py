"""Database authentication testers — try real login after TCP check."""
import socket

def try_db_auth(scheme: str, host: str, port: int, user: str, pwd: str, dbname: str) -> str:
    try:
        if scheme in ("postgresql", "postgres"):
            try:
                import psycopg2
                conn = psycopg2.connect(host=host, port=port, user=user, password=pwd, dbname=dbname or "postgres", connect_timeout=3)
                ver = conn.server_version; conn.close()
                return f"认证OK (PG {ver//10000}.{ver%10000//100})"
            except ImportError: pass
        if scheme in ("mysql", "mariadb"):
            for mod in ("pymysql", "mysql.connector"):
                try:
                    driver = __import__(mod)
                    if mod == "pymysql":
                        conn = driver.connect(host=host, port=port, user=user, password=pwd, database=dbname or "", connect_timeout=3)
                    else:
                        conn = driver.connect(host=host, port=port, user=user, password=pwd, database=dbname or "", connection_timeout=3)
                    conn.close(); return "认证OK (MySQL)"
                except ImportError: pass
                except Exception as e: return _auth_error(e)
        if scheme in ("postgresql","postgres","mysql","mariadb"):
            return "认证跳过（无驱动，pip install psycopg2-binary / pymysql）"
    except Exception as e:
        return _auth_error(e)
    return ""

def try_redis_auth(host: str, port: int, pwd: str) -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3); s.connect((host, port))
        cmd = ("AUTH " + pwd + "\r\n").encode()
        s.sendall(cmd)
        resp = s.recv(128); s.close()
        if b"+OK" in resp: return "认证OK (Redis)"
        if b"-ERR" in resp or b"-WRONGPASS" in resp: return "认证FAIL 密码错误"
        return "认证? " + repr(resp[:50])
    except Exception as e:
        return "认证FAIL " + str(e)[:60]

def try_mongo_auth(host: str, port: int, user: str, pwd: str, dbname: str) -> str:
    try:
        import pymongo
        from urllib.parse import quote_plus
        uri = "mongodb://" + quote_plus(user) + ":" + quote_plus(pwd) + "@" + host + ":" + str(port) + "/"
        if dbname: uri += dbname
        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=3000)
        client.admin.command("ping"); client.close()
        return "认证OK (MongoDB)"
    except ImportError:
        return "认证跳过（pip install pymongo）"
    except Exception as e:
        return _auth_error(e)

def _auth_error(e: Exception) -> str:
    msg = str(e).lower()
    if "password" in msg or "authentication" in msg or "denied" in msg or "role" in msg:
        return "认证FAIL 用户名或密码错误"
    if "timeout" in msg or "refused" in msg:
        return "认证FAIL 连接超时"
    if "does not exist" in msg:
        return "认证FAIL 数据库不存在"
    return "认证FAIL " + str(e)[:60]
