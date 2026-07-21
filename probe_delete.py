# -*- coding: utf-8 -*-
"""Probe target system for DELETE endpoint support."""
import json, sys, io, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

base = 'http://localhost:8080'

# First login to get a token
accounts = [
    ('admin', 'admin123'),
    ('admin', 'Admin123!'),
    ('admin', 'admin'),
    ('test_admin', 'admin123'),
    ('buyer1', 'buyer123'),
    ('buyer', 'buyer123'),
]
token = ''
for username, password in accounts:
    try:
        data = json.dumps({'username': username, 'password': password}).encode()
        req = urllib.request.Request(base + '/api/auth/login', data=data,
                                     headers={'Content-Type': 'application/json'})
        resp = urllib.request.urlopen(req, timeout=5)
        body = json.loads(resp.read())
        token = body.get('token', '')
        if token:
            print(f"Login OK: {username} -> token={token[:20]}...")
            break
    except Exception as e:
        code = getattr(e, 'code', '?')
        print(f"Login failed: {username} -> {code}")

if not token:
    print("No valid login found, trying without auth")

# Probe DELETE on various resource endpoints
# Use a non-existent ID to avoid actually deleting anything
test_id = 'qb_probe_nonexistent_99999'
endpoints = [
    f'/api/users/{test_id}',
    f'/api/orders/{test_id}',
    f'/api/products/{test_id}',
    f'/api/products/admin/{test_id}',
    f'/api/coupons/{test_id}',
    f'/api/cart/items/{test_id}',
    f'/api/categories/{test_id}',
    f'/api/inventory/{test_id}',
    f'/api/payments/{test_id}',
    f'/api/refunds/{test_id}',
    f'/api/addresses/{test_id}',
    f'/api/reviews/{test_id}',
    f'/api/users/addresses/{test_id}',
]

print(f"\nProbing DELETE endpoints (token={'yes' if token else 'no'}):")
for ep in endpoints:
    try:
        req = urllib.request.Request(base + ep, method='DELETE')
        if token:
            req.add_header('Authorization', 'Bearer ' + token)
        resp = urllib.request.urlopen(req, timeout=3)
        body = resp.read().decode()[:100]
        print(f"  DELETE {ep}: {resp.status} {body}")
    except Exception as e:
        code = getattr(e, 'code', '?')
        body = ''
        try:
            body = e.read().decode()[:100]
        except:
            pass
        # Distinguish between "route exists but resource not found" (HTML 404)
        # and "no route" (JSON 404 with "no route")
        route_exists = 'no route' not in body and code != 405
        status = 'ROUTE_EXISTS' if route_exists else 'NO_ROUTE'
        print(f"  DELETE {ep}: {code} [{status}] {body[:60]}")
