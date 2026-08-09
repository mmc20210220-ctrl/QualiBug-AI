# -*- coding: utf-8 -*-
"""Session H: FK-reference columns must never be unique-key nonce-suffixed.

Root cause: fixture auto-create bodies replayed the schema-declared UNIQUE
field set globally; a column that is a FOREIGN KEY (cart_items.sku ->
products.sku) got a nonce suffix appended, fabricating a nonexistent
reference and failing the fixture create with 404 before the rule under test
was observed. The exclusion is table-scoped: a create on the referenced
table itself (products.sku, the parent side) still gets its own unique key
suffixed; business-detail arrays (items[].sku) are never rewritten.
"""
from __future__ import annotations

import unittest

from ai_test_asset_center.disposable_identity_materializer import (
    declared_fk_reference_columns,
    declared_schema_tables,
    declared_unique_fields,
    materialize_unique_create_fields,
)

_SCHEMA = """
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  email TEXT UNIQUE NOT NULL
);

CREATE TABLE products (
  sku TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE cart_items (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  sku TEXT NOT NULL REFERENCES products(sku),
  qty INT NOT NULL
);

CREATE TABLE inventory (
  sku TEXT PRIMARY KEY REFERENCES products(sku),
  available_qty INT NOT NULL
);
"""


class DeclaredFkReferenceColumnsTest(unittest.TestCase):
    def test_parses_child_side_fk_columns_per_table(self):
        fk = declared_fk_reference_columns(_SCHEMA)
        self.assertIn("cartitems", fk)
        self.assertIn("sku", fk["cartitems"])
        self.assertIn("userid", fk["cartitems"])
        self.assertIn("sku", fk["inventory"])
        # products.sku is the REFERENCED side — never a child FK
        self.assertNotIn("sku", fk.get("products", set()))
        # email UNIQUE is not a FK
        self.assertNotIn("email", fk.get("users", set()))


class MaterializeUniqueCreateFieldsFkGuardTest(unittest.TestCase):
    def setUp(self):
        self.unique = declared_unique_fields(_SCHEMA)
        self.fk = declared_fk_reference_columns(_SCHEMA)
        self.tables = declared_schema_tables(_SCHEMA)

    def test_cart_items_create_keeps_fk_sku_literal(self):
        # cart_items.sku REFERENCES products(sku): the value must name an
        # existing product, never a suffixed literal.
        body = {"qty": 1, "sku": "SKU-PHONE-001"}
        new_body, materialized = materialize_unique_create_fields(
            body, "NONCE", self.unique,
            fk_reference_columns=self.fk, table_hint="cart", schema_tables=self.tables,
        )
        self.assertEqual(new_body, {"qty": 1, "sku": "SKU-PHONE-001"})
        self.assertEqual(materialized, [])

    def test_products_create_still_suffixes_own_unique_key(self):
        # products.sku is the referenced (parent) side: the create generates
        # its own key, so cross-run uniqueness still needs the suffix.
        body = {"sku": "SKU-PHONE-001", "title": "phone"}
        new_body, materialized = materialize_unique_create_fields(
            body, "NONCE", self.unique,
            fk_reference_columns=self.fk, table_hint="products", schema_tables=self.tables,
        )
        self.assertEqual(new_body["sku"], "SKU-PHONE-001-NONCE")
        self.assertEqual(materialized, ["sku"])

    def test_detail_array_sku_never_rewritten(self):
        # items[].sku is a business-detail reference (order lines), not a
        # batch-create array: the array key names no declared table.
        body = {"items": [{"sku": "SKU-PHONE-001", "qty": 1}]}
        new_body, materialized = materialize_unique_create_fields(
            body, "NONCE", self.unique,
            fk_reference_columns=self.fk, table_hint="orders", schema_tables=self.tables,
        )
        self.assertEqual(new_body["items"][0]["sku"], "SKU-PHONE-001")
        self.assertEqual(materialized, [])

    def test_batch_create_array_naming_own_table_still_suffixed(self):
        body = {"products": [{"sku": "SKU-PHONE-001"}]}
        new_body, materialized = materialize_unique_create_fields(
            body, "NONCE", self.unique,
            fk_reference_columns=self.fk, table_hint="products", schema_tables=self.tables,
        )
        self.assertEqual(new_body["products"][0]["sku"], "SKU-PHONE-001-NONCE")
        self.assertEqual(materialized, ["products[0].sku"])


class ValidationBodyOwnershipStripTest(unittest.TestCase):
    """Session H: validation arms must isolate the field under test."""

    def test_ownership_identity_fields_stripped(self):
        from ai_test_asset_center.experiment_protocols_base import (
            _strip_ownership_identity_fields,
        )

        body = {"sku": "SKU-PHONE-001", "qty": -1, "userId": "00000000-0000-0000-0000-000000000001"}
        stripped = _strip_ownership_identity_fields(body)
        self.assertEqual(stripped, {"sku": "SKU-PHONE-001", "qty": -1})

    def test_mutation_field_preserved_when_ownership_key(self):
        from ai_test_asset_center.experiment_protocols_base import (
            _strip_ownership_identity_fields,
        )

        body = {"userId": "abc", "qty": 1}
        stripped = _strip_ownership_identity_fields(body, keep={"userId"})
        self.assertEqual(stripped, body)

    def test_nested_detail_arrays_stripped(self):
        from ai_test_asset_center.experiment_protocols_base import (
            _strip_ownership_identity_fields,
        )

        body = {"items": [{"sku": "S1", "qty": 1, "userId": "x"}], "couponCode": "C1"}
        stripped = _strip_ownership_identity_fields(body)
        self.assertEqual(stripped, {"items": [{"sku": "S1", "qty": 1}], "couponCode": "C1"})


if __name__ == "__main__":
    unittest.main()
