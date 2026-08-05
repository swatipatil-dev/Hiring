"""Outbound email queue (RQ + Redis) with rate limiting and retry.

Named ``mailq`` (not ``email``) to avoid any confusion with Python's stdlib
``email`` package that email_service.py imports from.
"""
