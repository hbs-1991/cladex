"""Thin adapters over the external SDKs and APIs the app talks to.

Everything here may import `core/` and third-party packages, and nothing else: no
module. That is what makes an integration substitutable — the day
the voice provider or the agent SDK changes, exactly one directory moves.
"""
