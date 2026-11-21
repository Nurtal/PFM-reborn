from django.db import models


class LegacyIdMap(models.Model):
    """Where each row of the old Platform-Manager database went (idempotent re-runs)."""

    legacy_table = models.CharField(max_length=64)
    legacy_id = models.BigIntegerField()
    model = models.CharField(max_length=100)
    new_id = models.BigIntegerField()
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["legacy_table", "legacy_id"], name="unique_legacy_row"),
        ]

    def __str__(self) -> str:
        return f"{self.legacy_table}#{self.legacy_id} -> {self.model}#{self.new_id}"
