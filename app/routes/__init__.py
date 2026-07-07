"""Routes package for the Git-Powered Knowledge API."""

# NOTE (open-core P1): This __init__ is intentionally empty of eager imports.
#
# Every ``from .routes.<module> import ...`` statement in app.main or
# app.modules causes Python to initialise the ``app.routes`` package first,
# which means ANY eager import here runs unconditionally.
#
# Routes are registered through two paths:
#   • app.main._configure()  — direct lazy ``from .routes.<X> import`` calls
#   • app.modules.<M>.__init__ — modular lazy imports
#
# Do NOT add module-level imports of route sub-modules here.
