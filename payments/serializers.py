from decimal import Decimal
from rest_framework import serializers
from accounts.serializers import WritableFieldsOnlyMixin
from .models import DEMO_CHANNELS, WalletTransaction


class TopUpInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    amount = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=Decimal("10"), max_value=Decimal("10000"))
    request_id = serializers.UUIDField()
    demo_channel = serializers.ChoiceField(choices=DEMO_CHANNELS)
    demo_outcome = serializers.ChoiceField(choices=["SUCCESS", "FAILED", "CANCELLED"])


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = ("id", "kind", "amount", "balance_after", "channel", "description", "created_at")
