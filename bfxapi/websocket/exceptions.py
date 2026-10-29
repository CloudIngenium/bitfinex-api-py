from bfxapi.exceptions import BfxBaseException


class ConnectionNotOpen(BfxBaseException):
    pass


class BucketRestartLimitError(ConnectionNotOpen):
    """A subscription bucket kept failing fast and its supervisor gave up.

    A `ConnectionNotOpen`: its subscriptions deliver nothing until the client
    reconnects, which gives the bucket a fresh supervisor.
    """


class ActionRequiresAuthentication(BfxBaseException):
    pass


class ReconnectionTimeoutError(BfxBaseException):
    pass


class VersionMismatchError(BfxBaseException):
    pass


class SubIdError(BfxBaseException):
    pass


class UnknownChannelError(BfxBaseException):
    pass


class UnknownEventError(BfxBaseException):
    pass


class UnknownSubscriptionError(BfxBaseException):
    pass
