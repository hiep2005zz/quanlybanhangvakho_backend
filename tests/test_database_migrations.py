from sqlalchemy import create_engine, inspect, text

from app.db.init_db import _ensure_dealer_credit_limit_column, _ensure_legacy_columns


def test_migrates_existing_dealers_table_with_credit_limit_default():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE dealers (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO dealers (id) VALUES (1)"))

        _ensure_dealer_credit_limit_column(engine)
        _ensure_dealer_credit_limit_column(engine)

        assert "credit_limit" in {
            column["name"] for column in inspect(engine).get_columns("dealers")
        }
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT credit_limit FROM dealers WHERE id = 1")
            ).scalar_one() == 50000000.0
    finally:
        engine.dispose()


def test_migrates_existing_product_and_inventory_unit_columns():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO users (id) VALUES (1)"))
            connection.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO products (id) VALUES (1)"))
            connection.execute(text(
                "CREATE TABLE inventory_transactions (id INTEGER PRIMARY KEY)"
            ))
            connection.execute(text(
                "INSERT INTO inventory_transactions (id) VALUES (1)"
            ))

        _ensure_legacy_columns(engine)
        _ensure_legacy_columns(engine)

        user_columns = {
            column["name"] for column in inspect(engine).get_columns("users")
        }
        product_columns = {
            column["name"] for column in inspect(engine).get_columns("products")
        }
        inventory_columns = {
            column["name"]
            for column in inspect(engine).get_columns("inventory_transactions")
        }
        assert "avatar_url" in user_columns
        assert {"base_unit", "units_json"} <= product_columns
        assert {"unit_name", "conversion_rate", "base_quantity"} <= inventory_columns
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT base_unit FROM products WHERE id = 1")
            ).scalar_one() == "Cái"
            assert connection.execute(
                text("SELECT unit_name, conversion_rate, base_quantity "
                     "FROM inventory_transactions WHERE id = 1")
            ).one() == ("Cái", 1.0, 0.0)
    finally:
        engine.dispose()
