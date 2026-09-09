"""Delivery channels. One module per mechanism, each satisfying
:class:`~app.notifications.channels.base.NotificationChannel`.

Nothing is imported here: a channel is pulled in by
:func:`app.notifications.setup`, so a deployment never imports a client
library for a channel it does not use.
"""
