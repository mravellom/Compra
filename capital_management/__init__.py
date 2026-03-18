"""
Capital Management System — capital-aware execution for real-money arbitrage.

SELECT → SIZE → EXECUTE → PROTECT → ADAPT

Principles:
1. Capital preservation over profit maximization.
2. Fail-safe: default is REJECT.
3. Survive losing streaks before optimizing winning ones.
4. Every threshold configurable, no magic numbers.
"""
