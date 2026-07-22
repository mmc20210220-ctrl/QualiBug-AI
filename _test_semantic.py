# -*- coding: utf-8 -*-
"""Direct test: does _validation_protocol_material generate semantic values?"""
import json, sys, io
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from ai_test_asset_center.experiment_protocols_base import _validation_protocol_material

# Simulate an operation with a typical registration schema
operation = {
    "method": "POST",
    "path": "/api/auth/register",
    "request_example": {
        "email": "user@example.com",
        "password": "SecurePass123!",
        "name": "Test User",
        "phone": "13800138000",
    },
    "request_schema": {
        "type": "object",
        "properties": {
            "email": {"type": "string", "format": "email"},
            "password": {"type": "string", "minLength": 8},
            "name": {"type": "string"},
            "phone": {"type": "string"},
        },
        "required": ["email", "password", "name"],
    },
}

property_spec = {
    "field": "password",
    "source_intent": "password must be at least 8 characters",
}

control, treatment, mutation = _validation_protocol_material(operation, property_spec)
print(f"Control: {json.dumps(control, ensure_ascii=False)}")
print(f"Treatment: {json.dumps(treatment, ensure_ascii=False)}")
print(f"Mutation: {json.dumps(mutation, ensure_ascii=False)}")
print()

# Test with price field
operation2 = {
    "method": "POST",
    "path": "/api/products/admin",
    "request_example": {
        "name": "Test Product",
        "price": 99.99,
        "sku": "TEST-001",
        "stock": 100,
    },
    "request_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "price": {"type": "number"},
            "sku": {"type": "string"},
            "stock": {"type": "integer"},
        },
        "required": ["name", "price", "sku"],
    },
}

property_spec2 = {"field": "price", "source_intent": "price must be positive"}
control2, treatment2, mutation2 = _validation_protocol_material(operation2, property_spec2)
print(f"Control2: {json.dumps(control2, ensure_ascii=False)}")
print(f"Treatment2: {json.dumps(treatment2, ensure_ascii=False)}")
print(f"Mutation2: {json.dumps(mutation2, ensure_ascii=False)}")
print()

# Test with NO request_schema (only request_example)
operation3 = {
    "method": "POST",
    "path": "/api/auth/register",
    "request_example": {
        "email": "user@example.com",
        "password": "SecurePass123!",
    },
}
property_spec3 = {"field": "password"}
control3, treatment3, mutation3 = _validation_protocol_material(operation3, property_spec3)
print(f"Control3 (no schema): {json.dumps(control3, ensure_ascii=False)}")
print(f"Treatment3: {json.dumps(treatment3, ensure_ascii=False)}")
print(f"Mutation3: {json.dumps(mutation3, ensure_ascii=False)}")
