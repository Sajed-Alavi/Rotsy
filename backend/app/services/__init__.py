"""Domain services (business logic) used by routers.

Services are framework-agnostic: they take a :class:`~app.core.nexus_client.NexusClient`
(and a :class:`~app.core.cache.Cache` where relevant) and return plain data.
Routers translate these into HTTP responses / SSE streams.
"""
