"""PostgreSQL safety net: two confirmed bookings of a resource can never overlap.

The application check in ``booking.services`` is the primary guard (and the
only one on SQLite, where writes are serialised by BEGIN IMMEDIATE).
"""

from django.db import migrations

CREATE = """
CREATE EXTENSION IF NOT EXISTS btree_gist;
ALTER TABLE booking_booking ADD CONSTRAINT booking_no_overlap
  EXCLUDE USING gist (resource_id WITH =, tstzrange(start, "end", '[)') WITH &&)
  WHERE (status = 'confirmed');
"""
DROP = "ALTER TABLE booking_booking DROP CONSTRAINT IF EXISTS booking_no_overlap;"


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CREATE)


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(DROP)


class Migration(migrations.Migration):
    dependencies = [("booking", "0001_initial")]
    operations = [migrations.RunPython(forwards, backwards)]
