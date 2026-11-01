from django.db import migrations


class Migration(migrations.Migration):
    """Keep Invoice.merchant equal to its upload's merchant.

    Django cannot declare a composite foreign key, and bulk_create skips model
    validation, so the database enforces this rule.
    """

    dependencies = [
        ("imports", "0001_initial"),
        ("invoices", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE invoices_invoice
                ADD CONSTRAINT invoice_upload_merchant_fk
                FOREIGN KEY (upload_id, merchant_id)
                REFERENCES imports_upload (id, merchant_id)
            """,
            reverse_sql="""
                ALTER TABLE invoices_invoice
                DROP CONSTRAINT invoice_upload_merchant_fk
            """,
        ),
    ]
