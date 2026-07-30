from __future__ import annotations

import io
import sqlite3
import zipfile

from ai_test_asset_center.enterprise_material_formats import (
    ENTERPRISE_DATABASE_MODEL_SUFFIXES,
    inspect_pk_document_container,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    DocumentSource,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.database_model_adapter import (
    DATABASE_MODEL_STRUCTURE_SCHEMA,
    DatabaseModelDocumentAdapter,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.planner import (
    plan_document_parsing,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.registry import (
    build_default_registry,
)


def _source(filename: str, data: bytes, source_id: str = "src_db") -> DocumentSource:
    return DocumentSource(source_id=source_id, filename=filename, data=data)


def _powerdesigner_xml() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<PowerDesigner>
  <Model Id="model_1">
    <Name>ERP Physical Model</Name>
    <DBMS>PostgreSQL</DBMS>
    <Tables>
      <Table Id="table_customer">
        <Name>Customer</Name><Code>customer</Code><Comment>Customer master</Comment>
        <Columns>
          <Column Id="customer_id"><Name>ID</Name><Code>id</Code><DataType>BIGINT</DataType><Mandatory>1</Mandatory></Column>
          <Column Id="customer_name"><Name>Name</Name><Code>name</Code><DataType>VARCHAR(100)</DataType></Column>
        </Columns>
        <Keys>
          <Key Id="customer_pk"><Name>PK Customer</Name><Key.Columns><Column Ref="customer_id"/></Key.Columns></Key>
        </Keys>
        <PrimaryKey><Key Ref="customer_pk"/></PrimaryKey>
      </Table>
      <Table Id="table_order">
        <Name>Order</Name><Code>sales_order</Code>
        <Columns>
          <Column Id="order_id"><Name>ID</Name><Code>id</Code><DataType>BIGINT</DataType><Mandatory>1</Mandatory></Column>
          <Column Id="order_customer"><Name>Customer ID</Name><Code>customer_id</Code><DataType>BIGINT</DataType><Mandatory>1</Mandatory></Column>
        </Columns>
        <Keys>
          <Key Id="order_pk"><Key.Columns><Column Ref="order_id"/></Key.Columns></Key>
        </Keys>
        <PrimaryKey><Key Ref="order_pk"/></PrimaryKey>
        <Indexes>
          <Index Id="idx_order_customer"><Name>IX Order Customer</Name><Code>ix_order_customer</Code><Unique>0</Unique><Index.Columns><Column Ref="order_customer"/></Index.Columns></Index>
        </Indexes>
      </Table>
    </Tables>
    <References>
      <Reference Id="fk_order_customer">
        <Name>Order Customer</Name><Code>fk_order_customer</Code>
        <ParentTable><Table Ref="table_customer"/></ParentTable>
        <ChildTable><Table Ref="table_order"/></ChildTable>
        <DeleteConstraint>RESTRICT</DeleteConstraint>
        <ReferenceJoins>
          <ReferenceJoin Id="join_1">
            <Object1><Column Ref="customer_id"/></Object1>
            <Object2><Column Ref="order_customer"/></Object2>
          </ReferenceJoin>
        </ReferenceJoins>
      </Reference>
    </References>
  </Model>
</PowerDesigner>
"""


def _mwb_bytes() -> bytes:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<data>
  <value type="object" struct-name="db.mysql.Schema" id="schema_shop">
    <value type="string" key="name">shop</value>
  </value>
  <value type="object" struct-name="db.mysql.Table" id="table_customer">
    <value type="string" key="name">customer</value>
    <link key="owner">schema_shop</link>
    <value type="list" key="columns">
      <value type="object" struct-name="db.mysql.Column" id="customer_id">
        <value type="string" key="name">id</value><value type="string" key="formattedType">BIGINT</value><value type="int" key="isNotNull">1</value>
      </value>
    </value>
    <value type="list" key="indices">
      <value type="object" struct-name="db.mysql.Index" id="customer_pk">
        <value type="string" key="name">PRIMARY</value><value type="int" key="unique">1</value><value type="int" key="isPrimary">1</value>
        <value type="list" key="columns"><value type="object" id="customer_pk_col"><link key="referencedColumn">customer_id</link></value></value>
      </value>
    </value>
  </value>
  <value type="object" struct-name="db.mysql.Table" id="table_order">
    <value type="string" key="name">sales_order</value>
    <link key="owner">schema_shop</link>
    <value type="list" key="columns">
      <value type="object" struct-name="db.mysql.Column" id="order_id"><value type="string" key="name">id</value><value type="string" key="formattedType">BIGINT</value><value type="int" key="isNotNull">1</value></value>
      <value type="object" struct-name="db.mysql.Column" id="order_customer"><value type="string" key="name">customer_id</value><value type="string" key="formattedType">BIGINT</value><value type="int" key="isNotNull">1</value></value>
    </value>
    <value type="list" key="foreignKeys">
      <value type="object" struct-name="db.mysql.ForeignKey" id="fk_order_customer">
        <value type="string" key="name">fk_order_customer</value>
        <link key="referencedTable">table_customer</link>
        <value type="list" key="columns"><link>order_customer</link></value>
        <value type="list" key="referencedColumns"><link>customer_id</link></value>
        <value type="string" key="deleteRule">RESTRICT</value>
      </value>
    </value>
  </value>
</data>
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("document.mwb.xml", xml)
        archive.writestr("document.properties", "version=1")
    return buffer.getvalue()


def test_powerdesigner_xml_pdm_preserves_tables_keys_and_relationships() -> None:
    result = DatabaseModelDocumentAdapter().extract(
        _source("erp.pdm", _powerdesigner_xml())
    )

    artifact = result["artifact_structure"]
    assert artifact["schema"] == DATABASE_MODEL_STRUCTURE_SCHEMA
    assert artifact["database_model_kind"] == "powerdesigner_pdm_xml"
    assert artifact["table_count"] == 2
    assert artifact["relationship_count"] == 1
    by_name = {row["name"]: row for row in artifact["tables"]}
    customer_id = next(row for row in by_name["customer"]["columns"] if row["name"] == "id")
    order_customer = next(row for row in by_name["sales_order"]["columns"] if row["name"] == "customer_id")
    assert customer_id["primary_key"] is True
    assert customer_id["nullable"] is False
    assert order_customer["source_locator"].startswith("erp.pdm#pdm-object=Table")
    relation = artifact["relationships"][0]
    assert relation["child_table"] == "sales_order"
    assert relation["child_columns"] == ["customer_id"]
    assert relation["parent_table"] == "customer"
    assert relation["parent_columns"] == ["id"]
    assert result["structure_receipt"]["status"] == "COMPLETE"
    assert all(row["source_locator"].startswith("erp.pdm#") for row in result["blocks"])


def test_binary_powerdesigner_pdm_is_explicitly_blocked() -> None:
    result = DatabaseModelDocumentAdapter().extract(
        _source("legacy.pdm", b"binary-powerdesigner-model")
    )

    assert result["structure_receipt"]["status"] == "BLOCKED"
    assert result["unsupported_content"][0]["reason_code"] == "POWERDESIGNER_BINARY_PDM_UNSUPPORTED"
    assert result["artifact_structure"]["tables"] == []


def test_mysql_workbench_mwb_preserves_schema_primary_key_and_foreign_key() -> None:
    mwb = _mwb_bytes()
    result = DatabaseModelDocumentAdapter().extract(_source("shop.mwb", mwb))

    artifact = result["artifact_structure"]
    assert artifact["database_model_kind"] == "mysql_workbench_mwb"
    assert artifact["database_family"] == "mysql"
    assert [row["name"] for row in artifact["schemas"]] == ["shop"]
    assert artifact["table_count"] == 2
    customer = next(row for row in artifact["tables"] if row["name"] == "customer")
    assert customer["columns"][0]["primary_key"] is True
    relation = artifact["relationships"][0]
    assert relation["child_table"] == "sales_order"
    assert relation["child_columns"] == ["customer_id"]
    assert relation["parent_table"] == "customer"
    assert relation["parent_columns"] == ["id"]
    assert result["structure_receipt"]["mysql_workbench_document_xml"] is True
    assert inspect_pk_document_container(mwb) == "mysql_workbench_model"


def test_renamed_mwb_is_structurally_detected_as_document_not_archive() -> None:
    mwb = _mwb_bytes()
    source = _source("model.bin", mwb)
    adapter = DatabaseModelDocumentAdapter()

    match = adapter.probe(source)
    plan = plan_document_parsing(source, build_default_registry())

    assert match is not None
    assert match.reason == "mysql_workbench_document_xml_container"
    assert plan["selected_adapters"][0]["adapter_name"] == adapter.name
    assert plan["detected_format"] == "mwb"
    assert plan["capability_family"] == "database_model"


def test_sqlite_schema_is_read_only_and_preserves_pk_fk_and_index(tmp_path) -> None:
    database_path = tmp_path / "orders.sqlite"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE customer(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE sales_order(
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                amount NUMERIC NOT NULL DEFAULT 0,
                FOREIGN KEY(customer_id) REFERENCES customer(id) ON DELETE RESTRICT
            );
            CREATE INDEX ix_sales_order_customer ON sales_order(customer_id);
            INSERT INTO customer(id, name) VALUES (1, 'secret customer row');
            INSERT INTO sales_order(id, customer_id, amount) VALUES (10, 1, 99.5);
            """
        )
        connection.commit()
    finally:
        connection.close()

    data = database_path.read_bytes()
    result = DatabaseModelDocumentAdapter().extract(
        _source("orders.sqlite", data)
    )

    artifact = result["artifact_structure"]
    assert artifact["database_model_kind"] == "sqlite_database"
    assert artifact["table_count"] == 2
    assert artifact["relationship_count"] == 1
    assert artifact["index_count"] >= 1
    assert result["structure_receipt"]["sqlite_open_mode"] == "read_only_immutable"
    assert result["structure_receipt"]["sqlite_data_rows_read"] == 0
    assert "secret customer row" not in result["plain_text"]
    relation = artifact["relationships"][0]
    assert relation["child_table"] == "sales_order"
    assert relation["child_columns"] == ["customer_id"]
    assert relation["parent_table"] == "customer"
    assert relation["parent_columns"] == ["id"]


def test_ambiguous_non_sqlite_db_is_not_guessed() -> None:
    result = DatabaseModelDocumentAdapter().extract(
        _source("unknown.db", b"not a sqlite database")
    )

    assert result["structure_receipt"]["status"] == "BLOCKED"
    assert result["unsupported_content"][0]["reason_code"] == "DATABASE_BINARY_IS_NOT_SQLITE"


def test_database_model_formats_are_registered_in_one_transport_family() -> None:
    assert ENTERPRISE_DATABASE_MODEL_SUFFIXES == frozenset(
        {".pdm", ".mwb", ".sqlite", ".sqlite3", ".db"}
    )
    registry = build_default_registry()
    assert registry.get("database-model-native-structure").parser_version == "1"
