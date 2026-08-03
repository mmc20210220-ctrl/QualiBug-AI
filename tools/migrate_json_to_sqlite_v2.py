"""
Migration tool to convert knowledge from JSON file format to SQLite.

This script migrates existing knowledge entries stored as individual JSON files
to the new enterprise-grade SQLite database with WAL mode and indexing.

Usage:
    python tools/migrate_json_to_sqlite.py --project my_project
    
    # Dry run (preview changes without making changes)
    python tools/migrate_json_to_sqlite.py --project my_project --dry-run
    
    # Force overwrite existing database
    python tools/migrate_json_to_sqlite.py --project my_project --force

Migration process:
1. Scan platform_outputs/{project}/learning_knowledge_base/*.json
2. Parse each JSON file into KnowledgeEntry
3. Validate data integrity
4. Create SQLite database with optimized schema
5. Import all entries with proper indexing
6. Generate migration report

Output:
- platform_outputs/{project}/knowledge.db (new SQLite database)
- platform_outputs/{project}/migration_report_{timestamp}.json
"""
from __future__ import annotations

import json
import logging
import argparse
import sys
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

# Define repo root directly (avoid import issues)
REPO_ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger(__name__)


def parse_json_entry(file_path: Path) -> Optional[dict]:
    """Parse a single JSON knowledge entry file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # Validate required fields
        required_fields = ["entry_id", "category", "key", "content", 
                          "confidence", "created_at", "updated_at"]
        for field in required_fields:
            if field not in data:
                logger.warning("Missing required field %s in %s", field, file_path)
                return None
        
        return data
    except Exception as e:
        logger.warning("Failed to parse %s: %s", file_path, e)
        return None


def scan_existing_json_entries(json_dir: Path) -> list[dict]:
    """Scan all existing JSON knowledge entries."""
    if not json_dir.exists():
        logger.info("No existing JSON knowledge base found at %s", json_dir)
        return []
    
    entries = []
    json_files = list(json_dir.glob("*.json"))
    
    logger.info("Found %d JSON files to migrate", len(json_files))
    
    for json_file in json_files:
        entry = parse_json_entry(json_file)
        if entry:
            entries.append(entry)
    
    return entries


def validate_entries(entries: list[dict]) -> tuple[int, int, list[str]]:
    """Validate migrated entries and return statistics."""
    valid_count = 0
    warning_count = 0
    warnings = []
    
    for entry in entries:
        # Validate confidence range
        confidence = entry.get("confidence", 0)
        if not (0 <= confidence <= 1):
            warnings.append(f"Invalid confidence {confidence} for {entry.get('entry_id', 'unknown')}")
            continue
        
        # Validate required fields
        if not entry.get("category") or not entry.get("key"):
            warnings.append(f"Missing category/key for {entry.get('entry_id', 'unknown')}")
            continue
        
        # Validate content is dict
        if not isinstance(entry.get("content"), dict):
            warnings.append(f"Invalid content type for {entry.get('entry_id', 'unknown')}")
            continue
        
        valid_count += 1
    
    return valid_count, warning_count, warnings


def create_migration_report(
    source_dir: Path,
    target_db: Path,
    total_entries: int,
    valid_entries: int,
    warnings: list[str],
    dry_run: bool,
    migration_time_ms: float
) -> dict:
    """Create detailed migration report."""
    report = {
        "migration_timestamp": datetime.now().isoformat(),
        "source_format": "JSON_files",
        "target_format": "SQLite",
        "source_directory": str(source_dir),
        "target_database": str(target_db),
        "dry_run": dry_run,
        "statistics": {
            "total_files_scanned": total_entries,
            "valid_entries_migrated": valid_entries,
            "warnings_count": len(warnings),
            "migration_duration_ms": migration_time_ms
        },
        "warnings": warnings[:10],  # First 10 warnings
        "status": "SUCCESS" if not dry_run else "DRY_RUN_COMPLETED"
    }
    
    return report


def migrate_json_to_sqlite(
    project: str,
    dry_run: bool = False,
    force: bool = False
) -> dict:
    """Migrate JSON knowledge entries to SQLite database."""
    import time
    
    start_time = time.time()
    
    json_dir = REPO_ROOT / "platform_outputs" / project / "learning_knowledge_base"
    db_path = REPO_ROOT / "platform_outputs" / project / "knowledge.db"
    
    logger.info("=" * 70)
    logger.info("Starting JSON to SQLite migration")
    logger.info("Project: %s", project)
    logger.info("Source: %s", json_dir)
    logger.info("Target: %s", db_path)
    logger.info("=" * 70)
    
    # Check if database already exists
    if db_path.exists() and not force:
        logger.warning("Database already exists: %s", db_path)
        logger.warning("Use --force to overwrite")
        return {
            "status": "SKIPPED",
            "reason": "Database already exists, use --force to overwrite"
        }
    
    # Scan existing JSON entries
    entries = scan_existing_json_entries(json_dir)
    
    if not entries:
        logger.info("No entries to migrate")
        report = create_migration_report(
            json_dir, db_path, 0, 0, [], dry_run, 0
        )
        return report
    
    # Validate entries
    valid_count, warning_count, warnings = validate_entries(entries)
    
    if warnings:
        logger.warning("Validation found %d warnings", len(warnings))
        for warning in warnings[:5]:
            logger.warning("  - %s", warning)
    
    if dry_run:
        logger.info("[DRY RUN] Would migrate %d entries", valid_count)
        report = create_migration_report(
            json_dir, db_path, len(entries), valid_count, warnings, True, 0
        )
        return report
    
    # Import LearningKnowledgeDB after setting up paths
    sys.path.insert(0, str(REPO_ROOT))
    from ai_test_asset_center.learning_knowledge_db import LearningKnowledgeDB
    
    # Create SQLite database and migrate
    logger.info("Creating SQLite database...")
    db = LearningKnowledgeDB(project=project)
    
    logger.info("Migrating %d entries...", valid_count)
    migrated_count = 0
    
    for entry_data in entries:
        try:
            db.store(
                category=entry_data["category"],
                key=entry_data["key"],
                content=entry_data["content"],
                confidence=entry_data["confidence"],
                expiry_days=None,  # Preserve original expiry
                domains=entry_data.get("domains", [])
            )
            migrated_count += 1
        except Exception as e:
            logger.error("Failed to migrate entry %s: %s", 
                        entry_data.get("entry_id", "unknown"), e)
    
    # Close database connection
    db.close()
    
    # Calculate migration time
    migration_time_ms = (time.time() - start_time) * 1000
    
    # Create migration report
    report = create_migration_report(
        json_dir, db_path, len(entries), migrated_count, warnings, False, migration_time_ms
    )
    
    # Save report
    report_path = (
        REPO_ROOT / 
        "platform_outputs" / 
        project / 
        f"migration_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    
    logger.info("Migration completed!")
    logger.info("  - Migrated: %d/%d entries", migrated_count, len(entries))
    logger.info("  - Duration: %.2f ms", migration_time_ms)
    logger.info("  - Report: %s", report_path)
    
    return report


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate JSON knowledge entries to SQLite database"
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Project name (e.g., 'my_project')"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview migration without making changes"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing database"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Run migration
    result = migrate_json_to_sqlite(
        project=args.project,
        dry_run=args.dry_run,
        force=args.force
    )
    
    # Print summary
    print("\n" + "=" * 70)
    print("MIGRATION REPORT")
    print("=" * 70)
    print("Status: {}".format(result["status"]))
    print("Total files scanned: {}".format(result["statistics"]["total_files_scanned"]))
    print("Valid entries: {}".format(result["statistics"]["valid_entries_migrated"]))
    print("Warnings: {}".format(result["statistics"]["warnings_count"]))
    print("Duration: {:.2f} ms".format(result["statistics"]["migration_duration_ms"]))
    
    if result.get("warnings"):
        print("\nWarnings:")
        for warning in result["warnings"][:5]:
            print("  - {}".format(warning))
    
    if result["status"] == "SUCCESS":
        print("\n[SUCCESS] Migration completed successfully!")
        print("Database location: platform_outputs/{}/knowledge.db".format(args.project))
    elif result["status"] == "DRY_RUN_COMPLETED":
        print("\n[INFO] Dry run completed. Use --force to perform actual migration.")
    elif result["status"] == "SKIPPED":
        print("\n[INFO] Skipped: {}".format(result.get("reason", "unknown")))
    
    print("=" * 70)
    
    # Exit code
    if result["status"] == "SUCCESS":
        sys.exit(0)
    elif result["status"] == "DRY_RUN_COMPLETED":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
