# Run this ONCE, manually, to load the real starting product catalog from
# seed_products.csv into the database: `python seed_products.py`
#
# This is a one-off CLI script (same category as seed_admin.py and
# register_pesapal_ipn.py) - a human runs it directly and reads the printed
# output, so it uses plain print() rather than the logging module (see
# CLAUDE.md's Observability section for why those two scripts deliberately
# stay on print()).
#
# What this does, per CSV row:
#   1. Find-or-create the row's Category (grouped under the one
#      "Uncategorized" CategoryGroup every fresh DB already has - a real
#      migration seeds that row unconditionally, see
#      alembic/versions/9efc87f6f25a_...). Staff can re-sort categories into
#      real groups later through the admin UI; this script isn't guessing at
#      a navigation structure, just satisfying the NOT NULL constraint.
#   2. Create ONE Product + ONE ProductVariant for the row (no attempt to
#      group multiple CSV rows into one product with several variants - the
#      sheet has near-duplicate-looking rows at different prices/SKUs that
#      look like separate stock batches, not real color/size variants, and
#      guessing wrong there would silently merge things that should stay
#      separate).
#   3. Create ONE InventoryRecord for that variant with the CSV's Quantity -
#      just variant_id now, no branch_id, since branches were removed
#      entirely (see CLAUDE.md's 2026-08-20 note).
#
# Rows with a blank Price (a handful in the sheet - hair clippers/earbuds
# the source data couldn't confirm a real price for) get created with
# price=0.0 and is_active=False, so they exist in the DB for staff to fix
# but never show up live to a customer with a nonsense price.
#
# Safe to re-run: ProductVariant.sku is globally unique, so a row whose SKU
# is already in the DB is skipped rather than creating a duplicate product.

import csv

from database import SessionLocal
from models import Category, CategoryGroup, InventoryRecord, Product, ProductVariant
from routers.products import _generate_unique_slug

CSV_PATH = "seed_products.csv"


def seed_products():
    db = SessionLocal()
    try:
        # Every fresh DB gets this row for real, from migration
        # 9efc87f6f25a - if it's missing, alembic upgrade head hasn't been
        # run, and there's nothing sensible to attach categories to yet.
        uncategorized = db.query(CategoryGroup).filter(CategoryGroup.name == "Uncategorized").first()
        if uncategorized is None:
            print("ERROR: no 'Uncategorized' CategoryGroup found - has `alembic upgrade head` been run?")
            return

        # Cache of Category rows already created THIS run, keyed by name -
        # avoids re-querying the DB for every single row when many rows
        # share the same category (most of this sheet does).
        categories_by_name: dict[str, Category] = {}

        created_products = 0
        skipped_existing = 0
        needs_price_review = []

        # One bulk query instead of one round-trip PER row just to check
        # "has this SKU already been seeded" - matters a lot on a re-run
        # after a partial failure, where most rows are already done and
        # would otherwise cost a full Neon round-trip each just to skip.
        existing_skus = {sku for (sku,) in db.query(ProductVariant.sku).all()}

        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sku = row["SKU"].strip()

                # Idempotent re-run: if this exact variant already exists,
                # don't create a second product for the same SKU.
                if sku in existing_skus:
                    skipped_existing += 1
                    continue

                category_name = row["Category"].strip()
                category = categories_by_name.get(category_name)
                if category is None:
                    category = db.query(Category).filter(Category.name == category_name).first()
                    if category is None:
                        category = Category(name=category_name, category_group_id=uncategorized.id)
                        db.add(category)
                        db.flush()  # assigns category.id for the product below
                    categories_by_name[category_name] = category

                price_text = row["Price"].strip()
                has_real_price = bool(price_text)
                price = float(price_text) if has_real_price else 0.0
                if not has_real_price:
                    needs_price_review.append(sku)

                # description is String(2000) - a couple of the sheet's
                # longer marketing blurbs run past that, and Postgres would
                # reject the insert outright rather than just truncating it.
                description = (row["Description"] or "").strip()[:2000] or None

                product = Product(
                    category_id=category.id,
                    name=row["Product Name"].strip(),
                    description=description,
                    slug=_generate_unique_slug(db, row["Product Name"].strip()),
                    is_active=has_real_price,
                )
                db.add(product)
                db.flush()  # assigns product.id for the variant below

                variant = ProductVariant(
                    product_id=product.id,
                    sku=sku,
                    variant_label=row["Model"].strip() or None,
                    price=price,
                )
                db.add(variant)
                db.flush()  # assigns variant.id for the inventory record below

                db.add(
                    InventoryRecord(
                        variant_id=variant.id,
                        quantity_available=int(row["Quantity"].strip()),
                    )
                )

                db.commit()
                created_products += 1
                if created_products % 25 == 0:
                    print(f"...{created_products} created so far", flush=True)

        print(f"Created {created_products} products (skipped {skipped_existing} already-seeded SKUs).")
        if needs_price_review:
            print(f"{len(needs_price_review)} products created INACTIVE (no price in the sheet) - needs a real price before going live:")
            for sku in needs_price_review:
                print(f"  - {sku}")
    finally:
        db.close()


if __name__ == "__main__":
    seed_products()
