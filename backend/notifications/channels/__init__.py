"""Delivery channels. One module per mechanism, each satisfying
:class:`~notifications.channels.base.NotificationChannel`.

Nothing is imported here: a channel is pulled in by
:func:`notifications.setup`, so a deployment never imports a client library
for a channel it does not use.
"""
