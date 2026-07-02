"""LangGraph nodes — one per agent type.

Each agent exposes a single async ``run(state, deps)`` entry point that takes
the current InvestigationState and returns an AgentOutput plus collected
evidence. Wiring into the LangGraph happens in graph.py (W6).
"""
