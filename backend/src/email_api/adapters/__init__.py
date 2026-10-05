"""Delivery adapters: one per connection type. See base.py for the contract."""
from .base import get_adapter, UnsupportedConnection  # noqa: F401
