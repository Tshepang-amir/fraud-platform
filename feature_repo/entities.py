"""Feast entity definitions for fraud platform.

Entities represent the primary keys used to look up feature values.
The card entity (card1 in IEEE-CIS) is the join key — all rolling
statistics are computed and stored per card.
"""

from feast import Entity
from feast.value_type import ValueType

card = Entity(
    name="card_id",
    join_keys=["card1"],
    value_type=ValueType.INT64,
    description="Card identifier (card1 in IEEE-CIS dataset)",
)
