"""
Schema validation: ORM models vs database migrations.

Introspects SQLAlchemy ORM metadata and compares it against the expected
database schema derived from migrations. Fails on any mismatch so that
ORM-DB drift is caught before it reaches production.
"""
import pytest
from sqlalchemy import inspect

from api.models import Base


# ── Expected schema: tables → columns → (type_prefix, nullable) ──────
# Source of truth: migrations/*.sql
# Only checks column presence, type family, and nullability.
# Exact type params (e.g., Numeric precision) are not checked here
# because SQLAlchemy introspection normalizes them differently.

EXPECTED_TABLES = {
    "master_products",
    "product_listings",
    "price_history",
    "opportunities",
    "opportunity_history",
    "category_arbitrage_stats",
    "price_predictions",
    "product_trends",
    "execution_orders",
    "execution_log",
    "trade_outcomes",
    "alert_configs",
    "alerts_sent",
    "product_snapshots",
    "orchestrator_pipelines",
}


class TestORMTableCoverage:
    """Every table created in migrations must have an ORM model."""

    def test_all_expected_tables_have_orm_models(self):
        orm_tables = set(Base.metadata.tables.keys())
        missing = EXPECTED_TABLES - orm_tables
        assert not missing, (
            f"Tables defined in migrations but missing ORM model: {missing}"
        )


class TestMasterProductModel:

    def _columns(self):
        mapper = inspect(Base.metadata.tables["master_products"])
        return {c.name: c for c in mapper.columns}

    def test_has_median_price_usd(self):
        cols = self._columns()
        assert "median_price_usd" in cols, "Missing column: median_price_usd (migration 015)"

    def test_has_listing_price_count(self):
        cols = self._columns()
        assert "listing_price_count" in cols, "Missing column: listing_price_count (migration 015)"

    def test_canonical_name_is_unique(self):
        cols = self._columns()
        assert cols["canonical_name"].unique, "canonical_name must be unique (migration 016)"


class TestProductListingModel:

    def _columns(self):
        mapper = inspect(Base.metadata.tables["product_listings"])
        return {c.name: c for c in mapper.columns}

    def test_url_is_unique(self):
        cols = self._columns()
        assert cols["url"].unique, "url must be unique (migration 012)"


class TestOpportunityHistoryModel:

    def _table(self):
        return Base.metadata.tables["opportunity_history"]

    def test_opportunity_id_has_fk(self):
        table = self._table()
        col = table.c.opportunity_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "opportunities.id" in fk_targets, (
            "opportunity_id must have FK to opportunities.id"
        )

    def test_master_product_id_has_fk(self):
        table = self._table()
        col = table.c.master_product_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "master_products.id" in fk_targets


class TestProductSnapshotModel:

    def _columns(self):
        mapper = inspect(Base.metadata.tables["product_snapshots"])
        return {c.name: c for c in mapper.columns}

    def test_has_all_columns(self):
        cols = self._columns()
        expected = {
            "id", "master_product_id", "snapshot_date",
            "listing_count", "marketplace_count", "seller_count",
            "avg_price", "min_price", "max_price",
            "total_reviews", "total_sales",
        }
        missing = expected - set(cols.keys())
        assert not missing, f"Missing columns in product_snapshots: {missing}"

    def test_has_fk_to_master_products(self):
        table = Base.metadata.tables["product_snapshots"]
        col = table.c.master_product_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "master_products.id" in fk_targets

    def test_has_unique_constraint(self):
        table = Base.metadata.tables["product_snapshots"]
        unique_sets = []
        for constraint in table.constraints:
            if hasattr(constraint, "columns"):
                col_names = {c.name for c in constraint.columns}
                if len(col_names) > 1:
                    unique_sets.append(col_names)
        assert {"master_product_id", "snapshot_date"} in unique_sets, (
            "product_snapshots must have UNIQUE(master_product_id, snapshot_date)"
        )


class TestAlertSentModel:

    def _columns(self):
        mapper = inspect(Base.metadata.tables["alerts_sent"])
        return {c.name: c for c in mapper.columns}

    def test_has_all_columns(self):
        cols = self._columns()
        expected = {"id", "opportunity_id", "alert_config_id", "channel", "sent_at"}
        missing = expected - set(cols.keys())
        assert not missing, f"Missing columns in alerts_sent: {missing}"

    def test_opportunity_fk(self):
        table = Base.metadata.tables["alerts_sent"]
        col = table.c.opportunity_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "opportunities.id" in fk_targets

    def test_alert_config_fk(self):
        table = Base.metadata.tables["alerts_sent"]
        col = table.c.alert_config_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "alert_configs.id" in fk_targets


class TestOrchestratorPipelineModel:

    def _columns(self):
        mapper = inspect(Base.metadata.tables["orchestrator_pipelines"])
        return {c.name: c for c in mapper.columns}

    def test_has_all_columns(self):
        cols = self._columns()
        expected = {
            "id", "pipeline_id", "opportunity_id", "product_id", "status",
            "decision", "decision_score", "signal_strength",
            "phases", "reasons", "total_duration_ms",
            "created_at", "completed_at",
        }
        missing = expected - set(cols.keys())
        assert not missing, f"Missing columns in orchestrator_pipelines: {missing}"

    def test_pipeline_id_is_primary_key(self):
        table = Base.metadata.tables["orchestrator_pipelines"]
        pk_cols = {c.name for c in table.primary_key.columns}
        assert "pipeline_id" in pk_cols


class TestJSONBColumns:
    """Columns stored as JSONB in DB must use JSONB type in ORM, not Text."""

    @pytest.mark.parametrize("table_name,column_name", [
        ("price_predictions", "features_used"),
        ("product_trends", "signals"),
        ("execution_orders", "risk_assessment"),
        ("execution_log", "details"),
        ("orchestrator_pipelines", "phases"),
        ("orchestrator_pipelines", "reasons"),
    ])
    def test_jsonb_column_type(self, table_name, column_name):
        table = Base.metadata.tables[table_name]
        col = table.c[column_name]
        type_name = type(col.type).__name__
        assert type_name == "JSONB", (
            f"{table_name}.{column_name} should be JSONB, got {type_name}"
        )


class TestForeignKeyCompleteness:
    """All FK relationships declared in migrations must exist in ORM."""

    @pytest.mark.parametrize("table_name,column_name,target", [
        ("product_listings", "master_product_id", "master_products.id"),
        ("opportunities", "master_product_id", "master_products.id"),
        ("opportunities", "buy_listing_id", "product_listings.id"),
        ("opportunities", "sell_listing_id", "product_listings.id"),
        ("opportunity_history", "opportunity_id", "opportunities.id"),
        ("opportunity_history", "master_product_id", "master_products.id"),
        ("price_predictions", "product_id", "master_products.id"),
        ("product_trends", "product_id", "master_products.id"),
        ("execution_orders", "opportunity_id", "opportunities.id"),
        ("execution_orders", "product_id", "master_products.id"),
        ("execution_log", "order_id", "execution_orders.id"),
        ("trade_outcomes", "opportunity_id", "opportunities.id"),
        ("trade_outcomes", "master_product_id", "master_products.id"),
        ("alerts_sent", "opportunity_id", "opportunities.id"),
        ("alerts_sent", "alert_config_id", "alert_configs.id"),
        ("product_snapshots", "master_product_id", "master_products.id"),
    ])
    def test_fk_exists(self, table_name, column_name, target):
        table = Base.metadata.tables[table_name]
        col = table.c[column_name]
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert target in fk_targets, (
            f"{table_name}.{column_name} must have FK -> {target}, "
            f"found: {fk_targets or 'none'}"
        )
