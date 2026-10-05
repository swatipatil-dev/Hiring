"""Multi-tenant email API: connections, senders, sender policy, messages, outbox.

Design and rules: docs/EMAIL_API.md. The short version — every accepted
email has a company, an authorised sender, a fixed delivery connection, an
initiating identity and a durable record, and no company's mail ever takes its
sender from an environment default.
"""
